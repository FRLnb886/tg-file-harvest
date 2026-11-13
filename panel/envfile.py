from __future__ import annotations

import os
from pathlib import Path


def read(path: str | Path) -> dict[str, str]:
    target = Path(path)
    if not target.exists():
        return {}
    values: dict[str, str] = {}
    for line in target.read_text("utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_values(path: str | Path, updates: dict[str, str]) -> None:
    target = Path(path)
    lines = target.read_text("utf-8").splitlines() if target.exists() else []
    pending = dict(updates)
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in pending:
                result.append(f"{key}={pending.pop(key)}")
                continue
        result.append(line)
    for key, value in pending.items():
        result.append(f"{key}={value}")
    target.write_text("\n".join(result).rstrip("\n") + "\n", "utf-8")
    os.chmod(target, 0o600)


def load_into_env(path: str | Path) -> dict[str, str]:
    values = read(path)
    for key, value in values.items():
        if value:
            os.environ.setdefault(key, value)
    return values
