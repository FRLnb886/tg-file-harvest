from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import envfile
from .paths import ENV_FILE, LOG_DIR, ROOT, ensure_dirs

MAX_TAIL_BYTES = 20000


@dataclass
class Run:
    id: str
    started_at: str
    dry_run: bool
    limit: int
    log_path: Path
    finished_at: str | None = None
    exit_code: int | None = None
    running: bool = True


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def start(self, dry_run: bool, limit: int) -> dict[str, Any]:
        ensure_dirs()
        with self._lock:
            active = [run for run in self._runs.values() if run.running]
            if active:
                raise HTTPException(409, f"已有采集在运行：{active[0].id}")

            run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            log_path = LOG_DIR / f"collect-{run_id}.log"
            command = [sys.executable, "-m", "collector.main", "--config", "config/"]
            if dry_run:
                command.append("--dry-run")
            if limit:
                command += ["--limit", str(int(limit))]

            environment = os.environ.copy()
            environment.update({k: v for k, v in envfile.read(ENV_FILE).items() if v})
            environment["PYTHONUNBUFFERED"] = "1"

            handle = log_path.open("ab")
            process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=environment,
                bufsize=0,
            )
            run = Run(
                id=run_id,
                started_at=_now(),
                dry_run=dry_run,
                limit=int(limit),
                log_path=log_path,
            )
            self._runs[run_id] = run
            threading.Thread(target=self._watch, args=(run, process, handle), daemon=True).start()
            return self._serialize(run)

    def tail(self, run_id: str, offset: int = 0) -> dict[str, Any]:
        run = self._runs.get(run_id)
        log_path = run.log_path if run is not None else LOG_DIR / f"collect-{run_id}.log"
        if run is None and not log_path.exists():
            raise HTTPException(404, "没有这次运行记录")

        data = b""
        if log_path.exists():
            with log_path.open("rb") as handle:
                handle.seek(max(0, offset))
                data = handle.read(MAX_TAIL_BYTES)
        return {
            "id": run_id,
            "text": data.decode("utf-8", "replace"),
            "next_offset": max(0, offset) + len(data),
            "running": bool(run and run.running),
            "exit_code": run.exit_code if run else None,
            "started_at": run.started_at if run else None,
            "finished_at": run.finished_at if run else None,
        }

    def list_runs(self) -> list[dict[str, Any]]:
        runs = {run.id: self._serialize(run) for run in self._runs.values()}
        # 面板重启后内存里的记录会丢，用日志文件把历史补回来
        for path in sorted(LOG_DIR.glob("collect-*.log"), reverse=True)[:20]:
            run_id = path.stem.replace("collect-", "", 1)
            runs.setdefault(
                run_id,
                {
                    "id": run_id,
                    "started_at": run_id,
                    "finished_at": None,
                    "exit_code": None,
                    "running": False,
                    "dry_run": None,
                    "limit": 0,
                },
            )
        return [runs[key] for key in sorted(runs, reverse=True)]

    def latest(self) -> dict[str, Any] | None:
        items = self.list_runs()
        return items[0] if items else None

    def _watch(self, run: Run, process: subprocess.Popen, handle: Any) -> None:
        try:
            code = process.wait()
        finally:
            handle.close()
        run.exit_code = code
        run.finished_at = _now()
        run.running = False

    @staticmethod
    def _serialize(run: Run) -> dict[str, Any]:
        return {
            "id": run.id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "exit_code": run.exit_code,
            "running": run.running,
            "dry_run": run.dry_run,
            "limit": run.limit,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
