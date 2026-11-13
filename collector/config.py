from __future__ import annotations

import os
from pathlib import Path
from typing import Any, TypeVar, get_type_hints

import yaml

from .models import (
    DEFAULT_MAX_SIZE,
    Channel,
    CollectSettings,
    Settings,
    StorageSettings,
    TelegramSettings,
)

T = TypeVar("T")


class ConfigError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} 顶层必须是映射")
    return data


def _build(cls: type[T], raw: Any, where: str) -> T:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 必须是映射")
    unknown = set(raw) - set(cls.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"{where} 存在未知字段: {', '.join(sorted(unknown))}")

    hints = get_type_hints(cls)
    values: dict[str, Any] = {}
    for name in cls.__dataclass_fields__:
        if name not in raw:
            continue
        value = raw[name]
        expected = hints.get(name)
        # YAML 里写成 "500" 这种带引号的数字时，先归一再比较，否则会抛 TypeError 而不是 ConfigError
        if expected is int and not isinstance(value, bool):
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{where}.{name} 必须是整数") from exc
        elif expected is float:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"{where}.{name} 必须是数字") from exc
        elif expected is bool and isinstance(value, str):
            value = value.strip().lower() in {"1", "true", "yes", "on"}
        elif expected is str and not isinstance(value, str):
            value = str(value)
        values[name] = value
    return cls(**values)


def _env(name: str, current: str) -> str:
    value = os.getenv(name)
    return value.strip() if value else current


def _env_int(name: str, current: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return current
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 必须是整数") from exc


def _apply_env(settings: Settings) -> None:
    tg = settings.telegram
    tg.api_id = _env_int("TG_API_ID", tg.api_id)
    tg.api_hash = _env("TG_API_HASH", tg.api_hash)
    tg.session = _env("TG_SESSION_STRING", tg.session)
    tg.bot_token = _env("BOT_TOKEN", tg.bot_token)
    tg.notify_chat_id = _env("BOT_CHAT_ID", tg.notify_chat_id)

    st = settings.storage
    st.account_id = _env("R2_ACCOUNT_ID", st.account_id)
    st.access_key_id = _env("R2_ACCESS_KEY_ID", st.access_key_id)
    st.secret_access_key = _env("R2_SECRET_ACCESS_KEY", st.secret_access_key)
    st.bucket = _env("R2_BUCKET", st.bucket)
    st.public_base = _env("R2_PUBLIC_BASE", st.public_base)
    st.state_bucket = _env("R2_STATE_BUCKET", st.state_bucket)
    st.state_key = _env("R2_STATE_KEY", st.state_key)
    st.key_prefix = _env("R2_KEY_PREFIX", st.key_prefix)


def _build_channels(raw: dict[str, Any]) -> list[Channel]:
    items = raw.get("channels")
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError("channels.yaml: channels 必须是列表")
    channels: list[Channel] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        channel = _build(Channel, item, f"channels.yaml: channels[{index}]")
        if not channel.id:
            raise ConfigError(f"channels.yaml: channels[{index}].id 不能为空")
        if channel.id in seen:
            raise ConfigError(f"channels.yaml: 频道 id 重复: {channel.id}")
        seen.add(channel.id)
        channels.append(channel)
    return channels


def validate(
    settings: Settings,
    *,
    require_telegram: bool = False,
    require_storage: bool = False,
    require_public_url: bool = False,
) -> None:
    collect = settings.collect
    if collect.max_size > DEFAULT_MAX_SIZE:
        raise ConfigError(f"collect.max_size 不能超过 {DEFAULT_MAX_SIZE}（500MB）")
    if collect.min_size < 0 or collect.min_size >= collect.max_size:
        raise ConfigError("collect.min_size 必须 >= 0 且小于 collect.max_size")
    if collect.retention_days < 0:
        raise ConfigError("collect.retention_days 不能为负")

    for channel in settings.channels:
        if channel.max_size > DEFAULT_MAX_SIZE:
            raise ConfigError(f"频道 {channel.id} 的 max_size 超过 500MB 上限")
        if channel.min_size < 0 or channel.min_size >= channel.max_size:
            raise ConfigError(f"频道 {channel.id} 的 min_size 必须 >= 0 且小于 max_size")

    if require_telegram:
        tg = settings.telegram
        if not settings.channels:
            raise ConfigError("channels.yaml 里没有任何频道，采集会空跑")
        if not tg.session:
            raise ConfigError("缺少 TG_SESSION_STRING（先本地运行 scripts/login.py 生成）")
        if not tg.api_id or not tg.api_hash:
            raise ConfigError("缺少 TG_API_ID / TG_API_HASH")
        if tg.rate_limit < 0:
            raise ConfigError("telegram.rate_limit 不能为负")

    if require_storage and not settings.storage.configured:
        raise ConfigError(
            "R2 配置不完整：需要 R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET"
        )

    if require_public_url and not settings.storage.public_base:
        raise ConfigError("缺少 R2_PUBLIC_BASE：没有它无法生成下载直链，采集会在下载完才失败")


def load(
    config_dir: str | Path = "config",
    *,
    require_telegram: bool = False,
    require_storage: bool = False,
    require_public_url: bool = False,
) -> Settings:
    base = Path(config_dir)
    raw = _read_yaml(base / "settings.yaml")
    raw_channels = _read_yaml(base / "channels.yaml")

    unknown = set(raw) - {"telegram", "storage", "collect"}
    if unknown:
        raise ConfigError(f"settings.yaml 存在未知字段: {', '.join(sorted(unknown))}")
    unknown_channels = set(raw_channels) - {"channels"}
    if unknown_channels:
        raise ConfigError(f"channels.yaml 存在未知字段: {', '.join(sorted(unknown_channels))}")

    settings = Settings(
        telegram=_build(TelegramSettings, raw.get("telegram"), "settings.yaml: telegram"),
        storage=_build(StorageSettings, raw.get("storage"), "settings.yaml: storage"),
        collect=_build(CollectSettings, raw.get("collect"), "settings.yaml: collect"),
        channels=_build_channels(raw_channels),
    )
    _apply_env(settings)
    validate(
        settings,
        require_telegram=require_telegram,
        require_storage=require_storage,
        require_public_url=require_public_url,
    )
    return settings
