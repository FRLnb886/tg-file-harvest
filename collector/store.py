from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .models import FileRecord

log = logging.getLogger(__name__)

ACCOUNT_KEY = "default"


class StateBackend(Protocol):
    def get_json(self) -> dict[str, Any] | None: ...

    def put_json(self, payload: dict[str, Any]) -> None: ...


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, state_path: str | Path, backend: StateBackend | None = None) -> None:
        self._path = Path(state_path)
        self._backend = backend
        self.cursors: dict[str, int] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.cooldowns: dict[str, str] = {}

    def load(self) -> None:
        data = None
        if self._backend is not None:
            data = self._backend.get_json()
        if data is None and self._path.exists():
            try:
                data = json.loads(self._path.read_text("utf-8"))
            except json.JSONDecodeError as exc:
                raise StoreError(f"状态文件损坏: {self._path}") from exc
        if not data:
            return
        if not isinstance(data, dict):
            raise StoreError(f"状态文件格式错误: {self._path}")
        self.cursors = {str(k): int(v) for k, v in (data.get("cursors") or {}).items()}
        self.files = dict(data.get("files") or {})
        self.cooldowns = dict(data.get("cooldowns") or {})
        log.info("已加载状态：%s 个频道游标，%s 条文件记录", len(self.cursors), len(self.files))

    def save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": int(time.time()),
            "cursors": self.cursors,
            "files": self.files,
            "cooldowns": self.cooldowns,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), "utf-8")
        os.replace(tmp, self._path)
        if self._backend is not None:
            self._backend.put_json(payload)

    def cursor(self, channel_id: str) -> int:
        return int(self.cursors.get(str(channel_id), 0))

    def update_cursor(self, channel_id: str, message_id: int) -> bool:
        key = str(channel_id)
        current = int(self.cursors.get(key, 0))
        incoming = int(message_id)
        if incoming <= current:
            return False
        self.cursors[key] = incoming
        return True

    def should_skip(self, unique_id: str) -> bool:
        record = self.files.get(str(unique_id))
        return bool(record) and record.get("status") == "ok"

    def upsert(self, record: FileRecord) -> None:
        self.files[record.unique_id] = record.to_dict()

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for record in self.files.values():
            status = str(record.get("status") or "unknown")
            result[status] = result.get(status, 0) + 1
        return result

    def set_cooldown(self, account_id: str, seconds: int) -> str:
        until = datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))
        value = until.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.cooldowns[account_id] = value
        return value

    def is_cooling_down(self, account_id: str = ACCOUNT_KEY) -> bool:
        raw = self.cooldowns.get(account_id)
        if not raw:
            return False
        try:
            until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            self.cooldowns.pop(account_id, None)
            return False
        if until <= datetime.now(timezone.utc):
            self.cooldowns.pop(account_id, None)
            return False
        return True

    def prune(self, retention_days: int) -> list[dict[str, Any]]:
        if retention_days <= 0:
            return []
        cutoff = int(time.time()) - retention_days * 86400
        expired = [rec for rec in self.files.values() if int(rec.get("ts") or 0) < cutoff]
        for record in expired:
            self.files.pop(record.get("unique_id", ""), None)
        return expired
