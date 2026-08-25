#!/usr/bin/env python3
"""Private configuration parsing for ``scripts/ops/backup_to_r2.py``.

This module owns every credential-shaped value the R2 backup pusher touches:

* reading the shared ``.env`` as a private regular file;
* turning ``DATABASE_URL`` into short-lived libpq ``service``/``passfile``
  files so no password ever reaches ``argv``, the process environment of a
  child, or a log line;
* choosing between the dedicated backup token (``R2_BACKUP_*``) and the
  shared read/write token (``R2_*``), and reporting which one was used.

Nothing here imports a third-party package, so it stays importable under a
bare ``python3`` even though the parent script requires the repository
``.venv`` interpreter for ``boto3``.

Every secret handed to :func:`register_secret` is scrubbed out of log lines
and exception messages by :func:`scrub`, which the parent script applies to
all of its output.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qsl, unquote, urlsplit


ENV_FILE_MAX_BYTES = 1024 * 1024
PG_SERVICE_NAME = "vkpi_r2_backup"
ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
R2_ENDPOINT_SUFFIX = ".r2.cloudflarestorage.com"
REDACTION_PLACEHOLDER = "***REDACTED***"
MIN_SECRET_LENGTH = 4

BACKUP_TOKEN_KEYS = (
    "R2_BACKUP_BUCKET",
    "R2_BACKUP_ACCESS_KEY_ID",
    "R2_BACKUP_SECRET_ACCESS_KEY",
)
SHARED_TOKEN_KEYS = (
    "R2_BUCKET_NAME",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
TOKEN_SOURCE_DEDICATED = "dedicated_backup_token"
TOKEN_SOURCE_FALLBACK = "shared_readwrite_token_fallback"
FALLBACK_WARNING_TEXT = "正在使用读写全权令牌,建议改用只写令牌"
FALLBACK_WARNING_CODE = "r2_token_fallback_shared_readwrite"


class BackupError(RuntimeError):
    """A public, credential-free backup failure.

    ``hint`` is scrubbed at construction time so a raw subprocess or SDK
    message can never smuggle a password into a traceback.
    """

    def __init__(
        self,
        stage: str,
        category: str,
        *,
        hint: str = "",
        status_code: int | None = None,
    ) -> None:
        self.stage = stage
        self.category = category
        self.hint = scrub(str(hint or ""))
        self.status_code = status_code
        message = f"backup_to_r2 failed: stage={stage} category={category}"
        if self.status_code is not None:
            message = f"{message} status={self.status_code}"
        if self.hint:
            message = f"{message} hint={self.hint}"
        super().__init__(message)


_SECRETS: list[str] = []


def register_secret(value: str | None) -> None:
    """Remember a value that must never appear in output."""

    text = str(value or "")
    if len(text) >= MIN_SECRET_LENGTH and text not in _SECRETS:
        _SECRETS.append(text)


def reset_secrets() -> None:
    """Drop the registry (tests and long-lived callers only)."""

    _SECRETS.clear()


def registered_secret_count() -> int:
    return len(_SECRETS)


def scrub(text: str) -> str:
    """Replace every registered secret in ``text`` with a visible marker."""

    result = str(text)
    for secret in _SECRETS:
        if secret and secret in result:
            result = result.replace(secret, REDACTION_PLACEHOLDER)
    return result


def read_private_file(path: Path, *, maximum_bytes: int = ENV_FILE_MAX_BYTES) -> bytes:
    """Read a private, regular, non-symlink file or raise :class:`BackupError`."""

    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupError(
            "configure", "env_file_unreadable", hint=f"path={path}"
        ) from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or path.is_symlink()
        or info.st_mode & (stat.S_IWGRP | stat.S_IRWXO)
    ):
        raise BackupError(
            "configure",
            "env_file_not_private_regular",
            hint=f"path={path} mode={stat.S_IMODE(info.st_mode):04o}",
        )
    if info.st_size > maximum_bytes:
        raise BackupError("configure", "env_file_too_large", hint=f"path={path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BackupError(
            "configure", "env_file_unreadable", hint=f"path={path}"
        ) from exc


def load_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines from a private env file into memory."""

    raw = read_private_file(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BackupError("configure", "env_file_not_utf8", hint=f"path={path}") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if ENV_KEY_RE.fullmatch(key):
            values[key] = value.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class PostgresCredentials:
    """libpq connection parameters plus the password, kept out of ``repr``."""

    params: tuple[tuple[str, str], ...]
    password: str = field(repr=False, default="")

    @property
    def dbname(self) -> str:
        for key, value in self.params:
            if key == "dbname":
                return value
        return ""

    @property
    def host(self) -> str:
        for key, value in self.params:
            if key == "host":
                return value
        return ""


def parse_database_url(value: str) -> PostgresCredentials:
    """Translate a PostgreSQL URL into libpq parameters.

    Mirrors the reviewed parser inside ``scripts/ops/backup_prod_vkpi.sh`` so
    both backup paths reject the same unsafe inputs.
    """

    text = str(value or "").strip()
    if not text:
        raise BackupError("configure", "database_url_missing")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise BackupError("configure", "database_url_invalid") from exc
    if parts.scheme not in {"postgres", "postgresql"} or parts.fragment:
        raise BackupError("configure", "database_url_invalid")

    params: dict[str, str] = {}
    if parts.hostname:
        params["host"] = unquote(parts.hostname)
    try:
        port = parts.port
    except ValueError as exc:
        raise BackupError("configure", "database_url_port_invalid") from exc
    if port is not None:
        params["port"] = str(port)
    if parts.username is not None:
        params["user"] = unquote(parts.username)
    password = unquote(parts.password) if parts.password is not None else ""
    if parts.path and parts.path != "/":
        params["dbname"] = unquote(parts.path[1:])
    for key, candidate in parse_qsl(parts.query, keep_blank_values=True):
        if not ENV_KEY_RE.fullmatch(key):
            raise BackupError("configure", "database_url_parameter_invalid")
        key = key.lower()
        if key == "password":
            password = candidate
        elif key in {"service", "passfile"}:
            raise BackupError("configure", "database_url_parameter_forbidden")
        else:
            params[key] = candidate
    unsafe = any(ch in candidate for candidate in params.values() for ch in "\r\n\0")
    if unsafe or any(ch in password for ch in "\r\n\0"):
        raise BackupError("configure", "database_url_control_character")
    if not params.get("dbname"):
        raise BackupError("configure", "database_url_without_dbname")
    return PostgresCredentials(
        params=tuple(sorted(params.items())),
        password=password,
    )


def write_private_text(path: Path, text: str) -> None:
    """Create a new 0600 file, refusing to reuse an existing path."""

    try:
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise BackupError(
            "configure", "private_file_create_failed", hint=f"path={path}"
        ) from exc
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def write_libpq_files(
    credentials: PostgresCredentials,
    *,
    service_path: Path,
    pgpass_path: Path,
) -> None:
    """Materialise short-lived libpq service/passfile files for ``pg_dump``."""

    body = (
        f"[{PG_SERVICE_NAME}]\n"
        + "\n".join(f"{key}={value}" for key, value in credentials.params)
        + "\n"
    )
    write_private_text(service_path, body)
    escaped = credentials.password.replace("\\", "\\\\").replace(":", "\\:")
    write_private_text(pgpass_path, f"*:*:*:*:{escaped}\n")


@dataclass(frozen=True)
class R2Credentials:
    """Resolved R2 endpoint/bucket plus the token actually in use."""

    endpoint: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    bucket: str
    token_source: str
    missing_backup_keys: tuple[str, ...]

    @property
    def is_fallback(self) -> bool:
        return self.token_source == TOKEN_SOURCE_FALLBACK


def _required(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip()


def _validate_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise BackupError("configure", "r2_endpoint_invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(R2_ENDPOINT_SUFFIX)
    ):
        raise BackupError("configure", "r2_endpoint_not_cloudflare_https")
    return endpoint


def resolve_r2_credentials(env: Mapping[str, str]) -> R2Credentials:
    """Prefer the dedicated backup token; fall back to the shared token.

    A fallback is never silent: the caller receives ``missing_backup_keys``
    and must log :data:`FALLBACK_WARNING_TEXT`.
    """

    missing = tuple(key for key in BACKUP_TOKEN_KEYS if not _required(env, key))
    if not missing:
        bucket = _required(env, "R2_BACKUP_BUCKET")
        access_key = _required(env, "R2_BACKUP_ACCESS_KEY_ID")
        secret_key = _required(env, "R2_BACKUP_SECRET_ACCESS_KEY")
        token_source = TOKEN_SOURCE_DEDICATED
    else:
        shared_missing = [key for key in SHARED_TOKEN_KEYS if not _required(env, key)]
        if shared_missing:
            raise BackupError(
                "configure",
                "r2_credentials_missing",
                hint="missing=" + ",".join(list(missing) + shared_missing),
            )
        bucket = _required(env, "R2_BUCKET_NAME")
        access_key = _required(env, "R2_ACCESS_KEY_ID")
        secret_key = _required(env, "R2_SECRET_ACCESS_KEY")
        token_source = TOKEN_SOURCE_FALLBACK

    endpoint = _required(env, "R2_BACKUP_ENDPOINT") or _required(env, "R2_ENDPOINT")
    if not endpoint:
        raise BackupError(
            "configure", "r2_credentials_missing", hint="missing=R2_ENDPOINT"
        )
    if not BUCKET_RE.fullmatch(bucket) or ".." in bucket:
        raise BackupError("configure", "r2_bucket_invalid")
    return R2Credentials(
        endpoint=_validate_endpoint(endpoint),
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        token_source=token_source,
        missing_backup_keys=missing,
    )
