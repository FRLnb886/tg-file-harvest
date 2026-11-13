from __future__ import annotations

import os

import uvicorn

from . import envfile
from .paths import ENV_FILE, ensure_dirs
from .security import get_token


def main() -> int:
    ensure_dirs()
    envfile.load_into_env(ENV_FILE)
    host = os.getenv("PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("PANEL_PORT", "8787"))
    token = get_token()

    print(f"面板地址：http://{host}:{port}")
    print(f"访问令牌：{token}")
    print("令牌保存在 work/panel-token（600），首次打开页面时粘贴进去即可。")
    uvicorn.run("panel.app:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
