#!/usr/bin/env python3
"""Safely manage release database aliases in PgBouncer.

Only the ``[databases]`` section is mutable.  Every accepted mapping must be an
explicit, credential-free loopback mapping to PostgreSQL on 127.0.0.1:5432.
The helper never prints configuration values and writes only hashes, safe
database identifiers, and file metadata to its JSON receipts.  Mutating
commands are intended to run as the OS account that owns the PgBouncer config
(normally ``postgres``); no root UID is assumed or accepted as a substitute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit


RECEIPT_SCHEMA = "vkpi-pgbouncer-release-map/v1"
DATABASE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(?:[;#].*)?$")
SAFE_MAPPING_KEYS = frozenset({"host", "port", "dbname"})
LOOPBACK_HOST = "127.0.0.1"
POSTGRES_PORT = "5432"
PRIVATE_FILE_MODE = 0o600
MAX_FILE_BYTES = 1024 * 1024


class PgbouncerMapError(RuntimeError):
    """A fail-closed PgBouncer release mapping violation."""


@dataclass(frozen=True)
class FileSnapshot:
    payload: bytes
    uid: int
    gid: int
    mode: int
    device: int
    inode: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True)
class ParsedConfig:
    databases: tuple[str, ...]
    body_start: int
    body_end: int
    newline: bytes
    trailing_newline: bool
    following_section: bool


def _validate_database(name: str, *, label: str) -> str:
    if name == "*" or not DATABASE_RE.fullmatch(name):
        raise PgbouncerMapError(f"{label} is not a safe PostgreSQL identifier")
    return name


def _validate_sha256(value: str, *, label: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise PgbouncerMapError(f"{label} is not a SHA-256 digest")
    return value


def _secure_parent(path: Path, *, label: str) -> Path:
    parent = path.absolute().parent
    try:
        info = parent.lstat()
    except FileNotFoundError as exc:
        raise PgbouncerMapError(f"{label} parent is missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PgbouncerMapError(f"{label} parent must be a real directory")
    if info.st_uid != os.geteuid():
        raise PgbouncerMapError(f"{label} parent is not controller-owned")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise PgbouncerMapError(f"{label} parent is group/world writable")
    return parent


def _readonly_parent(path: Path, *, label: str) -> Path:
    parent = path.absolute().parent
    try:
        info = parent.lstat()
    except FileNotFoundError as exc:
        raise PgbouncerMapError(f"{label} parent is missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise PgbouncerMapError(f"{label} parent must be a real directory")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise PgbouncerMapError(f"{label} parent is group/world writable")
    return parent


def _read_stable_file(
    path: Path,
    *,
    label: str,
    private: bool = False,
    require_parent_owner: bool = True,
) -> FileSnapshot:
    path = path.absolute()
    if require_parent_owner:
        _secure_parent(path, label=label)
    else:
        _readonly_parent(path, label=label)
    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise PgbouncerMapError(f"{label} is missing") from exc
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise PgbouncerMapError(f"{label} must be a regular single-link file")
    mode = stat.S_IMODE(initial.st_mode)
    if mode & 0o7022:
        raise PgbouncerMapError(f"{label} has unsafe mode bits")
    if private and (initial.st_uid != os.geteuid() or mode != PRIVATE_FILE_MODE):
        raise PgbouncerMapError(f"{label} must be controller-owned mode 0600")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino)
            != (initial.st_dev, initial.st_ino)
        ):
            raise PgbouncerMapError(f"{label} changed while it was opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise PgbouncerMapError(f"{label} exceeds the 1 MiB limit")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return FileSnapshot(
        payload=b"".join(chunks),
        uid=current.st_uid,
        gid=current.st_gid,
        mode=stat.S_IMODE(current.st_mode),
        device=current.st_dev,
        inode=current.st_ino,
    )


def _detect_newline(payload: bytes) -> bytes:
    without_crlf = payload.replace(b"\r\n", b"")
    if b"\r" in without_crlf:
        raise PgbouncerMapError("PgBouncer config contains unsupported bare CR")
    has_crlf = b"\r\n" in payload
    has_lf = b"\n" in without_crlf
    if has_crlf and has_lf:
        raise PgbouncerMapError("PgBouncer config contains mixed line endings")
    if not has_crlf and not has_lf:
        raise PgbouncerMapError("PgBouncer config must contain line endings")
    return b"\r\n" if has_crlf else b"\n"


def _parse_mapping_line(line: str) -> str:
    if line.count("=") < 1:
        raise PgbouncerMapError("invalid entry in [databases]")
    alias_raw, value_raw = line.split("=", 1)
    alias = _validate_database(alias_raw.strip(), label="database alias")
    if not value_raw.strip():
        raise PgbouncerMapError("empty entry in [databases]")

    values: dict[str, str] = {}
    for token in value_raw.split():
        if token.count("=") != 1:
            raise PgbouncerMapError("unsupported token in [databases]")
        key, value = token.split("=", 1)
        normalized_key = key.strip().lower()
        if (
            not normalized_key
            or not value
            or normalized_key in values
            or normalized_key not in SAFE_MAPPING_KEYS
        ):
            raise PgbouncerMapError("unsafe or duplicate option in [databases]")
        values[normalized_key] = value
    if set(values) != SAFE_MAPPING_KEYS:
        raise PgbouncerMapError("database mapping is not an explicit local mapping")
    mapped_database = _validate_database(
        values["dbname"],
        label="mapped database",
    )
    if (
        values["host"] != LOOPBACK_HOST
        or values["port"] != POSTGRES_PORT
        or mapped_database != alias
    ):
        raise PgbouncerMapError("database mapping does not target its local namesake")
    return alias


def parse_config(payload: bytes) -> ParsedConfig:
    newline = _detect_newline(payload)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PgbouncerMapError("PgBouncer config is not UTF-8") from exc
    if text.startswith("\ufeff"):
        raise PgbouncerMapError("PgBouncer config must not contain a BOM")

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    database_headers: list[int] = []
    section_indices: list[int] = []
    for index, raw_line in enumerate(lines):
        offsets.append(offset)
        offset += len(raw_line.encode("utf-8"))
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped.lower().startswith("%include"):
            raise PgbouncerMapError("PgBouncer include directives are not allowed")
        match = SECTION_RE.fullmatch(line)
        if match:
            section_indices.append(index)
            if match.group(1).lower() == "databases":
                database_headers.append(index)
    if len(database_headers) != 1:
        raise PgbouncerMapError("PgBouncer config must contain one [databases] section")

    header_index = database_headers[0]
    header_line = lines[header_index]
    if not header_line.endswith(("\n", "\r\n")):
        raise PgbouncerMapError("[databases] header must end with a line ending")
    next_sections = [index for index in section_indices if index > header_index]
    next_section = min(next_sections) if next_sections else len(lines)
    body_start = offsets[header_index] + len(header_line.encode("utf-8"))
    body_end = offsets[next_section] if next_section < len(lines) else len(payload)

    databases: list[str] = []
    seen: set[str] = set()
    for raw_line in lines[header_index + 1 : next_section]:
        line = raw_line.rstrip("\r\n").strip()
        if not line or line.startswith((";", "#")):
            continue
        alias = _parse_mapping_line(line)
        if alias in seen:
            raise PgbouncerMapError("duplicate database alias in [databases]")
        databases.append(alias)
        seen.add(alias)
    if not databases:
        raise PgbouncerMapError("[databases] must contain an explicit local mapping")

    return ParsedConfig(
        databases=tuple(databases),
        body_start=body_start,
        body_end=body_end,
        newline=newline,
        trailing_newline=payload.endswith(newline),
        following_section=next_section < len(lines),
    )


def _render_mapping(database: str) -> bytes:
    return (
        f"{database} = host={LOOPBACK_HOST} port={POSTGRES_PORT} "
        f"dbname={database}"
    ).encode("ascii")


def _prepared_payload(
    original: bytes,
    parsed: ParsedConfig,
    *,
    source_database: str,
    target_database: str,
) -> bytes:
    mapping_lines = [
        _render_mapping(source_database),
        _render_mapping(target_database),
    ]
    body = parsed.newline.join(mapping_lines)
    if parsed.following_section or parsed.trailing_newline:
        body += parsed.newline
    return original[: parsed.body_start] + body + original[parsed.body_end :]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_private(path: Path, payload: bytes, *, label: str) -> None:
    if len(payload) > MAX_FILE_BYTES:
        raise PgbouncerMapError(f"{label} exceeds the 1 MiB limit")
    path = path.absolute()
    parent = _secure_parent(path, label=label)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise PgbouncerMapError(f"{label} already exists")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    installed = _read_stable_file(path, label=label, private=True)
    if installed.payload != payload:
        raise PgbouncerMapError(f"{label} write verification failed")
    _fsync_directory(parent)


def _atomic_replace(
    path: Path,
    payload: bytes,
    *,
    expected: FileSnapshot,
    uid: int,
    gid: int,
    mode: int,
) -> FileSnapshot:
    if len(payload) > MAX_FILE_BYTES:
        raise PgbouncerMapError("PgBouncer config exceeds the 1 MiB limit")
    path = path.absolute()
    parent = _secure_parent(path, label="PgBouncer config")
    current = _read_stable_file(path, label="PgBouncer config")
    if (
        current.payload != expected.payload
        or current.device != expected.device
        or current.inode != expected.inode
        or (current.uid, current.gid, current.mode)
        != (expected.uid, expected.gid, expected.mode)
    ):
        raise PgbouncerMapError("PgBouncer config changed before replacement")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.release-map-",
        dir=parent,
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            temporary_info = os.fstat(handle.fileno())
            if (temporary_info.st_uid, temporary_info.st_gid) != (uid, gid):
                os.fchown(handle.fileno(), uid, gid)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        _fsync_directory(parent)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)

    installed = _read_stable_file(path, label="PgBouncer config")
    if (
        installed.payload != payload
        or (installed.uid, installed.gid, installed.mode) != (uid, gid, mode)
    ):
        raise PgbouncerMapError("PgBouncer config replacement verification failed")
    return installed


def _base_result(
    operation: str,
    snapshot: FileSnapshot,
    parsed: ParsedConfig,
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "operation": operation,
        "config_sha256": snapshot.sha256,
        "databases": list(parsed.databases),
        "mapping_count": len(parsed.databases),
        "mapping_endpoint": f"{LOOPBACK_HOST}:{POSTGRES_PORT}",
        "database_mapping_credentials_included": False,
    }


def inspect_config(
    config_path: Path,
    *,
    expected_source: str | None = None,
) -> dict[str, Any]:
    snapshot = _read_stable_file(config_path, label="PgBouncer config")
    parsed = parse_config(snapshot.payload)
    if expected_source is not None:
        source = _validate_database(expected_source, label="source database")
        if source not in parsed.databases:
            raise PgbouncerMapError("source database is not mapped")
    return _base_result("inspect", snapshot, parsed)


def _receipt_payload(
    *,
    config_path: Path,
    backup_path: Path,
    original: FileSnapshot,
    prepared: bytes,
    parsed: ParsedConfig,
    source_database: str,
    target_database: str,
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "operation": "prepare",
        "config_path": str(config_path.absolute()),
        "backup_path": str(backup_path.absolute()),
        "source_database": source_database,
        "target_database": target_database,
        "databases_after": [source_database, target_database],
        "config_sha256_before": original.sha256,
        "config_sha256_after": hashlib.sha256(prepared).hexdigest(),
        "backup_sha256": original.sha256,
        "backup_size_bytes": len(original.payload),
        "original_uid": original.uid,
        "original_gid": original.gid,
        "original_mode": original.mode,
        "newline": "crlf" if parsed.newline == b"\r\n" else "lf",
        "database_mapping_credentials_included": False,
    }


def prepare_config(
    config_path: Path,
    *,
    source_database: str,
    target_database: str,
    backup_path: Path,
    receipt_path: Path,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    distinct_paths = {
        config_path.absolute(),
        backup_path.absolute(),
        receipt_path.absolute(),
    }
    if len(distinct_paths) != 3:
        raise PgbouncerMapError("config, backup, and receipt paths must differ")
    source = _validate_database(source_database, label="source database")
    target = _validate_database(target_database, label="target database")
    if source == target:
        raise PgbouncerMapError("source and target databases must differ")
    original = _read_stable_file(config_path, label="PgBouncer config")
    if expected_sha256 is not None:
        expected = _validate_sha256(
            expected_sha256,
            label="expected config SHA-256",
        )
        if original.sha256 != expected:
            raise PgbouncerMapError("PgBouncer config hash changed before prepare")
    if original.uid != os.geteuid():
        raise PgbouncerMapError("PgBouncer config is not controller-owned")
    parsed = parse_config(original.payload)
    if source not in parsed.databases:
        raise PgbouncerMapError("source database is not mapped")
    prepared = _prepared_payload(
        original.payload,
        parsed,
        source_database=source,
        target_database=target,
    )
    prepared_parsed = parse_config(prepared)
    if prepared_parsed.databases != (source, target):
        raise PgbouncerMapError("prepared mappings do not match source and target")

    receipt = _receipt_payload(
        config_path=config_path,
        backup_path=backup_path,
        original=original,
        prepared=prepared,
        parsed=parsed,
        source_database=source,
        target_database=target,
    )
    receipt_bytes = (
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    _write_new_private(backup_path, original.payload, label="original config backup")
    _write_new_private(receipt_path, receipt_bytes, label="mapping receipt")
    installed = _atomic_replace(
        config_path,
        prepared,
        expected=original,
        uid=original.uid,
        gid=original.gid,
        mode=original.mode,
    )
    installed_parsed = parse_config(installed.payload)
    result = _base_result("prepare", installed, installed_parsed)
    result.update(
        {
            "config_sha256_before": original.sha256,
            "config_sha256_after": installed.sha256,
            "backup_sha256": original.sha256,
            "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
            "changed": installed.payload != original.payload,
        }
    )
    return result


def verify_config(
    config_path: Path,
    *,
    source_database: str,
    target_database: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    source = _validate_database(source_database, label="source database")
    target = _validate_database(target_database, label="target database")
    if source == target:
        raise PgbouncerMapError("source and target databases must differ")
    snapshot = _read_stable_file(config_path, label="PgBouncer config")
    parsed = parse_config(snapshot.payload)
    if parsed.databases != (source, target):
        raise PgbouncerMapError("PgBouncer mappings do not match source and target")
    if expected_sha256 is not None:
        expected = _validate_sha256(expected_sha256, label="expected config SHA-256")
        if snapshot.sha256 != expected:
            raise PgbouncerMapError("PgBouncer config hash does not match")
    result = _base_result("verify", snapshot, parsed)
    result["verified"] = True
    return result


def _read_receipt(path: Path) -> tuple[Mapping[str, Any], bytes]:
    snapshot = _read_stable_file(path, label="mapping receipt", private=True)
    try:
        receipt = json.loads(snapshot.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PgbouncerMapError("mapping receipt is invalid") from exc
    if not isinstance(receipt, dict):
        raise PgbouncerMapError("mapping receipt is invalid")
    return receipt, snapshot.payload


def _validate_restore_receipt(
    receipt: Mapping[str, Any],
    *,
    config_path: Path,
    backup_path: Path,
) -> tuple[str, str]:
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA
        or receipt.get("operation") != "prepare"
        or receipt.get("config_path") != str(config_path.absolute())
        or receipt.get("backup_path") != str(backup_path.absolute())
        or receipt.get("database_mapping_credentials_included") is not False
    ):
        raise PgbouncerMapError("mapping receipt binding is invalid")
    if receipt.get("original_uid") != os.geteuid():
        raise PgbouncerMapError("mapping receipt controller binding is invalid")
    source = _validate_database(
        str(receipt.get("source_database", "")),
        label="receipt source database",
    )
    target = _validate_database(
        str(receipt.get("target_database", "")),
        label="receipt target database",
    )
    if source == target or receipt.get("databases_after") != [source, target]:
        raise PgbouncerMapError("mapping receipt database binding is invalid")
    for key in ("config_sha256_before", "config_sha256_after", "backup_sha256"):
        _validate_sha256(str(receipt.get(key, "")), label=f"receipt {key}")
    if receipt.get("backup_sha256") != receipt.get("config_sha256_before"):
        raise PgbouncerMapError("mapping receipt backup binding is invalid")
    if (
        not isinstance(receipt.get("backup_size_bytes"), int)
        or receipt["backup_size_bytes"] < 1
        or not isinstance(receipt.get("original_uid"), int)
        or receipt["original_uid"] < 0
        or not isinstance(receipt.get("original_gid"), int)
        or receipt["original_gid"] < 0
        or not isinstance(receipt.get("original_mode"), int)
        or receipt["original_mode"] & 0o7022
        or receipt.get("newline") not in {"lf", "crlf"}
    ):
        raise PgbouncerMapError("mapping receipt file metadata is invalid")
    return source, target


def restore_original(
    config_path: Path,
    *,
    backup_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    receipt, receipt_bytes = _read_receipt(receipt_path)
    source, target = _validate_restore_receipt(
        receipt,
        config_path=config_path,
        backup_path=backup_path,
    )
    backup = _read_stable_file(
        backup_path,
        label="original config backup",
        private=True,
    )
    if (
        backup.sha256 != receipt["backup_sha256"]
        or len(backup.payload) != receipt["backup_size_bytes"]
    ):
        raise PgbouncerMapError("original config backup does not match receipt")
    backup_parsed = parse_config(backup.payload)
    if source not in backup_parsed.databases:
        raise PgbouncerMapError("original config backup does not map source database")
    expected_newline = b"\r\n" if receipt["newline"] == "crlf" else b"\n"
    if backup_parsed.newline != expected_newline:
        raise PgbouncerMapError("original config backup newline does not match receipt")

    current = _read_stable_file(config_path, label="PgBouncer config")
    if (current.uid, current.gid, current.mode) != (
        receipt["original_uid"],
        receipt["original_gid"],
        receipt["original_mode"],
    ):
        raise PgbouncerMapError("PgBouncer config metadata changed before restore")
    if current.sha256 == receipt["config_sha256_before"]:
        if current.payload != backup.payload:
            raise PgbouncerMapError("PgBouncer config hash collision during restore")
        changed = False
        restored = current
    else:
        if current.sha256 != receipt["config_sha256_after"]:
            raise PgbouncerMapError("PgBouncer config changed after prepare")
        verify_config(
            config_path,
            source_database=source,
            target_database=target,
            expected_sha256=receipt["config_sha256_after"],
        )
        restored = _atomic_replace(
            config_path,
            backup.payload,
            expected=current,
            uid=receipt["original_uid"],
            gid=receipt["original_gid"],
            mode=receipt["original_mode"],
        )
        changed = True
    if restored.payload != backup.payload or restored.sha256 != receipt["backup_sha256"]:
        raise PgbouncerMapError("original PgBouncer config restore verification failed")
    result = _base_result("restore-original", restored, parse_config(restored.payload))
    result.update(
        {
            "restored": True,
            "changed": changed,
            "backup_sha256": backup.sha256,
            "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        }
    )
    return result


def _pool_url_from_env(env_path: Path) -> str:
    snapshot = _read_stable_file(
        env_path,
        label="runtime environment",
        require_parent_owner=False,
    )
    try:
        lines = snapshot.payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PgbouncerMapError("runtime environment is not UTF-8") from exc
    values: list[str] = []
    pattern = re.compile(r"^\s*DATABASE_POOL_URL\s*=\s*(.*?)\s*$")
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = pattern.fullmatch(line)
        if not match:
            continue
        raw = match.group(1)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        if not raw:
            raise PgbouncerMapError("DATABASE_POOL_URL is empty")
        values.append(raw)
    if len(values) != 1:
        raise PgbouncerMapError(
            "runtime environment must contain one DATABASE_POOL_URL"
        )
    return values[0]


def _validated_pool_url(pool_url: str, *, expected_database: str) -> str:
    try:
        parsed = urlsplit(pool_url)
        port = parsed.port
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as exc:
        raise PgbouncerMapError("DATABASE_POOL_URL is invalid") from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != LOOPBACK_HOST
        or port != 6432
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.count("/") != 1
    ):
        raise PgbouncerMapError(
            "DATABASE_POOL_URL must target loopback PgBouncer on port 6432"
        )
    endpoint_overrides = {"host", "hostaddr", "port", "dbname", "database", "service"}
    if any(key.lower() in endpoint_overrides for key, _value in query_pairs):
        raise PgbouncerMapError("DATABASE_POOL_URL contains an endpoint override")
    database = unquote(parsed.path[1:])
    _validate_database(database, label="DATABASE_POOL_URL database")
    expected = _validate_database(expected_database, label="expected database")
    # The same credential envelope must prove both aliases before consumers
    # start.  Change only the validated path; never accept a caller-supplied URL
    # or query-string endpoint override.
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{quote(expected, safe='')}",
            parsed.query,
            "",
        )
    )


def _load_psycopg() -> Any:
    try:
        import psycopg
    except Exception as exc:  # pragma: no cover - depends on remote runtime
        raise PgbouncerMapError("psycopg is unavailable for PgBouncer probe") from exc
    return psycopg


def probe_pool(
    env_path: Path,
    *,
    expected_database: str,
) -> dict[str, Any]:
    expected = _validate_database(expected_database, label="expected database")
    pool_url = _validated_pool_url(
        _pool_url_from_env(env_path),
        expected_database=expected,
    )
    try:
        psycopg = _load_psycopg()
        with psycopg.connect(pool_url, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = 5000")
                cursor.execute("SELECT current_database()")
                row = cursor.fetchone()
    except PgbouncerMapError:
        raise
    except Exception as exc:
        raise PgbouncerMapError("PgBouncer connectivity probe failed") from exc
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 1
        or row[0] != expected
    ):
        raise PgbouncerMapError("PgBouncer returned an unexpected database identity")
    return {
        "schema_version": RECEIPT_SCHEMA,
        "operation": "probe",
        "connected": True,
        "database_name": expected,
        "mapping_endpoint": "127.0.0.1:6432",
        "credentials_included": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--config", required=True)
    inspect.add_argument("--source-db")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--config", required=True)
    prepare.add_argument("--source-db", required=True)
    prepare.add_argument("--target-db", required=True)
    prepare.add_argument("--backup", required=True)
    prepare.add_argument("--receipt", required=True)
    prepare.add_argument("--expected-sha256", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--config", required=True)
    verify.add_argument("--source-db", required=True)
    verify.add_argument("--target-db", required=True)
    verify.add_argument("--expected-sha256")

    restore = subparsers.add_parser("restore-original")
    restore.add_argument("--config", required=True)
    restore.add_argument("--backup", required=True)
    restore.add_argument("--receipt", required=True)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--env-file", required=True)
    probe.add_argument("--expected-db", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_config(
                Path(args.config),
                expected_source=args.source_db,
            )
        elif args.command == "prepare":
            result = prepare_config(
                Path(args.config),
                source_database=args.source_db,
                target_database=args.target_db,
                backup_path=Path(args.backup),
                receipt_path=Path(args.receipt),
                expected_sha256=args.expected_sha256,
            )
        elif args.command == "verify":
            result = verify_config(
                Path(args.config),
                source_database=args.source_db,
                target_database=args.target_db,
                expected_sha256=args.expected_sha256,
            )
        elif args.command == "restore-original":
            result = restore_original(
                Path(args.config),
                backup_path=Path(args.backup),
                receipt_path=Path(args.receipt),
            )
        else:
            result = probe_pool(
                Path(args.env_file),
                expected_database=args.expected_db,
            )
    except PgbouncerMapError as exc:
        sys.stderr.write(f"pgbouncer release map error: {exc}\n")
        return 2
    except OSError:
        sys.stderr.write("pgbouncer release map error: filesystem operation failed\n")
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
