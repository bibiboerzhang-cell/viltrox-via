"""Durable, claim-bounded Dealer/Event source-passport service.

Migration 248 provides the persistence layer.  The service deliberately keeps
raw business/contact values out of the evidence table: callers submit a SHA-256
of the value they reviewed, plus the public source URL and review metadata.
Every response is ``descriptive_only`` and exposes no global-coverage claim.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.source_passport_core import (
    PUBLISHER_TIERS,
    STABLE_LOCATION_RE,
    STABLE_ORG_RE,
    canonical_json_sha256,
    freshness,
)
from app.domains.source_passport_urls import source_url_identity


PASSPORT_TABLE = "vkpi_source_passports"
FIELD_TABLE = "vkpi_source_field_evidence"
REVISION_TABLE = "vkpi_source_passport_revisions"
CLAIM_STATUS = "descriptive_only"
ENTITY_TYPES = frozenset(
    {"dealer_location", "event_source", "event_opportunity", "source_registry"}
)
VERIFICATION_STATUSES = frozenset(
    {"unknown", "observed", "verified", "rejected", "needs_review"}
)
VALUE_STATUSES = frozenset(
    {"unknown", "observed", "not_found", "unavailable", "conflict"}
)
IDENTITY_STATUSES = frozenset({"unknown", "unresolved", "exact", "conflict"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ENTITY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$")

FIELD_NAMES: dict[str, frozenset[str]] = {
    "dealer_location": frozenset(
        {
            "location.name",
            "location.address",
            "location.country_code",
            "location.postal_code",
            "contact.phone",
            "contact.email",
            "contact.store_hours",
            "contact.public_services",
            "social.instagram",
            "social.facebook",
            "social.youtube",
            "social.tiktok",
            "social.x",
            "viltrox.product_page",
            "activity.page",
        }
    ),
    "event_source": frozenset(
        {
            "publisher.name",
            "publisher.canonical_url",
            "publisher.country_code",
            "publisher.source_kind",
            "publisher.terms_robots_status",
        }
    ),
    "event_opportunity": frozenset(
        {
            "event.title",
            "event.start_date",
            "event.end_date",
            "event.timezone",
            "event.venue",
            "event.address",
            "event.country_code",
            "event.official_url",
            "event.registration_url",
            "event.status",
            "viltrox.presence",
        }
    ),
    "source_registry": frozenset(
        {
            "publisher.name",
            "publisher.canonical_url",
            "publisher.country_code",
            "publisher.source_kind",
            "publisher.terms_robots_status",
            "candidate.source_entity_key",
            "candidate.source_url",
            "candidate.location",
            "candidate.activity",
            "viltrox.product_page",
        }
    ),
}

_PASSPORT_INPUT_FIELDS = frozenset(
    {
        "entity_type",
        "dealer_id",
        "event_source_id",
        "event_opportunity_id",
        "registry_source_id",
        "stable_org_key",
        "exact_location_key",
        "publisher_name",
        "publisher_tier",
        "canonical_url",
        "identity_status",
        "verification_status",
        "verified_at",
        "stale_after_days",
    }
)
_FIELD_INPUT_FIELDS = frozenset(
    {
        "passport_id",
        "field_name",
        "value_sha256",
        "source_url",
        "publisher_tier",
        "evidence_scope",
        "value_status",
        "verification_status",
        "observed_at",
        "verified_at",
        "stale_after_days",
    }
)


class SourcePassportSchemaUnavailable(RuntimeError):
    """Migration 248 has not been applied or cannot be inspected safely."""


def _json_bind() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _now_sql() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _json_load(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _require_schema() -> None:
    try:
        ready = all(table_exists(name) for name in (PASSPORT_TABLE, FIELD_TABLE, REVISION_TABLE))
    except Exception as exc:
        raise SourcePassportSchemaUnavailable("source_passport_schema_unavailable") from exc
    if not ready:
        raise SourcePassportSchemaUnavailable("migration_248_pending")


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


def _stale_after_days(value: Any) -> int:
    parsed = 30 if value in (None, "") else _positive_int(value, "stale_after_days")
    if parsed > 3650:
        raise ValueError("stale_after_days must be <= 3650")
    return parsed


def _timestamp(value: Any, field: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_url(value: Any, *, required: bool) -> str:
    identity = source_url_identity(value)
    if identity["valid"]:
        return str(identity["canonical_url"])
    if required or value not in (None, ""):
        raise ValueError("source URL must be a credential-free public HTTPS URL")
    return ""


def _only_known_fields(payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ValueError(f"unsupported evidence fields: {', '.join(unknown)}")


def _publisher_tier(value: Any) -> str:
    tier = str(value or "unknown").strip()
    if tier not in PUBLISHER_TIERS:
        raise ValueError("unsupported publisher_tier")
    return tier


def _verification_status(value: Any) -> str:
    status = str(value or "unknown").strip().casefold()
    if status not in VERIFICATION_STATUSES:
        raise ValueError("unsupported verification_status")
    return status


def _entity_identity(
    payload: Mapping[str, Any],
) -> tuple[str, str, int | None, str | None, str | None, str | None]:
    entity_type = str(payload.get("entity_type") or "").strip()
    if entity_type not in ENTITY_TYPES:
        raise ValueError("unsupported entity_type")
    dealer_id: int | None = None
    event_source_id: str | None = None
    event_opportunity_id: str | None = None
    registry_source_id: str | None = None
    if entity_type == "dealer_location":
        dealer_id = _positive_int(payload.get("dealer_id"), "dealer_id")
        entity_key = f"dealer:{dealer_id}"
        if payload.get("event_source_id") not in (None, "") or payload.get(
            "event_opportunity_id"
        ) not in (None, "") or payload.get("registry_source_id") not in (None, ""):
            raise ValueError("dealer_location accepts only dealer_id")
    elif entity_type == "event_source":
        event_source_id = str(payload.get("event_source_id") or "").strip()
        entity_key = event_source_id
        if payload.get("dealer_id") not in (None, "") or payload.get(
            "event_opportunity_id"
        ) not in (None, "") or payload.get("registry_source_id") not in (None, ""):
            raise ValueError("event_source accepts only event_source_id")
    elif entity_type == "event_opportunity":
        event_opportunity_id = str(payload.get("event_opportunity_id") or "").strip()
        entity_key = event_opportunity_id
        if payload.get("dealer_id") not in (None, "") or payload.get(
            "event_source_id"
        ) not in (None, "") or payload.get("registry_source_id") not in (None, ""):
            raise ValueError("event_opportunity accepts only event_opportunity_id")
    else:
        registry_source_id = str(payload.get("registry_source_id") or "").strip()
        entity_key = registry_source_id
        if (
            payload.get("dealer_id") not in (None, "")
            or payload.get("event_source_id") not in (None, "")
            or payload.get("event_opportunity_id") not in (None, "")
        ):
            raise ValueError("source_registry accepts only registry_source_id")
    if not SAFE_ENTITY_KEY_RE.fullmatch(entity_key):
        raise ValueError("entity identity is missing or invalid")
    return (
        entity_type,
        entity_key,
        dealer_id,
        event_source_id,
        event_opportunity_id,
        registry_source_id,
    )


def _passport_id(organization_id: int, entity_type: str, entity_key: str) -> str:
    digest = canonical_json_sha256(
        {"organization_id": organization_id, "entity_type": entity_type, "entity_key": entity_key}
    )
    return f"spp_{digest[:32]}"


def _claim_boundaries() -> dict[str, bool]:
    return {
        "global_full_coverage_claim_allowed": False,
        "publisher_passport_proves_authorization": False,
        "product_page_proves_current_inventory": False,
        "field_evidence_proves_sales_or_roi": False,
        "activity_evidence_proves_attendance_or_local_impact": False,
    }


def _public_passport(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    verified_at = row.get("verified_at")
    stale_days = int(row.get("stale_after_days") or 30)
    current = freshness(
        verified_at,
        as_of=now or datetime.now(timezone.utc),
        stale_after_days=stale_days,
    )
    return {
        "id": row.get("id"),
        "organization_id": row.get("organization_id"),
        "entity_type": row.get("entity_type"),
        "entity_key": row.get("entity_key"),
        "dealer_id": row.get("dealer_id"),
        "event_source_id": row.get("event_source_id"),
        "event_opportunity_id": row.get("event_opportunity_id"),
        "registry_source_id": row.get("registry_source_id"),
        "stable_org_key": row.get("stable_org_key") or None,
        "exact_location_key": row.get("exact_location_key") or None,
        "publisher_name": row.get("publisher_name") or None,
        "publisher_tier": row.get("publisher_tier") or "unknown",
        "canonical_url": row.get("canonical_url") or None,
        "identity_status": row.get("identity_status") or "unknown",
        "verification_status": row.get("verification_status") or "unknown",
        "freshness": current,
        "freshness_status_at_write": row.get("freshness_status_at_write") or "unavailable",
        "verified_at": str(verified_at) if verified_at not in (None, "") else None,
        "stale_after_days": stale_days,
        "reviewer_staff_id": row.get("reviewer_staff_id"),
        "claim_status": CLAIM_STATUS,
        "record_sha256": row.get("record_sha256"),
        "revision_no": row.get("revision_no"),
        "created_at": str(row.get("created_at")) if row.get("created_at") is not None else None,
        "updated_at": str(row.get("updated_at")) if row.get("updated_at") is not None else None,
    }


def build_passport_record(
    payload: Mapping[str, Any],
    *,
    organization_id: Any,
    reviewer_staff_id: Any,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Validate a passport request and return its server-owned durable record."""
    if not isinstance(payload, Mapping):
        raise ValueError("passport body must be an object")
    _only_known_fields(payload, _PASSPORT_INPUT_FIELDS)
    org_id = _positive_int(organization_id, "organization_id")
    reviewer_id = _positive_int(reviewer_staff_id, "reviewer_staff_id")
    (
        entity_type,
        entity_key,
        dealer_id,
        source_id,
        opportunity_id,
        registry_source_id,
    ) = _entity_identity(payload)
    stable_org_key = str(payload.get("stable_org_key") or "").strip()
    exact_location_key = str(payload.get("exact_location_key") or "").strip()
    if stable_org_key and not STABLE_ORG_RE.fullmatch(stable_org_key):
        raise ValueError("stable_org_key is invalid")
    if exact_location_key and not STABLE_LOCATION_RE.fullmatch(exact_location_key):
        raise ValueError("exact_location_key is invalid")
    if entity_type != "dealer_location" and (stable_org_key or exact_location_key):
        raise ValueError("stable Dealer keys are valid only for dealer_location")

    verification = _verification_status(payload.get("verification_status"))
    requested_identity = str(payload.get("identity_status") or "unknown").strip().casefold()
    if requested_identity not in IDENTITY_STATUSES:
        raise ValueError("unsupported identity_status")
    tier = _publisher_tier(payload.get("publisher_tier"))
    verified_at = _timestamp(payload.get("verified_at"), "verified_at")
    stale_days = _stale_after_days(payload.get("stale_after_days"))
    checked = freshness(
        verified_at,
        as_of=as_of or datetime.now(timezone.utc),
        stale_after_days=stale_days,
    )
    canonical_url = _canonical_url(
        payload.get("canonical_url"), required=verification == "verified"
    )

    if verification == "verified":
        if tier == "unknown":
            raise ValueError("verified passport requires a known publisher_tier")
        if checked["status"] != "fresh":
            raise ValueError("verified passport requires a current verified_at")
        if requested_identity != "exact":
            raise ValueError("verified passport requires identity_status=exact")
        if entity_type == "dealer_location" and not (stable_org_key and exact_location_key):
            raise ValueError("verified Dealer passport requires exact stable organization/location keys")
    elif requested_identity == "exact":
        # Exact identity is still reviewable evidence, even when only observed,
        # but it still needs a current, known publisher source.  ``exact`` must
        # never become a label that can be asserted with an empty URL.
        if tier == "unknown" or not canonical_url or checked["status"] != "fresh":
            raise ValueError("exact identity requires a current known publisher source")
        if entity_type == "dealer_location" and not (stable_org_key and exact_location_key):
            raise ValueError("exact Dealer identity requires stable organization/location keys")

    identity_evidence = {
        "status": verification,
        "value_status": "observed" if verification in {"observed", "verified"} else "unknown",
        "evidence_scope": "publisher_identity",
        "publisher_tier": tier,
        "source_url": canonical_url or None,
        "verified_at": _timestamp_text(verified_at),
        "reviewer_id": f"staff_{reviewer_id}",
    }
    record = {
        "organization_id": org_id,
        "id": _passport_id(org_id, entity_type, entity_key),
        "entity_type": entity_type,
        "entity_key": entity_key,
        "dealer_id": dealer_id,
        "event_source_id": source_id,
        "event_opportunity_id": opportunity_id,
        "registry_source_id": registry_source_id,
        "stable_org_key": stable_org_key,
        "exact_location_key": exact_location_key,
        "publisher_name": str(payload.get("publisher_name") or "").strip(),
        "publisher_tier": tier,
        "canonical_url": canonical_url,
        "identity_status": requested_identity,
        "verification_status": verification,
        "freshness_status_at_write": str(checked["status"]),
        "verified_at": _timestamp_text(verified_at),
        "stale_after_days": stale_days,
        "reviewer_staff_id": reviewer_id,
        "claim_status": CLAIM_STATUS,
        "identity_evidence_json": identity_evidence,
    }
    record["record_sha256"] = canonical_json_sha256(record)
    return record


