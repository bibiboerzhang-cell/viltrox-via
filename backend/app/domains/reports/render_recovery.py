"""Crash-recovery protocol for locally rendered report artifacts.

The generator owns publication.  This module deliberately does not own a
scheduler or write to the database: it creates/validates the protocol envelope,
publishes the final completion marker, and exposes a read-only reconciliation
decision.  A future worker may implement the transaction boundary described by
``reconciliation_lock_plan`` only after PostgreSQL/SQLite integration tests.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.domains.reports import pdf_renderer


RENDER_METADATA_KEY = "_render_v1"
RENDER_SCHEMA_VERSION = "report-render.v1"
COMPLETION_SCHEMA_VERSION = "report-completion.v1"
DEFAULT_LEASE_SECONDS = 15 * 60
MAX_MANIFEST_BYTES = 256 * 1024

_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ATTEMPT_RE = re.compile(r"^[a-f0-9]{32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ReportReadyCasConflict(RuntimeError):
    """The terminal ready transition did not own the rendering attempt."""


def terminal_ready_cas_sql(dialect: str = "portable") -> str:
    """Return the SQLite/PostgreSQL terminal CAS for the v026 TEXT column.

    ``vkpi_report_runs.metadata_json`` is declared ``TEXT`` in both the
    PostgreSQL migration and the SQLite compatibility schema.  Comparing the
    exact string inserted at attempt creation is therefore portable and also
    binds the embedded attempt id.  It intentionally does *not* use JSON/JSONB
    operators or casts.  Reordering/rewriting metadata makes the CAS fail
    closed; the reconciler then requires review.
    """
    clean = str(dialect or "portable").strip().lower()
    if clean not in {"portable", "postgres", "postgresql", "sqlite", "sqlite3"}:
        raise ValueError("unsupported report CAS dialect")
    return """
        UPDATE vkpi_report_runs
        SET status='ready', metadata_json=?, error_message=''
        WHERE id=? AND status='rendering' AND metadata_json=?
    """


@dataclass(frozen=True)
class CompletionValidation:
    """Side-effect-free validation result for one manifest document."""

    status: str
    reasons: tuple[str, ...]
    manifest: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return self.status == "valid"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid": self.valid,
            "reasons": list(self.reasons),
            "manifest": self.manifest,
        }


@dataclass(frozen=True)
class CompletionInspection:
    """Read-only storage/DB-row comparison for a completion bundle."""

    status: str
    reasons: tuple[str, ...]
    storage_valid: bool
    db_rows_match: bool
    manifest: dict[str, Any] | None = None
    db_row_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "storage_valid": self.storage_valid,
            "db_rows_match": self.db_rows_match,
            "db_row_reasons": list(self.db_row_reasons),
            "manifest": self.manifest,
        }


@dataclass(frozen=True)
class ReconciliationLockPlan:
    """Unexecuted SQL boundary for a future transactional reconciler."""

    dialect: str
    begin_sql: str | None
    select_sql: str
    terminal_cas_required: bool = True
    tested_against_live_database: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dialect": self.dialect,
            "begin_sql": self.begin_sql,
            "select_sql": self.select_sql,
            "terminal_cas_required": self.terminal_cas_required,
            "tested_against_live_database": self.tested_against_live_database,
        }


def _utc_moment(value: datetime | None = None) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _utc_moment(moment).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _direct_name(value: Any) -> str | None:
    clean = str(value or "").strip()
    if not clean or clean in {".", ".."}:
        return None
    candidate = Path(clean)
    if candidate.is_absolute() or candidate.name != clean or "/" in clean or "\\" in clean:
        return None
    return clean


def _expected_files(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    reasons: list[str] = []
    if not isinstance(value, list) or not value:
        return [], ["expected_files_missing"]
    normalized: list[dict[str, str]] = []
    formats: set[str] = set()
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            reasons.append("expected_file_invalid")
            continue
        file_format = str(item.get("format") or "").strip().lower()
        name = _direct_name(item.get("name"))
        if not _FORMAT_RE.fullmatch(file_format):
            reasons.append("expected_format_invalid")
        if not name:
            reasons.append("expected_name_invalid")
        if file_format in formats:
            reasons.append("expected_format_duplicate")
        if name and name in names:
            reasons.append("expected_name_duplicate")
        if _FORMAT_RE.fullmatch(file_format) and name:
            formats.add(file_format)
            names.add(name)
            normalized.append({"format": file_format, "name": name})
    return normalized, reasons


def protocol_from_metadata(metadata: Any) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Parse protocol metadata without reading storage or mutating input."""
    payload = _metadata(metadata)
    raw = payload.get(RENDER_METADATA_KEY)
    if raw is None:
        return None, ("legacy_protocol",)
    if not isinstance(raw, Mapping):
        return None, ("render_protocol_invalid",)
    protocol = dict(raw)
    reasons: list[str] = []
    if protocol.get("schema_version") != RENDER_SCHEMA_VERSION:
        reasons.append("render_schema_invalid")
    report_uid = _direct_name(protocol.get("report_uid"))
    if not report_uid:
        reasons.append("report_uid_invalid")
    attempt_id = str(protocol.get("attempt_id") or "").strip().lower()
    if not _ATTEMPT_RE.fullmatch(attempt_id):
        reasons.append("attempt_id_invalid")
    if _parse_iso(protocol.get("lease_expires_at")) is None:
        reasons.append("lease_expires_at_invalid")
    expected, expected_reasons = _expected_files(protocol.get("expected_files"))
    reasons.extend(expected_reasons)
    completion = protocol.get("completion_manifest")
    if not isinstance(completion, Mapping):
        reasons.append("completion_manifest_descriptor_invalid")
    else:
        completion_name = _direct_name(completion.get("name"))
        expected_manifest_name = (
            f".{report_uid}.{attempt_id}.complete.json" if report_uid and _ATTEMPT_RE.fullmatch(attempt_id) else ""
        )
        if not completion_name or completion_name != expected_manifest_name:
            reasons.append("completion_manifest_name_invalid")
        if completion.get("schema_version") != COMPLETION_SCHEMA_VERSION:
            reasons.append("completion_manifest_schema_invalid")
        state = str(completion.get("state") or "")
        if state not in {"pending", "published"}:
            reasons.append("completion_manifest_state_invalid")
        if state == "published":
            try:
                manifest_size = int(completion.get("file_size_bytes"))
            except (TypeError, ValueError):
                manifest_size = -1
            if manifest_size < 1:
                reasons.append("completion_manifest_size_invalid")
            if not _SHA256_RE.fullmatch(str(completion.get("sha256_hex") or "").strip().lower()):
                reasons.append("completion_manifest_digest_invalid")
    if reasons:
        return None, tuple(dict.fromkeys(reasons))
    protocol["report_uid"] = report_uid
    protocol["attempt_id"] = attempt_id
    protocol["expected_files"] = expected
    protocol["completion_manifest"] = dict(completion)
    return protocol, ()


