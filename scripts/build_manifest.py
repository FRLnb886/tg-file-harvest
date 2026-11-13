#!/usr/bin/env python3
"""把采集状态转成前端用的精简 manifest.json；内容无变化时不改写文件，避免无意义的提交。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_manifest(state: dict[str, Any], limit: int) -> dict[str, Any]:
    files = state.get("files") or {}
    items: list[dict[str, Any]] = []
    for record in files.values():
        if not isinstance(record, dict):
            continue
        if record.get("status") != "ok" or not record.get("url"):
            continue
        items.append(
            {
                "id": str(record.get("unique_id", "")),
                "name": str(record.get("name", "")),
                "size": int(record.get("size") or 0),
                "url": str(record["url"]),
                "ts": int(record.get("ts") or 0),
                "channel": str(record.get("channel_id", "")),
            }
        )
    items.sort(key=lambda item: (-item["ts"], item["id"]))
    items = items[: max(1, int(limit))]
    return {"updated_at": _now(), "items": items}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"找不到状态文件: {path}（先跑一次采集）")
    try:
        data = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"状态文件不是合法 JSON: {path}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"状态文件格式错误: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成前端 manifest.json")
    parser.add_argument("--state", default="work/state.json")
    parser.add_argument("--out", default="web/data/manifest.json")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--force", action="store_true", help="即使内容无变化也重写文件")
    args = parser.parse_args(argv)

    state = load_state(Path(args.state))
    manifest = build_manifest(state, args.limit)

    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        try:
            previous = json.loads(out_path.read_text("utf-8"))
        except json.JSONDecodeError:
            previous = {}
        if isinstance(previous, dict) and previous.get("items") == manifest["items"]:
            print(f"manifest 无变化，保持 {previous.get('updated_at', '-')}（{len(manifest['items'])} 条）")
            return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(out_path)
    print(f"已写入 {out_path}：{len(manifest['items'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