def _snapshot_from_existing(row: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "organization_id",
        "id",
        "entity_type",
        "entity_key",
        "dealer_id",
        "event_source_id",
        "event_opportunity_id",
        "registry_source_id",
        "stable_org_key",
        "exact_location_key",
        "publisher_name",
        "publisher_tier",
        "canonical_url",
        "identity_status",
        "verification_status",
        "freshness_status_at_write",
        "verified_at",
        "stale_after_days",
        "reviewer_staff_id",
        "claim_status",
        "identity_evidence_json",
        "record_sha256",
    )
    out = {key: row.get(key) for key in keys}
    out["identity_evidence_json"] = _json_load(out.get("identity_evidence_json"), {})
    if out.get("verified_at") not in (None, ""):
        out["verified_at"] = str(out["verified_at"])
    return out


def save_passport(
    payload: Mapping[str, Any],
    *,
    organization_id: Any,
    reviewer_staff_id: Any,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Atomically upsert one passport and append a revision snapshot."""
    _require_schema()
    record = build_passport_record(
        payload,
        organization_id=organization_id,
        reviewer_staff_id=reviewer_staff_id,
        as_of=as_of,
    )
    conn = get_conn()
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    existing_row = conn.execute(
        f"SELECT * FROM {PASSPORT_TABLE} WHERE organization_id=? AND id=?{lock}",
        (record["organization_id"], record["id"]),
    ).fetchone()
    existing = _row(existing_row)
    previous = _snapshot_from_existing(existing) if existing else {}
    revision_no = int(existing.get("revision_no") or 0) + 1
    changed_fields = sorted(
        key for key, value in record.items() if previous.get(key) != value
    )
    snapshot = {**record, "revision_no": revision_no}
    snapshot_sha = canonical_json_sha256(snapshot)
    json_bind = _json_bind()
    try:
        if existing:
            conn.execute(
                f"""
                UPDATE {PASSPORT_TABLE}
                SET stable_org_key=?, exact_location_key=?, publisher_name=?,
                    publisher_tier=?, canonical_url=?, identity_status=?,
                    verification_status=?, freshness_status_at_write=?, verified_at=?,
                    stale_after_days=?, reviewer_staff_id=?, claim_status=?,
                    identity_evidence_json={json_bind}, record_sha256=?, revision_no=?,
                    updated_at={_now_sql()}
                WHERE organization_id=? AND id=?
                """,
                (
                    record["stable_org_key"], record["exact_location_key"],
                    record["publisher_name"], record["publisher_tier"],
                    record["canonical_url"], record["identity_status"],
                    record["verification_status"], record["freshness_status_at_write"],
                    record["verified_at"], record["stale_after_days"],
                    record["reviewer_staff_id"], CLAIM_STATUS,
                    json.dumps(record["identity_evidence_json"], ensure_ascii=False, sort_keys=True),
                    record["record_sha256"], revision_no,
                    record["organization_id"], record["id"],
                ),
            )
        else:
            conn.execute(
                f"""
                INSERT INTO {PASSPORT_TABLE}
                  (organization_id,id,entity_type,entity_key,dealer_id,event_source_id,
                   event_opportunity_id,registry_source_id,stable_org_key,exact_location_key,publisher_name,
                   publisher_tier,canonical_url,identity_status,verification_status,
                   freshness_status_at_write,verified_at,stale_after_days,reviewer_staff_id,
                   claim_status,identity_evidence_json,record_sha256,revision_no,
                   created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,{json_bind},?,?,
                        {_now_sql()},{_now_sql()})
                """,
                (
                    record["organization_id"], record["id"], record["entity_type"],
                    record["entity_key"], record["dealer_id"], record["event_source_id"],
                    record["event_opportunity_id"], record["registry_source_id"], record["stable_org_key"],
                    record["exact_location_key"], record["publisher_name"],
                    record["publisher_tier"], record["canonical_url"],
                    record["identity_status"], record["verification_status"],
                    record["freshness_status_at_write"], record["verified_at"],
                    record["stale_after_days"], record["reviewer_staff_id"], CLAIM_STATUS,
                    json.dumps(record["identity_evidence_json"], ensure_ascii=False, sort_keys=True),
                    record["record_sha256"], revision_no,
                ),
            )
        conn.execute(
            f"""
            INSERT INTO {REVISION_TABLE}
              (organization_id,passport_id,revision_no,snapshot_sha256,snapshot_json,
               changed_fields,reviewer_staff_id,created_at)
            VALUES (?,?,?,?,{json_bind},{json_bind},?,{_now_sql()})
            """,
            (
                record["organization_id"], record["id"], revision_no, snapshot_sha,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                json.dumps(changed_fields, ensure_ascii=False), record["reviewer_staff_id"],
            ),
        )
        conn.commit()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        raise
    return {
        "ok": True,
        "created": not bool(existing),
        "passport": _public_passport(snapshot, now=as_of),
        "revision": {
            "revision_no": revision_no,
            "snapshot_sha256": snapshot_sha,
            "changed_fields": changed_fields,
        },
        "claim_status": CLAIM_STATUS,
        "claim_boundaries": _claim_boundaries(),
    }


def build_field_evidence_record(
    payload: Mapping[str, Any],
    *,
    organization_id: Any,
    reviewer_staff_id: Any,
    entity_type: str,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("field evidence body must be an object")
    _only_known_fields(payload, _FIELD_INPUT_FIELDS)
    org_id = _positive_int(organization_id, "organization_id")
    reviewer_id = _positive_int(reviewer_staff_id, "reviewer_staff_id")
    passport_id = str(payload.get("passport_id") or "").strip()
    if not re.fullmatch(r"spp_[0-9a-f]{32}", passport_id):
        raise ValueError("passport_id is invalid")
    if entity_type not in ENTITY_TYPES:
        raise ValueError("unsupported entity_type")
    field_name = str(payload.get("field_name") or "").strip().casefold()
    if field_name not in FIELD_NAMES[entity_type]:
        raise ValueError("field_name is not allowed for this entity_type")
    value_status = str(payload.get("value_status") or "unknown").strip().casefold()
    if value_status not in VALUE_STATUSES:
        raise ValueError("unsupported value_status")
    value_sha = str(payload.get("value_sha256") or "").strip().casefold()
    if value_sha and not SHA256_RE.fullmatch(value_sha):
        raise ValueError("value_sha256 must be a lowercase SHA-256")
    if value_status == "observed" and not value_sha:
        raise ValueError("observed field evidence requires value_sha256")
    verification = _verification_status(payload.get("verification_status"))
    tier = _publisher_tier(payload.get("publisher_tier"))
    source_url = _canonical_url(
        payload.get("source_url"), required=verification == "verified"
    )
    observed_at = _timestamp(payload.get("observed_at"), "observed_at")
    verified_at = _timestamp(payload.get("verified_at"), "verified_at")
    stale_days = _stale_after_days(payload.get("stale_after_days"))
    checked = freshness(
        verified_at or observed_at,
        as_of=as_of or datetime.now(timezone.utc),
        stale_after_days=stale_days,
    )
    if verification == "verified":
        if value_status not in {"observed", "not_found"}:
            raise ValueError("verified evidence requires observed or not_found value_status")
        if tier == "unknown":
            raise ValueError("verified evidence requires a known publisher_tier")
        if verified_at is None or checked["status"] != "fresh":
            raise ValueError("verified evidence requires a current verified_at")
    evidence_scope = str(payload.get("evidence_scope") or "").strip()
    expected_scope = f"{entity_type}_field"
    if evidence_scope != expected_scope:
        raise ValueError(f"evidence_scope must equal {expected_scope}")
    record = {
        "organization_id": org_id,
        "passport_id": passport_id,
        "field_name": field_name,
        "value_sha256": value_sha,
        "source_url": source_url,
        "publisher_tier": tier,
        "evidence_scope": evidence_scope,
        "value_status": value_status,
        "verification_status": verification,
        "freshness_status_at_write": str(checked["status"]),
        "observed_at": _timestamp_text(observed_at),
        "verified_at": _timestamp_text(verified_at),
        "stale_after_days": stale_days,
        "reviewer_staff_id": reviewer_id,
        "claim_status": CLAIM_STATUS,
    }
    record["evidence_json"] = {
        "field_name": field_name,
        "value_status": value_status,
        "value_sha256": value_sha or None,
        "source_url": source_url or None,
        "publisher_tier": tier,
        "evidence_scope": evidence_scope,
        "verification_status": verification,
        "observed_at": record["observed_at"],
        "verified_at": record["verified_at"],
        "reviewer_id": f"staff_{reviewer_id}",
    }
    record["record_sha256"] = canonical_json_sha256(record)
    record["id"] = f"spe_{record['record_sha256'][:32]}"
    return record


def append_field_evidence(
    payload: Mapping[str, Any],
    *,
    organization_id: Any,
    reviewer_staff_id: Any,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Append an immutable field evidence row; identical evidence is idempotent."""
    _require_schema()
    org_id = _positive_int(organization_id, "organization_id")
    passport_id = str(payload.get("passport_id") or "").strip()
    conn = get_conn()
    passport_row = conn.execute(
        f"SELECT id,entity_type FROM {PASSPORT_TABLE} WHERE organization_id=? AND id=?",
        (org_id, passport_id),
    ).fetchone()
    if passport_row is None:
        raise LookupError("source passport not found")
    passport = _row(passport_row)
    record = build_field_evidence_record(
        payload,
        organization_id=org_id,
        reviewer_staff_id=reviewer_staff_id,
        entity_type=str(passport.get("entity_type") or ""),
        as_of=as_of,
    )
    json_bind = _json_bind()
    try:
        cursor = conn.execute(
            f"""
            INSERT INTO {FIELD_TABLE}
              (organization_id,id,passport_id,field_name,value_sha256,source_url,
               publisher_tier,evidence_scope,value_status,verification_status,
               freshness_status_at_write,observed_at,verified_at,stale_after_days,
               reviewer_staff_id,claim_status,evidence_json,record_sha256,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,{json_bind},?,{_now_sql()})
            ON CONFLICT (organization_id,id) DO NOTHING
            """,
            (
                record["organization_id"], record["id"], record["passport_id"],
                record["field_name"], record["value_sha256"], record["source_url"],
                record["publisher_tier"], record["evidence_scope"], record["value_status"],
                record["verification_status"], record["freshness_status_at_write"],
                record["observed_at"], record["verified_at"], record["stale_after_days"],
                record["reviewer_staff_id"], CLAIM_STATUS,
                json.dumps(record["evidence_json"], ensure_ascii=False, sort_keys=True),
                record["record_sha256"],
            ),
        )
        conn.commit()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        raise
    inserted = getattr(cursor, "rowcount", 1) != 0
    public = {key: value for key, value in record.items() if key != "evidence_json"}
    public["claim_status"] = CLAIM_STATUS
    return {
        "ok": True,
        "inserted": inserted,
        "evidence": public,
        "claim_status": CLAIM_STATUS,
        "claim_boundaries": _claim_boundaries(),
    }


def list_passports(
    *,
    organization_id: Any,
    entity_type: str | None = None,
    entity_key: str | None = None,
    offset: int = 0,
    limit: int = 100,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Read current passport state, recomputing freshness at request time."""
    _require_schema()
    org_id = _positive_int(organization_id, "organization_id")
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(int(limit or 100), 500))
    clauses = ["organization_id=?"]
    params: list[Any] = [org_id]
    if entity_type:
        normalized_type = str(entity_type).strip()
        if normalized_type not in ENTITY_TYPES:
            raise ValueError("unsupported entity_type")
        clauses.append("entity_type=?")
        params.append(normalized_type)
    if entity_key:
        normalized_key = str(entity_key).strip()
        if not SAFE_ENTITY_KEY_RE.fullmatch(normalized_key):
            raise ValueError("entity_key is invalid")
        clauses.append("entity_key=?")
        params.append(normalized_key)
    params.extend([safe_limit, safe_offset])
    rows = get_conn().execute(
        f"""
        SELECT * FROM {PASSPORT_TABLE}
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, id
        LIMIT ? OFFSET ?
        """,
        params,
    ).fetchall()
    items = [_public_passport(_row(item), now=as_of) for item in rows]
    return {
        "items": items,
        "pagination": {"offset": safe_offset, "limit": safe_limit, "returned": len(items)},
        "claim_status": CLAIM_STATUS,
        "global_coverage": {
            "denominator": None,
            "rate": None,
            "status": "unavailable",
            "reason": "global_source_universe_not_registered",
        },
        "claim_boundaries": _claim_boundaries(),
    }


def list_field_evidence(
    passport_id: str,
    *,
    organization_id: Any,
    offset: int = 0,
    limit: int = 100,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    _require_schema()
    org_id = _positive_int(organization_id, "organization_id")
    safe_id = str(passport_id or "").strip()
    if not re.fullmatch(r"spp_[0-9a-f]{32}", safe_id):
        raise ValueError("passport_id is invalid")
    safe_offset = max(0, int(offset or 0))
    safe_limit = max(1, min(int(limit or 100), 500))
    rows = get_conn().execute(
        f"""
        SELECT id,passport_id,field_name,value_sha256,source_url,publisher_tier,
               evidence_scope,value_status,verification_status,freshness_status_at_write,
               observed_at,verified_at,stale_after_days,reviewer_staff_id,claim_status,
               record_sha256,created_at
        FROM {FIELD_TABLE}
        WHERE organization_id=? AND passport_id=?
        ORDER BY created_at DESC,id
        LIMIT ? OFFSET ?
        """,
        (org_id, safe_id, safe_limit, safe_offset),
    ).fetchall()
    now = as_of or datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for raw in rows:
        item = _row(raw)
        item["claim_status"] = CLAIM_STATUS
        item["freshness"] = freshness(
            item.get("verified_at") or item.get("observed_at"),
            as_of=now,
            stale_after_days=int(item.get("stale_after_days") or 30),
        )
        for key in ("observed_at", "verified_at", "created_at"):
            item[key] = str(item[key]) if item.get(key) is not None else None
        items.append(item)
    return {
        "passport_id": safe_id,
        "items": items,
        "pagination": {"offset": safe_offset, "limit": safe_limit, "returned": len(items)},
        "claim_status": CLAIM_STATUS,
        "claim_boundaries": _claim_boundaries(),
    }


__all__ = [
    "CLAIM_STATUS",
    "ENTITY_TYPES",
    "FIELD_NAMES",
    "SourcePassportSchemaUnavailable",
    "append_field_evidence",
    "build_field_evidence_record",
    "build_passport_record",
    "list_field_evidence",
    "list_passports",
    "save_passport",
]
