"""Fail-closed human review for Marketing Brain skill runs.

The existing ``vkpi_skill_runs`` table already carries ``human_score``,
``business_result`` and ``accepted`` columns, but it previously had no write
path that proved who reviewed a run or which evidence supported the decision.
This module updates those columns and appends a reviewer event in one database
transaction.  It never calls a model, provider, or business adapter.
"""
from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.platform import event_ledger, review_contract

_RUNS = "vkpi_skill_runs"
_EVENTS = "vkpi_event_ledger"
_SOURCE = "skill_studio.human_review"

logger = get_logger(__name__)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _review_events(conn: Any, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT id, actor_id, payload_json
        FROM {_EVENTS}
        WHERE event_type IN ('skill_run_accepted', 'skill_run_rejected')
          AND entity_type = 'skill_run'
          AND entity_id = ?
          AND source = ?
        ORDER BY id DESC
        """,
        (str(run_id), _SOURCE),
    ).fetchall()
    return [dict(row) for row in rows]


def _safe_snapshot(value: Any) -> tuple[Any, str]:
    raw = value if isinstance(value, (dict, list)) else _loads(value)
    snapshot = review_contract.redact_review_snapshot(raw)
    return snapshot, review_contract.review_snapshot_sha256(snapshot)


def _usable_production_output(skill_name: str, output: dict[str, Any]) -> bool:
    """Apply a server-owned success contract per reviewable Skill.

    Human review may audit failures/empty runs elsewhere, but those rows must
    never enter the learning-usability denominator.
    """
    name = str(skill_name or "").strip()
    status = str(output.get("status") or "").strip().lower()
    if name == "creator_match":
        recommendations = output.get("recommendations")
        return isinstance(recommendations, list) and bool(recommendations)
    if name == "brief_generate":
        brief = output.get("brief")
        return (
            output.get("ok") is True
            and isinstance(brief, dict)
            and bool(str(brief.get("hook") or "").strip())
            and bool(brief.get("deliverables"))
        )
    if name == "content_score":
        source = output.get("source") if isinstance(output.get("source"), dict) else {}
        return (
            status == "ok"
            and bool(str(source.get("target_id") or "").strip())
            and bool(str(output.get("summary") or "").strip())
        )
    if name == "roi_review":
        return (
            status == "ready"
            and output.get("missing_data") is False
            and isinstance(output.get("roi"), dict)
        )
    if name == "campaign_plan":
        plan = output.get("plan") if isinstance(output.get("plan"), dict) else {}
        return status == "ok" and bool(plan.get("timeline")) and bool(plan.get("creator_mix"))
    return False


def get_skill_review_candidate(run_id: int) -> dict[str, Any]:
    """Return redacted immutable input/output for a manager's exact review."""
    if not table_exists(_RUNS):
        return {"ok": False, "reason": "review_ledger_unavailable"}
    row = get_conn().execute(
        f"""
        SELECT id, skill_name, skill_version, input_schema, output,
               model_used, prompt_version, accepted, human_score,
               business_result, created_at
        FROM {_RUNS} WHERE id=?
        """,
        (int(run_id),),
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "skill_run_not_found"}
    run = dict(row)
    if any(run.get(key) is not None for key in ("accepted", "human_score", "business_result")):
        return {"ok": False, "reason": "skill_run_already_reviewed"}
    output = _loads(run.get("output"))
    if not output or not _usable_production_output(str(run.get("skill_name") or ""), output):
        return {"ok": False, "reason": "skill_run_output_not_reviewable"}
    input_snapshot, input_hash = _safe_snapshot(run.get("input_schema"))
    output_snapshot, output_hash = _safe_snapshot(output)
    if not isinstance(input_snapshot, dict) or not output_snapshot:
        return {"ok": False, "reason": "skill_run_output_not_reviewable"}
    return {
        "ok": True,
        "run_id": int(run["id"]),
        "skill_name": str(run.get("skill_name") or ""),
        "input_snapshot": input_snapshot,
        "input_snapshot_json": review_contract.canonical_review_json(input_snapshot),
        "input_sha256": input_hash,
        "output_snapshot": output_snapshot,
        "output_snapshot_json": review_contract.canonical_review_json(output_snapshot),
        "output_summary": str(output.get("summary") or output.get("reason") or "")[:500],
        "output_sha256": output_hash,
        "model_used": run.get("model_used"),
        "prompt_version": run.get("prompt_version"),
        "created_at": str(run.get("created_at") or ""),
    }


def review_skill_run(
    run_id: int,
    *,
    staff: dict[str, Any] | None,
    accepted: bool,
    human_score: float,
    business_result: str,
    evidence: list[dict[str, Any]],
    correlation_id: str,
    expected_input_sha256: str,
    expected_output_sha256: str,
) -> dict[str, Any]:
    """Review one production skill run exactly once with auditable evidence."""
    try:
        rid = int(run_id)
        score = float(human_score)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "invalid_review_identity_or_score"}
    reviewer = review_contract.reviewer_context(staff)
    if reviewer is None:
        return {"ok": False, "reason": "review_scope_unavailable"}
    actor_id, organization_id = reviewer
    result_text = review_contract.normalize_review_text(business_result, max_length=1000)
    correlation = review_contract.normalize_correlation(correlation_id)
    evidence_rows = review_contract.normalize_evidence(evidence)
    expected_input_hash = str(expected_input_sha256 or "").strip().lower()
    expected_hash = str(expected_output_sha256 or "").strip().lower()
    if rid <= 0:
        return {"ok": False, "reason": "invalid_review_identity_or_score"}
    if not 0.0 <= score <= 5.0:
        return {"ok": False, "reason": "human_score_out_of_range"}
    if result_text is None:
        return {"ok": False, "reason": "business_result_required"}
    if evidence_rows is None:
        return {"ok": False, "reason": "review_evidence_required"}
    if correlation is None:
        return {"ok": False, "reason": "review_correlation_required"}
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in (expected_input_hash, expected_hash)
    ):
        return {"ok": False, "reason": "review_candidate_required"}
    if not table_exists(_RUNS) or not table_exists(_EVENTS):
        return {"ok": False, "reason": "review_ledger_unavailable"}

    conn = get_conn()
    try:
        if not is_postgres_runtime() and not bool(getattr(conn, "in_transaction", False)):
            conn.execute("BEGIN IMMEDIATE")
        lock_clause = " FOR UPDATE" if is_postgres_runtime() else ""
        row = conn.execute(
            f"""
            SELECT id, skill_name, skill_version, input_schema, model_used,
                   prompt_version, accepted, human_score, business_result, output
            FROM {_RUNS}
            WHERE id = ?{lock_clause}
            """,
            (rid,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "skill_run_not_found"}
        run = dict(row)
        skill_name = str(run.get("skill_name") or "").strip()
        marker = skill_name.lower()
        existing_business = str(run.get("business_result") or "").strip().lower()
        if marker.startswith("test") or "smoke" in marker or existing_business in {
            "pytest", "test", "demo", "dry_run", "smoke",
        }:
            conn.rollback()
            return {"ok": False, "reason": "nonproduction_skill_run"}
        output = _loads(run.get("output"))
        if not output or not _usable_production_output(skill_name, output):
            conn.rollback()
            return {"ok": False, "reason": "skill_run_output_not_reviewable"}
        input_snapshot, input_sha256 = _safe_snapshot(run.get("input_schema"))
        _output_snapshot, output_sha256 = _safe_snapshot(output)
        if not hmac.compare_digest(input_sha256, expected_input_hash) or not hmac.compare_digest(
            output_sha256, expected_hash,
        ):
            conn.rollback()
            return {"ok": False, "reason": "skill_review_candidate_changed"}

        events = _review_events(conn, rid)
        for event in events:
            payload = _loads(event.get("payload_json"))
            if str(payload.get("correlation_id") or "") != correlation:
                continue
            same = (
                bool(payload.get("accepted")) is bool(accepted)
                and float(payload.get("human_score")) == score
                and str(payload.get("business_result") or "") == result_text
                and payload.get("evidence") == evidence_rows
                and str(event.get("actor_id") or "") == str(actor_id)
                and str(payload.get("output_sha256") or "") == expected_hash
                and str(payload.get("input_sha256") or "") == expected_input_hash
            )
            conn.rollback()
            if not same:
                return {"ok": False, "reason": "review_correlation_conflict"}
            return {
                "ok": True,
                "run_id": rid,
                "event_id": int(event["id"]),
                "accepted": bool(accepted),
                "human_score": score,
                "idempotent": True,
            }

        if events or any(
            run.get(key) is not None for key in ("accepted", "human_score", "business_result")
        ):
            conn.rollback()
            return {"ok": False, "reason": "skill_run_already_reviewed"}

        cursor = conn.execute(
            f"""
            UPDATE {_RUNS}
            SET accepted = ?, human_score = ?, business_result = ?
            WHERE id = ?
              AND accepted IS NULL
              AND human_score IS NULL
              AND business_result IS NULL
            """,
            (bool(accepted), score, result_text, rid),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return {"ok": False, "reason": "skill_review_state_changed"}

        reviewed_at = datetime.now(timezone.utc).isoformat()
        event_id = event_ledger.insert_required(
            conn,
            "skill_run_accepted" if accepted else "skill_run_rejected",
            entity_type="skill_run",
            entity_id=rid,
            actor_type="staff",
            actor_id=actor_id,
            source=_SOURCE,
            payload={
                "accepted": bool(accepted),
                "human_score": score,
                "business_result": result_text,
                "evidence": evidence_rows,
                "correlation_id": correlation,
                "reviewed_at": reviewed_at,
                "output_sha256": output_sha256,
                "input_sha256": input_sha256,
            },
            trace_id=event_ledger.new_trace_id("skill_run", rid),
            provenance={
                "kind": "human_review",
                "source": "skill_studio",
                "evidence_count": len(evidence_rows),
                "evidence_verification": "staff_attestation_bound_to_skill_run",
                "server_bound_run_id": rid,
                "server_bound_output_sha256": output_sha256,
                "server_bound_input_sha256": input_sha256,
                "skill_version": str(run.get("skill_version") or ""),
                "model_used": str(run.get("model_used") or ""),
                "prompt_version": str(run.get("prompt_version") or ""),
                "review_eligibility": "usable_production_output",
            },
            organization_id=organization_id,
        )
        conn.commit()
        return {
            "ok": True,
            "run_id": rid,
            "event_id": event_id,
            "accepted": bool(accepted),
            "human_score": score,
            "idempotent": False,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("skill_review.rollback_failed", exc_info=True)
        logger.warning("skill_review.failed", extra={"run_id": run_id}, exc_info=True)
        return {"ok": False, "reason": "skill_review_failed"}


def verified_acceptance_stats(skill_name: str = "") -> dict[str, Any]:
    """Aggregate only reviews backed by a staff event and provenance."""
    if not table_exists(_RUNS) or not table_exists(_EVENTS):
        return {"status": "unavailable"}
    where = ""
    params: list[Any] = []
    name = str(skill_name or "").strip()
    if name:
        where = " AND sr.skill_name = ?"
        params.append(name)
    params.append(_SOURCE)
    row = get_conn().execute(
        f"""
        SELECT COUNT(*) AS judged,
               COUNT(*) FILTER (WHERE sr.accepted = TRUE) AS accepted,
               AVG(sr.human_score) AS avg_human_score
        FROM {_RUNS} sr
        WHERE sr.accepted IS NOT NULL
          AND sr.human_score IS NOT NULL
          AND sr.business_result IS NOT NULL
          AND sr.business_result <> ''{where}
          AND EXISTS (
              SELECT 1 FROM {_EVENTS} ev
              WHERE ev.event_type = CASE
                        WHEN sr.accepted = TRUE THEN 'skill_run_accepted'
                        ELSE 'skill_run_rejected'
                    END
                AND ev.entity_type = 'skill_run'
                AND ev.entity_id = CAST(sr.id AS TEXT)
                AND ev.organization_id = 1
                AND ev.actor_type = 'staff'
                AND ev.actor_id <> ''
                AND ev.source = ?
                AND ev.trace_id <> ''
                AND ev.provenance_json IS NOT NULL
                AND CAST(ev.provenance_json AS TEXT) NOT IN ('', '{{}}', 'null')
                AND COALESCE(ev.provenance_json->>'evidence_verification', '')
                    = 'staff_attestation_bound_to_skill_run'
                AND COALESCE(ev.provenance_json->>'review_eligibility', '')
                    = 'usable_production_output'
                AND COALESCE(ev.provenance_json->>'server_bound_input_sha256', '')
                    ~ '^[0-9a-f]{{64}}$'
                AND COALESCE(ev.provenance_json->>'server_bound_output_sha256', '')
                    ~ '^[0-9a-f]{{64}}$'
          )
        """,
        tuple(params),
    ).fetchone()
    data = dict(row) if row else {}
    judged = int(data.get("judged") or 0)
    accepted_count = int(data.get("accepted") or 0)
    return {
        "status": "ok",
        "skill_name": name or None,
        "judged": judged,
        "accepted": accepted_count,
        "acceptance_rate": round(accepted_count / judged, 3) if judged else None,
        "avg_human_score": (
            float(data["avg_human_score"]) if data.get("avg_human_score") is not None else None
        ),
    }
