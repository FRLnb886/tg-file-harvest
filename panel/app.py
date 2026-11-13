from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import channels as channel_store
from .account import AccountManager
from .paths import STATE_PATH, WEB_DIR
from .runs import RunManager
from .security import require_token

MAX_SIZE = 500 * 1024 * 1024

app = FastAPI(title="tg-file-harvest panel", docs_url=None, redoc_url=None)
api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
account = AccountManager()
runs = RunManager()


class LoginStart(BaseModel):
    api_id: int = Field(gt=0, le=10**12)
    api_hash: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=2, max_length=32)


class LoginCode(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class LoginPassword(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ChannelItem(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = ""
    enabled: bool = True
    min_size: int = Field(default=1024, ge=0)
    max_size: int = Field(default=MAX_SIZE, ge=1)
    include_ext: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)


class ChannelPayload(BaseModel):
    items: list[ChannelItem]


class CollectRequest(BaseModel):
    dry_run: bool = False
    limit: int = Field(default=0, ge=0, le=100000)


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except json.JSONDecodeError:
        return {}


@api.get("/summary")
def summary() -> dict[str, Any]:
    state = read_state()
    files = state.get("files") or {}
    counts: dict[str, int] = {}
    for record in files.values():
        status = str(record.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    channels = channel_store.list_channels()
    return {
        "mode": "panel",
        "account": account.status(),
        "channels": len(channels),
        "enabled_channels": sum(1 for channel in channels if channel["enabled"]),
        "counts": counts,
        "items": len(files),
        "keywords": sorted({kw for channel in channels for kw in channel["include_keywords"]}),
        "last_run": runs.latest(),
        "state_updated_at": state.get("updated_at"),
        "channels_detail": channels,
    }


@api.get("/records")
def records(
    status: str = "",
    q: str = "",
    channel: str = "",
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    files = read_state().get("files") or {}
    needle = q.strip().lower()
    rows: list[dict[str, Any]] = []
    for unique_id, record in files.items():
        if status and record.get("status") != status:
            continue
        if channel and record.get("channel_id") != channel:
            continue
        if needle and needle not in str(record.get("name", "")).lower():
            continue
        rows.append(
            {
                "id": str(unique_id),
                "name": str(record.get("name", "")),
                "size": int(record.get("size") or 0),
                "channel": str(record.get("channel_id", "")),
                "status": str(record.get("status") or "unknown"),
                "ts": int(record.get("ts") or 0),
                "url": str(record.get("url") or ""),
                "error": str(record.get("error") or ""),
                "attempts": int(record.get("attempts") or 0),
            }
        )
    rows.sort(key=lambda row: (-row["ts"], row["id"]))
    safe_limit = max(1, min(int(limit), 500))
    safe_offset = max(0, int(offset))
    return {
        "total": len(rows),
        "offset": safe_offset,
        "limit": safe_limit,
        "items": rows[safe_offset : safe_offset + safe_limit],
    }


@api.get("/channels")
def get_channels() -> dict[str, Any]:
    return {"items": channel_store.list_channels()}


@api.put("/channels")
def put_channels(payload: ChannelPayload) -> dict[str, Any]:
    items = [item.model_dump() for item in payload.items]
    return {"items": channel_store.save_channels(items)}


@api.get("/account")
def account_status() -> dict[str, Any]:
    return account.status()


@api.post("/account/login/start")
async def login_start(payload: LoginStart) -> dict[str, Any]:
    return await account.start(payload.api_id, payload.api_hash, payload.phone)


@api.post("/account/login/code")
async def login_code(payload: LoginCode) -> dict[str, Any]:
    return await account.submit_code(payload.code)


@api.post("/account/login/password")
async def login_password(payload: LoginPassword) -> dict[str, Any]:
    return await account.submit_password(payload.password)


@api.post("/account/check")
async def account_check() -> dict[str, Any]:
    return await account.check()


@api.post("/account/logout")
async def account_logout() -> dict[str, Any]:
    return await account.logout()


@api.post("/collect")
def collect(payload: CollectRequest) -> dict[str, Any]:
    return runs.start(payload.dry_run, payload.limit)


@api.get("/runs")
def list_runs() -> dict[str, Any]:
    return {"items": runs.list_runs()}


@api.get("/runs/{run_id}")
def run_log(run_id: str, offset: int = 0) -> dict[str, Any]:
    if not run_id.replace("-", "").isdigit():
        raise HTTPException(400, "运行 id 不合法")
    return runs.tail(run_id, offset)


app.include_router(api)
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
