"""Immutable manager verification receipts for outreach reply actuals.

``vkpi_messages`` is mutable and client-writable, so neither its direction nor
its timestamps are an actual.  This module lets an org-1 manager attest the
server-resolved exact first inbound snapshot, or attest no reply only after the
frozen window closes.  Receipt and required event are append-only and atomic.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.market_brain import outreach_truth_bridge as bridge
from app.domains.market_brain.outreach_reply_receipt_validation import (
    verified_receipt_matches_binding,
)
from app.domains.platform import event_ledger, review_contract
from app.domains.staff import is_manager_staff

TABLE = bridge.REPLY_TABLE
EVENT_TYPE = "action_outreach_reply_verified"
EVENT_SOURCE = "gtm.outreach_reply_truth"
_REQUIRED_TABLES = (TABLE, bridge.TABLE, "vkpi_messages", "vkpi_event_ledger")
CANDIDATE_TTL_SECONDS = 900
logger = get_logger(__name__)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _request_contract(
    *, binding_id: int, outcome: str, actor_staff_id: int,
    expected_candidate_sha256: str, candidate_observed_at: str,
) -> dict[str, Any]:
    return {
        "schema": "vkpi_action_outreach_reply_request/v1",
        "organization_id": bridge.ORGANIZATION_ID,
        "binding_id": int(binding_id),
        "outcome": outcome,
        "actor_staff_id": int(actor_staff_id),
        "expected_candidate_sha256": expected_candidate_sha256,
        "candidate_observed_at": candidate_observed_at,
    }


def _receipt_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    inbound_id = row.get("inbound_message_id")
    review_candidate = _loads(row.get("review_candidate_json"))
    return {
        "schema": "vkpi_action_outreach_reply_truth_receipt/v1",
        "organization_id": int(row["organization_id"]),
        "binding_id": int(row["binding_id"]),
        "outcome": str(row["outcome"]),
        "inbound_message_id": int(inbound_id) if inbound_id is not None else None,
        "inbound_captured_at": (
            bridge._dt(row.get("inbound_captured_at")).isoformat()
            if row.get("inbound_captured_at") is not None else None
        ),
        "inbound_created_at": (
            bridge._dt(row.get("inbound_created_at")).isoformat()
            if row.get("inbound_created_at") is not None else None
        ),
        "first_outbound_at": bridge._dt(row["first_outbound_at"]).isoformat(),
        "observation_end_at": bridge._dt(row["observation_end_at"]).isoformat(),
        "candidate_observed_at": bridge._dt(row["candidate_observed_at"]).isoformat(),
        "verified_at": bridge._dt(row["verified_at"]).isoformat(),
        "actor_staff_id": int(row["actor_staff_id"]),
        "correlation_id": str(row["correlation_id"]),
        "request_fingerprint": str(row["request_fingerprint"]),
        "binding_fingerprint": str(row["binding_fingerprint"]),
        "review_candidate_sha256": str(row["review_candidate_sha256"]),
        "review_candidate": review_candidate,
    }


def _provenance(receipt_fingerprint: str, outcome: str) -> dict[str, Any]:
    return {
        "evidence_verification": "manager_attested_server_resolved_reply_snapshot",
        "outcome": outcome,
        "message_table_is_candidate_only": True,
        "client_message_id_or_timestamp_used": False,
        "reply_receipt_immutable": True,
        "receipt_fingerprint": receipt_fingerprint,
    }


def _event_matches(conn: Any, receipt: dict[str, Any]) -> bool:
    rows = conn.execute(
        """
        SELECT actor_id, payload_json, provenance_json
        FROM vkpi_event_ledger
        WHERE organization_id=? AND event_type=?
          AND entity_type='action_outreach_reply_receipt'
          AND entity_id=? AND source=?
        ORDER BY id
        """,
        (bridge.ORGANIZATION_ID, EVENT_TYPE, str(receipt["id"]), EVENT_SOURCE),
    ).fetchall()
    if len(rows) != 1:
        return False
    event = dict(rows[0])
    fingerprint = str(receipt.get("receipt_fingerprint") or "")
    return bool(
        str(event.get("actor_id") or "") == str(receipt.get("actor_staff_id") or "")
        and _loads(event.get("payload_json"))
        == {**_receipt_snapshot(receipt), "receipt_fingerprint": fingerprint}
        and _loads(event.get("provenance_json"))
        == _provenance(fingerprint, str(receipt.get("outcome") or ""))
    )


def _receipt_proof_valid(conn: Any, receipt: dict[str, Any]) -> bool:
    try:
        candidate = _loads(receipt.get("review_candidate_json"))
        return bool(
            bridge._sha256(_receipt_snapshot(receipt))
            == str(receipt.get("receipt_fingerprint") or "")
            and review_contract.review_snapshot_sha256(candidate)
            == str(receipt.get("review_candidate_sha256") or "")
            and review_contract.redact_review_snapshot(candidate) == candidate
            and len(review_contract.canonical_review_json(candidate).encode("utf-8"))
            <= 65_536
            and _stored_candidate_matches_receipt(candidate, receipt)
            and _event_matches(conn, receipt)
        )
    except Exception:
        return False


def _stored_candidate_matches_receipt(
    candidate: dict[str, Any], receipt: dict[str, Any],
) -> bool:
    """Bind the immutable human-visible snapshot to every signed receipt fact."""
    inbound = candidate.get("resolved_inbound")
    outcome = str(receipt.get("outcome") or "")
    if not isinstance(candidate, dict):
        return False
    if outcome == "replied":
        if not isinstance(inbound, dict):
            return False
        inbound_matches = bool(
            int(inbound.get("message_id") or 0)
            == int(receipt.get("inbound_message_id") or 0)
            and bridge._dt(inbound.get("captured_at"))
            == bridge._dt(receipt.get("inbound_captured_at"))
            and bridge._dt(inbound.get("created_at"))
            == bridge._dt(receipt.get("inbound_created_at"))
        )
    else:
        inbound_matches = bool(outcome == "no_reply" and inbound is None)
    first = candidate.get("first_outbound")
    return bool(
        candidate.get("schema")
        == "vkpi_action_outreach_reply_review_candidate/v1"
        and int(candidate.get("organization_id") or 0) == bridge.ORGANIZATION_ID
        and int(candidate.get("binding_id") or 0) == int(receipt.get("binding_id") or 0)
        and str(candidate.get("binding_fingerprint") or "")
        == str(receipt.get("binding_fingerprint") or "")
        and str(candidate.get("requested_outcome") or "") == outcome
        and candidate.get("eligible") is True
        and str(candidate.get("eligibility_reason") or "") == "eligible"
        and bridge._dt(candidate.get("server_now"))
        == bridge._dt(receipt.get("candidate_observed_at"))
        and bridge._dt(candidate.get("observation_end_at"))
        == bridge._dt(receipt.get("observation_end_at"))
        and isinstance(first, dict)
        and bridge._dt(first.get("captured_at"))
        == bridge._dt(receipt.get("first_outbound_at"))
        and inbound_matches
    )


def _load_receipt(conn: Any, where: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT * FROM {TABLE} WHERE organization_id=? AND {where}",
        (bridge.ORGANIZATION_ID, *params),
    ).fetchone()
    return dict(row) if row is not None else None


def _idempotent_result(
    conn: Any, receipt: dict[str, Any], *, request_fingerprint: str,
) -> dict[str, Any]:
    if str(receipt.get("request_fingerprint") or "") != request_fingerprint:
        return {"ok": False, "reason": "outreach_reply_correlation_conflict"}
    if not _receipt_proof_valid(conn, receipt):
        return {"ok": False, "reason": "outreach_reply_event_conflict"}
    return {
        "ok": True,
        "id": int(receipt["id"]),
        "binding_id": int(receipt["binding_id"]),
        "outcome": str(receipt["outcome"]),
        "inbound_message_id": (
            int(receipt["inbound_message_id"])
            if receipt.get("inbound_message_id") is not None else None
        ),
        "correlation_id": str(receipt["correlation_id"]),
        "idempotent": True,
    }


def _race_result(
    conn: Any, *, binding_id: int, correlation: str, request_fingerprint: str,
) -> dict[str, Any] | None:
    by_correlation = _load_receipt(conn, "correlation_id=?", (correlation,))
    if by_correlation is not None:
        return _idempotent_result(
            conn, by_correlation, request_fingerprint=request_fingerprint,
        )
    by_binding = _load_receipt(conn, "binding_id=?", (binding_id,))
    if by_binding is not None:
        return {"ok": False, "reason": "outreach_reply_already_verified"}
    return None


def _eligible_inbounds(
    conn: Any, binding: dict[str, Any], *, server_now: Any,
) -> list[dict[str, Any]]:
    first = bridge._dt(binding.get("first_outbound_at"))
    end = bridge._dt(binding.get("observation_end_at"))
    now = bridge._dt(server_now)
    if first is None or end is None or now is None:
        return []
    rows = conn.execute(
        """
        SELECT id, project_id, kol_id, source, direction, body, snippet,
               evidence_url, captured_at, created_at
        FROM vkpi_messages WHERE project_id=? AND kol_id=?
        ORDER BY captured_at, id
        """,
        (int(binding["project_id"]), int(binding["kol_id"])),
    ).fetchall()
    candidates: list[tuple[Any, int, dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        captured = bridge._dt(row.get("captured_at"))
        created = bridge._dt(row.get("created_at"))
        if (
            str(row.get("direction") or "").strip().lower() == "inbound"
            and captured is not None
            and created is not None
            and first < captured <= created <= end
            and created <= now
        ):
            candidates.append((captured, int(row["id"]), {**row, "captured_at": captured,
                                                           "created_at": created}))
    return [item[2] for item in sorted(candidates, key=lambda item: (item[0], item[1]))]


def _source_class(value: Any) -> str:
    """Expose only a bounded class; source is client-writable and not trust."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"manual", "email", "instagram", "youtube", "tiktok"} else "other"


