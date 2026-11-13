from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from . import envfile
from .paths import ENV_FILE

log = logging.getLogger("panel.account")


def describe_error(exc: Exception) -> str:
    name = type(exc).__name__
    seconds = getattr(exc, "seconds", None)
    if seconds:
        return f"{name}（需等待 {seconds} 秒）"
    message = str(exc).strip()
    return f"{name}: {message}" if message else name


class AccountManager:
    def __init__(self) -> None:
        self._client: Any = None
        self._api_id = 0
        self._api_hash = ""
        self._phone = ""
        self._code_hash = ""

    def status(self) -> dict[str, Any]:
        stored = envfile.read(ENV_FILE)
        return {
            "has_session": bool(stored.get("TG_SESSION_STRING")),
            "api_id": int(stored.get("TG_API_ID") or 0),
            "api_hash_set": bool(stored.get("TG_API_HASH")),
            "pending": self._client is not None,
            "phone": self._phone,
        }

    async def start(self, api_id: int, api_hash: str, phone: str) -> dict[str, Any]:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_hash = (api_hash or "").strip()
        phone = (phone or "").strip()
        if not api_id or not api_hash:
            raise HTTPException(400, "api_id / api_hash 不能为空")
        if not phone.startswith("+") or not phone[1:].isdigit():
            raise HTTPException(400, "手机号要带国家码，例如 +8613800000000")

        await self._disconnect()
        self._api_id, self._api_hash, self._phone = int(api_id), api_hash, phone
        self._client = TelegramClient(StringSession(), self._api_id, self._api_hash)
        try:
            await self._client.connect()
            sent = await self._client.send_code_request(self._phone)
            self._code_hash = sent.phone_code_hash
        except Exception as exc:
            await self._disconnect()
            raise HTTPException(400, f"发送验证码失败：{describe_error(exc)}") from exc
        return {"need_code": True, "phone": self._phone}

    async def submit_code(self, code: str) -> dict[str, Any]:
        from telethon.errors import SessionPasswordNeededError

        if self._client is None:
            raise HTTPException(400, "请先点击发送验证码")
        try:
            await self._client.sign_in(
                phone=self._phone, code=code.strip(), phone_code_hash=self._code_hash
            )
        except SessionPasswordNeededError:
            return {"need_password": True}
        except Exception as exc:
            raise HTTPException(400, f"验证码登录失败：{describe_error(exc)}") from exc
        return await self._finish()

    async def submit_password(self, password: str) -> dict[str, Any]:
        if self._client is None:
            raise HTTPException(400, "请先完成验证码这一步")
        try:
            await self._client.sign_in(password=password)
        except Exception as exc:
            raise HTTPException(400, f"两步验证失败：{describe_error(exc)}") from exc
        return await self._finish()

    async def check(self) -> dict[str, Any]:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        stored = envfile.read(ENV_FILE)
        session = stored.get("TG_SESSION_STRING", "")
        api_id = int(stored.get("TG_API_ID") or 0)
        api_hash = stored.get("TG_API_HASH", "")
        if not session:
            return {"ok": False, "reason": "还没有保存 session，请先登录"}
        if not api_id or not api_hash:
            return {"ok": False, "reason": "缺少 TG_API_ID / TG_API_HASH"}

        client = TelegramClient(StringSession(session), api_id, api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return {"ok": False, "reason": "session 已失效或被注销，请重新登录"}
            me = await client.get_me()
            return {"ok": True, "user": self._user_dict(me)}
        except Exception as exc:
            return {"ok": False, "reason": describe_error(exc)}
        finally:
            await client.disconnect()

    async def logout(self) -> dict[str, Any]:
        stored = envfile.read(ENV_FILE)
        session = stored.get("TG_SESSION_STRING", "")
        if session:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            client = TelegramClient(
                StringSession(session),
                int(stored.get("TG_API_ID") or 0),
                stored.get("TG_API_HASH", ""),
            )
            try:
                await client.connect()
                await client.log_out()
            except Exception as exc:
                log.warning("远端注销失败，仅清理本地 session：%s", exc)
            finally:
                await client.disconnect()
        envfile.write_values(ENV_FILE, {"TG_SESSION_STRING": ""})
        await self._disconnect()
        return {"ok": True}

    async def _finish(self) -> dict[str, Any]:
        me = await self._client.get_me()
        session = self._client.session.save()
        await self._disconnect()
        envfile.write_values(
            ENV_FILE,
            {
                "TG_API_ID": str(self._api_id),
                "TG_API_HASH": self._api_hash,
                "TG_SESSION_STRING": session,
            },
        )
        user = self._user_dict(me)
        log.info("账号登录成功：%s", user.get("id"))
        return {"ok": True, "user": user}

    async def _disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    @staticmethod
    def _user_dict(me: Any) -> dict[str, Any]:
        full_name = " ".join(
            part
            for part in (getattr(me, "first_name", "") or "", getattr(me, "last_name", "") or "")
            if part
        )
        return {
            "id": getattr(me, "id", None),
            "name": full_name or None,
            "username": getattr(me, "username", None),
            "phone": getattr(me, "phone", None),
        }
