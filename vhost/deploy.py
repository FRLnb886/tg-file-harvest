#!/usr/bin/env python3
"""部署 vhost/ 下的 PHP 入口页到 Miyo 虚拟主机。

用法（令牌从环境变量 MIYO_TOKEN 读取，或 --token 传入）：
    python vhost/deploy.py init [--keep-remote]     # 生成 config.php（含随机 trigger_secret）
    python vhost/deploy.py deploy [--dry-run]       # 上传 vhost/*.php 与 index.html
    python vhost/deploy.py ls [/]                   # 列远端目录
    python vhost/deploy.py read /config.php         # 读远端文件
    python vhost/deploy.py rm /probe.php            # 删远端文件

域名解析不出来时（面板 DNS 偶尔抽风），用 `--api-ip` 指定 IP 直连：
    python vhost/deploy.py --api-ip 154.12.63.109 deploy

令牌在面板「账号设置 → AI 部署助手」生成，只显示一次；不要提交进仓库。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.parse
from pathlib import Path

API_HOST = "vhost.miyohost.cn"
API = "https://" + API_HOST
API_IP = ""

HERE = Path(__file__).resolve().parent
UPLOAD_FILES = ["index.php", "lib.php", "latest.php", "trigger.php", "config.php", "index.html"]


def token_from(args: argparse.Namespace) -> str:
    token = args.token or os.getenv("MIYO_TOKEN", "")
    if not token:
        sys.exit("缺少部署令牌：设置环境变量 MIYO_TOKEN，或用 --token 传入")
    return token.strip()


def call(token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """用 curl 发请求（支持 --resolve 按 IP 直连，绕开 DNS 故障）。"""
    command = [
        "curl", "-sS", "--max-time", "40", "-X", method,
        "-H", "Authorization: Bearer " + token,
        "-H", "Content-Type: application/json",
        "-w", "\n%{http_code}",
    ]
    if API_IP:
        command += ["--resolve", f"{API_HOST}:443:{API_IP}"]
    if body is not None:
        command += ["--data-binary", json.dumps(body)]
    command.append(API + path)

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return 0, {"error": f"请求失败：{exc}"}

    raw = result.stdout
    status = 0
    if "\n" in raw:
        raw, _, tail = raw.rpartition("\n")
        status = int(tail.strip()) if tail.strip().isdigit() else 0
    if status == 0 and result.stderr:
        return 0, {"error": result.stderr.strip()[:200]}
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {"error": raw[:300]}
    return status, payload


def cmd_init(args: argparse.Namespace) -> int:
    target = HERE / "config.php"
    if target.exists() and not args.force:
        sys.exit(f"{target} 已存在（要覆盖加 --force）")

    template = (HERE / "config.example.php").read_text("utf-8")
    secret = secrets.token_urlsafe(18)
    github_token = ""

    if args.keep_remote:
        token = token_from(args)
        status, body = call(token, "POST", "/api/deploy/files/read", {"path": "/config.php"})
        if status == 200 and body.get("content"):
            remote = body["content"]
            found_secret = re.search(r"'trigger_secret'\s*=>\s*'([^']*)'", remote)
            found_token = re.search(r"'token'\s*=>\s*'([^']*)'", remote)
            if found_secret and found_secret.group(1):
                secret = found_secret.group(1)
            if found_token:
                github_token = found_token.group(1)
            print("已复用远端 config.php 的 trigger_secret / token")
        else:
            print(f"远端还没有 config.php（{status}），生成新的")

    content = template.replace("CHANGE_ME_RANDOM_SECRET", secret)
    content = re.sub(r"'token'\s*=>\s*'[^']*'", f"'token'          => '{github_token}'", content)
    target.write_text(content, "utf-8")
    os.chmod(target, 0o600)
    print(f"已生成 {target}")
    print(f"trigger_secret = {secret}")
    if not github_token:
        print("提示：token 为空，「立即检查」按钮会返回 502；填入 fine-grained token（Actions: Read and write）后即可用")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    token = token_from(args)
    if not (HERE / "config.php").exists():
        sys.exit("缺少 vhost/config.php，先运行：python vhost/deploy.py init --keep-remote")

    files = [name for name in UPLOAD_FILES if (HERE / name).exists()]
    missing = [name for name in UPLOAD_FILES if not (HERE / name).exists()]
    if missing:
        print(f"跳过不存在的文件：{', '.join(missing)}")

    for name in files:
        content = (HERE / name).read_text("utf-8")
        if args.dry_run:
            print(f"[dry-run] 将上传 /{name}（{len(content)} 字符）")
            continue
        status, body = call(token, "POST", "/api/deploy/files/write", {"path": f"/{name}", "content": content})
        ok = status == 200 and body.get("ok")
        print(f"{'OK  ' if ok else 'FAIL'} /{name} -> {status} {'' if ok else json.dumps(body, ensure_ascii=False)[:160]}")
        if not ok:
            return 1

    status, me = call(token, "GET", "/api/deploy/me")
    if status == 200:
        host = me.get("host", {})
        print(f"\n访问地址：{host.get('temp_url') or host.get('temp_domain')}")
        print(f"环境：{host.get('runtime')}｜套餐：{host.get('plan_name')}｜磁盘：{host.get('disk_used')}/{host.get('disk_mb')} MB")
    else:
        print(f"\n读取主机信息失败：{status} {json.dumps(me, ensure_ascii=False)[:160]}")
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    token = token_from(args)
    path = args.path or "/"
    status, body = call(token, "GET", "/api/deploy/files?path=" + urllib.parse.quote(path))
    if status != 200:
        print(status, body)
        return 1
    print(f"{path} -> {status}")
    for entry in body.get("entries", []):
        kind = "d" if entry.get("type") == "dir" else "-"
        print(f"  {kind} {entry.get('name'):<24}{entry.get('size', 0):>9}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    token = token_from(args)
    status, body = call(token, "POST", "/api/deploy/files/read", {"path": args.path})
    print(status)
    print(body.get("content", body) if isinstance(body, dict) else body)
    return 0 if status == 200 else 1


def cmd_rm(args: argparse.Namespace) -> int:
    token = token_from(args)
    status, body = call(token, "POST", "/api/deploy/files/delete", {"path": args.path})
    print(f"delete {args.path} -> {status} {json.dumps(body, ensure_ascii=False)[:160]}")
    return 0 if status == 200 else 1


def main(argv: list[str] | None = None) -> int:
    global API_IP

    parser = argparse.ArgumentParser(description="部署 PHP 入口页到 Miyo 虚拟主机")
    parser.add_argument("--token", default="", help="部署令牌（默认读环境变量 MIYO_TOKEN）")
    parser.add_argument("--api-ip", default="", help="面板域名解析不出时，指定 IP 直连（也可用 MIYO_API_IP）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="生成本地 config.php")
    p_init.add_argument("--keep-remote", action="store_true", help="复用远端已有的 trigger_secret / token")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的 config.php")
    p_init.set_defaults(func=cmd_init)

    p_deploy = sub.add_parser("deploy", help="上传文件")
    p_deploy.add_argument("--dry-run", action="store_true")
    p_deploy.set_defaults(func=cmd_deploy)

    p_ls = sub.add_parser("ls", help="列远端目录")
    p_ls.add_argument("path", nargs="?", default="/")
    p_ls.set_defaults(func=cmd_ls)

    p_read = sub.add_parser("read", help="读远端文件")
    p_read.add_argument("path")
    p_read.set_defaults(func=cmd_read)

    p_rm = sub.add_parser("rm", help="删远端文件")
    p_rm.add_argument("path")
    p_rm.set_defaults(func=cmd_rm)

    args = parser.parse_args(argv)
    API_IP = (args.api_ip or os.getenv("MIYO_API_IP", "")).strip()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
