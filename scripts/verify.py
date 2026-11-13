#!/usr/bin/env python3
"""自检：配置、状态、manifest 一致性；可选连 Telegram 与 R2 做真实探测。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from collector import config as config_module  # noqa: E402
from collector.models import Settings  # noqa: E402
from collector.storage_github import GitHubStorage  # noqa: E402
from collector.storage_r2 import R2Storage  # noqa: E402


class Checks:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def ok(self, name: str, detail: str = "") -> None:
        print(f"[ OK ] {name}{' - ' + detail if detail else ''}")

    def fail(self, name: str, detail: str = "") -> None:
        self.failures += 1
        print(f"[FAIL] {name}{' - ' + detail if detail else ''}")

    def warn(self, name: str, detail: str = "") -> None:
        self.warnings += 1
        print(f"[WARN] {name}{' - ' + detail if detail else ''}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.ok(name, detail)
        else:
            self.fail(name, detail)
        return condition


def load_settings(checks: Checks, config_dir: str, need_telegram: bool) -> Settings | None:
    try:
        settings = config_module.load(
            config_dir, require_telegram=need_telegram, require_storage=False
        )
    except (config_module.ConfigError, OSError) as exc:
        checks.fail("配置可加载", str(exc))
        return None

    checks.ok("配置可加载", f"{len(settings.channels)} 个频道")
    checks.check("频道列表非空", bool(settings.channels), "channels.yaml 里至少配一个频道")

    backend = settings.storage.backend
    if backend == "github":
        checks.ok("存储后端", f"github（{settings.storage.github_repo or '未设置仓库'}）")
        if not settings.storage.github_repo or not settings.storage.github_token:
            checks.warn("GitHub 存储凭据", "需要 GITHUB_REPOSITORY 与 GITHUB_TOKEN")
    else:
        checks.ok("存储后端", "r2")
        if settings.storage.configured:
            checks.ok("R2 凭据齐全", settings.storage.bucket)
        else:
            checks.warn("R2 凭据齐全", "未配置，只能 dry-run，无法上传")
        if settings.storage.public_base:
            checks.ok("公开直链前缀", settings.storage.public_base)
        else:
            checks.warn("公开直链前缀", "缺少 R2_PUBLIC_BASE，无法生成直链")
    return settings


def check_state(checks: Checks, state_path: Path) -> dict[str, Any] | None:
    if not state_path.exists():
        checks.warn("状态文件存在", f"{state_path} 不存在，先跑一次采集")
        return None
    try:
        state = json.loads(state_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        checks.fail("状态文件合法", str(exc))
        return None
    checks.ok("状态文件合法", str(state_path))

    files = state.get("files") or {}
    counts: dict[str, int] = {}
    for record in files.values():
        status = str(record.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    checks.ok("记录统计", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "空")
    checks.check(
        "游标存在",
        bool(state.get("cursors")),
        f"{len(state.get('cursors') or {})} 个频道已推进",
    )
    return state


def check_manifest(checks: Checks, manifest_path: Path, state: dict[str, Any] | None) -> None:
    if not manifest_path.exists():
        checks.warn("manifest 存在", f"{manifest_path} 不存在")
        return
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        checks.fail("manifest 合法", str(exc))
        return

    items = manifest.get("items")
    if not isinstance(items, list):
        checks.fail("manifest 合法", "items 必须是列表")
        return
    checks.ok("manifest 合法", f"{len(items)} 条，updated_at={manifest.get('updated_at', '-')}")

    bad_urls = [item for item in items if not str(item.get("url", "")).startswith("http")]
    checks.check("manifest 直链完整", not bad_urls, f"{len(bad_urls)} 条缺 url")

    ts_values = [int(item.get("ts") or 0) for item in items]
    checks.check("manifest 按时间倒序", ts_values == sorted(ts_values, reverse=True))

    if state is None:
        return
    ok_ids = {
        str(record.get("unique_id"))
        for record in (state.get("files") or {}).values()
        if record.get("status") == "ok"
    }
    extra = [str(item.get("id")) for item in items if str(item.get("id")) not in ok_ids]
    checks.check("manifest 记录均来自已入库", not extra, f"{len(extra)} 条在 state 里找不到对应 ok 记录")


def check_storage(checks: Checks, settings: Settings) -> None:
    if settings.storage.backend != "github":
        check_r2(checks, settings)
        return
    storage = GitHubStorage(settings.storage)
    if not storage.configured:
        checks.warn("GitHub 存储连通性", "缺少仓库或 token，跳过")
        return
    try:
        release_id = storage._release_id(settings.storage.github_tag, create=False)
        checks.ok(
            "Release 可读",
            f"tag={settings.storage.github_tag} id={release_id}" if release_id else "还没有 release，首次采集会自动创建",
        )
    except Exception as exc:
        checks.fail("Release 可读", f"{type(exc).__name__}: {exc}")
        return
    try:
        state = storage.get_json() or {}
        checks.ok("状态可读", f"{len(state.get('files') or {})} 条记录")
    except Exception as exc:
        checks.fail("状态可读", f"{type(exc).__name__}: {exc}")


def check_r2(checks: Checks, settings: Settings) -> None:
    storage = R2Storage(settings.storage)
    if not storage.configured:
        checks.warn("R2 连通性", "未配置，跳过")
        return
    probe_key = ".probe/verify.txt"
    body = b"tg-file-harvest probe"
    try:
        storage.head_bucket()
        checks.ok("R2 桶可访问", settings.storage.bucket)
        storage.put_bytes(probe_key, body)
        checks.check("R2 读写一致", storage.get_bytes(probe_key) == body)
        storage.delete_keys([probe_key])
        checks.check("R2 删除探针", not storage.exists(probe_key))
    except Exception as exc:  # boto3 的异常类型太多，探测阶段统一视为失败
        checks.fail("R2 连通性", f"{type(exc).__name__}: {exc}")


async def check_telegram(checks: Checks, settings: Settings) -> None:
    from collector.telegram_client import TgClient

    tg = TgClient(settings.telegram)
    try:
        await tg.connect()
    except Exception as exc:
        checks.fail("Telegram 登录", f"{type(exc).__name__}: {exc}")
        return

    checks.ok("Telegram 登录", "session 有效")
    try:
        for channel in settings.channels:
            if not channel.enabled:
                continue
            try:
                entity = await tg.raw.get_entity(channel.id)
                title = getattr(entity, "title", None) or getattr(entity, "username", None)
                checks.ok(f"频道可读 {channel.id}", str(title))
            except Exception as exc:
                checks.fail(f"频道可读 {channel.id}", f"{type(exc).__name__}: {exc}")
    finally:
        await tg.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="项目自检")
    parser.add_argument("--config", default="config")
    parser.add_argument("--state", default="work/state.json")
    parser.add_argument("--manifest", default="web/data/manifest.json")
    parser.add_argument("--telegram", action="store_true", help="连 Telegram 验证 session 与频道可读")
    parser.add_argument("--storage", "--r2", dest="storage", action="store_true",
                        help="连存储后端验证（github release 或 R2 读写权限）")
    args = parser.parse_args(argv)

    checks = Checks()
    settings = load_settings(checks, args.config, need_telegram=args.telegram)
    state = check_state(checks, Path(args.state))
    check_manifest(checks, Path(args.manifest), state)

    if settings is not None:
        if args.storage:
            check_storage(checks, settings)
        if args.telegram:
            asyncio.run(check_telegram(checks, settings))

    print(f"\n结果：{checks.failures} 项失败，{checks.warnings} 项警告")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