def _message_review(row: dict[str, Any]) -> dict[str, Any]:
    """Small redacted content proof; never return addresses, metadata or raw URLs."""
    body = str(row.get("body") or "")[:800]
    snippet = str(row.get("snippet") or "")[:400]
    evidence_url = str(row.get("evidence_url") or "")[:4000]
    try:
        parsed = urlsplit(evidence_url)
        evidence_host = (
            str(parsed.hostname or "").lower()[:253]
            if parsed.scheme.lower() in {"http", "https"} and parsed.hostname else None
        )
    except ValueError:
        evidence_host = None
    redacted = review_contract.redact_review_snapshot({
        "body_excerpt": body,
        "snippet_excerpt": snippet,
    })
    if not isinstance(redacted, dict):
        redacted = {"body_excerpt": "[REDACTED]", "snippet_excerpt": "[REDACTED]"}
    visible_values = (
        str(redacted.get("body_excerpt") or "").strip(),
        str(redacted.get("snippet_excerpt") or "").strip(),
    )
    reviewable = any(
        value and value not in {"[REDACTED]", "[REDACTED URL]"}
        for value in visible_values
    )
    return {
        **redacted,
        "reviewable_content": reviewable,
        "body_sha256": bridge._sha256({"body": body}),
        "snippet_sha256": bridge._sha256({"snippet": snippet}),
        "evidence_host": evidence_host,
        "evidence_ref_sha256": bridge._sha256({"evidence_url": evidence_url}),
        "raw_evidence_url_returned": False,
    }


