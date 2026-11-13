from __future__ import annotations

import json
import logging
import mimetypes
from typing import Any
from urllib.parse import quote

from .models import StorageSettings

log = logging.getLogger(__name__)


class R2Error(RuntimeError):
    pass


def _safe_segment(value: str) -> str:
    cleaned = str(value or "").replace("/", "_").replace("\\", "_").strip()
    return cleaned or "unnamed"


class R2Storage:
    def __init__(self, settings: StorageSettings) -> None:
        self._settings = settings
        self._s3: Any = None
        self._bucket = settings.bucket
        self._state_bucket = settings.resolved_state_bucket
        self._state_key = settings.state_key
        self._public_base = settings.public_base.rstrip("/")
        self._prefix = settings.key_prefix.strip("/")

    @property
    def configured(self) -> bool:
        return self._settings.configured

    def _client(self) -> Any:
        if self._s3 is not None:
            return self._s3
        if not self.configured:
            raise R2Error("R2 未配置，无法访问对象存储")
        import boto3
        from botocore.config import Config

        self._s3 = boto3.client(
            "s3",
            endpoint_url=self._settings.resolved_endpoint,
            aws_access_key_id=self._settings.access_key_id,
            aws_secret_access_key=self._settings.secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=15,
                read_timeout=300,
            ),
        )
        return self._s3

    def get_json(self) -> dict[str, Any] | None:
        if not self.configured:
            return None
        from botocore.exceptions import ClientError

        try:
            response = self._client().get_object(Bucket=self._state_bucket, Key=self._state_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        body = response["Body"].read()
        return json.loads(body.decode("utf-8")) if body else {}

    def put_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        # 单次 PutObject 在 S3/R2 语义上即原子替换，无需临时 key + copy
        self._client().put_object(
            Bucket=self._state_bucket,
            Key=self._state_key,
            Body=body,
            ContentType="application/json",
        )

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> None:
        self._client().put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        response = self._client().get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def exists(self, key: str, bucket: str | None = None) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client().head_object(Bucket=bucket or self._bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def build_key(self, channel_id: str, sha256: str, name: str) -> str:
        parts = [part for part in (self._prefix, _safe_segment(channel_id), sha256[:12]) if part]
        return "/".join(parts + [_safe_segment(name)])

    def public_url(self, key: str) -> str:
        if not self._public_base:
            raise R2Error("未配置 R2_PUBLIC_BASE，无法生成下载直链")
        return f"{self._public_base}/{quote(key, safe='/')}"

    def put_file(self, local_path: str, key: str, name: str) -> None:
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        extra = {
            "ContentType": content_type,
            "ContentDisposition": f"attachment; filename*=UTF-8''{quote(name)}",
        }
        self._client().upload_file(str(local_path), self._bucket, key, ExtraArgs=extra)

    def upload_and_url(
        self, sha256: str, local_path: str, channel_id: str, name: str
    ) -> tuple[str, str]:
        key = self.build_key(channel_id, sha256, name)
        if self.exists(key):
            log.info("对象已存在，跳过上传: %s", key)
        else:
            self.put_file(local_path, key, name)
        return key, self.public_url(key)

    def delete_keys(self, keys: list[str]) -> int:
        unique = [key for key in dict.fromkeys(keys) if key]
        deleted = 0
        for start in range(0, len(unique), 1000):
            batch = unique[start : start + 1000]
            self._client().delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            deleted += len(batch)
        return deleted

    def head_bucket(self) -> None:
        self._client().head_bucket(Bucket=self._bucket)
