from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SkipFile(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class DownloadResult:
    path: str
    sha256: str
    size: int


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def download_file(
    tg: Any, message: Any, dest_dir: str | Path, max_size: int
) -> DownloadResult:
    file = getattr(message, "file", None)
    if file is None:
        raise SkipFile("no_file")

    size = int(getattr(file, "size", 0) or 0)
    if size and size > max_size:
        raise SkipFile("too_large")

    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    token = str(getattr(file, "unique_id", None) or f"{message.chat_id}_{message.id}")
    tmp_path = destination / f".tmp_{token}"

    try:
        # 传文件对象而不是路径：传路径时 Telethon 会给无扩展名的 .tmp_xxx 补上媒体后缀，
        # 实际写盘到 .tmp_xxx.zip（同名冲突还会变成 " (1).zip"），导致下面的存在性判断失真
        with open(tmp_path, "wb") as handle:
            await tg.raw.download_media(message, file=handle)
        if not tmp_path.exists() or tmp_path.stat().st_size == 0:
            raise SkipFile("empty_download")
        digest = sha256_file(tmp_path)
        final_path = destination / digest
        os.replace(tmp_path, final_path)
        return DownloadResult(str(final_path), digest, final_path.stat().st_size)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