def _candidate_envelope(
    conn: Any,
    binding: dict[str, Any],
    *,
    outcome: str,
    observed_at: Any,
) -> dict[str, Any]:
    observed = bridge._dt(observed_at)
    first = bridge._dt(binding.get("first_outbound_at"))
    outbound_created = bridge._dt(binding.get("first_outbound_created_at"))
    start = bridge._dt(binding.get("observation_start_at"))
    end = bridge._dt(binding.get("observation_end_at"))
    approved = bridge._dt(binding.get("action_approved_at"))
    if None in {observed, first, outbound_created, start, end, approved}:
        raise ValueError("candidate clock unavailable")
    current_outbounds, outbound_flags = bridge._eligible_project_outbounds(
        conn,
        projects=[{"id": int(binding["project_id"])}],
        kol_id=int(binding["kol_id"]),
        start=start,
        end=end,
        approved_at=approved,
        server_now=observed,
    )
    current_outbound = (
        current_outbounds[0]["outbound"] if len(current_outbounds) == 1 else None
    )
    outbound_scope_clean = not (
        outbound_flags.get("preapproval") or outbound_flags.get("unverified_clock")
    )
    outbound_still_first = bool(
        outbound_scope_clean
        and current_outbound is not None
        and int(current_outbound["id"]) == int(binding["first_outbound_message_id"])
        and current_outbound["captured_at"] == first
        and current_outbound["created_at"] == outbound_created
    )
    outbound_review = _message_review(current_outbound or {})
    inbounds = _eligible_inbounds(conn, binding, server_now=observed)
    inbound = inbounds[0] if inbounds else None
    inbound_review = _message_review(inbound or {})
    inbound_reviewable = bool(inbound is not None and inbound_review["reviewable_content"])
    window_closed = observed >= end
    eligible = bool(
        window_closed and outbound_still_first and outbound_review["reviewable_content"]
        and ((outcome == "replied" and inbound_reviewable)
             or (outcome == "no_reply" and inbound is None))
    )
    if not outbound_still_first:
        reason = "binding_first_outbound_changed"
    elif not outbound_review["reviewable_content"]:
        reason = "outbound_content_unreviewable"
    elif not window_closed:
        reason = "observation_window_open"
    elif outcome == "replied" and inbound is None:
        reason = "verified_inbound_not_observed"
    elif outcome == "replied" and not inbound_reviewable:
        reason = "inbound_content_unreviewable"
    elif outcome == "no_reply" and inbound is not None:
        reason = "reply_exists"
    else:
        reason = "eligible"
    candidate = {
        "schema": "vkpi_action_outreach_reply_review_candidate/v1",
        "organization_id": bridge.ORGANIZATION_ID,
        "binding_id": int(binding["id"]),
        "binding_fingerprint": str(binding["binding_fingerprint"]),
        "action_inbox_id": int(binding["action_inbox_id"]),
        "prediction_run_id": str(binding["prediction_run_id"]),
        "project_id": int(binding["project_id"]),
        "kol_pool_id": int(binding["kol_pool_id"]),
        "kol_id": int(binding["kol_id"]),
        "product_sku": str(binding["product_sku"]),
        "channel": str(binding["channel"]),
        "action_approved_at": approved.isoformat(),
        "approval_snapshot_sha256": str(binding["approval_snapshot_sha256"]),
        "observation_start_at": start.isoformat(),
        "observation_end_at": end.isoformat(),
        "requested_outcome": outcome,
        "server_now": observed.isoformat(),
        "window_closed": window_closed,
        "binding_first_outbound_still_exact": outbound_still_first,
        "outbound_scope_has_no_invalid_candidates": outbound_scope_clean,
        "eligible": eligible,
        "eligibility_reason": reason,
        "first_outbound": {
            "message_id": int(binding["first_outbound_message_id"]),
            "captured_at": first.isoformat(),
            "created_at": outbound_created.isoformat(),
            "evidence_class": "manager_attested_mutable_message_snapshot",
            "review_content": outbound_review,
        },
        "resolved_inbound": (
            {
                "message_id": int(inbound["id"]),
                "captured_at": inbound["captured_at"].isoformat(),
                "created_at": inbound["created_at"].isoformat(),
                "source_class": _source_class(inbound.get("source")),
                "source_is_client_writable": True,
                "review_content": inbound_review,
            }
            if inbound is not None else None
        ),
    }
    redacted = review_contract.redact_review_snapshot(candidate)
    if not isinstance(redacted, dict):
        raise ValueError("candidate redaction failed")
    return redacted


