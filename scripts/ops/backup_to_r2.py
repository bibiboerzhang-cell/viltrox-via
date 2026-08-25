#!/usr/bin/env python3
"""Push one verified production PostgreSQL backup to Cloudflare R2.

Run with the repository virtualenv interpreter, never a bare ``python3``:

    /opt/viltrox-2.0/.venv/bin/python -B \
        /opt/viltrox-2.0/current/scripts/ops/backup_to_r2.py \
        --env-file /opt/viltrox-2.0/.env \
        --work-dir /mnt/HC_Volume_106700445/vkpi-backups/r2-staging

``boto3`` only exists inside ``.venv``.  A bare ``python3`` used to degrade
silently to "no R2"; here the missing import is a hard, named failure
(``stage=client category=boto3_missing``).

What one run does:

1. resolve credentials from the private ``.env`` (never from ``argv``, never
   from the process environment of a child, never printed);
2. skip immediately, before the expensive dump, when both the object and its
   sidecar already exist for this stamp.  ``--stamp`` defaults to the current
   UTC second, so a scheduled run always writes a new key; the skip is what
   makes an explicit ``--stamp`` retry safe to repeat.  A dump present without
   its sidecar is treated as an interrupted upload and redone, not skipped;
3. ``pg_dump --format=custom`` into a work directory that defaults to the
   attached volume, validate the archive with ``pg_restore --list``, gzip it
   and compute the SHA-256 of the uploaded bytes;
4. upload ``vkpi-db/<YYYY>/<MM>/<DD>/prod-db-<stamp>.dump.gz`` plus its
   ``.sha256`` sidecar;
5. read back: ``HEAD`` the object and compare the byte length, then ``GET``
   the sidecar and compare its content.  A mismatch is a failure that keeps
   the local temporary files and exits non-zero;
6. report this run's uploaded bytes and, when the token may list, the
   cumulative object count and size under the prefix.

The dedicated backup token (``R2_BACKUP_BUCKET`` /
``R2_BACKUP_ACCESS_KEY_ID`` / ``R2_BACKUP_SECRET_ACCESS_KEY``) should be
scoped to the backup bucket only.  Read-back verification needs ``HEAD`` and
``GET``, so the narrowest workable Cloudflare role is object read+write on
that one bucket.  When those variables are absent the run falls back to the
shared ``R2_*`` token and says so loudly.

Everything on stdout is one JSON object per line; failures also print one
JSON line on stderr.  Exit codes: 0 success or idempotent skip, 1 failure,
2 usage error.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(1, str(_SCRIPTS_DIR))
_OPS_DIR = Path(__file__).resolve().parent
if str(_OPS_DIR) not in sys.path:
    sys.path.insert(1, str(_OPS_DIR))

from stdout_utils import out as stdout_out  # noqa: E402
from backup_to_r2_config import (  # noqa: E402
    FALLBACK_WARNING_CODE,
    FALLBACK_WARNING_TEXT,
    PG_SERVICE_NAME,
    BackupError,
    PostgresCredentials,
    R2Credentials,
    load_env_file,
    parse_database_url,
    register_secret,
    reset_secrets,
    resolve_r2_credentials,
    scrub,
    write_libpq_files,
)
from backup_to_r2_transport import (  # noqa: E402
    build_client,
    inventory,
    object_exists,
    upload_object,
    verify_readback,
)


DEFAULT_ROOT = "/opt/viltrox-2.0"
DEFAULT_WORK_DIR = "/mnt/HC_Volume_106700445/vkpi-backups/r2-staging"
DEFAULT_PREFIX = "vkpi-db"
DEFAULT_MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024
STAMP_RE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})T\d{6}Z$")
PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9/_-]{0,63}$")
DUMP_CONTENT_TYPE = "application/gzip"
SIDECAR_CONTENT_TYPE = "text/plain"
GZIP_COMPRESSLEVEL = 6
COPY_CHUNK_BYTES = 1024 * 1024
SUBPROCESS_ERROR_CHARS = 500
PG_DUMP_TIMEOUT_SEC = 7200
PG_RESTORE_TIMEOUT_SEC = 900
SUBPROCESS_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "TZ", "LD_LIBRARY_PATH")
RETAINED_CATEGORY_PREFIX = "readback_"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(event: str, *, stream: Any = None, **fields: Any) -> None:
    """Write one scrubbed JSON log line.

    Redaction is never silent: when a registered secret had to be removed an
    extra ``log_redaction_applied`` line names the event it happened in.
    """

    payload: dict[str, Any] = {"event": event, "at": utcnow()}
    payload.update(fields)
    line = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    safe = scrub(line)
    target = stream if stream is not None else sys.stdout
    if safe != line:
        stdout_out(
            json.dumps(
                {
                    "event": "log_redaction_applied",
                    "at": utcnow(),
                    "source_event": event,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    stdout_out(safe, file=target)


def emit_error(event: str, **fields: Any) -> None:
    emit(event, stream=sys.stderr, **fields)


def default_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def object_keys(prefix: str, stamp: str) -> tuple[str, str, str]:
    """Return ``(dump_key, sidecar_key, filename)`` for one stamp."""

    match = STAMP_RE.fullmatch(stamp)
    if match is None:
        raise BackupError("configure", "stamp_invalid", hint=f"stamp={stamp}")
    if not PREFIX_RE.fullmatch(prefix) or "//" in prefix or ".." in prefix:
        raise BackupError("configure", "prefix_invalid", hint=f"prefix={prefix}")
    filename = f"prod-db-{stamp}.dump.gz"
    dump_key = (
        f"{prefix}/{match['year']}/{match['month']}/{match['day']}/{filename}"
    )
    return dump_key, f"{dump_key}.sha256", filename


def sidecar_body(sha256_hex: str, filename: str) -> bytes:
    """``sha256sum`` wire format, matching the on-host backup sidecars."""

    return f"{sha256_hex}  {filename}\n".encode("utf-8")


def ensure_work_dir(path: Path) -> None:
    """Create/validate a private work directory, warning when it is on ``/``."""

    if path.is_symlink():
        raise BackupError("workspace", "work_dir_is_symlink", hint=f"path={path}")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise BackupError(
            "workspace", "work_dir_create_failed", hint=f"path={path}"
        ) from exc
    if not path.is_dir():
        raise BackupError("workspace", "work_dir_not_directory", hint=f"path={path}")
    info = path.stat()
    if info.st_mode & stat.S_IWOTH:
        raise BackupError(
            "workspace", "work_dir_world_writable", hint=f"path={path}"
        )
    try:
        root_device = os.stat("/").st_dev
    except OSError as exc:
        raise BackupError("workspace", "root_device_unreadable") from exc
    if info.st_dev == root_device:
        emit(
            "work_dir_on_root_device",
            severity="warning",
            path=str(path),
            message=(
                "work directory shares the root filesystem; the dump will "
                "consume system-disk space"
            ),
        )


def check_free_space(path: Path, *, minimum_bytes: int) -> int:
    usage = shutil.disk_usage(path)
    if usage.free < minimum_bytes:
        raise BackupError(
            "workspace",
            "insufficient_free_space",
            hint=f"free={usage.free} required={minimum_bytes} path={path}",
        )
    return usage.free


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _subprocess_env(extra: Mapping[str, str]) -> dict[str, str]:
    """Build a minimal child environment; no inherited PG*/DATABASE_URL."""

    env = {
        key: os.environ[key]
        for key in SUBPROCESS_ENV_PASSTHROUGH
        if os.environ.get(key)
    }
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(extra)
    return env


def _run(
    binary: str,
    arguments: list[str],
    *,
    env: Mapping[str, str],
    stage: str,
    timeout: int,
) -> None:
    resolved = shutil.which(binary)
    if not resolved:
        raise BackupError(stage, "binary_not_found", hint=f"binary={binary}")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed binary, no shell
            [resolved, *arguments],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackupError(stage, "timeout", hint=f"binary={binary}") from exc
    except OSError as exc:
        raise BackupError(stage, "spawn_failed", hint=f"binary={binary}") from exc
    if completed.returncode != 0:
        emit_error(
            "subprocess_failed",
            stage=stage,
            binary=binary,
            returncode=completed.returncode,
            stderr_head=scrub(completed.stderr or "")[:SUBPROCESS_ERROR_CHARS],
        )
        raise BackupError(
            stage,
            "nonzero_exit",
            hint=f"binary={binary} returncode={completed.returncode}",
        )


def create_dump(
    *,
    staging: Path,
    stamp: str,
    credentials: PostgresCredentials,
) -> tuple[Path, str, int, int]:
    """Dump, validate and gzip. Returns ``(gz_path, sha256, gz_size, raw_size)``."""

    service_path = staging / ".pgservice"
    pgpass_path = staging / ".pgpass"
    raw_path = staging / f"prod-db-{stamp}.dump"
    gz_path = staging / f"prod-db-{stamp}.dump.gz"
    try:
        write_libpq_files(
            credentials, service_path=service_path, pgpass_path=pgpass_path
        )
        env = _subprocess_env(
            {
                "PGSERVICEFILE": str(service_path),
                "PGSERVICE": PG_SERVICE_NAME,
                "PGPASSFILE": str(pgpass_path),
                "PGCONNECT_TIMEOUT": "15",
                "HOME": str(staging),
                "TMPDIR": str(staging),
                "PGCLIENTENCODING": "UTF8",
            }
        )
        # Only file paths reach argv; the password lives in the 0600 passfile.
        _run(
            "pg_dump",
            ["--format=custom", "--no-owner", "--no-acl", f"--file={raw_path}"],
            env=env,
            stage="dump",
            timeout=PG_DUMP_TIMEOUT_SEC,
        )
    finally:
        service_path.unlink(missing_ok=True)
        pgpass_path.unlink(missing_ok=True)

    if not raw_path.is_file() or raw_path.stat().st_size == 0:
        raise BackupError("dump", "empty_archive")
    raw_size = raw_path.stat().st_size
    _run(
        "pg_restore",
        ["--list", str(raw_path)],
        env=_subprocess_env({"HOME": str(staging), "TMPDIR": str(staging)}),
        stage="dump",
        timeout=PG_RESTORE_TIMEOUT_SEC,
    )

    # pg_dump's custom format is already zlib-compressed; the gzip wrapper is
    # the reviewed transport envelope, not a space optimisation.
    with raw_path.open("rb") as source:
        with gzip.open(gz_path, "wb", compresslevel=GZIP_COMPRESSLEVEL) as target:
            shutil.copyfileobj(source, target, COPY_CHUNK_BYTES)
    raw_path.unlink()
    gz_size = gz_path.stat().st_size
    if gz_size == 0:
        raise BackupError("dump", "empty_compressed_archive")
    return gz_path, sha256_file(gz_path), gz_size, raw_size


def _cleanup(staging: Path, *, retain: bool, reason: str) -> None:
    if retain:
        emit(
            "temporary_files_retained",
            severity="warning",
            path=str(staging),
            reason=reason,
            message="local artifacts kept for manual inspection; delete them once resolved",
        )
        return
    shutil.rmtree(staging, ignore_errors=True)
    if staging.exists():
        emit_error("temporary_cleanup_incomplete", path=str(staging))
    else:
        emit("temporary_files_removed", path=str(staging))


def run(args: argparse.Namespace, *, client_factory: Callable[[R2Credentials], Any]) -> int:
    started = time.monotonic()
    env = load_env_file(Path(args.env_file))
    raw_url = str(env.get("DATABASE_URL") or "").strip()
    register_secret(raw_url)
    postgres = parse_database_url(raw_url)
    register_secret(postgres.password)
    r2 = resolve_r2_credentials(env)
    register_secret(r2.access_key)
    register_secret(r2.secret_key)
    if r2.is_fallback:
        emit(
            FALLBACK_WARNING_CODE,
            severity="warning",
            message=FALLBACK_WARNING_TEXT,
            missing_backup_keys=list(r2.missing_backup_keys),
            advice=(
                "provision R2_BACKUP_BUCKET / R2_BACKUP_ACCESS_KEY_ID / "
                "R2_BACKUP_SECRET_ACCESS_KEY scoped to the backup bucket"
            ),
        )

    stamp = args.stamp or default_stamp()
    dump_key, sidecar_key, filename = object_keys(args.prefix, stamp)
    emit(
        "backup_started",
        stamp=stamp,
        database=postgres.dbname,
        object_key=dump_key,
        token_source=r2.token_source,
        work_dir=args.work_dir,
    )

    client = client_factory(r2)
    if object_exists(
        client, bucket=r2.bucket, key=dump_key, stage="idempotency"
    ) and object_exists(
        client, bucket=r2.bucket, key=sidecar_key, stage="idempotency"
    ):
        emit(
            "backup_skipped_existing_stamp",
            stamp=stamp,
            object_key=dump_key,
            sidecar_key=sidecar_key,
            uploaded_bytes=0,
            inventory=inventory(client, bucket=r2.bucket, prefix=args.prefix),
        )
        return 0

    work_dir = Path(args.work_dir)
    ensure_work_dir(work_dir)
    check_free_space(work_dir, minimum_bytes=args.min_free_bytes)
    staging = Path(tempfile.mkdtemp(prefix=f".vkpi-r2-{stamp}.", dir=work_dir))
    os.chmod(staging, 0o700)
    retain = bool(args.keep_temp)
    retain_reason = "keep_temp_flag" if retain else ""
    try:
        gz_path, sha256_hex, gz_size, raw_size = create_dump(
            staging=staging, stamp=stamp, credentials=postgres
        )
        emit(
            "dump_ready",
            stamp=stamp,
            dump_bytes=raw_size,
            compressed_bytes=gz_size,
            sha256=sha256_hex,
        )
        sidecar = sidecar_body(sha256_hex, filename)
        with gz_path.open("rb") as handle:
            upload_object(
                client,
                bucket=r2.bucket,
                key=dump_key,
                body=handle,
                content_type=DUMP_CONTENT_TYPE,
                sha256_hex=sha256_hex,
            )
        upload_object(
            client,
            bucket=r2.bucket,
            key=sidecar_key,
            body=sidecar,
            content_type=SIDECAR_CONTENT_TYPE,
            sha256_hex=hashlib.sha256(sidecar).hexdigest(),
        )
        emit(
            "upload_completed",
            object_key=dump_key,
            sidecar_key=sidecar_key,
            uploaded_bytes=gz_size + len(sidecar),
        )
        verify_readback(
            client,
            bucket=r2.bucket,
            dump_key=dump_key,
            sidecar_key=sidecar_key,
            expected_size=gz_size,
            expected_sha256=sha256_hex,
            expected_sidecar=sidecar,
        )
        emit("readback_verified", object_key=dump_key, sha256=sha256_hex)
        emit(
            "backup_completed",
            stamp=stamp,
            object_key=dump_key,
            sidecar_key=sidecar_key,
            uploaded_bytes=gz_size + len(sidecar),
            compressed_bytes=gz_size,
            dump_bytes=raw_size,
            sha256=sha256_hex,
            token_source=r2.token_source,
            duration_sec=round(time.monotonic() - started, 3),
            inventory=inventory(client, bucket=r2.bucket, prefix=args.prefix),
        )
        return 0
    except BackupError as exc:
        if exc.category.startswith(RETAINED_CATEGORY_PREFIX):
            retain = True
            retain_reason = exc.category
            # The object is deliberately left in place: this script has no
            # delete path.  Say so, because re-running with the same --stamp
            # would find the key present and skip as "already done".  A normal
            # timer run gets a fresh stamp and is unaffected.
            emit(
                "readback_mismatch_object_left_in_place",
                severity="warning",
                object_key=dump_key,
                sidecar_key=sidecar_key,
                category=exc.category,
                message=(
                    "the uploaded object failed verification and was not "
                    "deleted; do not re-run this same stamp before removing it"
                ),
            )
        raise
    finally:
        _cleanup(staging, retain=retain, reason=retain_reason or "unspecified")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push a verified production PostgreSQL backup to Cloudflare R2"
    )
    parser.add_argument(
        "--env-file",
        default=os.environ.get("VKPI_BACKUP_R2_ENV_FILE")
        or f"{DEFAULT_ROOT}/.env",
        help="Private env file holding DATABASE_URL and the R2 token",
    )
    parser.add_argument(
        "--work-dir",
        default=os.environ.get("VKPI_BACKUP_R2_WORKDIR") or DEFAULT_WORK_DIR,
        help="Directory for temporary dump artifacts (keep it off the system disk)",
    )
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="R2 key prefix")
    parser.add_argument(
        "--stamp", default="", help="Override the UTC stamp (YYYYMMDDTHHMMSSZ)"
    )
    parser.add_argument(
        "--min-free-bytes",
        type=int,
        default=DEFAULT_MIN_FREE_BYTES,
        help="Refuse to start when the work directory has less free space",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary artifacts even on success (debugging)",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[R2Credentials], Any] = build_client,
) -> int:
    args = parse_args(argv)
    if args.min_free_bytes < 0:
        emit_error("usage_error", detail="--min-free-bytes must be non-negative")
        return 2
    try:
        return run(args, client_factory=client_factory)
    except BackupError as exc:
        emit_error(
            "backup_failed",
            stage=exc.stage,
            category=exc.category,
            status_code=exc.status_code,
            hint=exc.hint,
        )
        return 1
    except Exception as exc:  # noqa: BLE001 - public, credential-free boundary
        emit_error(
            "backup_failed",
            stage="internal",
            category="unexpected",
            detail=scrub(f"{type(exc).__name__}: {exc}")[:SUBPROCESS_ERROR_CHARS],
        )
        return 1
    finally:
        reset_secrets()


if __name__ == "__main__":
    raise SystemExit(main())
