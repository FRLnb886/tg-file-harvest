from __future__ import annotations

import hmac
import os
import secrets

from fastapi import Header, HTTPException

from .paths import TOKEN_FILE, ensure_dirs


def get_token() -> str:
    configured = os.getenv("PANEL_TOKEN")
    if configured:
        return configured.strip()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text("utf-8").strip()
        if token:
            return token
    ensure_dirs()
    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token, "utf-8")
    os.chmod(TOKEN_FILE, 0o600)
    return token


def require_token(x_panel_token: str | None = Header(default=None)) -> None:
    expected = get_token()
    if not x_panel_token or not hmac.compare_digest(x_panel_token, expected):
        raise HTTPException(status_code=401, detail="访问令牌无效")
