from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_MAX_SIZE = 500 * 1024 * 1024


@dataclass
class Channel:
    id: str
    title: str = ""
    enabled: bool = True
    min_size: int = 1024
    max_size: int = DEFAULT_MAX_SIZE
    include_ext: list[str] | None = None
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    latest_only: bool = False
    extract_zip: bool = False

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        self.title = str(self.title or self.id)
        if self.include_ext:
            self.include_ext = [str(ext).lower().lstrip(".") for ext in self.include_ext]
        self.include_keywords = [str(kw).strip() for kw in self.include_keywords if str(kw).strip()]
        self.exclude_keywords = [str(kw).strip() for kw in self.exclude_keywords if str(kw).strip()]


@dataclass
class TelegramSettings:
    api_id: int = 0
    api_hash: str = ""
    session: str = ""
    rate_limit: float = 1.5
    flood_extra: int = 5
    flood_max_sleep: int = 300
    bot_token: str = ""
    notify_chat_id: str = ""


@dataclass
class StorageSettings:
    account_id: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    public_base: str = ""
    state_bucket: str = ""
    state_key: str = "state/state.json"
    key_prefix: str = ""
    endpoint: str = ""

    @property
    def configured(self) -> bool:
        return all([self.account_id, self.access_key_id, self.secret_access_key, self.bucket])

    @property
    def resolved_state_bucket(self) -> str:
        return self.state_bucket or self.bucket

    @property
    def resolved_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if not self.account_id:
            return ""
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


@dataclass
class CollectSettings:
    min_size: int = 1024
    max_size: int = DEFAULT_MAX_SIZE
    retention_days: int = 90
    download_dir: str = "work/downloads"
    state_path: str = "work/state.json"


@dataclass
class Settings:
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    collect: CollectSettings = field(default_factory=CollectSettings)
    channels: list[Channel] = field(default_factory=list)


@dataclass
class FileRecord:
    unique_id: str
    channel_id: str
    message_id: int
    name: str
    size: int = 0
    sha256: str = ""
    key: str = ""
    url: str = ""
    ts: int = 0
    status: str = "pending"
    error: str = ""
    attempts: int = 0
    parts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
