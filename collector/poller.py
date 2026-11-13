from __future__ import annotations

from typing import Any, AsyncIterator

from .models import Channel, CollectSettings
from .naming import extension, extract_name


async def poll_messages(client: Any, channel_id: str, cursor: int = 0) -> AsyncIterator[Any]:
    # reverse=True 保证从旧到新，游标才能单调推进；min_id 做增量，不用 offset_id 翻页
    async for message in client.iter_messages(channel_id, min_id=int(cursor or 0), reverse=True):
        yield message


def is_eligible(message: Any, channel: Channel, collect: CollectSettings) -> bool:
    file = getattr(message, "file", None)
    if file is None:
        return False

    size = int(getattr(file, "size", 0) or 0)
    min_size = max(channel.min_size, collect.min_size)
    max_size = min(channel.max_size, collect.max_size)
    if size and size < min_size:
        return False
    if size and size > max_size:
        return False

    name = extract_name(message)
    ext = extension(name)
    if channel.include_ext and ext not in channel.include_ext:
        return False

    haystack = f"{name}\n{getattr(message, 'caption', '') or ''}\n{getattr(message, 'text', '') or ''}"
    lowered = haystack.lower()
    # include_keywords 非空时，至少命中一个才采集
    if channel.include_keywords:
        if not any(keyword.lower() in lowered for keyword in channel.include_keywords):
            return False
    for keyword in channel.exclude_keywords:
        if keyword.lower() in lowered:
            return False
    return True