def new_report_render_protocol(
    report_uid: str,
    expected_files: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Create the metadata envelope persisted before any data file is written."""
    clean_uid = _direct_name(report_uid)
    if not clean_uid:
        raise ValueError("report_uid must be a direct storage name")
    clean_attempt = str(attempt_id or secrets.token_hex(16)).strip().lower()
    if not _ATTEMPT_RE.fullmatch(clean_attempt):
        raise ValueError("attempt_id must be 32 lowercase hexadecimal characters")
    try:
        lease = int(lease_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("lease_seconds must be an integer") from exc
    if lease < 1 or lease > 24 * 60 * 60:
        raise ValueError("lease_seconds must be between 1 and 86400")
    expected, reasons = _expected_files(list(expected_files))
    if reasons:
        raise ValueError(",".join(reasons))
    manifest_name = f".{clean_uid}.{clean_attempt}.complete.json"
    return {
        "schema_version": RENDER_SCHEMA_VERSION,
        "report_uid": clean_uid,
        "attempt_id": clean_attempt,
        "lease_expires_at": _iso(_utc_moment(now) + timedelta(seconds=lease)),
        "expected_files": expected,
        "completion_manifest": {
            "schema_version": COMPLETION_SCHEMA_VERSION,
            "name": manifest_name,
            "state": "pending",
        },
    }


def with_report_render_protocol(metadata: Mapping[str, Any], protocol: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(metadata)
    result[RENDER_METADATA_KEY] = dict(protocol)
    return result


def build_completion_manifest_document(
    metadata: Any,
    stored_files: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    completed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a canonical manifest and reject incomplete or ambiguous bundles."""
    protocol, reasons = protocol_from_metadata(metadata)
    if reasons or protocol is None:
        raise ValueError(",".join(reasons or ("render_protocol_missing",)))
    expected_by_format = {item["format"]: item for item in protocol["expected_files"]}
    actual: dict[str, dict[str, Any]] = {}
    for raw_format, stored in stored_files:
        file_format = str(raw_format or "").strip().lower()
        if file_format in actual:
            raise ValueError("stored_format_duplicate")
        expected = expected_by_format.get(file_format)
        if expected is None:
            raise ValueError("stored_format_unexpected")
        if not isinstance(stored, Mapping):
            raise ValueError("stored_file_invalid")
        name = Path(str(stored.get("file_path") or "")).name
        if name != expected["name"]:
            raise ValueError("stored_name_mismatch")
        try:
            size = int(stored.get("file_size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ValueError("stored_size_invalid") from exc
        digest = str(stored.get("sha256_hex") or "").strip().lower()
        if size < 0:
            raise ValueError("stored_size_invalid")
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("stored_digest_invalid")
        actual[file_format] = {
            "format": file_format,
            "name": name,
            "size": size,
            "sha256": digest,
        }
    if set(actual) != set(expected_by_format):
        raise ValueError("stored_files_incomplete")
    ordered = [actual[item["format"]] for item in protocol["expected_files"]]
    return {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "report_uid": protocol["report_uid"],
        "attempt_id": protocol["attempt_id"],
        "completed_at": _iso(_utc_moment(completed_at)),
        "files": ordered,
    }


def completion_manifest_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(document), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def publish_report_completion_manifest(
    metadata: Any,
    stored_files: Sequence[tuple[str, Mapping[str, Any]]],
    *,
    completed_at: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Atomically no-clobber publish the final marker after all data files."""
    protocol, reasons = protocol_from_metadata(metadata)
    if reasons or protocol is None:
        raise ValueError(",".join(reasons or ("render_protocol_missing",)))
    document = build_completion_manifest_document(metadata, stored_files, completed_at=completed_at)
    stored = pdf_renderer.store_bytes(
        completion_manifest_bytes(document),
        filename=str(protocol["completion_manifest"]["name"]),
    )
    return document, stored


def metadata_with_published_manifest(
    metadata: Any,
    stored_manifest: Mapping[str, Any],
    *,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    payload = _metadata(metadata)
    protocol, reasons = protocol_from_metadata(payload)
    if reasons or protocol is None:
        raise ValueError(",".join(reasons or ("render_protocol_missing",)))
    expected_name = str(protocol["completion_manifest"]["name"])
    if Path(str(stored_manifest.get("file_path") or "")).name != expected_name:
        raise ValueError("completion_manifest_stored_name_mismatch")
    try:
        size = int(stored_manifest.get("file_size_bytes"))
    except (TypeError, ValueError) as exc:
        raise ValueError("completion_manifest_stored_size_invalid") from exc
    digest = str(stored_manifest.get("sha256_hex") or "").strip().lower()
    if size < 1 or not _SHA256_RE.fullmatch(digest):
        raise ValueError("completion_manifest_stored_integrity_invalid")
    protocol["completion_manifest"] = {
        **protocol["completion_manifest"],
        "state": "published",
        "file_size_bytes": size,
        "sha256_hex": digest,
        "published_at": _iso(_utc_moment(published_at)),
    }
    payload[RENDER_METADATA_KEY] = protocol
    return payload


def validate_completion_document(metadata: Any, manifest: Any) -> CompletionValidation:
    """Pure validator: no filesystem, database, clock, or input mutation."""
    protocol, protocol_reasons = protocol_from_metadata(metadata)
    if protocol_reasons:
        status = "manual_required" if protocol_reasons == ("legacy_protocol",) else "invalid"
        return CompletionValidation(status, protocol_reasons)
    if protocol is None or not isinstance(manifest, Mapping):
        return CompletionValidation("invalid", ("manifest_document_invalid",))
    document = dict(manifest)
    reasons: list[str] = []
    if document.get("schema_version") != COMPLETION_SCHEMA_VERSION:
        reasons.append("manifest_schema_invalid")
    if document.get("report_uid") != protocol["report_uid"]:
        reasons.append("report_uid_mismatch")
    if document.get("attempt_id") != protocol["attempt_id"]:
        reasons.append("attempt_id_mismatch")
    if _parse_iso(document.get("completed_at")) is None:
        reasons.append("completed_at_invalid")
    raw_files = document.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        reasons.append("manifest_files_missing")
        raw_files = []
    expected = {item["format"]: item["name"] for item in protocol["expected_files"]}
    actual: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for item in raw_files:
        if not isinstance(item, Mapping):
            reasons.append("manifest_file_invalid")
            continue
        file_format = str(item.get("format") or "").strip().lower()
        name = _direct_name(item.get("name"))
        if not _FORMAT_RE.fullmatch(file_format):
            reasons.append("manifest_format_invalid")
        if file_format in actual:
            reasons.append("manifest_format_duplicate")
        if not name:
            reasons.append("manifest_name_invalid")
        elif name in names:
            reasons.append("manifest_name_duplicate")
        if file_format not in expected:
            reasons.append("manifest_format_unexpected")
        elif name and expected[file_format] != name:
            reasons.append("manifest_name_mismatch")
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError):
            size = -1
        if size < 0:
            reasons.append("manifest_size_invalid")
        digest = str(item.get("sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            reasons.append("manifest_digest_invalid")
        if _FORMAT_RE.fullmatch(file_format) and name:
            actual[file_format] = {
                "format": file_format,
                "name": name,
                "size": size,
                "sha256": digest,
            }
            names.add(name)
    if set(actual) != set(expected):
        reasons.append("manifest_files_incomplete")
    if reasons:
        return CompletionValidation("invalid", tuple(dict.fromkeys(reasons)), document)
    normalized = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "report_uid": protocol["report_uid"],
        "attempt_id": protocol["attempt_id"],
        "completed_at": str(document["completed_at"]),
        "files": [actual[item["format"]] for item in protocol["expected_files"]],
    }
    return CompletionValidation("valid", (), normalized)


def _read_manifest(metadata: Any) -> tuple[CompletionValidation, dict[str, Any] | None]:
    protocol, reasons = protocol_from_metadata(metadata)
    if reasons:
        status = "manual_required" if reasons == ("legacy_protocol",) else "invalid"
        return CompletionValidation(status, reasons), None
    assert protocol is not None
    descriptor = protocol["completion_manifest"]
    manifest_path = pdf_renderer.configured_report_storage_path() / str(descriptor["name"])
    try:
        opened = pdf_renderer.open_stored_file(manifest_path)
    except (FileNotFoundError, OSError, ValueError):
        return CompletionValidation("invalid", ("manifest_missing_or_unsafe",)), None
    try:
        expected_size = descriptor.get("file_size_bytes") if descriptor.get("state") == "published" else None
        expected_sha = descriptor.get("sha256_hex") if descriptor.get("state") == "published" else ""
        pdf_renderer.verify_opened_file(opened, expected_size=expected_size, expected_sha256=str(expected_sha or ""))
        if opened.size > MAX_MANIFEST_BYTES:
            return CompletionValidation("invalid", ("manifest_too_large",)), None
        raw = b"".join(opened.iter_bytes(MAX_MANIFEST_BYTES + 1))
    except (OSError, ValueError):
        opened.close()
        return CompletionValidation("invalid", ("manifest_integrity_invalid",)), None
    finally:
        opened.close()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return CompletionValidation("invalid", ("manifest_json_invalid",)), None
    validation = validate_completion_document(metadata, document)
    return validation, validation.manifest


def _compare_file_rows(
    manifest: Mapping[str, Any],
    file_rows: Iterable[Mapping[str, Any]],
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    rows_by_format: dict[str, Mapping[str, Any]] = {}
    rows = [dict(row) for row in file_rows]
    for row in rows:
        file_format = str(row.get("file_format") or "").strip().lower()
        if file_format in rows_by_format:
            reasons.append("db_file_format_duplicate")
        rows_by_format[file_format] = row
    expected_files = list(manifest.get("files") or [])
    expected_formats = {str(item["format"]) for item in expected_files}
    if set(rows_by_format) != expected_formats:
        reasons.append("db_file_formats_mismatch")
    root = pdf_renderer.configured_report_storage_path().resolve(strict=True)
    for item in expected_files:
        file_format = str(item["format"])
        row = rows_by_format.get(file_format)
        if row is None:
            continue
        try:
            row_root, row_path = pdf_renderer._direct_stored_path(str(row.get("file_path") or ""))
        except (OSError, ValueError):
            reasons.append("db_file_path_unsafe")
            continue
        if row_root != root or row_path.name != item["name"]:
            reasons.append("db_file_name_mismatch")
        try:
            row_size = int(row.get("file_size_bytes"))
        except (TypeError, ValueError):
            row_size = -1
        if row_size != int(item["size"]):
            reasons.append("db_file_size_mismatch")
        if str(row.get("sha256_hex") or "").strip().lower() != item["sha256"]:
            reasons.append("db_file_digest_mismatch")
    deduped = tuple(dict.fromkeys(reasons))
    return not deduped, deduped


def inspect_report_completion(
    metadata: Any,
    file_rows: Iterable[Mapping[str, Any]] = (),
) -> CompletionInspection:
    """Read and verify a bundle without writing DB rows or deleting files."""
    validation, manifest = _read_manifest(metadata)
    if not validation.valid or manifest is None:
        return CompletionInspection(
            validation.status,
            validation.reasons,
            False,
            False,
            validation.manifest,
        )
    storage_reasons: list[str] = []
    root = pdf_renderer.configured_report_storage_path()
    for item in manifest["files"]:
        try:
            opened = pdf_renderer.open_stored_file(root / str(item["name"]))
            try:
                pdf_renderer.verify_opened_file(
                    opened,
                    expected_size=item["size"],
                    expected_sha256=item["sha256"],
                )
            finally:
                opened.close()
        except (FileNotFoundError, OSError, ValueError):
            storage_reasons.append(f"data_file_invalid:{item['format']}")
    if storage_reasons:
        return CompletionInspection(
            "invalid",
            tuple(storage_reasons),
            False,
            False,
            manifest,
        )
    rows_match, row_reasons = _compare_file_rows(manifest, file_rows)
    return CompletionInspection(
        "valid",
        (),
        True,
        rows_match,
        manifest,
        row_reasons,
    )


def reconcile_report_run_dry_run(
    report: Mapping[str, Any],
    file_rows: Iterable[Mapping[str, Any]] = (),
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Default-safe reconciler: classify only; never writes or deletes."""
    materialized_rows = [dict(row) for row in file_rows]
    base = {
        "mode": "dry_run",
        "mutated_database": False,
        "deleted_files": False,
        "report_run_id": report.get("id"),
        "report_uid": report.get("report_uid"),
    }
    protocol, reasons = protocol_from_metadata(report.get("metadata_json"))
    if reasons == ("legacy_protocol",):
        return {**base, "action": "manual_required", "reasons": ["legacy_protocol"]}
    if reasons or protocol is None:
        return {**base, "action": "manual_required", "reasons": list(reasons)}
    if str(report.get("status") or "") != "rendering":
        return {**base, "action": "not_applicable", "reasons": ["status_not_rendering"]}
    lease_expires_at = _parse_iso(protocol["lease_expires_at"])
    assert lease_expires_at is not None
    if _utc_moment(now) < lease_expires_at:
        return {
            **base,
            "action": "wait_for_lease",
            "reasons": ["lease_active"],
            "lease_expires_at": _iso(lease_expires_at),
        }
    inspection = inspect_report_completion(report.get("metadata_json"), materialized_rows)
    if not inspection.storage_valid:
        return {
            **base,
            "action": "manual_required",
            "reasons": list(inspection.reasons),
            "inspection": inspection.as_dict(),
        }
    if inspection.db_rows_match:
        action = "ready_cas_candidate"
        action_reasons: list[str] = []
    elif inspection.db_row_reasons == ("db_file_formats_mismatch",) and not materialized_rows:
        action = "rebuild_file_rows_then_ready_cas_candidate"
        action_reasons = ["db_file_rows_missing"]
    else:
        action = "manual_required"
        action_reasons = list(inspection.db_row_reasons)
    return {
        **base,
        "action": action,
        "reasons": action_reasons,
        "inspection": inspection.as_dict(),
    }


def reconciliation_lock_plan(dialect: str) -> ReconciliationLockPlan:
    """Return, but never execute, the future claim transaction contract.

    PostgreSQL workers must claim rows with ``FOR UPDATE SKIP LOCKED`` inside an
    explicit transaction.  SQLite has no row locks, so the equivalent boundary
    begins with ``BEGIN IMMEDIATE`` before selecting.  Neither path is wired to
    a scheduler in Round5 and neither result claims live-database verification.
    """
    clean = str(dialect or "").strip().lower()
    base_select = (
        "SELECT id, report_uid, status, metadata_json FROM vkpi_report_runs "
        "WHERE status='rendering' ORDER BY triggered_at ASC LIMIT ?"
    )
    if clean in {"postgres", "postgresql"}:
        return ReconciliationLockPlan(
            "postgresql",
            None,
            base_select + " FOR UPDATE SKIP LOCKED",
        )
    if clean in {"sqlite", "sqlite3"}:
        return ReconciliationLockPlan(
            "sqlite",
            "BEGIN IMMEDIATE",
            base_select,
        )
    raise ValueError("unsupported reconciliation dialect")
