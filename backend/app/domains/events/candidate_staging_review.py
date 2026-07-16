"""Evidence, review, and immutable receipt transitions for candidate staging.

The storage/validation primitives live in :mod:`candidate_staging`.  Keeping
human transitions in this module preserves the repository's 1000-line source
guard while retaining one public candidate-staging API.  No function here
creates or mutates a Dealer/Event business row.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.domains.events import candidate_staging as core
from app.domains.source_passport_urls import source_url_identity


CANDIDATE_TABLE = core.CANDIDATE_TABLE
EVIDENCE_LINK_TABLE = core.EVIDENCE_LINK_TABLE
PASSPORT_TABLE = core.PASSPORT_TABLE
FIELD_EVIDENCE_TABLE = core.FIELD_EVIDENCE_TABLE
CLAIM_STATUS = core.CLAIM_STATUS
CANDIDATE_TYPES = core.CANDIDATE_TYPES
EVIDENCE_ROLES = core.EVIDENCE_ROLES
CandidateStagingStateConflict = core.CandidateStagingStateConflict


def _require_schema(*, include_evidence: bool = False) -> None:
    core._require_schema(include_evidence=include_evidence)


def _positive_int(value: Any, field: str) -> int:
    return core._positive_int(value, field)


def _candidate_id(value: Any) -> str:
    return core._candidate_id(value)


def _staff_id(value: Any) -> int:
    return core._staff_id(value)


def _row(value: Any) -> dict[str, Any]:
    return core._row(value)


def _locked_candidate(
    conn: Any, *, organization_id: int, candidate_id: str
) -> dict[str, Any]:
    return core._locked_candidate(
        conn, organization_id=organization_id, candidate_id=candidate_id
    )


def _expected_evidence_sha(
    candidate: Mapping[str, Any], evidence_role: str
) -> str | None:
    return core._expected_evidence_sha(candidate, evidence_role)


def _fresh_verified(row: Mapping[str, Any], *, as_of: datetime) -> bool:
    return core._fresh_verified(row, as_of=as_of)


def _now_sql() -> str:
    return core._now_sql()


def _commit(conn: Any) -> None:
    core._commit(conn)


def _rollback(conn: Any) -> None:
    core._rollback(conn)


def _is_constraint_conflict(exc: Exception) -> bool:
    return core._is_constraint_conflict(exc)


def _public_candidate(
    raw: Mapping[str, Any], *, include_payload: bool
) -> dict[str, Any]:
    return core._public_candidate(raw, include_payload=include_payload)


def link_field_evidence(
    candidate_id: Any,
    *,
    organization_id: Any,
    candidate_type: str,
    field_evidence_id: Any,
    evidence_role: Any,
    reviewer_staff_id: Any,
) -> dict[str, Any]:
    """Link one exact, current, already-verified field-evidence row."""
    _require_schema(include_evidence=True)
    org_id = _positive_int(organization_id, "organization_id")
    cid = _candidate_id(candidate_id)
    staff_id = _staff_id(reviewer_staff_id)
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    evidence_id = str(field_evidence_id or "").strip()
    if not core._EVIDENCE_ID_RE.fullmatch(evidence_id):
        raise ValueError("field_evidence_id is invalid")
    role = str(evidence_role or "").strip().casefold()
    if role not in EVIDENCE_ROLES:
        raise ValueError("unsupported evidence_role")

    conn = core.get_conn()
    try:
        candidate = _locked_candidate(
            conn, organization_id=org_id, candidate_id=cid
        )
        if candidate.get("candidate_type") != candidate_type:
            raise LookupError("candidate not found")
        if candidate.get("promotion_gate_status") == "manually_promoted":
            raise CandidateStagingStateConflict("manual_promotion_receipt_is_immutable")
        evidence_row = conn.execute(
            f"""
            SELECT evidence.*,passport.entity_type AS passport_entity_type,
                   passport.registry_source_id AS passport_registry_source_id,
                   passport.identity_status AS passport_identity_status,
                   passport.verification_status AS passport_verification_status,
                   passport.freshness_status_at_write AS passport_freshness_status_at_write,
                   passport.verified_at AS passport_verified_at,
                   passport.stale_after_days AS passport_stale_after_days,
                   passport.reviewer_staff_id AS passport_reviewer_staff_id,
                   passport.claim_status AS passport_claim_status
            FROM {FIELD_EVIDENCE_TABLE} evidence
            JOIN {PASSPORT_TABLE} passport
              ON passport.organization_id=evidence.organization_id
             AND passport.id=evidence.passport_id
            WHERE evidence.organization_id=? AND evidence.id=?
            """,
            (org_id, evidence_id),
        ).fetchone()
        if evidence_row is None:
            raise LookupError("verified field evidence not found")
        evidence = _row(evidence_row)
        if evidence.get("field_name") not in core._ROLE_FIELD_NAMES[role]:
            raise CandidateStagingStateConflict("evidence_field_role_mismatch")
        expected_value_sha = _expected_evidence_sha(candidate, role)
        if not expected_value_sha or evidence.get("value_sha256") != expected_value_sha:
            raise CandidateStagingStateConflict("evidence_candidate_value_mismatch")
        if evidence.get("evidence_scope") != "source_registry_field":
            raise CandidateStagingStateConflict("evidence_scope_mismatch")
        if evidence.get("passport_entity_type") != "source_registry" or evidence.get(
            "passport_registry_source_id"
        ) != candidate.get("source_registry_id"):
            raise CandidateStagingStateConflict("evidence_source_registry_mismatch")
        candidate_url = source_url_identity(candidate.get("source_url"))
        evidence_url = source_url_identity(evidence.get("source_url"))
        if (
            not candidate_url["valid"]
            or not evidence_url["valid"]
            or candidate_url["canonical_url"] != evidence_url["canonical_url"]
        ):
            raise CandidateStagingStateConflict("evidence_candidate_url_mismatch")

        as_of = datetime.now(timezone.utc)
        passport_view = {
            "verification_status": evidence.get("passport_verification_status"),
            "freshness_status_at_write": evidence.get(
                "passport_freshness_status_at_write"
            ),
            "verified_at": evidence.get("passport_verified_at"),
            "stale_after_days": evidence.get("passport_stale_after_days"),
            "reviewer_staff_id": evidence.get("passport_reviewer_staff_id"),
            "claim_status": evidence.get("passport_claim_status"),
        }
        if evidence.get("passport_identity_status") != "exact" or not _fresh_verified(
            passport_view, as_of=as_of
        ):
            raise CandidateStagingStateConflict(
                "current_verified_source_registry_passport_required"
            )
        if evidence.get("value_status") != "observed" or not _fresh_verified(
            evidence, as_of=as_of
        ):
            raise CandidateStagingStateConflict(
                "current_verified_observed_field_evidence_required"
            )
        existing_passport = str(candidate.get("source_passport_id") or "")
        evidence_passport = str(evidence.get("passport_id") or "")
        if existing_passport and existing_passport != evidence_passport:
            raise CandidateStagingStateConflict("candidate_source_passport_mismatch")
        if not existing_passport:
            conn.execute(
                f"UPDATE {CANDIDATE_TABLE} SET source_passport_id=?,"
                f"updated_at={_now_sql()} WHERE organization_id=? AND id=?",
                (evidence_passport, org_id, cid),
            )
        cursor = conn.execute(
            f"""
            INSERT INTO {EVIDENCE_LINK_TABLE}
              (organization_id,candidate_id,field_evidence_id,evidence_role,
               added_by_staff_id,created_at)
            VALUES (?,?,?,?,?,{_now_sql()})
            ON CONFLICT (organization_id,candidate_id,field_evidence_id,evidence_role)
            DO NOTHING
            """,
            (org_id, cid, evidence_id, role, staff_id),
        )
        _commit(conn)
    except (CandidateStagingStateConflict, LookupError, ValueError):
        _rollback(conn)
        raise
    except Exception as exc:
        _rollback(conn)
        if _is_constraint_conflict(exc):
            raise CandidateStagingStateConflict(
                "candidate_evidence_link_rejected_by_database"
            ) from exc
        raise
    return {
        "ok": True,
        "inserted": getattr(cursor, "rowcount", 1) != 0,
        "candidate_id": cid,
        "field_evidence_id": evidence_id,
        "evidence_role": role,
        "source_passport_id": evidence_passport,
        "verified_value_sha256": expected_value_sha,
        "promotion_gate_status": str(candidate.get("promotion_gate_status") or "blocked"),
        "automatic_promotion": False,
        "business_rows_written": 0,
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


def _approval_readiness(
    conn: Any,
    *,
    organization_id: int,
    candidate: Mapping[str, Any],
) -> None:
    if not candidate.get("source_passport_id"):
        raise CandidateStagingStateConflict("verified_source_registry_passport_required")
    if candidate.get("candidate_type") == "dealer_location" and not (
        candidate.get("stable_org_key") and candidate.get("stable_location_key")
    ):
        raise CandidateStagingStateConflict("exact_stable_dealer_location_required")
    required_role = (
        "location"
        if candidate.get("candidate_type") == "dealer_location"
        else "activity"
    )
    evidence = conn.execute(
        f"""
        SELECT evidence.*,passport.entity_type AS passport_entity_type,
               passport.registry_source_id AS passport_registry_source_id,
               passport.identity_status AS passport_identity_status,
               passport.verification_status AS passport_verification_status,
               passport.freshness_status_at_write AS passport_freshness_status_at_write,
               passport.verified_at AS passport_verified_at,
               passport.stale_after_days AS passport_stale_after_days,
               passport.reviewer_staff_id AS passport_reviewer_staff_id,
               passport.claim_status AS passport_claim_status
        FROM {EVIDENCE_LINK_TABLE} link
        JOIN {FIELD_EVIDENCE_TABLE} evidence
          ON evidence.organization_id=link.organization_id
         AND evidence.id=link.field_evidence_id
        JOIN {PASSPORT_TABLE} passport
          ON passport.organization_id=evidence.organization_id
         AND passport.id=evidence.passport_id
        WHERE link.organization_id=? AND link.candidate_id=?
          AND link.evidence_role=? AND evidence.passport_id=?
        ORDER BY evidence.verified_at DESC
        LIMIT 1
        """,
        (
            organization_id,
            candidate["id"],
            required_role,
            candidate["source_passport_id"],
        ),
    ).fetchone()
    if evidence is None:
        raise CandidateStagingStateConflict("required_verified_field_evidence_missing")
    item = _row(evidence)
    as_of = datetime.now(timezone.utc)
    candidate_url = source_url_identity(candidate.get("source_url"))
    evidence_url = source_url_identity(item.get("source_url"))
    passport_view = {
        "verification_status": item.get("passport_verification_status"),
        "freshness_status_at_write": item.get("passport_freshness_status_at_write"),
        "verified_at": item.get("passport_verified_at"),
        "stale_after_days": item.get("passport_stale_after_days"),
        "reviewer_staff_id": item.get("passport_reviewer_staff_id"),
        "claim_status": item.get("passport_claim_status"),
    }
    expected_fields = core._ROLE_FIELD_NAMES[required_role]
    expected_value_sha = _expected_evidence_sha(candidate, required_role)
    if (
        item.get("field_name") not in expected_fields
        or not expected_value_sha
        or item.get("value_sha256") != expected_value_sha
        or item.get("evidence_scope") != "source_registry_field"
        or item.get("value_status") != "observed"
        or item.get("passport_entity_type") != "source_registry"
        or item.get("passport_registry_source_id")
        != candidate.get("source_registry_id")
        or item.get("passport_identity_status") != "exact"
        or not candidate_url["valid"]
        or not evidence_url["valid"]
        or candidate_url["canonical_url"] != evidence_url["canonical_url"]
        or not _fresh_verified(passport_view, as_of=as_of)
        or not _fresh_verified(item, as_of=as_of)
    ):
        raise CandidateStagingStateConflict("current_verified_evidence_required")


def review_candidate(
    candidate_id: Any,
    *,
    organization_id: Any,
    candidate_type: str,
    decision: Any,
    reviewer_staff_id: Any,
) -> dict[str, Any]:
    """Explicitly approve or reject one candidate; never promote it."""
    _require_schema(include_evidence=True)
    org_id = _positive_int(organization_id, "organization_id")
    cid = _candidate_id(candidate_id)
    staff_id = _staff_id(reviewer_staff_id)
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    requested = str(decision or "").strip().casefold()
    if requested not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    conn = core.get_conn()
    try:
        candidate = _locked_candidate(
            conn, organization_id=org_id, candidate_id=cid
        )
        if candidate.get("candidate_type") != candidate_type:
            raise LookupError("candidate not found")
        if candidate.get("promotion_gate_status") == "manually_promoted":
            raise CandidateStagingStateConflict("manual_promotion_receipt_is_immutable")
        if requested == "approved":
            _approval_readiness(
                conn, organization_id=org_id, candidate=candidate
            )
            gate = "eligible_for_manual_promotion"
        else:
            gate = "blocked"
        conn.execute(
            f"""
            UPDATE {CANDIDATE_TABLE}
            SET review_status=?,reviewer_staff_id=?,reviewed_at={_now_sql()},
                promotion_gate_status=?,promotion_target_type='',
                promotion_target_id='',promotion_reviewer_staff_id=NULL,
                promoted_at=NULL,updated_at={_now_sql()}
            WHERE organization_id=? AND id=?
            """,
            (requested, staff_id, gate, org_id, cid),
        )
        stored = _locked_candidate(conn, organization_id=org_id, candidate_id=cid)
        _commit(conn)
    except (CandidateStagingStateConflict, LookupError, ValueError):
        _rollback(conn)
        raise
    except Exception as exc:
        _rollback(conn)
        if _is_constraint_conflict(exc):
            raise CandidateStagingStateConflict(
                "candidate_review_rejected_by_database_trigger"
            ) from exc
        raise
    return {
        "ok": True,
        "candidate": _public_candidate(stored, include_payload=False),
        "automatic_promotion": False,
        "business_rows_written": 0,
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


def record_manual_promotion_receipt(
    candidate_id: Any,
    *,
    organization_id: Any,
    candidate_type: str,
    target_id: Any,
    reviewer_staff_id: Any,
) -> dict[str, Any]:
    """Record an immutable link to an exact, already-existing business target."""
    _require_schema(include_evidence=True)
    org_id = _positive_int(organization_id, "organization_id")
    cid = _candidate_id(candidate_id)
    staff_id = _staff_id(reviewer_staff_id)
    if candidate_type not in CANDIDATE_TYPES:
        raise ValueError("unsupported candidate_type")
    target = str(target_id or "").strip()
    if not target or len(target) > 160:
        raise ValueError("target_id is invalid")
    conn = core.get_conn()
    try:
        candidate = _locked_candidate(
            conn, organization_id=org_id, candidate_id=cid
        )
        if candidate.get("candidate_type") != candidate_type:
            raise LookupError("candidate not found")
        if candidate.get("promotion_gate_status") == "manually_promoted":
            if (
                str(candidate.get("promotion_target_type") or "") == candidate_type
                and str(candidate.get("promotion_target_id") or "") == target
            ):
                _commit(conn)
                return {
                    "ok": True,
                    "idempotent": True,
                    "candidate": _public_candidate(candidate, include_payload=False),
                    "automatic_promotion": False,
                    "business_rows_written": 0,
                    "claim_status": CLAIM_STATUS,
                    "full_us_coverage": False,
                    "global_denominator": None,
                    "global_coverage_rate": None,
                }
            raise CandidateStagingStateConflict("manual_promotion_receipt_is_immutable")
        if candidate.get("review_status") != "approved" or candidate.get(
            "promotion_gate_status"
        ) != "eligible_for_manual_promotion":
            raise CandidateStagingStateConflict(
                "candidate_is_not_eligible_for_manual_promotion"
            )
        _approval_readiness(conn, organization_id=org_id, candidate=candidate)

        if candidate_type == "dealer_location":
            if not re.fullmatch(r"[1-9][0-9]*", target):
                raise ValueError("Dealer target_id must be a positive integer")
            exact_target = conn.execute(
                """
                SELECT dealer.id
                FROM vkpi_dealers dealer
                JOIN vkpi_dealer_identity_aliases alias
                  ON alias.dealer_id=dealer.id
                 AND alias.organization_id=?
                 AND alias.stable_location_key=?
                 AND alias.verified_at IS NOT NULL
                WHERE dealer.id=?
                """,
                (org_id, candidate.get("stable_location_key"), int(target)),
            ).fetchone()
        else:
            exact_target = conn.execute(
                """
                SELECT id FROM vkpi_event_opportunities
                WHERE organization_id=? AND id=? AND official_url=?
                  AND verification_status='verified'
                """,
                (org_id, target, candidate.get("source_url")),
            ).fetchone()
        if exact_target is None:
            raise CandidateStagingStateConflict(
                "exact_existing_business_target_required"
            )
        conn.execute(
            f"""
            UPDATE {CANDIDATE_TABLE}
            SET promotion_gate_status='manually_promoted',
                promotion_target_type=?,promotion_target_id=?,
                promotion_reviewer_staff_id=?,promoted_at={_now_sql()},
                updated_at={_now_sql()}
            WHERE organization_id=? AND id=?
            """,
            (candidate_type, target, staff_id, org_id, cid),
        )
        stored = _locked_candidate(conn, organization_id=org_id, candidate_id=cid)
        _commit(conn)
    except (CandidateStagingStateConflict, LookupError, ValueError):
        _rollback(conn)
        raise
    except Exception as exc:
        _rollback(conn)
        if _is_constraint_conflict(exc):
            raise CandidateStagingStateConflict(
                "manual_promotion_receipt_rejected_by_database_trigger"
            ) from exc
        raise
    return {
        "ok": True,
        "idempotent": False,
        "candidate": _public_candidate(stored, include_payload=False),
        "automatic_promotion": False,
        "business_rows_written": 0,
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
    }


__all__ = [
    "link_field_evidence",
    "record_manual_promotion_receipt",
    "review_candidate",
]
