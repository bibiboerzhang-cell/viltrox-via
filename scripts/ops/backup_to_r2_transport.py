#!/usr/bin/env python3
"""Cloudflare R2 transport for ``scripts/ops/backup_to_r2.py``.

This is the only module that touches ``boto3``, so it is also the only place
where "run me with the repository ``.venv`` interpreter" is enforceable.  The
import lives inside :func:`build_client`: a bare ``python3`` raises a named
``stage=client category=boto3_missing`` failure instead of silently degrading
to "no off-host backup".

Every SDK exception is converted into a credential-free :class:`BackupError`
before it can reach a traceback, because signed-request details may carry
sensitive material.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backup_to_r2_config import BackupError, R2Credentials


SHA256_METADATA_KEY = "vkpi-sha256"
MAX_INVENTORY_PAGES = 100
ABSENT_ERROR_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def build_client(credentials: R2Credentials) -> Any:
    """Create the R2 S3 client, importing boto3 only once we intend to use it."""

    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise BackupError(
            "client",
            "boto3_missing",
            hint=(
                "boto3 is unavailable; run this script with the repository "
                ".venv interpreter (.venv/bin/python). A bare python3 has no "
                "boto3 and must not silently skip the R2 upload."
            ),
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=credentials.endpoint,
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=120,
            retries={"mode": "standard", "total_max_attempts": 3},
            # Never inherit the residential/scraping proxy from the process.
            proxies={},
            s3={"addressing_style": "auto"},
        ),
    )


def status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        metadata = response.get("ResponseMetadata")
        if isinstance(metadata, Mapping):
            raw = metadata.get("HTTPStatusCode")
            if isinstance(raw, int) and not isinstance(raw, bool):
                return raw
            if isinstance(raw, str) and raw.isdigit():
                return int(raw)
    return None


def error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            return str(error.get("Code") or "")
    return ""


def classify(stage: str, exc: BaseException) -> BackupError:
    """Map an SDK/transport exception onto a public failure category."""

    status = status_code(exc)
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
    return BackupError(stage, category, status_code=status)


def is_absent(exc: BaseException) -> bool:
    return status_code(exc) == 404 or error_code(exc) in ABSENT_ERROR_CODES


def object_exists(client: Any, *, bucket: str, key: str, stage: str) -> bool:
    """HEAD one key. A non-404 transport error is a failure, not a miss."""

    try:
        client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        if is_absent(exc):
            return False
        raise classify(stage, exc) from None
    return True


def upload_object(
    client: Any,
    *,
    bucket: str,
    key: str,
    body: Any,
    content_type: str,
    sha256_hex: str,
) -> None:
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata={SHA256_METADATA_KEY: sha256_hex},
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise classify("upload", exc) from None


def read_body(response: Mapping[str, Any]) -> bytes:
    body = response.get("Body")
    if body is None or not callable(getattr(body, "read", None)):
        raise BackupError("verify", "missing_response_body")
    try:
        data = body.read()
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise classify("verify", exc) from None
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if not isinstance(data, bytes):
        raise BackupError("verify", "non_bytes_response")
    return data


def verify_readback(
    client: Any,
    *,
    bucket: str,
    dump_key: str,
    sidecar_key: str,
    expected_size: int,
    expected_sha256: str,
    expected_sidecar: bytes,
) -> None:
    """HEAD the dump for size, then GET the sidecar and compare its content.

    Both mismatch categories start with ``readback_`` so the caller knows to
    keep the local artifacts for inspection.
    """

    try:
        head = client.head_object(Bucket=bucket, Key=dump_key)
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise classify("verify", exc) from None
    if not isinstance(head, Mapping):
        raise BackupError("verify", "readback_head_malformed")
    try:
        remote_size = int(head.get("ContentLength", -1))
    except (TypeError, ValueError) as exc:
        raise BackupError("verify", "readback_head_malformed") from exc
    if remote_size != expected_size:
        raise BackupError(
            "verify",
            "readback_size_mismatch",
            hint=f"remote={remote_size} local={expected_size}",
        )
    metadata = head.get("Metadata")
    if isinstance(metadata, Mapping):
        remote_sha = str(metadata.get(SHA256_METADATA_KEY) or "")
        if remote_sha and remote_sha != expected_sha256:
            raise BackupError("verify", "readback_metadata_sha256_mismatch")

    try:
        response = client.get_object(Bucket=bucket, Key=sidecar_key)
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise classify("verify", exc) from None
    if not isinstance(response, Mapping):
        raise BackupError("verify", "readback_get_malformed")
    downloaded = read_body(response)
    if downloaded != expected_sidecar:
        raise BackupError(
            "verify",
            "readback_sha256_mismatch",
            hint=f"sidecar_bytes={len(downloaded)}",
        )


def inventory(client: Any, *, bucket: str, prefix: str) -> dict[str, Any]:
    """Best-effort cumulative object count/size under the prefix.

    A backup-scoped token may legitimately lack ``ListBucket``; that is
    reported as ``available=false`` with a reason, never as a fake zero.
    """

    total_objects = 0
    total_bytes = 0
    token: str | None = None
    for page in range(MAX_INVENTORY_PAGES):
        request: dict[str, Any] = {"Bucket": bucket, "Prefix": f"{prefix}/"}
        if token:
            request["ContinuationToken"] = token
        try:
            response = client.list_objects_v2(**request)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            failure = classify("inventory", exc)
            return {
                "available": False,
                "reason": failure.category,
                "status_code": failure.status_code,
                "pages_read": page,
            }
        if not isinstance(response, Mapping):
            return {
                "available": False,
                "reason": "listing_response_malformed",
                "pages_read": page + 1,
            }
        for item in response.get("Contents") or []:
            total_objects += 1
            try:
                total_bytes += int(item.get("Size") or 0)
            except (TypeError, ValueError):
                return {
                    "available": False,
                    "reason": "listing_size_not_numeric",
                    "pages_read": page + 1,
                }
        token = (
            response.get("NextContinuationToken")
            if response.get("IsTruncated")
            else None
        )
        if not token:
            return {
                "available": True,
                "objects": total_objects,
                "bytes": total_bytes,
                "pages_read": page + 1,
            }
    return {
        "available": False,
        "reason": "listing_page_limit_reached",
        "objects_seen": total_objects,
        "bytes_seen": total_bytes,
        "pages_read": MAX_INVENTORY_PAGES,
    }
