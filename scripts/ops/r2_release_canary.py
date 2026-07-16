#!/usr/bin/env python3
"""Run a destructive-but-self-cleaning Cloudflare R2 release canary.

This command is intentionally inert unless both ``--execute`` and the exact
``VKPI_R2_CANARY_CONFIRM`` value are present.  It never accepts an object key
from the caller: every run owns one unpredictable key under a dedicated
namespace and deletes it before reporting success.

The evidence contract proves the complete object path used by V-KPI:

* put one small random object with an application SHA-256 metadata value;
* HEAD and verify byte length plus metadata;
* ranged GET and verify the exact returned slice;
* full GET and verify the original bytes;
* DELETE and prove a subsequent HEAD is a 404/NoSuchKey.

Credentials are loaded only into memory and never written to stdout, argv or
the receipt.  Raw SDK errors are deliberately not persisted because signed
request details may contain sensitive material.

Until a trusted receipt seal/consumer and stale-object scavenger are wired,
successful execution remains diagnostic-only and exits non-zero so a deploy
script cannot accidentally promote it into a release gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


_STDOUT_UTILS_DIR = Path(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in sys.path:
    sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402


CONFIRM_ENV = "VKPI_R2_CANARY_CONFIRM"
CONFIRM_VALUE = "WRITE_VERIFY_DELETE_UNIQUE_R2_CANARY"
CANARY_NAMESPACE = "vkpi/release-canary"
PAYLOAD_BYTES = 4096
RANGE_START = 17
RANGE_END = 1040
REQUIRED_ENV = (
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class CanaryError(RuntimeError):
    """A public, credential-free canary failure."""

    def __init__(self, operation: str, category: str, status_code: int | None = None):
        self.operation = operation
        self.category = category
        self.status_code = status_code
        super().__init__(f"R2 canary failed: operation={operation} category={category}")


@dataclass(frozen=True)
class Settings:
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str

    @property
    def bucket_fingerprint(self) -> str:
        return hashlib.sha256(self.bucket.encode("utf-8")).hexdigest()


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CanaryError("configure", "env_file_unreadable") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or path.is_symlink()
        or info.st_mode & (stat.S_IWGRP | stat.S_IRWXO)
    ):
        raise CanaryError("configure", "env_file_not_private_regular")
    if info.st_size > maximum_bytes:
        raise CanaryError("configure", "env_file_too_large")
    try:
        return path.read_bytes()
    except OSError:
        raise CanaryError("configure", "env_file_unreadable") from None


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    raw = _regular_file(path, maximum_bytes=1024 * 1024)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CanaryError("configure", "env_file_not_utf8") from None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value.strip().strip("'").strip('"')
    return values


def settings_from_environment(env: Mapping[str, str]) -> Settings:
    missing = [key for key in REQUIRED_ENV if not str(env.get(key) or "").strip()]
    if missing:
        raise CanaryError("configure", "missing_required_environment")
    endpoint = str(env["R2_ENDPOINT"]).strip()
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        raise CanaryError("configure", "invalid_endpoint") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
    ):
        raise CanaryError("configure", "invalid_cloudflare_r2_endpoint")
    bucket = str(env["R2_BUCKET_NAME"]).strip()
    if not BUCKET_RE.fullmatch(bucket) or ".." in bucket:
        raise CanaryError("configure", "invalid_bucket")
    return Settings(
        endpoint=endpoint,
        access_key=str(env["R2_ACCESS_KEY_ID"]).strip(),
        secret_key=str(env["R2_SECRET_ACCESS_KEY"]).strip(),
        bucket=bucket,
    )


def build_client(settings: Settings) -> Any:
    # Imports remain below the explicit execution gate so --help and plan mode
    # cannot initialize an SDK credential chain or contact metadata services.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key,
        aws_secret_access_key=settings.secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "standard", "total_max_attempts": 3},
            # Never inherit the residential/scraping proxy from the process.
            proxies={},
            s3={"addressing_style": "auto"},
        ),
    )


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        metadata = response.get("ResponseMetadata")
        if isinstance(metadata, dict):
            try:
                return int(metadata.get("HTTPStatusCode"))
            except (TypeError, ValueError):
                return None
    return None


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            return str(error.get("Code") or "")
    return ""


def _classified(operation: str, exc: BaseException) -> CanaryError:
    status = _status_code(exc)
    if status in {401, 403}:
        category = "access_denied"
    elif status == 404:
        category = "not_found"
    elif status == 429:
        category = "throttled"
    elif status is not None and status >= 500:
        category = "upstream_5xx"
    elif status is not None:
        category = "client_error"
    else:
        category = "transport_or_sdk_error"
    return CanaryError(operation, category, status)


def _read_body(response: Mapping[str, Any], operation: str) -> bytes:
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        raise CanaryError(operation, "missing_response_body")
    try:
        data = body.read()
    except Exception as exc:
        raise _classified(operation, exc) from None
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    if not isinstance(data, bytes):
        raise CanaryError(operation, "non_bytes_response")
    return data


def _head_is_absent(client: Any, *, bucket: str, key: str) -> bool:
    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        return _status_code(exc) == 404 or _error_code(exc) in {"404", "NoSuchKey", "NotFound"}
    return False


def run_canary(
    *,
    client: Any,
    settings: Settings,
    payload: bytes,
    key: str,
    release_id: str,
    expected_app_sha: str,
) -> dict[str, Any]:
    if len(payload) != PAYLOAD_BYTES:
        raise CanaryError("configure", "invalid_payload_size")
    if not key.startswith(CANARY_NAMESPACE + "/") or not re.fullmatch(
        r"vkpi/release-canary/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.bin", key
    ):
        raise CanaryError("configure", "invalid_owned_canary_key")
    if (
        not RELEASE_ID_RE.fullmatch(release_id)
        or release_id in {".", ".."}
        or not GIT_SHA_RE.fullmatch(expected_app_sha)
    ):
        raise CanaryError("configure", "invalid_release_binding")

    expected_sha = hashlib.sha256(payload).hexdigest()
    operations = {
        "put": False,
        "head": False,
        "range_get": False,
        "full_get": False,
        "delete": False,
        "absence_confirmed": False,
    }
    cleanup_required = True  # PUT may commit even when its response is lost.
    failure: CanaryError | None = None
    try:
        try:
            client.put_object(
                Bucket=settings.bucket,
                Key=key,
                Body=payload,
                ContentType="application/octet-stream",
                Metadata={"vkpi-sha256": expected_sha},
            )
            operations["put"] = True

            head = client.head_object(Bucket=settings.bucket, Key=key)
            metadata = head.get("Metadata") if isinstance(head, dict) else None
            if (
                not isinstance(head, dict)
                or int(head.get("ContentLength", -1)) != len(payload)
                or not isinstance(metadata, dict)
                or metadata.get("vkpi-sha256") != expected_sha
            ):
                raise CanaryError("head", "metadata_or_length_mismatch")
            operations["head"] = True

            ranged_response = client.get_object(
                Bucket=settings.bucket,
                Key=key,
                Range=f"bytes={RANGE_START}-{RANGE_END}",
            )
            ranged = _read_body(ranged_response, "range_get")
            if ranged != payload[RANGE_START : RANGE_END + 1]:
                raise CanaryError("range_get", "byte_mismatch")
            operations["range_get"] = True

            full_response = client.get_object(Bucket=settings.bucket, Key=key)
            downloaded = _read_body(full_response, "full_get")
            if hashlib.sha256(downloaded).hexdigest() != expected_sha or downloaded != payload:
                raise CanaryError("full_get", "sha256_or_byte_mismatch")
            operations["full_get"] = True
        except CanaryError:
            raise
        except Exception as exc:
            pending = next((name for name, done in operations.items() if not done), "unknown")
            raise _classified(pending, exc) from None
    except CanaryError as exc:
        failure = exc
    finally:
        if cleanup_required:
            try:
                client.delete_object(Bucket=settings.bucket, Key=key)
                operations["delete"] = True
                operations["absence_confirmed"] = _head_is_absent(
                    client, bucket=settings.bucket, key=key
                )
            except Exception as exc:
                if failure is None:
                    failure = _classified("delete", exc)
            if not operations["absence_confirmed"] and failure is None:
                failure = CanaryError("delete", "absence_not_confirmed")

    if failure is not None:
        failure.operations = operations  # type: ignore[attr-defined]
        raise failure
    return {
        "schema_version": 1,
        "evidence_type": "vkpi_r2_write_read_delete_canary",
        "status": "passed",
        "checked_at": utcnow(),
        "release_id": release_id,
        "expected_app_sha": expected_app_sha,
        "namespace": CANARY_NAMESPACE,
        "object_key": key,
        "bucket_sha256": settings.bucket_fingerprint,
        "endpoint_host": urlsplit(settings.endpoint).hostname,
        "payload_bytes": len(payload),
        "payload_sha256": expected_sha,
        "range": {
            "start": RANGE_START,
            "end": RANGE_END,
            "bytes": RANGE_END - RANGE_START + 1,
            "sha256": hashlib.sha256(payload[RANGE_START : RANGE_END + 1]).hexdigest(),
        },
        "transport": {"proxy_mode": "disabled", "max_attempts": 3},
        "operations": operations,
        "credentials_persisted": False,
        "release_gate_eligible": False,
        "release_gate_blockers": [
            "trusted_receipt_consumer_and_seal_not_implemented",
            "stale_canary_scavenger_not_implemented",
        ],
    }


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise CanaryError("artifact", "refuse_overwrite")
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CanaryError("artifact", "unsafe_parent")
    if path.parent.stat().st_mode & stat.S_IWOTH:
        raise CanaryError("artifact", "world_writable_parent")
    if not parent_existed:
        os.chmod(path.parent, 0o700)
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError:
        raise CanaryError("artifact", "temporary_create_failed") from None
    temporary = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict opt-in R2 write/read/delete release canary")
    parser.add_argument("--execute", action="store_true", help="Actually contact R2")
    parser.add_argument("--env-file", default="", help="Optional private env file loaded in memory")
    parser.add_argument("--artifact", default="", help="Required non-existing receipt path in execute mode")
    parser.add_argument("--release-id", default="", help="Release identifier bound into the receipt")
    parser.add_argument("--expected-app-sha", default="", help="Exact 40-char application SHA")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[Settings], Any] = build_client,
) -> int:
    args = parse_args(argv)
    if not args.execute:
        stdout_out(
            json.dumps(
                {
                    "status": "not_executed",
                    "network_contacted": False,
                    "required_flag": "--execute",
                    "required_confirmation_env": CONFIRM_ENV,
                    "operations": ["put", "head", "range_get", "full_get", "delete", "head_404"],
                },
                sort_keys=True,
            )
        )
        return 2
    if os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
        stdout_out(
            "R2 canary confirmation is absent or invalid; no client was created.",
            file=sys.stderr,
        )
        return 2
    if not args.artifact:
        stdout_out("--artifact is required in execute mode.", file=sys.stderr)
        return 2
    if (
        not RELEASE_ID_RE.fullmatch(args.release_id)
        or args.release_id in {".", ".."}
        or not GIT_SHA_RE.fullmatch(args.expected_app_sha)
    ):
        stdout_out(
            "A safe --release-id and exact --expected-app-sha are required.",
            file=sys.stderr,
        )
        return 2

    merged = dict(os.environ)
    active_client: Any | None = None
    try:
        if args.env_file:
            merged.update(load_env_file(Path(args.env_file)))
        settings = settings_from_environment(merged)
        payload = secrets.token_bytes(PAYLOAD_BYTES)
        key = f"{CANARY_NAMESPACE}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}.bin"
        try:
            active_client = client_factory(settings)
        except Exception:
            raise CanaryError("client", "construction_failed") from None
        result = run_canary(
            client=active_client,
            settings=settings,
            payload=payload,
            key=key,
            release_id=args.release_id,
            expected_app_sha=args.expected_app_sha,
        )
        _write_private_json(Path(args.artifact), result)
    except CanaryError as exc:
        failure = {
            "schema_version": 1,
            "evidence_type": "vkpi_r2_write_read_delete_canary",
            "status": "failed",
            "checked_at": utcnow(),
            "release_id": args.release_id,
            "expected_app_sha": args.expected_app_sha,
            "failure": {
                "operation": exc.operation,
                "category": exc.category,
                "status_code": exc.status_code,
            },
            "operations": getattr(exc, "operations", {}),
            "credentials_persisted": False,
            "release_gate_eligible": False,
        }
        try:
            _write_private_json(Path(args.artifact), failure)
        except CanaryError:
            pass
        stdout_out(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Preserve a stable, credential-free public boundary even for an
        # unexpected SDK/runtime exception outside the classified operations.
        stdout_out(
            "R2 canary failed: operation=internal category=unexpected",
            file=sys.stderr,
        )
        return 1
    finally:
        close = getattr(active_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        merged.pop("R2_ACCESS_KEY_ID", None)
        merged.pop("R2_SECRET_ACCESS_KEY", None)
    stdout_out(
        json.dumps(
            {
                "status": "diagnostic_passed_not_release_eligible",
                "artifact": args.artifact,
            },
            sort_keys=True,
        )
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
