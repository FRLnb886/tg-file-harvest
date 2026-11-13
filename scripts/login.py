#!/usr/bin/env python3
"""本地一次性登录，生成 Telethon StringSession。会话字符串等同账号登录态，只打印不落盘。"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 TG_SESSION_STRING")
    parser.add_argument("--api-id", default=os.getenv("TG_API_ID", ""), help="my.telegram.org 的 api_id")
    parser.add_argument("--api-hash", default=os.getenv("TG_API_HASH", ""), help="my.telegram.org 的 api_hash")
    parser.add_argument("--phone", default=os.getenv("TG_PHONE", ""), help="含国家码的手机号")
    parser.add_argument("--out", default="", help="可选：写入文件（权限 600），默认只打印到标准输出")
    return parser


async def run(args: argparse.Namespace) -> int:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = args.api_id or input("api_id: ").strip()
    api_hash = args.api_hash or input("api_hash: ").strip()
    phone = args.phone or input("手机号（含国家码，如 +8613800000000）: ").strip()
    if not api_id.isdigit() or not api_hash or not phone:
        print("api_id / api_hash / phone 都不能为空", file=sys.stderr)
        return 2

    def ask_code() -> str:
        return input("验证码: ").strip()

    def ask_password() -> str:
        value = getpass.getpass("两步验证密码（没有则直接回车）: ")
        return value

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start(phone=phone, code_callback=ask_code, password=ask_password)
    me = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()

    print(
        f"登录成功：{getattr(me, 'first_name', '')} @{getattr(me, 'username', None) or '-'} "
        f"(id={getattr(me, 'id', '-')})",
        file=sys.stderr,
    )
    if args.out:
        target = Path(args.out)
        target.write_text(session_string, "utf-8")
        os.chmod(target, 0o600)
        print(f"session 已写入 {target}（权限 600），请勿提交到仓库", file=sys.stderr)
    else:
        print("把下面这行存进 GitHub Secrets 的 TG_SESSION_STRING（不要提交、不要贴到聊天里）：", file=sys.stderr)
        print(session_string)
    return 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
