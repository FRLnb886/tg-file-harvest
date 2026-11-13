from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from .models import TelegramSettings
from .poller import poll_messages

log = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramConfigError(TelegramError):
    pass


class FloodWaitAbort(TelegramError):
    pass


class TgClient:
    def __init__(
        self,
        settings: TelegramSettings,
        on_flood_wait: Callable[[int], None] | None = None,
    ) -> None:
        self._settings = settings
        self._on_flood_wait = on_flood_wait
        self._client: Any = None

    @property
    def raw(self) -> Any:
        if self._client is None:
            raise TelegramError("Telegram 客户端尚未连接")
        return self._client

    async def connect(self) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        settings = self._settings
        if not settings.session:
            raise TelegramConfigError("缺少 TG_SESSION_STRING，请先运行 scripts/login.py")
        if not settings.api_id or not settings.api_hash:
            raise TelegramConfigError("缺少 TG_API_ID / TG_API_HASH")

        self._client = TelegramClient(
            StringSession(settings.session), settings.api_id, settings.api_hash
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self.disconnect()
            raise TelegramConfigError("session 已失效或被注销，请重新运行 scripts/login.py")

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def reconnect(self) -> None:
        await self.disconnect()
        await asyncio.sleep(2)
        await self.connect()

    async def run(self, factory: Callable[[], Awaitable[Any]]) -> Any:
        from telethon.errors import FloodWaitError

        await asyncio.sleep(max(0.0, float(self._settings.rate_limit)))
        try:
            return await factory()
        except FloodWaitError as exc:
            wait = self._flood_delay(exc)
            await asyncio.sleep(wait)
            return await factory()
        except (ConnectionError, asyncio.TimeoutError, OSError) as exc:
            log.warning("网络异常，重连后重试：%s", exc)
            await self.reconnect()
            return await factory()

    async def iter_channel_messages(
        self, channel_id: str, cursor: int = 0
    ) -> AsyncIterator[Any]:
        from telethon.errors import FloodWaitError

        try:
            async for message in poll_messages(self.raw, channel_id, cursor):
                yield message
        except FloodWaitError as exc:
            # 消息流异常后无法续读，记录冷却后直接结束本轮，下次 cron 从游标继续
            wait = self._flood_delay(exc)
            raise FloodWaitAbort(f"拉取消息时命中 FloodWait {wait}s") from exc

    async def iter_latest_messages(
        self, channel_id: str, limit: int = 50
    ) -> AsyncIterator[Any]:
        from telethon.errors import FloodWaitError

        try:
            async for message in self.raw.iter_messages(channel_id, limit=limit):
                yield message
        except FloodWaitError as exc:
            wait = self._flood_delay(exc)
            raise FloodWaitAbort(f"拉取最新消息时命中 FloodWait {wait}s") from exc

    def _flood_delay(self, exc: Any) -> int:
        wait = int(getattr(exc, "seconds", 0) or 0) + int(self._settings.flood_extra)
        log.warning("Telegram 限流，需要等待 %s 秒", wait)
        if self._on_flood_wait is not None:
            self._on_flood_wait(wait)
        if wait > int(self._settings.flood_max_sleep):
            raise FloodWaitAbort(
                f"FloodWait {wait}s 超过上限 {self._settings.flood_max_sleep}s"
            ) from exc
        return wait
