from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CHANNELS_FILE = CONFIG_DIR / "channels.yaml"
WEB_DIR = ROOT / "web"
WORK_DIR = ROOT / "work"
LOG_DIR = WORK_DIR / "logs"
STATE_PATH = WORK_DIR / "state.json"
MANIFEST_PATH = WEB_DIR / "data" / "manifest.json"
ENV_FILE = ROOT / ".env"
TOKEN_FILE = WORK_DIR / "panel-token"


def ensure_dirs() -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
