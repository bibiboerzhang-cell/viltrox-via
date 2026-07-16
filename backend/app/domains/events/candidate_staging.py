"""Fail-closed control plane for Dealer/Event candidate staging.

Migration 257 owns the durable constraints.  This service deliberately stops
at a human-reviewed *candidate* and an append-only manual-promotion receipt:
it never creates or updates a Dealer/Event business row, never crawls a source,
and never treats a public listing as authorization, inventory, participation,
sales, ROI, attendance, or local impact.

The preview path is database-free.  Every mutation is organization-scoped,
manager-routed by the API, explicitly opted into with ``record_only=false``,
bounded to one candidate/evidence/decision at a time, and transactionally
fails closed when migration 257 or its database trigger is unavailable.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.events import us_coverage_registry
from app.domains.source_passport_core import (
    SOURCE_ID_RE,
    STABLE_LOCATION_RE,
    STABLE_ORG_RE,
    canonical_json_sha256,
)
from app.domains.source_passport_urls import source_url_identity


CANDIDATE_TABLE = "vkpi_dealer_event_candidates"
EVIDENCE_LINK_TABLE = "vkpi_candidate_field_evidence_links"
PASSPORT_TABLE = "vkpi_source_passports"
FIELD_EVIDENCE_TABLE = "vkpi_source_field_evidence"
CLAIM_STATUS = "descriptive_only"
CANDIDATE_TYPES = frozenset({"dealer_location", "event_opportunity"})
REVIEW_STATUSES = frozenset({"pending", "needs_review", "approved", "rejected"})
EVIDENCE_ROLES = frozenset(
    {"source_listing", "identity", "location", "activity", "product_presence"}
)
MAX_CANDIDATE_PAYLOAD_BYTES = 262_144
MAX_CANDIDATE_PAYLOAD_NODES = 10_000
MAX_CANDIDATE_PAYLOAD_DEPTH = 12
_ROLE_FIELD_NAMES: dict[str, frozenset[str]] = {
    "source_listing": frozenset({"candidate.source_url"}),
    "identity": frozenset({"candidate.source_entity_key"}),
    "location": frozenset({"candidate.location"}),
    "activity": frozenset({"candidate.activity"}),
    "product_presence": frozenset({"viltrox.product_page"}),
}
_SOURCE_ENTITY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$")
_CANDIDATE_ID_RE = re.compile(r"^cand_[0-9a-f]{32}$")
_EVIDENCE_ID_RE = re.compile(r"^spe_[0-9a-f]{32}$")
_PREVIEW_FIELDS = frozenset(
    {
        "record_only",
        "source_registry_id",
        "source_entity_key",
        "source_url",
        "stable_org_key",
        "stable_location_key",
        "candidate_payload",
    }
)
_FORBIDDEN_CLAIM_KEYS = frozenset(
    {
        "authorized",
        "authorization_status",
        "current_inventory",
        "inventory_status",
        "gmv",
        "roi",
        "sales",
        "attendance_confirmed",
        "viltrox_participation_confirmed",
        "local_impact",
    }
)
_FORBIDDEN_CLAIM_KEYS_FLAT = frozenset(
    re.sub(r"[^a-z0-9]", "", key) for key in _FORBIDDEN_CLAIM_KEYS
)


class CandidateStagingSchemaUnavailable(RuntimeError):
    """Migration 257 is not available to the current process/database."""


class CandidateStagingStateConflict(RuntimeError):
    """A requested candidate transition failed a durable truth constraint."""


def _json_bind() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _now_sql() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _json_load(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _candidate_id(value: Any) -> str:
    candidate_id = str(value or "").strip()
    if not _CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate_id is invalid")
    return candidate_id


def _staff_id(value: Any) -> int:
    return _positive_int(value, "reviewer_staff_id")


def _require_schema(*, include_evidence: bool = False) -> None:
    names = [CANDIDATE_TABLE, EVIDENCE_LINK_TABLE]
    if include_evidence:
        names.extend([PASSPORT_TABLE, FIELD_EVIDENCE_TABLE])
    try:
        ready = all(table_exists(name) for name in names)
    except Exception as exc:
        raise CandidateStagingSchemaUnavailable(
            "candidate_staging_schema_unavailable"
        ) from exc
    if not ready:
        raise CandidateStagingSchemaUnavailable("migration_257_pending")


def _rollback(conn: Any) -> None:
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        rollback()


def _commit(conn: Any) -> None:
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()


def _is_constraint_conflict(exc: Exception) -> bool:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    return sqlstate in {"23503", "23505", "23514", "P0001"} or exc.__class__.__name__ in {
        "IntegrityError",
        "UniqueViolation",
        "CheckViolation",
        "ForeignKeyViolation",
        "RaiseException",
    }


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fresh_verified(row: Mapping[str, Any], *, as_of: datetime) -> bool:
    verified_at = _timestamp(row.get("verified_at"))
    try:
        stale_after_days = int(row.get("stale_after_days") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        row.get("verification_status") == "verified"
        and row.get("freshness_status_at_write") == "fresh"
        and row.get("reviewer_staff_id")
        and row.get("claim_status") == CLAIM_STATUS
        and verified_at is not None
        and verified_at <= as_of + timedelta(minutes=5)
        and stale_after_days > 0
        and verified_at >= as_of - timedelta(days=stale_after_days)
    )


def _bounded_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("candidate_payload must be an object")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate_payload must be JSON-compatible") from exc
    if len(encoded) > MAX_CANDIDATE_PAYLOAD_BYTES:
        raise ValueError(
            f"candidate_payload must be <= {MAX_CANDIDATE_PAYLOAD_BYTES} UTF-8 bytes"
        )
    nodes = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal nodes
        if depth > MAX_CANDIDATE_PAYLOAD_DEPTH:
            raise ValueError("candidate_payload nesting is too deep")
        nodes += 1
        if nodes > MAX_CANDIDATE_PAYLOAD_NODES:
            raise ValueError("candidate_payload contains too many values")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if len(str(key)) > 240:
                    raise ValueError("candidate_payload key is too long")
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)

    walk(value, 0)
    return dict(value)


def _expected_evidence_sha(
    candidate: Mapping[str, Any], evidence_role: str
) -> str | None:
    payload = _json_load(candidate.get("candidate_payload_json"))
    if evidence_role == "source_listing":
        return canonical_json_sha256(str(candidate.get("source_url") or ""))
    if evidence_role == "identity":
        return canonical_json_sha256(str(candidate.get("source_entity_key") or ""))
    if evidence_role == "location":
        location = payload.get("address") or payload.get("location")
        return canonical_json_sha256(location) if location not in (None, "") else None
    if evidence_role == "activity":
        value = str(candidate.get("content_sha256") or "")
        return value if re.fullmatch(r"[0-9a-f]{64}", value) else None
    if evidence_role == "product_presence":
        product = payload.get("viltrox_product_page") or payload.get("product_page")
        return canonical_json_sha256(product) if product not in (None, "") else None
    return None


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _known_registry_ids(candidate_type: str) -> set[str]:
    report = us_coverage_registry.audit_registry()
    key = "dealer_discovery_sources" if candidate_type == "dealer_location" else "event_sources"
    return {str(item.get("id") or "") for item in report.get(key, [])}


def _forbidden_claim_paths(value: Any, *, path: str = "candidate_payload") -> list[str]:
    hits: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().casefold()
            item_path = f"{path}.{raw_key}"
            flat_key = re.sub(r"[^a-z0-9]", "", key)
            if key in _FORBIDDEN_CLAIM_KEYS or flat_key in _FORBIDDEN_CLAIM_KEYS_FLAT:
                hits.append(item_path)
            hits.extend(_forbidden_claim_paths(item, path=item_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_forbidden_claim_paths(item, path=f"{path}[{index}]"))
    return hits


def preview_candidate(
    payload: Mapping[str, Any],
    *,
    candidate_type: str,
    organization_id: Any,
) -> dict[str, Any]:
    """Validate one candidate envelope without network, SQL or persistence."""
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    if not isinstance(payload, Mapping):
        raise ValueError("candidate preview body must be an object")
    unknown = sorted(set(payload) - set(_PREVIEW_FIELDS))
    if unknown:
        raise ValueError(f"unsupported candidate preview fields: {', '.join(unknown)}")
    if payload.get("record_only", True) is not True:
        raise ValueError("candidate preview is record_only and cannot persist or promote")

    org_id = _positive_int(organization_id, "organization_id")
    source_registry_id = str(payload.get("source_registry_id") or "").strip()
    if not SOURCE_ID_RE.fullmatch(source_registry_id):
        raise ValueError("source_registry_id is invalid")
    if source_registry_id not in _known_registry_ids(candidate_type):
        raise ValueError("source_registry_id is not registered for this candidate type")

    source_entity_key = str(payload.get("source_entity_key") or "").strip()
    if not _SOURCE_ENTITY_KEY_RE.fullmatch(source_entity_key):
        raise ValueError("source_entity_key is invalid")
    source_identity = source_url_identity(payload.get("source_url"))
    if not source_identity["valid"]:
        raise ValueError("source_url must be a credential-free public HTTPS URL")
    if len(str(source_identity["canonical_url"]).encode("utf-8")) > 2048:
        raise ValueError("source_url must be <= 2048 UTF-8 bytes")

    stable_org_key = str(payload.get("stable_org_key") or "").strip()
    stable_location_key = str(payload.get("stable_location_key") or "").strip()
    if candidate_type == "dealer_location":
        if bool(stable_org_key) != bool(stable_location_key):
            raise ValueError("Dealer stable organization/location keys must be supplied together")
        if stable_org_key and not STABLE_ORG_RE.fullmatch(stable_org_key):
            raise ValueError("stable_org_key is invalid")
        if stable_location_key and not STABLE_LOCATION_RE.fullmatch(stable_location_key):
            raise ValueError("stable_location_key is invalid")
    else:
        if stable_org_key:
            raise ValueError("stable_org_key is valid only for Dealer candidates")
        if stable_location_key and not STABLE_LOCATION_RE.fullmatch(stable_location_key):
            raise ValueError("stable_location_key is invalid")

    candidate_payload = _bounded_payload(payload.get("candidate_payload", {}))
    forbidden = _forbidden_claim_paths(candidate_payload)
    if forbidden:
        raise ValueError(
            "candidate_payload contains unsupported business claims: " + ", ".join(forbidden)
        )

    content_sha = canonical_json_sha256(candidate_payload)
    required_role = "location" if candidate_type == "dealer_location" else "activity"
    if required_role == "location":
        location_value = candidate_payload.get("address") or candidate_payload.get("location")
        required_value_sha = (
            canonical_json_sha256(location_value)
            if location_value not in (None, "")
            else None
        )
    else:
        required_value_sha = content_sha
    candidate_id = "cand_" + canonical_json_sha256(
        {
            "organization_id": org_id,
            "candidate_type": candidate_type,
            "source_registry_id": source_registry_id,
            "source_entity_key": source_entity_key,
        }
    )[:32]
    reasons = [
        "staging_row_not_persisted",
        "human_review_required",
        "verified_source_registry_passport_required",
        "verified_field_evidence_link_required",
    ]
    if candidate_type == "dealer_location" and not stable_location_key:
        reasons.append("exact_stable_dealer_location_required")

    return {
        "ok": True,
        "record_only": True,
        "contract": {
            "id": "vkpi.dealer_event.candidate_staging.preview",
            "version": 1,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
            "candidate_rows_written": 0,
        },
        "candidate": {
            "id": candidate_id,
            "organization_id": org_id,
            "candidate_type": candidate_type,
            "source_registry_id": source_registry_id,
            "source_entity_key": source_entity_key,
            "source_url": source_identity["canonical_url"],
            "stable_org_key": stable_org_key or None,
            "stable_location_key": stable_location_key or None,
            "content_sha256": content_sha,
        },
        "promotion_gate": {
            "status": "blocked",
            "eligible": False,
            "automatic_promotion": False,
            "reasons": reasons,
            "required_evidence_role": required_role,
            "required_field_names": sorted(_ROLE_FIELD_NAMES[required_role]),
            "required_value_sha256": required_value_sha,
            "database_trigger_revalidates_on_approval": True,
        },
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
        "claim_boundaries": {
            "candidate_proves_viltrox_authorization": False,
            "product_page_proves_current_inventory": False,
            "event_listing_proves_viltrox_participation": False,
            "candidate_proves_sales_roi_or_local_impact": False,
        },
    }


def _public_candidate(
    raw: Mapping[str, Any],
    *,
    include_payload: bool,
    evidence_links: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    required_role = (
        "location" if raw.get("candidate_type") == "dealer_location" else "activity"
    )
    item = {
        "id": raw.get("id"),
        "organization_id": raw.get("organization_id"),
        "candidate_type": raw.get("candidate_type"),
        "source_registry_id": raw.get("source_registry_id"),
        "source_entity_key": raw.get("source_entity_key"),
        "source_url": raw.get("source_url"),
        "stable_org_key": raw.get("stable_org_key") or None,
        "stable_location_key": raw.get("stable_location_key") or None,
        "content_sha256": raw.get("content_sha256"),
        "review_status": raw.get("review_status"),
        "reviewer_staff_id": raw.get("reviewer_staff_id"),
        "reviewed_at": str(raw.get("reviewed_at")) if raw.get("reviewed_at") else None,
        "source_passport_id": raw.get("source_passport_id"),
        "promotion_gate_status": raw.get("promotion_gate_status"),
        "promotion_target_type": raw.get("promotion_target_type") or None,
        "promotion_target_id": raw.get("promotion_target_id") or None,
        "promotion_reviewer_staff_id": raw.get("promotion_reviewer_staff_id"),
        "promoted_at": str(raw.get("promoted_at")) if raw.get("promoted_at") else None,
        "claim_status": CLAIM_STATUS,
        "created_at": str(raw.get("created_at")) if raw.get("created_at") else None,
        "updated_at": str(raw.get("updated_at")) if raw.get("updated_at") else None,
        "automatic_promotion": False,
        "business_rows_written": 0,
        "manual_review_requirements": {
            "required_evidence_role": required_role,
            "required_field_names": sorted(_ROLE_FIELD_NAMES[required_role]),
            "required_value_sha256": _expected_evidence_sha(raw, required_role),
            "source_passport_must_be_current_verified_exact": True,
            "database_trigger_revalidates_on_approval": True,
        },
    }
    if include_payload:
        item["candidate_payload"] = _json_load(raw.get("candidate_payload_json"))
    if evidence_links is not None:
        item["evidence_links"] = [dict(link) for link in evidence_links]
    return item


def _locked_candidate(
    conn: Any,
    *,
    organization_id: int,
    candidate_id: str,
) -> dict[str, Any]:
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    row = conn.execute(
        f"SELECT * FROM {CANDIDATE_TABLE} "
        f"WHERE organization_id=? AND id=?{lock}",
        (organization_id, candidate_id),
    ).fetchone()
    if row is None:
        raise LookupError("candidate not found")
    return _row(row)


def _reset_existing_candidate(
    conn: Any,
    *,
    existing: Mapping[str, Any],
    candidate: Mapping[str, Any],
    payload_json: str,
    organization_id: int,
) -> str:
    if str(existing.get("promotion_gate_status") or "") == "manually_promoted":
        raise CandidateStagingStateConflict("manual_promotion_receipt_is_immutable")
    candidate_id = _candidate_id(existing.get("id"))
    conn.execute(
        f"""
        UPDATE {CANDIDATE_TABLE}
        SET source_url=?, stable_org_key=?, stable_location_key=?,
            content_sha256=?, candidate_payload_json={_json_bind()},
            review_status='pending', reviewer_staff_id=NULL, reviewed_at=NULL,
            source_passport_id=NULL, promotion_gate_status='blocked',
            promotion_target_type='', promotion_target_id='',
            promotion_reviewer_staff_id=NULL, promoted_at=NULL,
            claim_status=?, updated_at={_now_sql()}
        WHERE organization_id=? AND id=?
        """,
        (
            candidate["source_url"],
            candidate.get("stable_org_key") or "",
            candidate.get("stable_location_key") or "",
            candidate["content_sha256"],
            payload_json,
            CLAIM_STATUS,
            organization_id,
            candidate_id,
        ),
    )
    conn.execute(
        f"DELETE FROM {EVIDENCE_LINK_TABLE} "
        "WHERE organization_id=? AND candidate_id=?",
        (organization_id, candidate_id),
    )
    return candidate_id


def _prepare_candidate_stage(
    payload: Mapping[str, Any],
    *,
    candidate_type: str,
    organization_id: Any,
) -> tuple[dict[str, Any], int, str, str]:
    if not isinstance(payload, Mapping):
        raise ValueError("candidate stage body must be an object")
    if payload.get("record_only") is not False:
        raise ValueError("candidate staging requires explicit record_only=false")
    preview_input = dict(payload)
    preview_input["record_only"] = True
    preview = preview_candidate(
        preview_input,
        candidate_type=candidate_type,
        organization_id=organization_id,
    )
    _require_schema()
    candidate = dict(preview["candidate"])
    org_id = _positive_int(candidate["organization_id"], "organization_id")
    candidate_id = _candidate_id(candidate["id"])
    payload_json = json.dumps(
        dict(payload.get("candidate_payload") or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return candidate, org_id, candidate_id, payload_json


def _persist_candidate_on_connection(
    conn: Any,
    *,
    candidate: Mapping[str, Any],
    candidate_type: str,
    organization_id: int,
    candidate_id: str,
    payload_json: str,
) -> tuple[dict[str, Any], bool]:
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    existing_row = conn.execute(
        f"SELECT * FROM {CANDIDATE_TABLE} "
        "WHERE organization_id=? AND candidate_type=? "
        f"AND source_registry_id=? AND source_entity_key=?{lock}",
        (
            organization_id,
            candidate_type,
            candidate["source_registry_id"],
            candidate["source_entity_key"],
        ),
    ).fetchone()
    existing = _row(existing_row)
    if existing:
        candidate_id = _reset_existing_candidate(
            conn,
            existing=existing,
            candidate=candidate,
            payload_json=payload_json,
            organization_id=organization_id,
        )
    else:
        cursor = conn.execute(
            f"""
            INSERT INTO {CANDIDATE_TABLE}
              (organization_id,id,candidate_type,source_registry_id,
               source_entity_key,source_url,stable_org_key,stable_location_key,
               content_sha256,candidate_payload_json,review_status,
               promotion_gate_status,claim_status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,{_json_bind()},'pending','blocked',?,
                    {_now_sql()},{_now_sql()})
            ON CONFLICT (
              organization_id,candidate_type,source_registry_id,source_entity_key
            ) DO NOTHING
            """,
            (
                organization_id,
                candidate_id,
                candidate_type,
                candidate["source_registry_id"],
                candidate["source_entity_key"],
                candidate["source_url"],
                candidate.get("stable_org_key") or "",
                candidate.get("stable_location_key") or "",
                candidate["content_sha256"],
                payload_json,
                CLAIM_STATUS,
            ),
        )
        if getattr(cursor, "rowcount", 1) == 0:
            raced_row = conn.execute(
                f"SELECT * FROM {CANDIDATE_TABLE} "
                "WHERE organization_id=? AND candidate_type=? "
                f"AND source_registry_id=? AND source_entity_key=?{lock}",
                (
                    organization_id,
                    candidate_type,
                    candidate["source_registry_id"],
                    candidate["source_entity_key"],
                ),
            ).fetchone()
            if raced_row is None:
                raise CandidateStagingStateConflict(
                    "concurrent_candidate_stage_could_not_be_resolved"
                )
            existing = _row(raced_row)
            candidate_id = _reset_existing_candidate(
                conn,
                existing=existing,
                candidate=candidate,
                payload_json=payload_json,
                organization_id=organization_id,
            )
    stored = _locked_candidate(conn, organization_id=organization_id, candidate_id=candidate_id)
    return stored, bool(existing)
def _candidate_stage_result(stored: Mapping[str, Any], *, restaged: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "created": not restaged,
        "restaged": restaged,
        "candidate": _public_candidate(stored, include_payload=False),
        "contract": {
            "id": "vkpi.dealer_event.candidate_staging.write",
            "version": 1,
            "network_accessed": False,
            "database_accessed": True,
            "candidate_rows_written": 1,
            "business_rows_written": 0,
        },
        "claim_status": CLAIM_STATUS,
        "automatic_promotion": False,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }
def _claim_guard(
    conn: Any, *, source_id: str, claim_token: str, organization_id: int
) -> None:
    row = conn.execute(
        """
        SELECT id FROM vkpi_event_watch_targets
        WHERE id=? AND source_kind='dealer_event' AND country_code='US'
          AND status='active' AND enabled=TRUE
          AND activity_sync_claim_token=?
          AND activity_sync_claim_organization_id=?
          AND activity_sync_claim_expires_at>clock_timestamp()
        FOR UPDATE
        """,
        (source_id, claim_token, organization_id),
    ).fetchone()
    if row is None:
        raise CandidateStagingStateConflict("source_claim_fenced")
def stage_candidate(
    payload: Mapping[str, Any], *, candidate_type: str, organization_id: Any
) -> dict[str, Any]:
    """Create or re-stage one review candidate; no business row is touched."""
    candidate, org_id, candidate_id, payload_json = _prepare_candidate_stage(
        payload, candidate_type=candidate_type, organization_id=organization_id
    )
    conn = get_conn()
    try:
        stored, restaged = _persist_candidate_on_connection(
            conn,
            candidate=candidate,
            candidate_type=candidate_type,
            organization_id=org_id,
            candidate_id=candidate_id,
            payload_json=payload_json,
        )
        _commit(conn)
    except (CandidateStagingStateConflict, LookupError, ValueError):
        _rollback(conn)
        raise
    except Exception as exc:
        _rollback(conn)
        if _is_constraint_conflict(exc):
            raise CandidateStagingStateConflict(
                "candidate_stage_rejected_by_database"
            ) from exc
        raise
    return _candidate_stage_result(stored, restaged=restaged)
def stage_candidate_with_source_claim(
    payload: Mapping[str, Any],
    *,
    candidate_type: str,
    organization_id: Any,
    source_id: str,
    claim_token: str,
    connection: Any,
) -> dict[str, Any]:
    """Atomically fence a source lease and write one candidate on one PG connection."""
    if not is_postgres_runtime():
        raise CandidateStagingSchemaUnavailable("claimed_candidate_stage_requires_postgres")
    if not table_exists("vkpi_event_watch_targets"):
        raise CandidateStagingSchemaUnavailable("migration_261_pending")
    candidate, org_id, candidate_id, payload_json = _prepare_candidate_stage(
        payload, candidate_type=candidate_type, organization_id=organization_id
    )
    if candidate["source_registry_id"] != source_id:
        raise CandidateStagingStateConflict("source_claim_identity_mismatch")
    if not re.fullmatch(r"[0-9a-f]{32}", str(claim_token or "")):
        raise CandidateStagingStateConflict("source_claim_token_invalid")
    try:
        _claim_guard(
            connection,
            source_id=source_id,
            claim_token=claim_token,
            organization_id=org_id,
        )
        stored, restaged = _persist_candidate_on_connection(
            connection,
            candidate=candidate,
            candidate_type=candidate_type,
            organization_id=org_id,
            candidate_id=candidate_id,
            payload_json=payload_json,
        )
        # Re-check immediately before commit while the source row remains locked.
        _claim_guard(
            connection,
            source_id=source_id,
            claim_token=claim_token,
            organization_id=org_id,
        )
        _commit(connection)
    except (CandidateStagingStateConflict, LookupError, ValueError):
        _rollback(connection)
        raise
    except Exception as exc:
        _rollback(connection)
        if _is_constraint_conflict(exc):
            raise CandidateStagingStateConflict(
                "claimed_candidate_stage_rejected_by_database"
            ) from exc
        raise
    return _candidate_stage_result(stored, restaged=restaged)


def list_candidates(
    *,
    organization_id: Any,
    candidate_type: str,
    review_status: str | None = None,
    promotion_gate_status: str | None = None,
    include_payload: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Bounded manager-facing queue; callers cannot cross organizations."""
    _require_schema()
    org_id = _positive_int(organization_id, "organization_id")
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    safe_offset = max(0, int(offset or 0))
    limit_ceiling = 20 if include_payload else 100
    safe_limit = max(1, min(int(limit or 50), limit_ceiling))
    clauses = ["organization_id=?", "candidate_type=?"]
    params: list[Any] = [org_id, candidate_type]
    if review_status:
        status = str(review_status).strip().casefold()
        if status not in REVIEW_STATUSES:
            raise ValueError("unsupported review_status")
        clauses.append("review_status=?")
        params.append(status)
    if promotion_gate_status:
        gate = str(promotion_gate_status).strip().casefold()
        if gate not in {"blocked", "eligible_for_manual_promotion", "manually_promoted"}:
            raise ValueError("unsupported promotion_gate_status")
        clauses.append("promotion_gate_status=?")
        params.append(gate)
    where = " AND ".join(clauses)
    params.extend([safe_limit, safe_offset])
    rows = get_conn().execute(
        f"""
        SELECT * FROM {CANDIDATE_TABLE}
        WHERE {where}
        ORDER BY updated_at DESC,id
        LIMIT ? OFFSET ?
        """,
        tuple(params),
    ).fetchall()
    items = [
        _public_candidate(_row(raw), include_payload=bool(include_payload))
        for raw in rows
    ]
    return {
        "items": items,
        "count": len(items),
        "offset": safe_offset,
        "limit": safe_limit,
        "organization_id": org_id,
        "candidate_type": candidate_type,
        "raw_payload_included": bool(include_payload),
        "claim_status": CLAIM_STATUS,
        "automatic_promotion": False,
        "business_rows_written": 0,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


def get_candidate(
    candidate_id: Any,
    *,
    organization_id: Any,
    candidate_type: str,
) -> dict[str, Any]:
    """Return one manager-visible raw candidate and its bounded evidence links."""
    _require_schema(include_evidence=True)
    org_id = _positive_int(organization_id, "organization_id")
    cid = _candidate_id(candidate_id)
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    conn = get_conn()
    row = conn.execute(
        f"SELECT * FROM {CANDIDATE_TABLE} "
        "WHERE organization_id=? AND id=? AND candidate_type=?",
        (org_id, cid, candidate_type),
    ).fetchone()
    if row is None:
        raise LookupError("candidate not found")
    evidence_rows = conn.execute(
        f"""
        SELECT link.field_evidence_id,link.evidence_role,link.added_by_staff_id,
               link.created_at,evidence.field_name,evidence.value_status,
               evidence.verification_status,evidence.freshness_status_at_write,
               evidence.verified_at,evidence.source_url
        FROM {EVIDENCE_LINK_TABLE} link
        JOIN {FIELD_EVIDENCE_TABLE} evidence
          ON evidence.organization_id=link.organization_id
         AND evidence.id=link.field_evidence_id
        WHERE link.organization_id=? AND link.candidate_id=?
        ORDER BY link.created_at DESC,link.field_evidence_id
        LIMIT 100
        """,
        (org_id, cid),
    ).fetchall()
    links = []
    for raw in evidence_rows:
        link = _row(raw)
        for field in ("created_at", "verified_at"):
            if link.get(field) is not None:
                link[field] = str(link[field])
        links.append(link)
    return {
        "candidate": _public_candidate(
            _row(row), include_payload=True, evidence_links=links
        ),
        "claim_status": CLAIM_STATUS,
        "automatic_promotion": False,
        "business_rows_written": 0,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


def link_field_evidence(candidate_id: Any, **kwargs) -> dict[str, Any]:
    from app.domains.events.candidate_staging_review import link_field_evidence as impl

    return impl(candidate_id, **kwargs)


def review_candidate(candidate_id: Any, **kwargs) -> dict[str, Any]:
    from app.domains.events.candidate_staging_review import review_candidate as impl

    return impl(candidate_id, **kwargs)


def record_manual_promotion_receipt(candidate_id: Any, **kwargs) -> dict[str, Any]:
    from app.domains.events.candidate_staging_review import (
        record_manual_promotion_receipt as impl,
    )

    return impl(candidate_id, **kwargs)


def staging_summary(
    *,
    organization_id: Any,
    candidate_type: str,
) -> dict[str, Any]:
    """Read org-scoped queue counts; raw candidate payloads are never returned."""
    org_id = _positive_int(organization_id, "organization_id")
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    if not table_exists(CANDIDATE_TABLE) or not table_exists(EVIDENCE_LINK_TABLE):
        return {
            "status": "migration_pending",
            "organization_id": org_id,
            "candidate_type": candidate_type,
            "total": 0,
            "review_status": {},
            "promotion_gate_status": {},
            "linked_field_evidence": 0,
            "claim_status": CLAIM_STATUS,
            "automatic_promotion": False,
            "business_rows_written": 0,
        }

    rows = get_conn().execute(
        """
        SELECT review_status,promotion_gate_status,COUNT(*) AS n
        FROM vkpi_dealer_event_candidates
        WHERE organization_id=? AND candidate_type=?
        GROUP BY review_status,promotion_gate_status
        ORDER BY review_status,promotion_gate_status
        """,
        (org_id, candidate_type),
    ).fetchall()
    review_counts: Counter[str] = Counter()
    gate_counts: Counter[str] = Counter()
    total = 0
    for raw in rows:
        item = dict(raw)
        count = max(0, int(item.get("n") or 0))
        total += count
        review_counts[str(item.get("review_status") or "unknown")] += count
        gate_counts[str(item.get("promotion_gate_status") or "unknown")] += count
    evidence_row = get_conn().execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_candidate_field_evidence_links link
        JOIN vkpi_dealer_event_candidates candidate
          ON candidate.organization_id=link.organization_id
         AND candidate.id=link.candidate_id
        WHERE candidate.organization_id=? AND candidate.candidate_type=?
        """,
        (org_id, candidate_type),
    ).fetchone()
    linked_evidence = int(dict(evidence_row or {}).get("n") or 0)
    return {
        "status": "ready" if total else "empty",
        "organization_id": org_id,
        "candidate_type": candidate_type,
        "total": total,
        "review_status": dict(sorted(review_counts.items())),
        "promotion_gate_status": dict(sorted(gate_counts.items())),
        "linked_field_evidence": linked_evidence,
        "contract": {
            "read_only": True,
            "database_accessed": True,
            "network_accessed": False,
            "business_rows_written": 0,
        },
        "claim_status": CLAIM_STATUS,
        "automatic_promotion": False,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


__all__ = [
    "CANDIDATE_TABLE",
    "CANDIDATE_TYPES",
    "CandidateStagingSchemaUnavailable",
    "CandidateStagingStateConflict",
    "get_candidate",
    "link_field_evidence",
    "list_candidates",
    "preview_candidate",
    "record_manual_promotion_receipt",
    "review_candidate",
    "stage_candidate",
    "staging_summary",
]