def _current_outbound_is_exact(
    conn: Any, binding: dict[str, Any], *, observed_at: Any,
) -> bool:
    """Re-scan the complete outbound scope through the current server clock."""
    observed = bridge._dt(observed_at)
    first = bridge._dt(binding.get("first_outbound_at"))
    created = bridge._dt(binding.get("first_outbound_created_at"))
    start = bridge._dt(binding.get("observation_start_at"))
    end = bridge._dt(binding.get("observation_end_at"))
    approved = bridge._dt(binding.get("action_approved_at"))
    if None in {observed, first, created, start, end, approved}:
        return False
    candidates, flags = bridge._eligible_project_outbounds(
        conn,
        projects=[{"id": int(binding["project_id"])}],
        kol_id=int(binding["kol_id"]),
        start=start,
        end=end,
        approved_at=approved,
        server_now=observed,
    )
    if flags.get("preapproval") or flags.get("unverified_clock") or len(candidates) != 1:
        return False
    outbound = candidates[0]["outbound"]
    return bool(
        int(outbound["id"]) == int(binding["first_outbound_message_id"])
        and outbound["captured_at"] == first
        and outbound["created_at"] == created
    )


def get_reply_review_candidate(
    binding_id: int,
    *,
    outcome: str,
    staff: dict[str, Any] | None,
    _connection: Any = None,
) -> dict[str, Any]:
    """Return the exact redacted snapshot a manager may subsequently sign."""
    reviewer = review_contract.reviewer_context(staff)
    if reviewer is None or not is_manager_staff(staff or {}):
        return {"ok": False, "reason": "outreach_reply_scope_unavailable"}
    _actor_id, organization_id = reviewer
    if organization_id != bridge.ORGANIZATION_ID:
        return {"ok": False, "reason": "outreach_reply_scope_unavailable"}
    binding_id_value = bridge._positive_int(binding_id)
    normalized_outcome = str(outcome or "").strip().lower()
    if binding_id_value <= 0:
        return {"ok": False, "reason": "outreach_reply_binding_required"}
    if normalized_outcome not in {"replied", "no_reply"}:
        return {"ok": False, "reason": "outreach_reply_outcome_invalid"}
    if not all(table_exists(name) for name in _REQUIRED_TABLES):
        return {"ok": False, "reason": "outreach_reply_schema_unavailable"}
    conn = _connection or get_conn()
    try:
        row = conn.execute(
            f"SELECT * FROM {bridge.TABLE} WHERE organization_id=? AND id=?",
            (bridge.ORGANIZATION_ID, binding_id_value),
        ).fetchone()
        if row is None:
            return {"ok": False, "reason": "outreach_reply_binding_not_found"}
        binding = dict(row)
        if not bridge._binding_proof_valid(conn, binding):
            return {"ok": False, "reason": "outreach_reply_binding_proof_invalid"}
        observed = bridge._server_now(conn)
        if observed is None:
            return {"ok": False, "reason": "outreach_reply_server_clock_unavailable"}
        candidate = _candidate_envelope(
            conn, binding, outcome=normalized_outcome, observed_at=observed,
        )
        canonical = review_contract.canonical_review_json(candidate)
        return {
            "ok": True,
            "candidate": candidate,
            "candidate_canonical_json": canonical,
            "candidate_sha256": review_contract.review_snapshot_sha256(candidate),
            "candidate_observed_at": observed.isoformat(),
            "candidate_ttl_seconds": CANDIDATE_TTL_SECONDS,
        }
    except Exception:
        logger.warning("outreach reply candidate failed binding_id=%s", binding_id_value,
                       exc_info=True)
        return {"ok": False, "reason": "outreach_reply_candidate_unavailable"}


