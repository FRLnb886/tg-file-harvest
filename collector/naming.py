from __future__ import annotations

import re
import unicodedata
from typing import Any

INVALID_CHARS = frozenset('<>:"/\\|?*')
CONTROL_CHARS = frozenset(chr(code) for code in range(32)) | {chr(127)}
MAX_NAME_LENGTH = 120
FALLBACK_EXT = "bin"

NAME_PATTERN = re.compile(
    r"[^\s\\/:*?\"<>|]{1,120}?\."
    r"(?:zip|rar|7z|tar|gz|xz|pdf|apk|apks|iso|mkv|mp4|avi|mov|flv|txt|epub|mobi|djvu|"
    r"docx?|xlsx?|pptx?|csv|json|ipa|exe|msi|dmg|img|bin)",
    re.IGNORECASE,
)


def _media_name(message: Any) -> str | None:
    file = getattr(message, "file", None)
    name = getattr(file, "name", None) if file is not None else None
    if name:
        return str(name)
    for attribute in ("document", "video", "audio", "voice"):
        media = getattr(message, attribute, None)
        candidate = getattr(media, "file_name", None) if media is not None else None
        if candidate:
            return str(candidate)
    return None


def extract_name(message: Any) -> str:
    name = _media_name(message)
    if name:
        return name

    text = getattr(message, "caption", None) or getattr(message, "text", None) or ""
    match = NAME_PATTERN.search(str(text))
    if match:
        return match.group(0)

    chat = getattr(message, "chat", None)
    label = getattr(chat, "username", None) or getattr(chat, "id", None)
    if not label:
        label = getattr(message, "chat_id", "unknown")
    file = getattr(message, "file", None)
    ext = (getattr(file, "ext", None) or FALLBACK_EXT) if file is not None else FALLBACK_EXT
    return f"{label}_{getattr(message, 'id', 0)}.{ext}"


def sanitize_name(name: str, max_length: int = MAX_NAME_LENGTH) -> str:
    cleaned = unicodedata.normalize("NFC", str(name or ""))
    cleaned = cleaned.replace("\\", "/").split("/")[-1]
    cleaned = "".join("_" if (ch in INVALID_CHARS or ch in CONTROL_CHARS) else ch for ch in cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return f"unnamed.{FALLBACK_EXT}"
    if len(cleaned) <= max_length:
        return cleaned

    stem, dot, ext = cleaned.rpartition(".")
    if dot and ext and len(ext) <= 10:
        keep = max_length - len(ext) - 1
        trimmed = stem[:keep].rstrip(" .")
        if trimmed:
            return f"{trimmed}.{ext}"
    return cleaned[:max_length].rstrip(" .") or f"unnamed.{FALLBACK_EXT}"


def extension(name: str) -> str:
    _, dot, ext = str(name or "").rpartition(".")
    return ext.lower() if dot else ""
