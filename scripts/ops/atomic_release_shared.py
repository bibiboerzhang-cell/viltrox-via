"""Shared contracts and path validation for atomic V-KPI releases."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

if __package__:
    from .atomic_release_units import LayoutError
else:
    from atomic_release_units import LayoutError


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ENV_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
STAGING_CLONE_RE = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")
ROLLBACK_METADATA_SCHEMA = 3
RELEASE_CONTROLLER_NAME = ".release-controller"
ROLLBACK_METADATA_DIGEST_NAME = "metadata.sha256"
CONTROLLER_DIRECTORY_MODE = 0o700
CONTROLLER_FILE_MODE = 0o600
REQUIRED_RELEASE_PATHS = (
    "backend",
    "frontend/dist",
    "migrations",
    "scripts/start_admin.sh",
    "scripts/ops/systemd/viltrox-2.0-test.service",
    "scripts/ops/systemd/vkpi-worker-interactive.service",
    "scripts/ops/systemd/vkpi-worker-bulk@.service",
    "scripts/ops/systemd/vkpi-redis-worker.service",
    "BUILD_GIT_SHA",
    "BUILD_GIT_BRANCH",
    "BUILD_TIME",
)
SHARED_DIRECTORIES = ("runtime", "uploads", "frames", "creator_profiles", "backups")
SHARED_NESTED_DIRECTORIES = ("runtime/job-results",)
WORKER_WRITABLE_DIRECTORIES = ("uploads",)
WORKER_READONLY_DIRECTORIES = ("runtime", "frames", "creator_profiles")
SHARED_REQUIRED = (".env", ".venv")
SHARED_OPTIONAL = (".env.production", "submissions.db")
RELEASE_SHARED_ALIASES = {
    *SHARED_REQUIRED,
    *SHARED_DIRECTORIES,
    *SHARED_OPTIONAL,
}
MAX_ROLLBACK_FILE_BYTES = 1024 * 1024
POSTGRES_MIGRATION_EXCLUDE = frozenset(
    {
        "001_verification.sql",
        "002_intelligence.sql",
        "004_viltrox_matrix.sql",
        "010_party_layer_rollback.sql",
    }
)
MIGRATION_NAME_RE = re.compile(r"^[0-9]{3}[a-z]?_[A-Za-z0-9_.-]+\.sql$")
FORWARD_COMPATIBILITY_POLICY_ID = "vkpi-additive-nullable-defaultless-v1"
FORWARD_COMPATIBILITY_POLICY = {
    "305_vkpi_kol_pool_language_inferred.sql": {
        "table": "vkpi_kol_pool",
        "columns": (
            ("language_inferred", "TEXT"),
            ("language_inferred_confidence", "TEXT"),
            ("language_inferred_source", "TEXT"),
            ("language_inferred_sample_n", "INTEGER"),
            ("language_inferred_at", "TIMESTAMPTZ"),
            ("language_inferred_method", "TEXT"),
        ),
        "indexes": (
            (
                "idx_vkpi_kol_pool_language_inferred",
                "language_inferred",
            ),
        ),
    },
    "306_vkpi_product_persona_term_performance.sql": {
        "table": "vkpi_product_persona",
        "columns": (("term_performance_json", "JSONB"),),
        "indexes": (),
    },
    "307_users_token_version.sql": {
        "table": "users",
        "columns": (("token_version", "INTEGER"),),
        "indexes": (),
    },
}
_ADD_COLUMN_RE = re.compile(
    r"^ALTER\s+TABLE\s+([a-zA-Z0-9_]+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
    r"([a-zA-Z0-9_]+)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT_COLUMN_RE = re.compile(
    r"^COMMENT\s+ON\s+COLUMN\s+([a-zA-Z0-9_]+)\.([a-zA-Z0-9_]+)\s+"
    r"IS\s+'(?:[^']|'')*'$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_INDEX_RE = re.compile(
    r"^CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z0-9_]+)\s+"
    r"ON\s+([a-zA-Z0-9_]+)\s*\(\s*([a-zA-Z0-9_]+)\s*\)$",
    re.IGNORECASE | re.DOTALL,
)


def runtime_migration_manifest(migrations_dir: Path) -> tuple[str, ...]:
    """Return the exact forward sequence used by the Postgres startup runner."""

    names = tuple(
        sorted(
            path.name
            for path in migrations_dir.glob("*.sql")
            if not path.name.endswith("_down.sql")
            and path.name not in POSTGRES_MIGRATION_EXCLUDE
        )
    )
    if not names or len(names) != len(set(names)) or any(
        MIGRATION_NAME_RE.fullmatch(name) is None for name in names
    ):
        raise LayoutError("runtime migration manifest is empty or invalid")
    return names


def pending_runtime_migrations(
    manifest: tuple[str, ...],
    applied: tuple[str, ...],
) -> tuple[str, ...]:
    """Find every missing migration, including holes below the applied maximum."""

    if not applied or len(applied) != len(set(applied)) or any(
        MIGRATION_NAME_RE.fullmatch(name) is None for name in applied
    ):
        raise LayoutError("applied migration set is empty or invalid")
    manifest_set = set(manifest)
    unexpected = sorted(set(applied) - manifest_set - POSTGRES_MIGRATION_EXCLUDE)
    if unexpected:
        raise LayoutError(
            "applied migration set contains versions outside the runtime manifest: "
            + ",".join(unexpected)
        )
    return tuple(name for name in manifest if name not in set(applied))


def _forward_sql_statements(source: str) -> tuple[str, ...]:
    """Split reviewed DDL while ignoring line comments and quoted semicolons."""

    statements: list[str] = []
    current: list[str] = []
    index = 0
    quoted = False
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if not quoted and char == "-" and next_char == "-":
            lf = source.find("\n", index + 2)
            cr = source.find("\r", index + 2)
            line_ends = [position for position in (lf, cr) if position >= 0]
            if not line_ends:
                index = len(source)
            else:
                line_end = min(line_ends)
                index = line_end + 1
                if source[line_end] == "\r" and index < len(source) and source[index] == "\n":
                    index += 1
            current.append(" ")
            continue
        if char == "'":
            current.append(char)
            if quoted and next_char == "'":
                current.append(next_char)
                index += 2
                continue
            quoted = not quoted
            index += 1
            continue
        if char == ";" and not quoted:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if quoted:
        raise LayoutError("forward-compatible migration has an unterminated string literal")
    trailing = "".join(current).strip()
    if trailing:
        raise LayoutError("forward-compatible migration must terminate every statement")
    return tuple(statements)


def _validate_forward_migration(
    name: str,
    *,
    migrations_dir: Path,
) -> dict[str, object]:
    policy = FORWARD_COMPATIBILITY_POLICY.get(name)
    if policy is None:
        raise LayoutError(f"forward-compatible migration is not reviewed by policy: {name}")
    path = migrations_dir / name
    payload, _info = _read_regular_single_link(
        path,
        label=f"forward-compatible migration {name}",
        max_bytes=MAX_ROLLBACK_FILE_BYTES,
    )
    try:
        source = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LayoutError(f"forward-compatible migration is not UTF-8: {name}") from exc

    table = str(policy["table"])
    expected_columns = tuple(policy["columns"])
    expected_indexes = tuple(policy["indexes"])
    columns: list[tuple[str, str]] = []
    comments: list[str] = []
    indexes: list[tuple[str, str]] = []
    for statement in _forward_sql_statements(source):
        if match := _ADD_COLUMN_RE.fullmatch(statement):
            observed_table, column, definition = match.groups()
            normalized_definition = " ".join(definition.upper().split())
            expected_type = dict(expected_columns).get(column)
            if (
                observed_table != table
                or expected_type is None
                or normalized_definition not in {expected_type, f"{expected_type} NULL"}
            ):
                raise LayoutError(
                    f"forward-compatible migration changes an unreviewed column shape: {name}"
                )
            columns.append((column, expected_type))
            continue
        if match := _COMMENT_COLUMN_RE.fullmatch(statement):
            observed_table, column = match.groups()
            if observed_table != table or column not in dict(expected_columns):
                raise LayoutError(
                    f"forward-compatible migration comments an unreviewed column: {name}"
                )
            comments.append(column)
            continue
        if match := _CREATE_INDEX_RE.fullmatch(statement):
            index_name, observed_table, column = match.groups()
            if observed_table != table:
                raise LayoutError(
                    f"forward-compatible migration indexes an unreviewed table: {name}"
                )
            indexes.append((index_name, column))
            continue
        raise LayoutError(
            f"forward-compatible migration contains non-additive SQL: {name}"
        )

    expected_column_names = tuple(column for column, _type in expected_columns)
    if tuple(columns) != expected_columns or tuple(comments) != expected_column_names:
        raise LayoutError(
            f"forward-compatible migration column set differs from policy: {name}"
        )
    if tuple(indexes) != expected_indexes:
        raise LayoutError(
            f"forward-compatible migration index set differs from policy: {name}"
        )
    return {
        "name": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "table": table,
        "columns": [column for column, _type in expected_columns],
    }


def _forward_compatibility_evidence(
    declaration: str,
    *,
    migrations_dir: Path | None = None,
) -> dict[str, object] | None:
    if not declaration:
        return None
    names = tuple(declaration.split(","))
    if any(not name or name.strip() != name for name in names) or len(set(names)) != len(names):
        raise LayoutError("forward-compatibility declaration must be ordered and unique")
    directory = migrations_dir or Path(__file__).resolve().parents[2] / "migrations"
    migrations = [
        _validate_forward_migration(name, migrations_dir=directory) for name in names
    ]
    return {
        "policy_id": FORWARD_COMPATIBILITY_POLICY_ID,
        "guarantees": [
            "additive_columns_only",
            "nullable_columns",
            "defaultless_columns",
            "no_row_writes",
        ],
        "migrations": migrations,
    }


def _read_regular_single_link(
    path: Path,
    *,
    label: str,
    max_bytes: int | None = None,
) -> tuple[bytes, os.stat_result]:
    """Read a rollback input through a no-follow descriptor."""

    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise LayoutError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise LayoutError(f"{label} must be a regular single-link file: {path}")
    if max_bytes is not None and initial.st_size > max_bytes:
        raise LayoutError(f"{label} exceeds the one MiB limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise LayoutError(f"{label} must be a regular single-link file: {path}")
        if max_bytes is not None and info.st_size > max_bytes:
            raise LayoutError(f"{label} exceeds the one MiB limit")
        chunks: list[bytes] = []
        byte_count = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            byte_count += len(chunk)
            if max_bytes is not None and byte_count > max_bytes:
                raise LayoutError(f"{label} exceeds the one MiB limit")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks), info


def _secure_directory(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise LayoutError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise LayoutError(f"{label} must be a real directory: {path}")
    if info.st_uid != os.geteuid():
        raise LayoutError(f"{label} owner is not the release controller: {path}")
    if stat.S_IMODE(info.st_mode) != CONTROLLER_DIRECTORY_MODE:
        raise LayoutError(f"{label} mode must be 0700: {path}")
    return path


def _create_secure_directory(path: Path, *, label: str) -> Path:
    if path.exists() or path.is_symlink():
        raise LayoutError(f"{label} already exists: {path}")
    path.mkdir(mode=CONTROLLER_DIRECTORY_MODE)
    path.chmod(CONTROLLER_DIRECTORY_MODE)
    return _secure_directory(path, label=label)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new_capture(path: Path, payload: bytes) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, CONTROLLER_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), CONTROLLER_FILE_MODE)
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _capture_metadata(payload: bytes, info: os.stat_result) -> dict[str, int | str]:
    return {
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _validated_capture_metadata(
    raw: object,
    *,
    label: str,
) -> tuple[int, int, int, str]:
    if not isinstance(raw, dict):
        raise LayoutError(f"{label} metadata is missing or invalid")
    uid = raw.get("uid")
    gid = raw.get("gid")
    mode = raw.get("mode")
    digest = raw.get("sha256")
    if (
        isinstance(uid, bool)
        or not isinstance(uid, int)
        or uid < 0
        or isinstance(gid, bool)
        or not isinstance(gid, int)
        or gid < 0
        or isinstance(mode, bool)
        or not isinstance(mode, int)
        or not 0 <= mode <= 0o7777
        or not isinstance(digest, str)
        or not ENV_FINGERPRINT_RE.fullmatch(digest)
    ):
        raise LayoutError(f"{label} metadata is missing or invalid")
    return uid, gid, mode, digest


def _read_secure_controller_file(path: Path, *, label: str) -> bytes:
    payload, info = _read_regular_single_link(path, label=label)
    if info.st_uid != os.geteuid():
        raise LayoutError(f"{label} owner is not the release controller: {path}")
    if stat.S_IMODE(info.st_mode) != CONTROLLER_FILE_MODE:
        raise LayoutError(f"{label} mode must be 0600: {path}")
    return payload


def _validated_capture_restore(
    path: Path,
    raw_metadata: object,
    *,
    label: str,
) -> tuple[bytes, int, int, int]:
    uid, gid, mode, expected_digest = _validated_capture_metadata(
        raw_metadata,
        label=label,
    )
    payload = _read_secure_controller_file(path, label=label)
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise LayoutError(f"{label} hash mismatch")
    return payload, uid, gid, mode


def _restore_file_atomically(
    target: Path,
    *,
    payload: bytes,
    uid: int,
    gid: int,
    mode: int,
    label: str,
) -> None:
    if target.exists() or target.is_symlink():
        current = target.lstat()
        if stat.S_ISLNK(current.st_mode):
            raise LayoutError(f"refusing symlink {label} target: {target}")
        if not stat.S_ISREG(current.st_mode):
            raise LayoutError(f"{label} target must be a regular file: {target}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.restore-",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), uid, gid)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        replaced = True
        os.chown(target, uid, gid, follow_symlinks=False)
        os.chmod(target, mode, follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        installed_descriptor = os.open(target, flags)
        try:
            installed = os.fstat(installed_descriptor)
            installed_digest = hashlib.sha256()
            while True:
                chunk = os.read(installed_descriptor, 1024 * 1024)
                if not chunk:
                    break
                installed_digest.update(chunk)
            if (
                not stat.S_ISREG(installed.st_mode)
                or installed.st_uid != uid
                or installed.st_gid != gid
                or stat.S_IMODE(installed.st_mode) != mode
                or installed.st_size != len(payload)
                or installed_digest.digest() != hashlib.sha256(payload).digest()
            ):
                raise LayoutError(f"restored {label} content or metadata mismatch")
            os.fsync(installed_descriptor)
        finally:
            os.close(installed_descriptor)
        _fsync_directory(target.parent)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def _rollback_file_paths(values: list[str]) -> list[Path]:
    parsed: list[Path] = []
    for raw in values:
        candidate = Path(str(raw or ""))
        if not candidate.is_absolute() or candidate.name in {"", ".", ".."}:
            raise LayoutError("external rollback file path must be absolute and normalized")
        parent = candidate.parent.resolve(strict=False)
        if parent.exists() and not stat.S_ISDIR(parent.lstat().st_mode):
            raise LayoutError("external rollback file parent must be a real directory")
        normalized = parent / candidate.name
        if candidate != normalized:
            raise LayoutError("external rollback file path must be absolute and normalized")
        parsed.append(normalized)
    if len(parsed) != len(set(parsed)):
        raise LayoutError("external rollback file paths must be unique")
    return parsed


def _capture_rollback_file_sources(
    values: list[str],
) -> dict[Path, tuple[bytes, os.stat_result] | None]:
    sources: dict[Path, tuple[bytes, os.stat_result] | None] = {}
    for path in _rollback_file_paths(values):
        if path.exists() or path.is_symlink():
            payload, info = _read_regular_single_link(
                path,
                label="external rollback file",
                max_bytes=MAX_ROLLBACK_FILE_BYTES,
            )
            sources[path] = (payload, info)
        else:
            sources[path] = None
    return sources


def _rollback_capture_name(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def _write_rollback_file_captures(
    rollback_dir: Path,
    sources: dict[Path, tuple[bytes, os.stat_result] | None],
) -> dict[str, dict[str, int | str | bool]]:
    if not sources:
        return {}
    capture_dir = _create_secure_directory(
        rollback_dir / "rollback-files",
        label="external rollback file capture directory",
    )
    result: dict[str, dict[str, int | str | bool]] = {}
    for path, source in sources.items():
        if source is None:
            result[str(path)] = {"present": False}
            continue
        payload, info = source
        capture_name = _rollback_capture_name(path)
        _write_new_capture(capture_dir / capture_name, payload)
        result[str(path)] = {
            "present": True,
            "capture": capture_name,
            "bytes": len(payload),
            **_capture_metadata(payload, info),
        }
    _fsync_directory(capture_dir)
    return result


def _validated_rollback_file_restores(
    rollback_dir: Path,
    metadata: dict[str, object],
) -> dict[Path, tuple[bytes, int, int, int] | None]:
    required = metadata.get("rollback_files_required", False)
    if type(required) is not bool:
        raise LayoutError("external rollback file requirement is invalid")
    raw_files = metadata.get("rollback_files")
    if raw_files is None:
        if required:
            raise LayoutError("required external rollback file metadata is missing")
        return {}
    if not isinstance(raw_files, dict) or (required and not raw_files):
        raise LayoutError("external rollback file metadata is missing or invalid")
    paths = _rollback_file_paths(list(raw_files))
    if not paths:
        return {}
    capture_dir = _secure_directory(
        rollback_dir / "rollback-files",
        label="external rollback file capture directory",
    )
    restores: dict[Path, tuple[bytes, int, int, int] | None] = {}
    expected_captures: set[str] = set()
    for path in paths:
        entry = raw_files.get(str(path))
        if not isinstance(entry, dict) or type(entry.get("present")) is not bool:
            raise LayoutError("external rollback file metadata is missing or invalid")
        if entry["present"] is False:
            if set(entry) != {"present"}:
                raise LayoutError("absent external rollback file metadata is invalid")
            restores[path] = None
            continue
        expected_keys = {"present", "capture", "bytes", "uid", "gid", "mode", "sha256"}
        capture_name = _rollback_capture_name(path)
        byte_count = entry.get("bytes")
        if (
            set(entry) != expected_keys
            or entry.get("capture") != capture_name
            or isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or not 0 <= byte_count <= MAX_ROLLBACK_FILE_BYTES
        ):
            raise LayoutError("present external rollback file metadata is invalid")
        restore = _validated_capture_restore(
            capture_dir / capture_name,
            entry,
            label="external rollback file capture",
        )
        if len(restore[0]) != byte_count:
            raise LayoutError("external rollback file byte count mismatch")
        restores[path] = restore
        expected_captures.add(capture_name)
    if {path.name for path in capture_dir.iterdir()} != expected_captures:
        raise LayoutError("external rollback file capture set is inconsistent")
    return restores


def _validate_rollback_file_targets(
    restores: dict[Path, tuple[bytes, int, int, int] | None],
) -> None:
    for path in restores:
        current = path.lstat() if path.exists() or path.is_symlink() else None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise LayoutError(f"external rollback file target is unsafe: {path}")


def _restore_rollback_files(
    restores: dict[Path, tuple[bytes, int, int, int] | None],
) -> None:
    for path, restore in restores.items():
        if restore is not None:
            payload, uid, gid, mode = restore
            _restore_file_atomically(
                path,
                payload=payload,
                uid=uid,
                gid=gid,
                mode=mode,
                label="external rollback file",
            )
            continue
        if path.exists() or path.is_symlink():
            if not stat.S_ISREG(path.lstat().st_mode):
                raise LayoutError(f"external rollback file target is unsafe: {path}")
            path.unlink()
            _fsync_directory(path.parent)
        if path.exists() or path.is_symlink():
            raise LayoutError(f"absent external rollback file was not restored: {path}")


def _release_clone_name(release_id: str) -> str:
    release_id = _id(release_id)
    return "viltrox2_test_release_" + hashlib.sha256(
        release_id.encode("utf-8")
    ).hexdigest()[:20]


def _database_release_metadata(
    *,
    strategy: str,
    source_database: str,
    target_database: str,
    env_fingerprint_before: str,
    pending_migrations: str,
    compatibility_declaration: str,
    database_owner_release_id: str = "",
) -> dict[str, object]:
    if strategy == "in-place":
        if (
            source_database
            or target_database
            or env_fingerprint_before
            or database_owner_release_id
        ):
            raise LayoutError("in-place releases must not declare staging clone metadata")
        if pending_migrations != compatibility_declaration:
            raise LayoutError(
                "in-place migrations require an exact forward-compatibility declaration"
            )
        metadata: dict[str, object] = {
            "database_strategy": "in-place",
            "source_database": None,
            "target_database": None,
            "env_fingerprint_before": None,
            "database_owner_release_id": None,
        }
        evidence = _forward_compatibility_evidence(compatibility_declaration)
        if evidence is not None:
            metadata["forward_compatibility_evidence"] = evidence
        return metadata
    if strategy == "reuse-active-clone":
        if source_database:
            raise LayoutError("clone-reuse releases must not declare a new source database")
        if not database_owner_release_id:
            raise LayoutError("clone-reuse releases require the database owner release id")
        if not STAGING_CLONE_RE.fullmatch(target_database):
            raise LayoutError("clone-reuse target database is not release-specific")
        if _release_clone_name(database_owner_release_id) != target_database:
            raise LayoutError("clone-reuse owner release does not own the target database")
        if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
            raise LayoutError("clone-reuse env fingerprint must be a SHA-256 digest")
        if pending_migrations != compatibility_declaration:
            raise LayoutError(
                "clone-reuse migrations require an exact forward-compatibility declaration"
            )
        metadata: dict[str, object] = {
            "database_strategy": "reuse-active-clone",
            "source_database": None,
            "target_database": target_database,
            "env_fingerprint_before": env_fingerprint_before,
            "database_owner_release_id": database_owner_release_id,
        }
        evidence = _forward_compatibility_evidence(compatibility_declaration)
        if evidence is not None:
            metadata["forward_compatibility_evidence"] = evidence
        return metadata
    if strategy != "staging-clone":
        raise LayoutError(f"unsupported database release strategy: {strategy!r}")
    if database_owner_release_id:
        raise LayoutError("new staging clones must not inherit another database owner")
    if source_database != "viltrox2_test" and not STAGING_CLONE_RE.fullmatch(
        source_database
    ):
        raise LayoutError(
            "staging clone source must be the legacy base or a proven prior release clone"
        )
    if not STAGING_CLONE_RE.fullmatch(target_database):
        raise LayoutError("staging clone target database is not release-specific")
    if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
        raise LayoutError("staging clone env fingerprint must be a SHA-256 digest")
    if not pending_migrations:
        raise LayoutError("staging clone strategy requires pending migrations")
    if compatibility_declaration:
        raise LayoutError(
            "staging clone strategy must not claim in-place forward compatibility"
        )
    return {
        "database_strategy": "staging-clone",
        "source_database": source_database,
        "target_database": target_database,
        "env_fingerprint_before": env_fingerprint_before,
        "database_owner_release_id": None,
    }


def _id(value: str) -> str:
    if not RELEASE_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise LayoutError(f"invalid release id: {value!r}")
    return value


def _inside_releases(root: Path, target: Path) -> Path:
    releases = (root / "releases").resolve()
    resolved = target.resolve(strict=True)
    if resolved == releases or releases not in resolved.parents:
        raise LayoutError(f"release target escapes {releases}: {resolved}")
    if not resolved.is_dir():
        raise LayoutError(f"release target is not a directory: {resolved}")
    return resolved
