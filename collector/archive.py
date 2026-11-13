from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .naming import sanitize_name

MAX_FILES = 200
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 500 * 1024 * 1024


class ArchiveError(RuntimeError):
    pass


def extract_zip(
    source: str | Path,
    dest_dir: str | Path,
    *,
    max_files: int = MAX_FILES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_file_bytes: int = MAX_FILE_BYTES,
) -> list[Path]:
    """解开 zip 到 dest_dir，只保留普通文件；挡目录穿越、符号链接和 zip bomb。"""
    src = Path(source)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    extracted: list[Path] = []
    total = 0

    with zipfile.ZipFile(src) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            if info.file_size > max_file_bytes:
                raise ArchiveError(f"压缩包内单文件过大：{info.filename}")
            total += info.file_size
            if total > max_total_bytes:
                raise ArchiveError("解压后总大小超限")
            if len(extracted) >= max_files:
                raise ArchiveError("压缩包内文件数超限")

            raw = info.filename.replace("\\", "/")
            if raw.startswith("/") or ".." in raw.split("/"):
                raise ArchiveError(f"非法路径：{info.filename}")

            name = sanitize_name(Path(raw).name)
            if not name:
                continue
            target = (dest / name).resolve()
            if target != root and root not in target.parents:
                raise ArchiveError(f"非法路径：{info.filename}")
            suffix = 1
            while target.exists():
                stem = Path(name).stem
                ext = Path(name).suffix
                target = (dest / f"{stem}_{suffix}{ext}").resolve()
                suffix += 1
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, open(target, "wb") as writer:
                shutil.copyfileobj(reader, writer)
            extracted.append(target)

    if not extracted:
        raise ArchiveError("压缩包内没有可解压的文件")
    return extracted
