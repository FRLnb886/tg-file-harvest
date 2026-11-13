from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .models import StorageSettings

log = logging.getLogger(__name__)

API = "https://api.github.com"
UPLOAD_API = "https://uploads.github.com"
USER_AGENT = "tg-file-harvest"


class GitHubStorageError(RuntimeError):
    pass


class GitHubStorage:
    """把文件放进 GitHub Release 资产，直链形如
    https://github.com/<owner>/<repo>/releases/download/<tag>/<文件名>。
    只用 Actions 内置的 GITHUB_TOKEN，不依赖对象存储，也不需要绑卡。"""

    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._repo = (settings.github_repo or "").strip("/")
        self._token = settings.github_token
        self._tag = settings.github_tag or "latest"
        self._state_path = settings.state_key or "state/state.json"
        self._transport: Callable[..., tuple[int, bytes]] = self._urllib_request

    @property
    def configured(self) -> bool:
        return bool(self._repo and self._token)

    # ── 基础 HTTP ────────────────────────────────────────
    def _urllib_request(
        self, method: str, url: str, data: bytes | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", USER_AGENT)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code in (404, 422):
                return exc.code, body
            raise GitHubStorageError(f"{method} {url} -> {exc.code} {body[:200]!r}") from exc

    def _call(
        self, method: str, url: str, payload: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, bytes]:
        data = json.dumps(payload).encode() if payload is not None else None
        extra = {"Content-Type": "application/json"} if data else {}
        extra.update(headers or {})
        return self._transport(method, url, data, extra)

    # ── Release / 资产 ───────────────────────────────────
    def _release_id(self, tag: str, create: bool) -> int | None:
        status, body = self._call(
            "GET", f"{API}/repos/{self._repo}/releases/tags/{urllib.parse.quote(tag)}"
        )
        if status == 200:
            return int(json.loads(body)["id"])
        if not create:
            return None
        status, body = self._call(
            "POST",
            f"{API}/repos/{self._repo}/releases",
            {
                "tag_name": tag,
                "name": tag,
                "body": "由 tg-file-harvest 自动维护",
                "draft": False,
                "prerelease": False,
            },
        )
        if status not in (200, 201):
            raise GitHubStorageError(f"创建 release 失败：{status} {body[:200]!r}")
        return int(json.loads(body)["id"])

    def _assets(self, release_id: int) -> list[dict[str, Any]]:
        status, body = self._call(
            "GET", f"{API}/repos/{self._repo}/releases/{release_id}/assets?per_page=100"
        )
        if status != 200:
            raise GitHubStorageError(f"读取资产列表失败：{status}")
        return list(json.loads(body))

    def _delete_asset(self, asset_id: int) -> None:
        self._call("DELETE", f"{API}/repos/{self._repo}/releases/assets/{asset_id}")

    def _upload_asset(
        self, release_id: int, name: str, path: Path | None, body: bytes | None = None
    ) -> None:
        payload = body if body is not None else Path(path).read_bytes()
        query = urllib.parse.urlencode({"name": name})
        status, raw = self._transport(
            "POST",
            f"{UPLOAD_API}/repos/{self._repo}/releases/{release_id}/assets?{query}",
            payload,
            {"Content-Type": "application/octet-stream"},
        )
        if status not in (200, 201):
            raise GitHubStorageError(f"上传资产 {name} 失败：{status} {raw[:200]!r}")

    def asset_url(self, name: str) -> str:
        return (
            f"https://github.com/{self._repo}/releases/download/"
            f"{urllib.parse.quote(self._tag)}/{urllib.parse.quote(name)}"
        )

    def upload_and_url(
        self, sha256: str, local_path: str, channel_id: str, name: str, group: str = ""
    ) -> tuple[str, str]:
        release_id = self._release_id(self._tag, create=True)
        assert release_id is not None
        for asset in self._assets(release_id):
            # 只保留最新：同名或旧版本资产都清掉，保证 release 里只剩这一轮的文件
            if asset.get("name") == name or self._settings.github_prune_assets:
                self._delete_asset(int(asset["id"]))
                log.info("删除旧资产 %s", asset.get("name"))
        self._upload_asset(release_id, name, Path(local_path))
        url = self.asset_url(name)
        log.info("已上传 Release 资产：%s", url)
        return f"{self._tag}/{name}", url

    def delete_keys(self, keys: list[str]) -> int:
        if not keys:
            return 0
        by_tag: dict[str, set[str]] = {}
        for key in keys:
            tag, _, name = str(key).partition("/")
            if name:
                by_tag.setdefault(tag, set()).add(name)
        deleted = 0
        for tag, names in by_tag.items():
            release_id = self._release_id(tag, create=False)
            if release_id is None:
                continue
            for asset in self._assets(release_id):
                if asset.get("name") in names:
                    self._delete_asset(int(asset["id"]))
                    deleted += 1
        return deleted

    # ── 状态：走 Contents API，内容没变就不产生提交 ──────
    def get_json(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        path = urllib.parse.quote(self._state_path)
        status, body = self._call("GET", f"{API}/repos/{self._repo}/contents/{path}")
        if status == 404:
            return None
        if status != 200:
            raise GitHubStorageError(f"读取状态失败：{status}")
        payload = json.loads(body)
        content = base64.b64decode(payload.get("content") or "").decode("utf-8")
        return json.loads(content) if content.strip() else {}

    def put_json(self, payload: dict[str, Any]) -> None:
        if not self.configured:
            return
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        encoded = base64.b64encode(content.encode("utf-8")).decode()
        path = urllib.parse.quote(self._state_path)
        url = f"{API}/repos/{self._repo}/contents/{path}"

        status, body = self._call("GET", url)
        sha = None
        if status == 200:
            current = json.loads(body)
            sha = current.get("sha")
            existing = base64.b64decode(current.get("content") or "").decode("utf-8")
            if existing == content:
                return
        elif status != 404:
            raise GitHubStorageError(f"读取状态文件 sha 失败：{status}")

        request: dict[str, Any] = {"message": "chore: update harvest state", "content": encoded}
        if sha:
            request["sha"] = sha
        status, raw = self._call("PUT", url, request)
        if status not in (200, 201):
            raise GitHubStorageError(f"写入状态失败：{status} {raw[:200]!r}")


def build_from_env(settings: StorageSettings) -> GitHubStorage:
    """方便脚本按 Actions 环境变量直接构造。"""
    if not settings.github_repo:
        settings.github_repo = os.getenv("GITHUB_REPOSITORY", "")
    return GitHubStorage(settings)
