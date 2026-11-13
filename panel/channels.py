from __future__ import annotations

from typing import Any

import yaml
from fastapi import HTTPException

from collector import config as collector_config
from collector.models import DEFAULT_MAX_SIZE

from .paths import CHANNELS_FILE, CONFIG_DIR

FIELDS = (
    "id",
    "title",
    "enabled",
    "min_size",
    "max_size",
    "include_ext",
    "include_keywords",
    "exclude_keywords",
    "latest_only",
    "extract_zip",
)


def _raw() -> dict[str, Any]:
    if not CHANNELS_FILE.exists():
        return {"channels": []}
    data = yaml.safe_load(CHANNELS_FILE.read_text("utf-8")) or {}
    if not isinstance(data, dict):
        raise HTTPException(500, "channels.yaml 格式错误")
    return data


def list_channels() -> list[dict[str, Any]]:
    items = _raw().get("channels") or []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id", "")),
                "title": str(item.get("title") or item.get("id") or ""),
                "enabled": bool(item.get("enabled", True)),
                "min_size": int(item.get("min_size") or 1024),
                "max_size": int(item.get("max_size") or DEFAULT_MAX_SIZE),
                "include_ext": [str(x) for x in (item.get("include_ext") or [])],
                "include_keywords": [str(x) for x in (item.get("include_keywords") or [])],
                "exclude_keywords": [str(x) for x in (item.get("exclude_keywords") or [])],
                "latest_only": bool(item.get("latest_only", False)),
                "extract_zip": bool(item.get("extract_zip", False)),
            }
        )
    return result


def save_channels(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = {
        "channels": [
            {key: item[key] for key in FIELDS if item.get(key) is not None} for item in items
        ]
    }
    original = CHANNELS_FILE.read_text("utf-8") if CHANNELS_FILE.exists() else None
    CHANNELS_FILE.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), "utf-8"
    )
    # 用采集侧同一套校验器验证，失败就回滚，避免把坏配置写进仓库
    try:
        collector_config.load(CONFIG_DIR)
    except Exception as exc:
        if original is None:
            CHANNELS_FILE.unlink(missing_ok=True)
        else:
            CHANNELS_FILE.write_text(original, "utf-8")
        raise HTTPException(400, str(exc)) from exc
    return list_channels()
