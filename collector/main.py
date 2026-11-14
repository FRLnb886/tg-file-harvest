from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Sequence

from . import config, notifier, poller
from .archive import ArchiveError, extract_zip
from .downloader import DownloadResult, SkipFile, download_file, sha256_file
from .models import Channel, FileRecord, Settings, StorageSettings
from .naming import extension, extract_name, sanitize_name
from .storage_github import GitHubStorage, GitHubStorageError
from .storage_r2 import R2Error, R2Storage
from .store import ACCOUNT_KEY, Store, StoreError
from .telegram_client import FloodWaitAbort, TelegramError, TgClient

log = logging.getLogger("collector")

SAVE_EVERY = 50
MAX_ATTEMPTS = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector.main", description="Telegram 频道文件采集")
    parser.add_argument("--config", default="config", help="配置目录")
    parser.add_argument("--dry-run", action="store_true", help="只下载并打印，不上传、不写 R2 状态")
    parser.add_argument(
        "--limit", type=int, default=0, help="每个频道本轮最多入库的文件数，0 表示不限"
    )
    parser.add_argument("--prune-only", action="store_true", help="只清理超过保留期的记录与对象")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = config.load(
            args.config,
            require_telegram=not args.prune_only,
            require_storage=not args.dry_run,
            require_public_url=not args.dry_run and not args.prune_only,
        )
    except config.ConfigError as exc:
        log.error("配置错误：%s", exc)
        return 2
    storage = build_storage(settings.storage)
    store = Store(settings.collect.state_path, backend=storage if storage.configured else None)
    store.load()

    try:
        if args.prune_only:
            prune(settings, store, storage)
            return 0
        asyncio.run(collect(settings, store, storage, args))
    except KeyboardInterrupt:
        log.warning("已中断")
        return 130
    except (TelegramError, R2Error, GitHubStorageError, StoreError) as exc:
        log.error("%s", exc)
        return 1
    finally:
        # dry-run 不能推进游标落盘，否则真正采集会跳过这些消息
        if not args.dry_run:
            store.save()

    log.info("本轮结束，状态分布：%s", store.counts())
    return 0


def build_storage(storage_settings: StorageSettings) -> Any:
    if storage_settings.backend == "github":
        return GitHubStorage(storage_settings)
    return R2Storage(storage_settings)


def prune(settings: Settings, store: Store, storage: Any) -> int:
    expired = store.prune(settings.collect.retention_days)
    keys = [str(record.get("key")) for record in expired if record.get("key")]
    deleted = storage.delete_keys(keys) if keys and storage.configured else 0
    if keys and not storage.configured:
        log.warning("R2 未配置，只清理了本地记录，对象未删除")
    log.info("清理过期记录 %s 条，删除对象 %s 个", len(expired), deleted)
    return len(expired)


async def collect(settings: Settings, store: Store, storage: Any, args: Any) -> None:
    tg = TgClient(
        settings.telegram,
        on_flood_wait=lambda seconds: store.set_cooldown(ACCOUNT_KEY, seconds),
    )
    await tg.connect()
    try:
        for channel in settings.channels:
            if not channel.enabled:
                log.info("跳过停用频道 %s", channel.id)
                continue
            if store.is_cooling_down(ACCOUNT_KEY):
                log.warning("账号仍在 FloodWait 冷却期，本轮不再继续")
                break
            try:
                saved = await handle_channel(tg, store, storage, settings, channel, args)
                log.info("频道 %s 完成，新增入库 %s 个文件", channel.id, saved)
            except FloodWaitAbort as exc:
                log.error("FloodWait 退避超过上限，中止本轮：%s", exc)
                break
            except Exception:
                log.exception("频道 %s 处理失败", channel.id)
            if not args.dry_run:
                store.save()
    finally:
        await tg.disconnect()


