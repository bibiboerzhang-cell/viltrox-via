"""Fail-closed Cloudflare R2 / S3-compatible object storage helpers.

Object storage must not inherit the generic scraping proxy.  Botocore otherwise
reads ``HTTP_PROXY`` / ``HTTPS_PROXY`` from the process environment, which can
send uploads through a residential proxy and surface noisy 522/TLS failures.
The transport here is therefore direct by default and only uses the dedicated
``R2_PROXY_URL`` when it is explicitly configured.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)

from app.core.logging import get_logger


logger = get_logger(__name__)

# Botocore DEBUG records include the complete signed request headers (including
# the access-key id, request signature, multipart upload id and object URL).
# A process-wide DEBUG level is useful for crawlers, but must never turn the R2
# SDK into a credential-bearing wire log.  Keep our own classified warning while
# enforcing a safe floor for the SDK namespaces used by this module.
for _sdk_logger_name in ("boto3", "botocore", "s3transfer"):
    _sdk_logger = logging.getLogger(_sdk_logger_name)
    if _sdk_logger.level == logging.NOTSET or _sdk_logger.level < logging.WARNING:
        _sdk_logger.setLevel(logging.WARNING)

_REQUIRED_ENV = ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME")
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
_DEFAULT_READ_TIMEOUT_SECONDS = 60
_DEFAULT_TOTAL_MAX_ATTEMPTS = 3
_MAX_TOTAL_ATTEMPTS = 5
_STATUS_PATTERN = re.compile(r"(?<!\d)([45]\d\d)(?!\d)")
_T = TypeVar("_T")


@dataclass(frozen=True)
class _R2Settings:
    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str
    proxy_url: str = field(default="", repr=False)
    connect_timeout_seconds: int = _DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: int = _DEFAULT_READ_TIMEOUT_SECONDS
    total_max_attempts: int = _DEFAULT_TOTAL_MAX_ATTEMPTS

    @property
    def proxy_mode(self) -> str:
        return "dedicated" if self.proxy_url else "disabled"

    @property
    def fingerprint(self) -> str:
        # The fingerprint invalidates the singleton without retaining a second
        # plaintext copy of credentials or proxy authentication in globals.
        payload = "\0".join(
            (
                self.endpoint,
                self.access_key,
                self.secret_key,
                self.bucket,
                self.proxy_url,
                str(self.connect_timeout_seconds),
                str(self.read_timeout_seconds),
                str(self.total_max_attempts),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class R2StorageError(RuntimeError):
    """Sanitized R2 failure safe to expose to existing logs and API fallbacks."""

    def __init__(
        self,
        operation: str,
        category: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        self.operation = operation
        self.category = category
        self.retryable = bool(retryable)
        self.status_code = status_code
        status = f" status={status_code}" if status_code is not None else ""
        super().__init__(f"R2 {operation} failed ({category}; retryable={str(self.retryable).lower()}{status})")


class R2ConfigurationError(R2StorageError):
    """Invalid or missing object-storage transport configuration."""

    def __init__(self, reason: str = "invalid_configuration") -> None:
        super().__init__("configure", reason, retryable=False)


_client: Any | None = None
_client_fingerprint = ""
_client_lock = threading.Lock()


def _configured() -> bool:
    return all((os.getenv(key) or "").strip() for key in _REQUIRED_ENV)


def _require_configured() -> None:
    if not _configured():
        raise R2ConfigurationError("not_configured")


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise R2ConfigurationError(f"invalid_{name.lower()}") from None
    if value < minimum or value > maximum:
        raise R2ConfigurationError(f"invalid_{name.lower()}")
    return value


def _validated_url(value: str, *, config_name: str) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.fragment
        or (config_name == "R2_ENDPOINT" and (parsed.username or parsed.password))
    ):
        raise R2ConfigurationError(f"invalid_{config_name.lower()}")
    return text


def _load_settings() -> _R2Settings:
    _require_configured()
    endpoint = _validated_url(os.getenv("R2_ENDPOINT") or "", config_name="R2_ENDPOINT")
    proxy_raw = (os.getenv("R2_PROXY_URL") or "").strip()
    proxy_url = _validated_url(proxy_raw, config_name="R2_PROXY_URL") if proxy_raw else ""
    return _R2Settings(
        endpoint=endpoint,
        access_key=(os.getenv("R2_ACCESS_KEY_ID") or "").strip(),
        secret_key=(os.getenv("R2_SECRET_ACCESS_KEY") or "").strip(),
        bucket=(os.getenv("R2_BUCKET_NAME") or "").strip(),
        proxy_url=proxy_url,
        connect_timeout_seconds=_bounded_int_env("R2_CONNECT_TIMEOUT_SECONDS", _DEFAULT_CONNECT_TIMEOUT_SECONDS, 1, 60),
        read_timeout_seconds=_bounded_int_env("R2_READ_TIMEOUT_SECONDS", _DEFAULT_READ_TIMEOUT_SECONDS, 1, 300),
        total_max_attempts=_bounded_int_env("R2_MAX_ATTEMPTS", _DEFAULT_TOTAL_MAX_ATTEMPTS, 1, _MAX_TOTAL_ATTEMPTS),
    )


def _client_config(settings: _R2Settings) -> Config:
    # Passing an explicit empty mapping is intentional.  In botocore,
    # ``proxies=None`` means inherit environment proxies; ``proxies={}`` means
    # direct transport.  Dedicated proxy credentials remain inside botocore and
    # are never included in our logs or public errors.
    proxies = {"http": settings.proxy_url, "https": settings.proxy_url} if settings.proxy_url else {}
    return Config(
        signature_version="s3v4",
        connect_timeout=settings.connect_timeout_seconds,
        read_timeout=settings.read_timeout_seconds,
        retries={"mode": "standard", "total_max_attempts": settings.total_max_attempts},
        proxies=proxies,
        s3={"addressing_style": "auto"},
    )


def _close_client_best_effort(client: Any | None) -> None:
    """Release a retired botocore HTTP session without affecting rotation."""

    if client is None:
        return
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        # Botocore's public BaseClient.close() closes its endpoint HTTP session.
        close()
    except Exception:
        # The replacement is already installed.  Cleanup must never restore or
        # invalidate it, and raw transport errors may contain proxy credentials.
        logger.debug("r2.client_cleanup_failed")


def _client_context() -> tuple[Any, _R2Settings]:
    global _client, _client_fingerprint
    settings = _load_settings()
    fingerprint = settings.fingerprint
    retired_client: Any | None = None
    with _client_lock:
        if _client is None or _client_fingerprint != fingerprint:
            replacement = boto3.client(
                "s3",
                endpoint_url=settings.endpoint,
                aws_access_key_id=settings.access_key,
                aws_secret_access_key=settings.secret_key,
                config=_client_config(settings),
                region_name="auto",
            )
            retired_client = _client
            _client = replacement
            _client_fingerprint = fingerprint
        active_client = _client
    if retired_client is not active_client:
        _close_client_best_effort(retired_client)
    return active_client, settings


def _get_client() -> Any:
    """Compatibility seam for callers/tests that only need the singleton."""

    return _client_context()[0]


def _reset_client_for_tests() -> None:
    """Clear the singleton without contacting object storage."""

    global _client, _client_fingerprint
    with _client_lock:
        retired_client = _client
        _client = None
        _client_fingerprint = ""
    _close_client_best_effort(retired_client)


def _status_code(exc: BaseException) -> int | None:
    if isinstance(exc, ClientError):
        response = exc.response if isinstance(exc.response, dict) else {}
        metadata = response.get("ResponseMetadata") if isinstance(response.get("ResponseMetadata"), dict) else {}
        try:
            return int(metadata.get("HTTPStatusCode"))
        except (TypeError, ValueError):
            pass
    match = _STATUS_PATTERN.search(str(exc))
    return int(match.group(1)) if match else None


def _storage_error(operation: str, exc: BaseException) -> R2StorageError:
    if isinstance(exc, R2StorageError):
        return exc
    status = _status_code(exc)
    message = str(exc).lower()
    if status == 522:
        return R2StorageError(operation, "edge_522", retryable=True, status_code=status)
    if isinstance(exc, ProxyConnectionError):
        return R2StorageError(operation, "proxy_transport", retryable=True, status_code=status)
    if isinstance(exc, SSLError) or "bad record mac" in message or "ssl" in message or "tls" in message:
        return R2StorageError(operation, "tls_transport", retryable=True, status_code=status)
    if isinstance(exc, (ConnectTimeoutError, ReadTimeoutError)) or "timed out" in message or "timeout" in message:
        return R2StorageError(operation, "timeout", retryable=True, status_code=status)
    if isinstance(exc, (ConnectionClosedError, EndpointConnectionError)) or any(
        marker in message for marker in ("connection reset", "connection aborted", "remote end closed")
    ):
        return R2StorageError(operation, "connection_transport", retryable=True, status_code=status)
    if status == 429:
        return R2StorageError(operation, "throttled", retryable=True, status_code=status)
    if status is not None and 500 <= status <= 599:
        return R2StorageError(operation, "upstream_5xx", retryable=True, status_code=status)
    if status == 404:
        return R2StorageError(operation, "not_found", retryable=False, status_code=status)
    if status in {401, 403}:
        return R2StorageError(operation, "access_denied", retryable=False, status_code=status)
    if isinstance(exc, ClientError):
        return R2StorageError(operation, "client_error", retryable=False, status_code=status)
    if isinstance(exc, BotoCoreError):
        return R2StorageError(operation, "transport_error", retryable=True, status_code=status)
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return R2StorageError(operation, "local_io", retryable=False, status_code=status)
    return R2StorageError(operation, "unexpected", retryable=False, status_code=status)


def _execute(operation: str, callback: Callable[[Any, _R2Settings], _T]) -> _T:
    settings: _R2Settings | None = None
    try:
        client, settings = _client_context()
        return callback(client, settings)
    except R2ConfigurationError:
        raise
    except Exception as exc:
        failure = _storage_error(operation, exc)
        if failure.category != "not_found":
            logger.warning(
                "r2.%s_failed category=%s retryable=%s status_code=%s proxy_mode=%s total_max_attempts=%s",
                operation,
                failure.category,
                failure.retryable,
                failure.status_code or 0,
                settings.proxy_mode if settings is not None else ("dedicated" if (os.getenv("R2_PROXY_URL") or "").strip() else "disabled"),
                settings.total_max_attempts if settings is not None else 0,
            )
        # Suppress the raw exception chain: botocore messages may contain a
        # credential-bearing proxy URL.  The classified exception is the only
        # error allowed across this boundary.
        raise failure from None


def upload_file(local_path: str, r2_key: str, content_type: str = "") -> str:
    extra = {"ContentType": content_type} if content_type else None

    def _upload(client: Any, settings: _R2Settings) -> str:
        client.upload_file(local_path, settings.bucket, r2_key, ExtraArgs=extra)
        return r2_key

    return _execute("upload", _upload)


def download_file(r2_key: str, local_path: str) -> str:
    def _download(client: Any, settings: _R2Settings) -> str:
        client.download_file(settings.bucket, r2_key, local_path)
        return local_path

    return _execute("download", _download)


def delete_object(r2_key: str) -> bool:
    if not r2_key or not _configured():
        return False
    try:
        _execute("delete", lambda client, settings: client.delete_object(Bucket=settings.bucket, Key=r2_key))
        return True
    except R2StorageError as exc:
        if exc.category == "not_found":
            return False
        raise


def get_presigned_url(r2_key: str, expires: int = 3600) -> str:
    return _execute(
        "presign",
        lambda client, settings: client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.bucket, "Key": r2_key},
            ExpiresIn=expires,
        ),
    )


def object_exists(r2_key: str) -> bool:
    if not r2_key or not _configured():
        return False
    try:
        _execute("head", lambda client, settings: client.head_object(Bucket=settings.bucket, Key=r2_key))
        return True
    except R2StorageError as exc:
        if exc.category == "not_found":
            return False
        raise


__all__ = [
    "R2ConfigurationError",
    "R2StorageError",
    "delete_object",
    "download_file",
    "get_presigned_url",
    "object_exists",
    "upload_file",
]
