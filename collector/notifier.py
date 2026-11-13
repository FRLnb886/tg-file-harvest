from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from .models import TelegramSettings

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


def notify(telegram: TelegramSettings, name: str, url: str) -> bool:
    if not telegram.bot_token or not telegram.notify_chat_id:
        return False

    payload = json.dumps(
        {
            "chat_id": telegram.notify_chat_id,
            "text": f"新文件：{name}\n{url}",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/bot{telegram.bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
        return True
    except (urllib.error.URLError, OSError) as exc:
        # HTTPError 是 URLError 的子类，4xx/5xx 也会落到这里
        log.warning("通知失败：%s", exc)
        return False