async def handle_channel(
    tg: TgClient,
    store: Store,
    storage: Any,
    settings: Settings,
    channel: Channel,
    args: Any,
) -> int:
    if channel.latest_only:
        return await handle_latest(tg, store, storage, settings, channel, args)

    cursor = store.cursor(channel.id)
    log.info("频道 %s 从游标 %s 开始", channel.id, cursor)
    saved = 0
    pending = 0
    async for message in tg.iter_channel_messages(channel.id, cursor):
        outcome = await process_message(tg, store, storage, settings, channel, message, args)
        if outcome == "retry":
            log.warning("停在消息 %s，下次运行重试", message.id)
            break
        store.update_cursor(channel.id, int(message.id))
        pending += 1
        if outcome == "ok":
            saved += 1
        # 首次跑大频道容易被 Actions 的 10 分钟超时杀掉，定期落盘才能从游标续跑
        if not args.dry_run and pending >= SAVE_EVERY:
            store.save()
            pending = 0
        if args.limit and saved >= args.limit:
            log.info("频道 %s 达到本轮上限 %s，提前结束", channel.id, args.limit)
            break
    return saved


async def handle_latest(
    tg: TgClient,
    store: Store,
    storage: Any,
    settings: Settings,
    channel: Channel,
    args: Any,
    scan: int = 50,
) -> int:
    """只取最新一个匹配文件：追版本用，每轮只检查是否有更新，不扫历史。"""
    log.info("频道 %s 只取最新一个匹配文件（向后看 %s 条）", channel.id, scan)
    async for message in tg.iter_latest_messages(channel.id, scan):
        if not poller.is_eligible(message, channel, settings.collect):
            continue
        unique_id = unique_id_of(message)
        if store.should_skip(unique_id):
            known = (store.files.get(unique_id) or {}).get("name", unique_id)
            log.info("最新匹配文件已入库，本轮无需更新：%s", known)
            if not args.dry_run and drop_older_records(store, storage, channel.id, unique_id):
                store.save()
            return 0
        outcome = await process_message(tg, store, storage, settings, channel, message, args)
        if outcome in {"ok", "skipped", "duplicate"}:
            store.update_cursor(channel.id, int(message.id))
        if outcome == "ok" and not args.dry_run:
            drop_older_records(store, storage, channel.id, unique_id)
        if not args.dry_run:
            store.save()
        return 1 if outcome == "ok" else 0
    log.info("频道 %s 最近 %s 条里没有匹配的文件", channel.id, scan)
    return 0


async def process_message(
    tg: TgClient,
    store: Store,
    storage: Any,
    settings: Settings,
    channel: Channel,
    message: Any,
    args: Any,
) -> str:
    if not poller.is_eligible(message, channel, settings.collect):
        return "filtered"

    unique_id = unique_id_of(message)
    if store.should_skip(unique_id):
        return "duplicate"

    name = sanitize_name(extract_name(message))
    try:
        result: DownloadResult = await tg.run(
            lambda: download_file(tg, message, settings.collect.download_dir, channel.max_size)
        )
    except SkipFile as exc:
        if exc.reason == "empty_download":
            return record_failure(store, message, channel, name, exc.reason)
        store.upsert(record_of(message, channel, name, status="skipped", error=exc.reason))
        log.info("跳过 %s：%s", name, exc.reason)
        return "skipped"
    except FloodWaitAbort:
        raise
    except Exception as exc:
        log.exception("下载失败：%s", name)
        return record_failure(store, message, channel, name, f"{type(exc).__name__}: {exc}")

    try:
        if args.dry_run:
            log.info("[dry-run] %s（%s 字节）", name, result.size)
            if channel.extract_zip and extension(name) == "zip":
                try:
                    preview = extract_zip(result.path, Path(settings.collect.download_dir) / ".preview")
                    log.info("[dry-run] 解压后 %s 个文件：%s", len(preview), ", ".join(p.name for p in preview[:5]))
                except ArchiveError as exc:
                    log.warning("[dry-run] 解压失败：%s", exc)
                finally:
                    shutil.rmtree(Path(settings.collect.download_dir) / ".preview", ignore_errors=True)
            return "ok"

        if channel.extract_zip and extension(name) == "zip":
            try:
                parts = upload_zip_parts(
                    storage, channel, result.sha256, Path(result.path), settings.collect.download_dir
                )
            except ArchiveError as exc:
                log.error("解压失败 %s：%s", name, exc)
                return record_failure(store, message, channel, name, f"ArchiveError: {exc}")
            store.upsert(
                record_of(
                    message, channel, name, status="ok", size=result.size,
                    sha256=result.sha256, parts=parts,
                )
            )
            log.info("解压入库 %s -> %s 个文件（首个：%s）", name, len(parts), parts[0]["url"])
            notifier.notify(settings.telegram, name, parts[0]["url"])
            return "ok"

        key, url = storage.upload_and_url(result.sha256, result.path, channel.id, name)
        store.upsert(
            record_of(
                message, channel, name, status="ok", size=result.size,
                sha256=result.sha256, key=key, url=url,
            )
        )
        log.info("入库 %s -> %s", name, url)
        notifier.notify(settings.telegram, name, url)
        return "ok"
    finally:
        Path(result.path).unlink(missing_ok=True)


