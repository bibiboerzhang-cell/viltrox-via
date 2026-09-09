"""Read-only evidence for two local historical extensions, never a release waiver.

Pinned dumps prove historical ledger/DDL observations, not recovered migration SQL,
restorability, or today's database identity/schema. No database client is imported.
Only a future independently reviewed, current-database binding could authorize
adoption; this collector deliberately cannot produce a migration exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Sequence


HISTORICAL_KEYS = (
    "081_vkpi_repair_center_persistence.sql",
    "083_vkpi_repair_future_write_mapping.sql",
)
HISTORICAL_TABLES = tuple(sorted((
    "vkpi_repair_proposals", "vkpi_repair_preflight_checks",
    "vkpi_repair_implementation_packages", "vkpi_repair_handoff_log",
    "vkpi_repair_future_write_mappings",
)))
_APPLIED_AT = (
    "2026-05-24 21:11:20.523141+08", "2026-05-24 23:01:44.423547+08",
)
_MIGRATION_NAME = re.compile(r"^[0-9]{3}[a-z]?_[A-Za-z0-9_.-]+\.sql$")
_TOC = re.compile(
    r"^-- (?:Data for )?Name: (.*); Type: ([A-Z ]+); Schema: ([^;]+); Owner: [^;]+$"
)
_DDL_TYPES = frozenset({
    "TABLE", "SEQUENCE", "SEQUENCE OWNED BY", "DEFAULT", "CONSTRAINT",
    "FK CONSTRAINT", "INDEX",
})
_MAX_BACKUP_BYTES = 64 * 1024 * 1024


class HistoricalEvidenceError(ValueError):
    """Evidence failed validation; messages never include dump business content."""


@dataclass(frozen=True)
class _BackupSpec:
    relative_path: str
    sha256: str
    size_bytes: int
    key_count: int
    tables: tuple[str, ...]


_BACKUPS = (
    _BackupSpec(
        "runtime/db-backups/local-vkpi-pre-repair-proposal-first-write-20260524T132653Z.sql",
        "248aaa8376a32540a37dc25118a96b3416feae2e3d4bdd492f25608869c0cefa",
        55_845_091, 1,
        tuple(name for name in HISTORICAL_TABLES if name != "vkpi_repair_future_write_mappings"),
    ),
    _BackupSpec(
        "runtime/db-backups/local-vkpi-pre-repair-future-write-mapping-first-write-20260524T150158Z.sql",
        "1bd6341703b17f2a70f1b8b50a190ed290158f394f2d1fa4a72aad1f94dd40ed",
        55_918_851, 2, HISTORICAL_TABLES,
    ),
)


@dataclass(frozen=True)
class HistoricalLedgerEntry:
    version_key: str
    applied_at: str


@dataclass(frozen=True)
class SchemaObjectEvidence:
    name: str
    object_type: str
    sha256: str


@dataclass(frozen=True)
class BackupObservation:
    relative_path: str
    sha256: str
    size_bytes: int
    ledger: tuple[HistoricalLedgerEntry, ...]
    tables: tuple[str, ...]
    schema_objects: tuple[SchemaObjectEvidence, ...]
    schema_sha256: str


@dataclass(frozen=True)
class HistoricalBaselineReview:
    backups: tuple[BackupObservation, ...]
    canonical_manifest_sha256: str
    schema_version: int = field(default=1, init=False)
    status: str = field(default="historical_backup_verified_current_binding_missing", init=False)
    database_name_scope: str = field(default="viltrox2", init=False)
    evidence_semantics: str = field(default="historical_ledger_and_dump_ddl_only", init=False)
    schema_digest_format: str = field(default="pgdump_toc_body_sha256_v1", init=False)
    current_database_verified: bool = field(default=False, init=False)
    current_schema_verified: bool = field(default=False, init=False)
    original_migration_source_recovered: bool = field(default=False, init=False)
    backup_restore_verified: bool = field(default=False, init=False)
    migration_exclusion_authorized: bool = field(default=False, init=False)
    authorized_exclusions: tuple[str, ...] = field(default=(), init=False)


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_pinned_backup(root: Path, spec: _BackupSpec) -> str:
    path = root / spec.relative_path
    if path.resolve() != path or not path.is_relative_to(root):
        raise HistoricalEvidenceError("backup_path_alias_or_escape")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise HistoricalEvidenceError("backup_must_be_regular_single_link")
            if before.st_size != spec.size_bytes or before.st_size > _MAX_BACKUP_BYTES:
                raise HistoricalEvidenceError("backup_size_mismatch")
            payload = handle.read(_MAX_BACKUP_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise HistoricalEvidenceError("backup_unreadable") from exc
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise HistoricalEvidenceError("backup_changed_during_read")
    if hashlib.sha256(payload).hexdigest() != spec.sha256:
        raise HistoricalEvidenceError("backup_sha256_mismatch")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoricalEvidenceError("backup_encoding_invalid") from exc


def _schema_object(name: str, kind: str, schema: str, body: list[str]) -> SchemaObjectEvidence | None:
    if "vkpi_repair_" not in name:
        return None
    if schema != "public":
        raise HistoricalEvidenceError("historical_schema_unexpected")
    if kind in {"TABLE DATA", "SEQUENCE SET"}:
        return None  # Business rows and sequence values are not schema evidence.
    if kind not in _DDL_TYPES:
        raise HistoricalEvidenceError("historical_object_type_unreviewed")
    first = name.split(" ")[0]
    source = "\n".join(body).strip()
    if kind == "INDEX":
        target = re.search(r"\bON public\.([a-z_]+)\b", source)
        valid = target is not None and target.group(1) in HISTORICAL_TABLES
    elif kind.startswith("SEQUENCE"):
        valid = first in {table + "_id_seq" for table in HISTORICAL_TABLES}
    else:
        valid = first in HISTORICAL_TABLES
    if not valid or not source:
        raise HistoricalEvidenceError("historical_object_unknown_or_empty")
    return SchemaObjectEvidence(name, kind, hashlib.sha256(source.encode()).hexdigest())


def _extract_observation(source: str, spec: _BackupSpec, canonical: set[str]) -> BackupObservation:
    if not source.startswith("--\n-- PostgreSQL database dump\n") or (
        "-- PostgreSQL database dump complete" not in source[-512:]
    ):
        raise HistoricalEvidenceError("backup_dump_boundary_missing")
    objects: list[SchemaObjectEvidence] = []
    ledger: list[HistoricalLedgerEntry] = []
    seen_keys: set[str] = set()
    section: tuple[str, str, str] | None = None
    body: list[str] = []
    ledger_sections = 0
    in_ledger = False
    for line in source.splitlines():
        header = _TOC.fullmatch(line)
        if header:
            if section:
                item = _schema_object(*section, body)
                if item:
                    objects.append(item)
            section = header.group(1, 2, 3)
            body = []
        elif line == "COPY public.schema_migrations (version_key, applied_at) FROM stdin;":
            ledger_sections += 1
            in_ledger = True
        elif in_ledger:
            if line == "\\.":
                in_ledger = False
                continue
            columns = line.split("\t")
            if len(columns) != 2 or columns[0] in seen_keys:
                raise HistoricalEvidenceError("historical_ledger_malformed_or_duplicate")
            key, applied_at = columns
            seen_keys.add(key)
            if key in HISTORICAL_KEYS:
                ledger.append(HistoricalLedgerEntry(key, applied_at))
            elif key not in canonical:
                raise HistoricalEvidenceError("historical_ledger_unknown_key")
        elif section and section[1] in _DDL_TYPES and "vkpi_repair_" in section[0]:
            body.append(line)
    if section:
        item = _schema_object(*section, body)
        if item:
            objects.append(item)
    expected = tuple(HistoricalLedgerEntry(*row) for row in zip(
        HISTORICAL_KEYS[:spec.key_count], _APPLIED_AT[:spec.key_count],
    ))
    if ledger_sections != 1 or in_ledger or tuple(sorted(ledger, key=lambda row: row.version_key)) != expected:
        raise HistoricalEvidenceError("historical_ledger_evidence_mismatch")
    identities = [(item.name, item.object_type) for item in objects]
    tables = tuple(sorted(item.name for item in objects if item.object_type == "TABLE"))
    if len(identities) != len(set(identities)) or tables != spec.tables:
        raise HistoricalEvidenceError("historical_schema_set_mismatch")
    objects.sort(key=lambda item: (item.name, item.object_type))
    schema_digest = _digest([(item.name, item.object_type, item.sha256) for item in objects])
    return BackupObservation(spec.relative_path, spec.sha256, spec.size_bytes,
                             expected, tables, tuple(objects), schema_digest)


def collect_historical_baseline(*, repo_root: Path, canonical_manifest: Sequence[str],
                                target_database: str = "viltrox2") -> HistoricalBaselineReview:
    """Collect pinned local historical facts; database name is a scope, not identity proof."""
    if target_database != "viltrox2":
        raise HistoricalEvidenceError("historical_evidence_local_scope_only")
    names = tuple(canonical_manifest)
    if not names or len(names) != len(set(names)) or any(
        not isinstance(name, str) or _MIGRATION_NAME.fullmatch(name) is None
        or name.endswith("_down.sql") for name in names
    ):
        raise HistoricalEvidenceError("canonical_manifest_invalid")
    if set(names) & set(HISTORICAL_KEYS):
        raise HistoricalEvidenceError("historical_key_must_not_be_canonical")
    root = repo_root.resolve(strict=True)
    observations = tuple(_extract_observation(_read_pinned_backup(root, spec), spec, set(names))
                         for spec in _BACKUPS)
    older_tables = {item.name: item.sha256 for item in observations[0].schema_objects
                    if item.object_type == "TABLE"}
    newer_tables = {item.name: item.sha256 for item in observations[1].schema_objects
                    if item.object_type == "TABLE"}
    if any(newer_tables.get(name) != digest for name, digest in older_tables.items()):
        raise HistoricalEvidenceError("historical_table_definition_drift")
    return HistoricalBaselineReview(observations, _digest(sorted(names)))


def require_adoptable_review(_review: object) -> None:
    """Even edited JSON or a manufactured dataclass cannot turn this into an exclusion."""
    raise HistoricalEvidenceError("current_database_identity_and_schema_binding_missing")
