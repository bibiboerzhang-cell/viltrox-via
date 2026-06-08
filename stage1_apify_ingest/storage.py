"""
Dual-write storage: every asset lands on local SSD and R2 under the same
relative key. Done means present in both stores.
"""
from __future__ import annotations

import io
import json
import os
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig

from .config import Settings


class DualStore:
    def __init__(self, settings: Settings, commit: bool):
        self.s = settings
        self.commit = commit
        self._s3 = None

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client(
                "s3",
                endpoint_url=self.s.r2_endpoint,
                aws_access_key_id=self.s.r2_access_key,
                aws_secret_access_key=self.s.r2_secret_key,
                config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._s3

    def _local_path(self, key: str) -> str:
        return os.path.join(self.s.local_root, key)

    def local_exists(self, key: str) -> bool:
        path = self._local_path(key)
        return os.path.exists(path) and os.path.getsize(path) > 0

    def r2_exists(self, key: str) -> bool:
        if not self.s.r2_bucket:
            return False
        try:
            self.s3.head_object(Bucket=self.s.r2_bucket, Key=key)
            return True
        except Exception:
            return False

    def both_exist(self, key: str) -> bool:
        return self.local_exists(key) and self.r2_exists(key)

    def sync_existing(self, key: str) -> bool:
        """Repair a one-sided asset without re-fetching. Returns True if done."""
        if not self.commit:
            return False
        local = self.local_exists(key)
        r2 = self.r2_exists(key)
        if local and r2:
            return True
        if local and not r2:
            self._upload_local_to_r2(key)
            return self.both_exist(key)
        if r2 and not local:
            self._download_r2_to_local(key)
            return self.both_exist(key)
        return False

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        if not self.commit:
            print(f"[dry-run] would write {key} ({len(data)} bytes)")
            return
        path = self._local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
        self.s3.upload_fileobj(
            io.BytesIO(data),
            self.s.r2_bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def put_json(self, key: str, obj) -> None:
        self.put_bytes(
            key,
            json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
        )

    def read_json_local(self, key: str) -> Optional[dict]:
        path = self._local_path(key)
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _upload_local_to_r2(self, key: str) -> None:
        path = self._local_path(key)
        with open(path, "rb") as handle:
            self.s3.upload_fileobj(
                handle,
                self.s.r2_bucket,
                key,
                ExtraArgs={"ContentType": _content_type(key)},
            )

    def _download_r2_to_local(self, key: str) -> None:
        path = self._local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as handle:
            self.s3.download_fileobj(self.s.r2_bucket, key, handle)
        os.replace(tmp, path)


def _content_type(key: str) -> str:
    if key.endswith(".json"):
        return "application/json"
    if key.endswith(".jpg") or key.endswith(".jpeg"):
        return "image/jpeg"
    return "application/octet-stream"