def verify_reply(
    binding_id: int,
    *,
    outcome: str,
    correlation_id: str,
    expected_candidate_sha256: str,
    candidate_observed_at: str,
    staff: dict[str, Any] | None,
    _connection: Any = None,
) -> dict[str, Any]:
    """Append one immutable replied/no-reply receipt and required event."""
    reviewer = review_contract.reviewer_context(staff)
    if reviewer is None or not is_manager_staff(staff or {}):
        return {"ok": False, "reason": "outreach_reply_scope_unavailable"}
    actor_id, organization_id = reviewer
    binding_id_value = bridge._positive_int(binding_id)
    normalized_outcome = str(outcome or "").strip().lower()
    correlation = review_contract.normalize_correlation(correlation_id)
    expected_candidate_hash = str(expected_candidate_sha256 or "").strip().lower()
    expected_observed = bridge._dt(candidate_observed_at)
    if organization_id != bridge.ORGANIZATION_ID:
        return {"ok": False, "reason": "outreach_reply_scope_unavailable"}
    if binding_id_value <= 0:
        return {"ok": False, "reason": "outreach_reply_binding_required"}
    if normalized_outcome not in {"replied", "no_reply"}:
        return {"ok": False, "reason": "outreach_reply_outcome_invalid"}
    if correlation is None:
        return {"ok": False, "reason": "outreach_reply_correlation_required"}
    if (
        expected_observed is None
        or len(expected_candidate_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_candidate_hash)
    ):
        return {"ok": False, "reason": "outreach_reply_candidate_required"}
    if not all(table_exists(name) for name in _REQUIRED_TABLES):
        return {"ok": False, "reason": "outreach_reply_schema_unavailable"}

    request = _request_contract(
        binding_id=binding_id_value, outcome=normalized_outcome, actor_staff_id=actor_id,
        expected_candidate_sha256=expected_candidate_hash,
        candidate_observed_at=expected_observed.isoformat(),
    )
    request_fingerprint = bridge._sha256(request)
    conn = _connection or get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
        replay = _race_result(
            conn, binding_id=binding_id_value, correlation=correlation,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            conn.rollback()
            return replay

        lock = " FOR UPDATE" if is_postgres_runtime() else ""
        row = conn.execute(
            f"SELECT * FROM {bridge.TABLE} WHERE organization_id=? AND id=?{lock}",
            (bridge.ORGANIZATION_ID, binding_id_value),
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_binding_not_found"}
        binding = dict(row)
        if not bridge._binding_proof_valid(conn, binding):
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_binding_proof_invalid"}
        pool = bridge._lock_pool_kol_message_scope(
            conn,
            kol_pool_id=int(binding["kol_pool_id"]),
            project_ids=[int(binding["project_id"])],
        )
        if (
            pool is None
            or bridge._positive_int(pool.get("linked_main_kol_id")) != int(binding["kol_id"])
            or bridge._channel(pool.get("platform")) != bridge._channel(binding.get("channel"))
        ):
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_candidate_changed"}
        server_now = bridge._server_now(conn)
        end = bridge._dt(binding.get("observation_end_at"))
        first = bridge._dt(binding.get("first_outbound_at"))
        if server_now is None or end is None or first is None:
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_server_clock_unavailable"}
        if (
            expected_observed > server_now
            or server_now - expected_observed > timedelta(seconds=CANDIDATE_TTL_SECONDS)
        ):
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_candidate_changed"}
        candidate = _candidate_envelope(
            conn, binding, outcome=normalized_outcome, observed_at=expected_observed,
        )
        candidate_hash = review_contract.review_snapshot_sha256(candidate)
        if candidate_hash != expected_candidate_hash:
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_candidate_changed"}
        # Re-scan through the current database clock as an explicit phantom
        # guard.  Eligible evidence is also capped at frozen end, so any newly
        # inserted backdated row changes this exact ordered ID set.
        current_inbounds = _eligible_inbounds(conn, binding, server_now=server_now)
        reviewed_inbound = candidate.get("resolved_inbound")
        reviewed_first_id = (
            int(reviewed_inbound.get("message_id") or 0)
            if isinstance(reviewed_inbound, dict) else 0
        )
        current_first_id = int(current_inbounds[0]["id"]) if current_inbounds else 0
        if (
            current_first_id != reviewed_first_id
            or not _current_outbound_is_exact(conn, binding, observed_at=server_now)
        ):
            conn.rollback()
            return {"ok": False, "reason": "outreach_reply_candidate_changed"}
        if not bool(candidate.get("eligible")):
            conn.rollback()
            candidate_reason = str(candidate.get("eligibility_reason") or "")
            reason = {
                "observation_window_open": "outreach_no_reply_window_open",
                "verified_inbound_not_observed": "outreach_verified_inbound_not_observed",
                "reply_exists": "outreach_reply_exists",
                "inbound_content_unreviewable": "outreach_inbound_content_unreviewable",
                "outbound_content_unreviewable": "outreach_outbound_content_unreviewable",
            }.get(candidate_reason, "outreach_reply_candidate_changed")
            return {"ok": False, "reason": reason}
        inbounds = current_inbounds
        inbound = None
        if normalized_outcome == "replied":
            if not inbounds:
                conn.rollback()
                return {"ok": False, "reason": "outreach_verified_inbound_not_observed"}
            inbound = inbounds[0]
        else:
            if inbounds:
                conn.rollback()
                return {"ok": False, "reason": "outreach_reply_exists"}

        receipt_data = {
            "schema": "vkpi_action_outreach_reply_truth_receipt/v1",
            "organization_id": bridge.ORGANIZATION_ID,
            "binding_id": binding_id_value,
            "outcome": normalized_outcome,
            "actor_staff_id": actor_id,
            "inbound_message_id": int(inbound["id"]) if inbound is not None else None,
            "inbound_captured_at": (
                inbound["captured_at"].isoformat() if inbound is not None else None
            ),
            "inbound_created_at": (
                inbound["created_at"].isoformat() if inbound is not None else None
            ),
            "first_outbound_at": first.isoformat(),
            "observation_end_at": end.isoformat(),
            "candidate_observed_at": expected_observed.isoformat(),
            "verified_at": server_now.isoformat(),
            "correlation_id": correlation,
            "request_fingerprint": request_fingerprint,
            "binding_fingerprint": str(binding["binding_fingerprint"]),
            "review_candidate_sha256": candidate_hash,
            "review_candidate": candidate,
        }
        receipt_fingerprint = bridge._sha256(receipt_data)
        inserted = conn.execute(
            f"""
            INSERT INTO {TABLE} (
                organization_id, binding_id, outcome, inbound_message_id,
                inbound_captured_at, inbound_created_at, first_outbound_at,
                observation_end_at, candidate_observed_at, verified_at, actor_staff_id,
                correlation_id, request_fingerprint, binding_fingerprint,
                review_candidate_sha256, review_candidate_json, receipt_fingerprint
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,
            (
                bridge.ORGANIZATION_ID, binding_id_value, normalized_outcome,
                receipt_data["inbound_message_id"], receipt_data["inbound_captured_at"],
                receipt_data["inbound_created_at"], receipt_data["first_outbound_at"],
                receipt_data["observation_end_at"], receipt_data["candidate_observed_at"],
                receipt_data["verified_at"], actor_id, correlation, request_fingerprint,
                receipt_data["binding_fingerprint"], candidate_hash,
                review_contract.canonical_review_json(candidate), receipt_fingerprint,
            ),
        ).fetchone()
        if inserted is None:
            raise RuntimeError("outreach reply receipt insert returned no id")
        receipt_id = int(dict(inserted)["id"])
        event_ledger.insert_required(
            conn,
            EVENT_TYPE,
            entity_type="action_outreach_reply_receipt",
            entity_id=receipt_id,
            actor_type="staff",
            actor_id=actor_id,
            source=EVENT_SOURCE,
            payload={**receipt_data, "receipt_fingerprint": receipt_fingerprint},
            trace_id=event_ledger.new_trace_id("outreach-reply", binding_id_value, receipt_id),
            provenance=_provenance(receipt_fingerprint, normalized_outcome),
            organization_id=bridge.ORGANIZATION_ID,
        )
        conn.commit()
        return {
            "ok": True,
            "id": receipt_id,
            "binding_id": binding_id_value,
            "outcome": normalized_outcome,
            "inbound_message_id": receipt_data["inbound_message_id"],
            "correlation_id": correlation,
            "idempotent": False,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("outreach reply rollback failed", exc_info=True)
        try:
            raced = _race_result(
                conn, binding_id=binding_id_value, correlation=correlation,
                request_fingerprint=request_fingerprint,
            )
            if raced is not None:
                return raced
        except Exception:
            logger.debug("outreach reply race read failed", exc_info=True)
        logger.warning("outreach reply write failed binding_id=%s", binding_id_value,
                       exc_info=True)
        return {"ok": False, "reason": "outreach_reply_write_failed"}


def verified_receipt_for_binding(
    conn: Any, binding: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a receipt only when its snapshot, binding and one event all match."""
    try:
        receipt = _load_receipt(conn, "binding_id=?", (int(binding["id"]),))
        if receipt is None or not _receipt_proof_valid(conn, receipt):
            return None
        if not verified_receipt_matches_binding(
            receipt,
            binding,
            dt=bridge._dt,
            channel=bridge._channel,
        ):
            return None
        return receipt
    except Exception:
        return None


def resolve_verified_reply(conn: Any, binding: dict[str, Any]) -> dict[str, Any] | None:
    receipt = verified_receipt_for_binding(conn, binding)
    if receipt is None:
        return None
    replied = str(receipt["outcome"]) == "replied"
    inbound_ids = [int(receipt["inbound_message_id"])] if replied else []
    return {
        "reply_outcome": 1 if replied else 0,
        "reply_outcome_binding": "manager_verified_action_project_outreach_receipt/v1",
        "reply_outcome_bridge_id": int(binding["id"]),
        "reply_outcome_receipt_id": int(receipt["id"]),
        "reply_outcome_binding_fingerprint": str(binding["binding_fingerprint"]),
        "reply_outcome_receipt_fingerprint": str(receipt["receipt_fingerprint"]),
        "reply_outcome_project_id": int(binding["project_id"]),
        "reply_outcome_first_outbound_message_id": int(binding["first_outbound_message_id"]),
        "reply_outcome_first_outbound_at": bridge._dt(
            binding["first_outbound_at"]
        ).isoformat(),
        "reply_outcome_correlated_inbound_n": len(inbound_ids),
        "reply_outcome_correlated_inbound_message_ids": inbound_ids,
        "reply_outcome_note": (
            "Binary actual comes only from an immutable manager verification receipt "
            "bound to the unique server-resolved project and frozen window."
        ),
    }


def verified_actual_for_action(conn: Any, action_inbox_id: int) -> dict[str, Any] | None:
    """Re-verify the live immutable bridge+receipt used by verdict freezing."""
    try:
        binding = bridge._load_binding(
            conn, "action_inbox_id=?", (int(action_inbox_id),),
        )
        if binding is None or not bridge._binding_proof_valid(conn, binding):
            return None
        receipt = verified_receipt_for_binding(conn, binding)
        if receipt is None:
            return None
        return {
            "actual": 1 if str(receipt["outcome"]) == "replied" else 0,
            "binding_id": int(binding["id"]),
            "receipt_id": int(receipt["id"]),
            "prediction_run_id": str(binding["prediction_run_id"]),
            "binding_fingerprint": str(binding["binding_fingerprint"]),
            "receipt_fingerprint": str(receipt["receipt_fingerprint"]),
        }
    except Exception:
        return None


__all__ = [
    "TABLE", "CANDIDATE_TTL_SECONDS", "get_reply_review_candidate", "verify_reply",
    "verified_receipt_for_binding", "resolve_verified_reply", "verified_actual_for_action",
]