def record_failure(store: Store, message: Any, channel: Channel, name: str, reason: str) -> str:
    previous = store.files.get(unique_id_of(message)) or {}
    attempts = int(previous.get("attempts") or 0) + 1
    store.upsert(
        record_of(message, channel, name, status="failed", error=reason, attempts=attempts)
    )
    if attempts < MAX_ATTEMPTS:
        log.warning("%s 失败（第 %s/%s 次）：%s", name, attempts, MAX_ATTEMPTS, reason)
        return "retry"
    log.error("%s 连续失败 %s 次，放弃并推进游标：%s", name, attempts, reason)
    return "failed"


def upload_zip_parts(
    storage: Any, channel: Channel, archive_sha: str, archive_path: Path, work_dir: str
) -> list[dict[str, Any]]:
    extract_dir = Path(work_dir) / f".x_{archive_sha[:12]}"
    try:
        files = extract_zip(archive_path, extract_dir)
        parts: list[dict[str, Any]] = []
        for path in files:
            digest = sha256_file(path)
            key, url = storage.upload_and_url(
                digest, str(path), channel.id, path.name, group=archive_sha[:12]
            )
            parts.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": digest,
                    "key": key,
                    "url": url,
                }
            )
        log.info("解压 %s 得到 %s 个文件并已上传", archive_path.name, len(parts))
        return parts
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def drop_older_records(store: Store, storage: Any, channel_id: str, keep_unique_id: str) -> int:
    """latest_only 频道只保留最新一条记录，避免 manifest 里留下资产已被删除的死链。"""
    stale = [
        (uid, record)
        for uid, record in store.files.items()
        if record.get("channel_id") == channel_id and uid != keep_unique_id
    ]
    if not stale:
        return 0
    keys: list[str] = []
    for _uid, record in stale:
        if record.get("key"):
            keys.append(str(record["key"]))
        for part in record.get("parts") or []:
            if part.get("key"):
                keys.append(str(part["key"]))
        store.files.pop(_uid, None)
    if keys:
        try:
            storage.delete_keys(keys)
        except Exception as exc:  # 资产可能已被释放，删不掉不影响状态清理
            log.warning("清理旧对象失败（忽略）：%s", exc)
    log.info("已清理频道 %s 的 %s 条旧记录", channel_id, len(stale))
    return len(stale)


def unique_id_of(message: Any) -> str:
    file = getattr(message, "file", None)
    unique_id = getattr(file, "unique_id", None) if file is not None else None
    if unique_id:
        return str(unique_id)
    return f"{getattr(message, 'chat_id', 'unknown')}:{getattr(message, 'id', 0)}"


def record_of(
    message: Any,
    channel: Channel,
    name: str,
    *,
    status: str,
    size: int = 0,
    sha256: str = "",
    key: str = "",
    url: str = "",
    error: str = "",
    attempts: int = 0,
    parts: list[dict[str, Any]] | None = None,
) -> FileRecord:
    file = getattr(message, "file", None)
    fallback_size = int(getattr(file, "size", 0) or 0) if file is not None else 0
    date = getattr(message, "date", None)
    return FileRecord(
        unique_id=unique_id_of(message),
        channel_id=channel.id,
        message_id=int(getattr(message, "id", 0)),
        name=name,
        size=int(size or fallback_size),
        sha256=sha256,
        key=key,
        url=url,
        ts=int(date.timestamp()) if date is not None else 0,
        status=status,
        error=error,
        attempts=attempts,
        parts=parts or [],
    )


if __name__ == "__main__":
    raise SystemExit(main())
