"""Shared data-readiness gates for Market Brain claims.

The gate separates an implemented capability from enough recent, observed
business evidence to make an effectiveness claim. It is read-only and never
creates outcomes, evaluations, or feedback rows.
"""
from __future__ import annotations

import json
import hashlib
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.shared.data_readiness_policy import (
    DEFAULT_MAX_AGE_DAYS,
    READINESS_VERSION,
    DataReadiness,
    DataRequirement,
    build_source_readiness,
    evaluate_requirements,
)

logger = get_logger(__name__)

MIN_FINALIZED_OUTCOMES = 5
MIN_PREDICTION_EVALS = 5
MIN_REAL_FEEDBACK = 5
_NON_EVIDENCE_STATUSES = {
    "pending",
    "missing",
    "no_data",
    "unknown",
    "no_kol_linked",
    "unavailable",
}
_OUTCOME_WINDOW_SCHEMA = "vkpi_gtm_observation_window/v1"
_OUTCOME_WINDOW_CONTRACTS: dict[str, tuple[str, str, int]] = {
    "window_7d": (
        "7d",
        "auto:outreach+fulfillment+gifted"
        "(vkpi_messages/vkpi_shipments/vkpi_content_posts/"
        "vkpi_kol_video_evidence/vkpi_project_kol_assignments)",
        7,
    ),
    "window_14d": (
        "14d",
        "auto:evidence+shortlinks(vkpi_kol_video_evidence/"
        "vkpi_link_clicks JOIN vkpi_links)",
        14,
    ),
    "window_28d": (
        "28d",
        "auto:shopify_attribution(vkpi_sales_attributions;"
        "本地归因链未上云,诚实 pending)",
        28,
    ),
}
_REAL_FEEDBACK_NOTE_MARKERS = (
    "%test%", "%demo%", "%synthetic%", "%fixture%", "%smoke%", "%dry_run%",
)
_REAL_FEEDBACK_METADATA_MARKERS = (
    "%test%", "%demo%", "%synthetic%", "%fixture%", "%smoke%", "%dry_run%",
    '%"environment": "test"%', '%"source": "test"%',
    '%"is_test": true%', "%gtm_weight_feedback%",
)


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def outcome_window_evidence_sha256(value: Any) -> str:
    # Never mutate the caller's frozen evidence while checking its digest.
    payload = dict(_json_dict(value))
    payload.pop("evidence_sha256", None)
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal_outcome_window_evidence(value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value or {})
    payload["evidence_sha256"] = outcome_window_evidence_sha256(payload)
    return payload


def has_observed_outcome_evidence(row: dict[str, Any]) -> bool:
    """Return whether a row has one server-produced, closed observation window.

    Legacy/manual JSON remains visible to operators but is descriptive only. A
    free-form ``actual_result`` or a window without the exact v1 producer
    contract must never raise a learning/effectiveness score.
    """
    for key, (label, source, horizon_days) in _OUTCOME_WINDOW_CONTRACTS.items():
        window = _json_dict(row.get(key))
        if (
            window.get("schema") != _OUTCOME_WINDOW_SCHEMA
            or str(window.get("status") or "").strip().lower() != "filled"
            or str(window.get("window") or "").strip().lower() != label
            or str(window.get("source") or "") != source
            or not isinstance(window.get("metrics"), dict)
            or not window.get("metrics")
        ):
            continue
        claimed_hash = str(window.get("evidence_sha256") or "").strip().lower()
        if (
            len(claimed_hash) != 64
            or any(char not in "0123456789abcdef" for char in claimed_hash)
            or claimed_hash != outcome_window_evidence_sha256(window)
        ):
            continue
        start = _parse_ts(window.get("window_start"))
        end = _parse_ts(window.get("window_end"))
        filled = _parse_ts(window.get("filled_at"))
        if start and end and filled and start < end <= filled and end - start == timedelta(days=horizon_days):
            return True
    return False


def has_verified_outcome_evidence(
    conn: Any,
    row: dict[str, Any],
    *,
    evidence_field: str | None = None,
) -> bool:
    """Require the immutable server event that committed a structural window.

    A self-consistent JSON hash is only a structural check.  Claimable evidence
    must also have a matching ``gtm_window_observed`` event emitted by the
    server producer for this exact outcome, Action, field, and window digest.
    """
    try:
        outcome_id = int(row.get("id") or 0)
        action_id = int(row.get("action_inbox_id") or 0)
    except (TypeError, ValueError):
        return False
    if outcome_id <= 0 or action_id <= 0:
        return False
    fields = [evidence_field] if evidence_field else list(_OUTCOME_WINDOW_CONTRACTS)
    try:
        events = conn.execute(
            """
            SELECT actor_type, actor_id, payload_json, trace_id, provenance_json
            FROM vkpi_event_ledger
            WHERE organization_id=? AND event_type='gtm_window_observed'
              AND entity_type='gtm_outcome' AND entity_id=?
              AND source='gtm_windows.refresh'
            ORDER BY id
            """,
            (1, str(outcome_id)),
        ).fetchall()
    except Exception:
        return False
    for field in fields:
        contract = _OUTCOME_WINDOW_CONTRACTS.get(str(field))
        if contract is None:
            continue
        window = _json_dict(row.get(str(field)))
        if not has_observed_outcome_evidence({str(field): window}):
            continue
        digest = outcome_window_evidence_sha256(window)
        label = contract[0]
        for raw in events:
            event = dict(raw)
            payload = _json_dict(event.get("payload_json"))
            provenance = _json_dict(event.get("provenance_json"))
            if (
                str(event.get("actor_type") or "") == "system"
                and str(event.get("actor_id") or "") == "gtm_windows"
                and bool(str(event.get("trace_id") or "").strip())
                and int(payload.get("outcome_id") or 0) == outcome_id
                and int(payload.get("action_inbox_id") or 0) == action_id
                and str(payload.get("evidence_field") or "") == str(field)
                and str(payload.get("schema") or "") == _OUTCOME_WINDOW_SCHEMA
                and str(payload.get("window") or "").lower() == label
                and str(payload.get("evidence_sha256") or "") == digest
                and provenance.get("evidence_verification")
                == "server_produced_observation_window"
            ):
                return True
    return False


def outcome_evidence_sql(
    prefix: str = "", *, fields: Iterable[str] | None = None,
) -> str:
    """Postgres predicate for claimable server-produced GTM window evidence."""
    qualifier = f"{prefix}." if prefix else "vkpi_gtm_outcomes."
    selected = set(fields or _OUTCOME_WINDOW_CONTRACTS)

    def nonempty(column: str) -> str:
        ref = f"{qualifier}{column}"
        return f"{ref} IS NOT NULL AND {ref} <> '{{}}'::jsonb AND {ref} <> 'null'::jsonb"

    windows: list[str] = []
    for column, (label, source, _days) in _OUTCOME_WINDOW_CONTRACTS.items():
        if column not in selected:
            continue
        safe_source = source.replace("'", "''")
        windows.append(
            f"""(
                {qualifier}action_inbox_id IS NOT NULL
                AND {nonempty(column)}
                AND jsonb_typeof({qualifier}{column}) = 'object'
                AND COALESCE({qualifier}{column}->>'schema', '') = '{_OUTCOME_WINDOW_SCHEMA}'
                AND LOWER(COALESCE({qualifier}{column}->>'status', '')) = 'filled'
                AND LOWER(COALESCE({qualifier}{column}->>'window', '')) = '{label}'
                AND COALESCE({qualifier}{column}->>'source', '') = '{safe_source}'
                AND COALESCE({qualifier}{column}->>'window_start', '') <> ''
                AND COALESCE({qualifier}{column}->>'window_end', '') <> ''
                AND COALESCE({qualifier}{column}->>'filled_at', '') <> ''
                AND COALESCE({qualifier}{column}->>'evidence_sha256', '')
                    ~ '^[0-9a-f]{{64}}$'
                AND jsonb_typeof({qualifier}{column}->'metrics') = 'object'
                AND {qualifier}{column}->'metrics' <> '{{}}'::jsonb
                AND EXISTS (
                    SELECT 1 FROM vkpi_event_ledger window_ev
                    WHERE window_ev.organization_id = 1
                      AND window_ev.event_type = 'gtm_window_observed'
                      AND window_ev.entity_type = 'gtm_outcome'
                      AND window_ev.entity_id = CAST({qualifier}id AS TEXT)
                      AND window_ev.source = 'gtm_windows.refresh'
                      AND window_ev.actor_type = 'system'
                      AND window_ev.actor_id = 'gtm_windows'
                      AND window_ev.trace_id IS NOT NULL
                      AND window_ev.trace_id <> ''
                      AND COALESCE(window_ev.payload_json->>'action_inbox_id', '')
                          = CAST({qualifier}action_inbox_id AS TEXT)
                      AND COALESCE(window_ev.payload_json->>'evidence_field', '') = '{column}'
                      AND COALESCE(window_ev.payload_json->>'schema', '')
                          = '{_OUTCOME_WINDOW_SCHEMA}'
                      AND COALESCE(window_ev.payload_json->>'evidence_sha256', '')
                          = COALESCE({qualifier}{column}->>'evidence_sha256', '')
                      AND COALESCE(window_ev.provenance_json->>'evidence_verification', '')
                          = 'server_produced_observation_window'
                )
            )"""
        )
    return " OR ".join(windows)


def real_recommendation_feedback_sql(prefix: str = "") -> tuple[str, tuple[str, ...]]:
    """One shared predicate for distinct, human, non-fixture feedback units."""
    qualifier = f"{prefix}." if prefix else ""
    conditions = [
        f"LOWER(COALESCE({qualifier}feedback_type, '')) "
        "IN ('claim', 'shortlist', 'reject', 'create_project')",
        f"{qualifier}created_by_staff_id IS NOT NULL",
    ]
    params: list[str] = []
    for marker in _REAL_FEEDBACK_NOTE_MARKERS:
        conditions.append(f"LOWER(COALESCE({qualifier}note, '')) NOT LIKE ?")
        params.append(marker)
    for marker in _REAL_FEEDBACK_METADATA_MARKERS:
        conditions.append(
            f"LOWER(COALESCE(CAST({qualifier}metadata_json AS TEXT), '')) NOT LIKE ?"
        )
        params.append(marker)
    return " AND ".join(conditions), tuple(params)


def verified_prediction_binding_sql(prefix: str = "e") -> str:
    """Postgres predicate for a structurally verified outcome-bound actual."""
    qualifier = f"{prefix}." if prefix else ""
    actual_json = f"{qualifier}actual_json"
    outcome_id = f"{qualifier}outcome_id"
    actual_value = f"{qualifier}actual_value"
    return f"""(
        COALESCE({actual_json}->>'binding_status', '') = 'verified_against_outcome'
        AND COALESCE({actual_json}->>'outcome_id', '') = {outcome_id}::text
        AND COALESCE({actual_json}->>'binding_sha256', '') ~ '^[0-9a-f]{{64}}$'
        AND COALESCE({actual_json}->>'run_snapshot_sha256', '') ~ '^[0-9a-f]{{64}}$'
        AND COALESCE({actual_json}->>'outcome_evidence_sha256', '') ~ '^[0-9a-f]{{64}}$'
        AND COALESCE({actual_json}->>'evidence_field', '')
            IN ('actual_result', 'window_7d', 'window_14d', 'window_28d')
        AND COALESCE({actual_json}->>'metric_path', '') <> ''
        AND CASE
            WHEN COALESCE({actual_json}->>'value', '')
                ~ '^-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$'
            THEN ({actual_json}->>'value')::double precision = {actual_value}
            ELSE FALSE
        END
    )"""


def verified_prediction_event_sql(prefix: str = "e") -> str:
    """Require the immutable staff verification event for one prediction eval."""
    qualifier = f"{prefix}." if prefix else ""
    return f"""EXISTS (
        SELECT 1
        FROM vkpi_event_ledger pev
        WHERE pev.event_type = 'prediction_actual_verified'
          AND pev.organization_id = 1
          AND pev.entity_type = 'prediction_eval'
          AND pev.entity_id = CAST({qualifier}id AS TEXT)
          AND pev.actor_type = 'staff'
          AND COALESCE(pev.actor_id, '') <> ''
          AND pev.source = 'prediction_ledger.human_actual_review'
          AND COALESCE(pev.trace_id, '') <> ''
          AND COALESCE(pev.payload_json->>'run_id', '') = {qualifier}run_id
          AND COALESCE(pev.payload_json->>'outcome_id', '') = {qualifier}outcome_id::text
          AND COALESCE(pev.payload_json->>'actual_binding_sha256', '')
              = COALESCE({qualifier}actual_json->>'binding_sha256', '')
          AND COALESCE(pev.payload_json->>'run_snapshot_sha256', '')
              = COALESCE({qualifier}actual_json->>'run_snapshot_sha256', '')
          AND COALESCE(pev.payload_json->>'outcome_evidence_sha256', '')
              = COALESCE({qualifier}actual_json->>'outcome_evidence_sha256', '')
          AND COALESCE(pev.provenance_json->>'evidence_verification', '')
              = 'server_resolved_outcome_contract'
          AND COALESCE(pev.provenance_json->>'prediction_run_immutable', '') = 'true'
          AND COALESCE(pev.provenance_json->>'payload_sha256', '') ~ '^[0-9a-f]{{64}}$'
    )"""


def _row(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        raw = conn.execute(sql, params).fetchone()
        return dict(raw) if raw else {}
    except Exception:
        logger.warning("market_brain.data_readiness.query_failed", exc_info=True)
        return {}


_LEARNING_TABLES = (
    "vkpi_prediction_runs",
    "vkpi_gtm_outcomes",
    "vkpi_prediction_evals",
    "vkpi_event_ledger",
    "vkpi_recommendation_feedback",
)


def _learning_table_presence(conn: Any) -> dict[str, bool]:
    """Resolve the fixed readiness schema set in one PostgreSQL round trip."""
    if is_postgres_runtime():
        projections = ", ".join(
            f"to_regclass(current_schema() || '.{name}') IS NOT NULL AS {name}"
            for name in _LEARNING_TABLES
        )
        try:
            raw = conn.execute(f"SELECT {projections}").fetchone()
            row = dict(raw) if raw else {}
            return {name: bool(row.get(name)) for name in _LEARNING_TABLES}
        except Exception:
            logger.warning("market_brain.data_readiness.table_presence_batch_failed", exc_info=True)
    return {name: bool(table_exists(name)) for name in _LEARNING_TABLES}


def build_learning_readiness(
    *,
    conn: Any = None,
    now: datetime | None = None,
    min_finalized_outcomes: int = MIN_FINALIZED_OUTCOMES,
    min_prediction_evals: int = MIN_PREDICTION_EVALS,
    min_real_feedback: int = MIN_REAL_FEEDBACK,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    """Read the three independent evidence legs required for learning claims.

    A finalized GTM outcome only counts as observed evidence when it has a real
    result/window payload and a human decision timestamp/actor. Feedback only
    counts when it has a human actor and is not marked demo/synthetic or emitted
    by the derived GTM weight-feedback bridge.
    """
    db = conn or get_conn()
    tables = _learning_table_presence(db)
    observed_outcome_sql = outcome_evidence_sql("o")
    outcomes = {}
    evals = {}
    feedback = {}
    outreach_coverage: dict[str, Any] = {
        "status": "not_applicable", "registered_due": 0, "claimable": False,
        "claim_level": "descriptive_only",
    }
    if tables["vkpi_prediction_runs"]:
        from app.domains.market_brain import outreach_truth_bridge

        outreach_coverage = outreach_truth_bridge.outreach_prediction_coverage(
            db, now=now,
        )
    outreach_due = int(outreach_coverage.get("registered_due") or 0)
    outreach_claimable = bool(outreach_coverage.get("claimable"))

    if tables["vkpi_gtm_outcomes"]:
        outcomes = _row(
            db,
            f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE decision <> 'open'
                      AND decided_at IS NOT NULL
                      AND decided_by IS NOT NULL
                ) AS finalized_total,
                COUNT(DISTINCT action_inbox_id) FILTER (
                    WHERE decision <> 'open'
                      AND decided_at IS NOT NULL
                      AND decided_by IS NOT NULL
                      AND ({observed_outcome_sql})
                ) AS observed,
                MAX(decided_at) FILTER (
                    WHERE decision <> 'open'
                      AND decided_at IS NOT NULL
                      AND decided_by IS NOT NULL
                      AND ({observed_outcome_sql})
                ) AS freshest_at
            FROM vkpi_gtm_outcomes o
            """,
        )

    if tables["vkpi_prediction_evals"] and tables["vkpi_gtm_outcomes"]:
        eval_outcome_evidence_sql = outcome_evidence_sql("o")
        finite_actual_sql = """(
            e.actual_value IS NOT NULL
            AND LOWER(e.actual_value::text) NOT IN ('nan', 'infinity', '-infinity')
        )"""
        verified_binding_sql = verified_prediction_binding_sql("e")
        verified_event_sql = (
            verified_prediction_event_sql("e")
            if tables["vkpi_event_ledger"]
            else "FALSE"
        )
        evals = _row(
            db,
            f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE e.actual_value IS NOT NULL
                ) AS raw_actual,
                COUNT(*) FILTER (
                    WHERE {finite_actual_sql}
                ) AS finite_actual,
                COUNT(DISTINCT e.outcome_id) FILTER (
                    WHERE {finite_actual_sql}
                      AND e.outcome_id IS NOT NULL
                      AND {verified_binding_sql}
                      AND {verified_event_sql}
                      AND e.error_abs IS NOT NULL
                      AND LOWER(e.error_abs::text) NOT IN ('nan', 'infinity', '-infinity')
                      AND o.id IS NOT NULL
                      AND o.decision <> 'open'
                      AND o.decided_at IS NOT NULL
                      AND o.decided_by IS NOT NULL
                      AND ({eval_outcome_evidence_sql})
                ) AS observed,
                MAX(e.evaluated_at) FILTER (
                    WHERE {finite_actual_sql}
                      AND e.outcome_id IS NOT NULL
                      AND {verified_binding_sql}
                      AND {verified_event_sql}
                      AND e.error_abs IS NOT NULL
                      AND LOWER(e.error_abs::text) NOT IN ('nan', 'infinity', '-infinity')
                      AND o.id IS NOT NULL
                      AND o.decision <> 'open'
                      AND o.decided_at IS NOT NULL
                      AND o.decided_by IS NOT NULL
                      AND ({eval_outcome_evidence_sql})
                ) AS freshest_at
            FROM vkpi_prediction_evals e
            LEFT JOIN vkpi_gtm_outcomes o ON o.id = e.outcome_id
            LEFT JOIN vkpi_prediction_runs pr
              ON pr.organization_id=e.organization_id AND pr.run_id=e.run_id
            WHERE (
              COALESCE(pr.task_type, '') <> 'kol_outreach_reply_probability'
              OR {'TRUE' if outreach_claimable else 'FALSE'}
            )
            """,
        )

    if tables["vkpi_recommendation_feedback"]:
        real_feedback_sql, real_feedback_params = real_recommendation_feedback_sql()
        feedback = _row(
            db,
            f"""
            SELECT COUNT(DISTINCT recommendation_id) FILTER (
                       WHERE {real_feedback_sql}
                   ) AS observed,
                   MAX(created_at) FILTER (
                       WHERE {real_feedback_sql}
                   ) AS freshest_at
            FROM vkpi_recommendation_feedback
            """,
            real_feedback_params * 2,
        )

    requirements = [
        DataRequirement(
            key="finalized_outcomes",
            label="evidence-backed human-finalized GTM outcomes",
            observed=int(outcomes.get("observed") or 0),
            minimum=min_finalized_outcomes,
            freshest_at=outcomes.get("freshest_at"),
            max_age_days=max_age_days,
        ),
        DataRequirement(
            key="prediction_evals",
            label="distinct outcomes with finite, evidence-bound prediction actuals",
            observed=int(evals.get("observed") or 0),
            minimum=min_prediction_evals,
            freshest_at=evals.get("freshest_at"),
            max_age_days=max_age_days,
        ),
        DataRequirement(
            key="real_feedback",
            label="non-demo human recommendation feedback",
            observed=int(feedback.get("observed") or 0),
            minimum=min_real_feedback,
            freshest_at=feedback.get("freshest_at"),
            max_age_days=max_age_days,
        ),
    ]
    if outreach_due > 0 or outreach_coverage.get("status") == "error":
        required_actuals = max(
            50,
            int(math.ceil(outreach_due * 0.90)),
        )
        requirements.append(
            DataRequirement(
                key="outreach_prediction_coverage",
                label=(
                    "verified outreach actuals across every due registered prediction"
                ),
                observed=(
                    int(outreach_coverage.get("verified_actual") or 0)
                    if outreach_claimable else 0
                ),
                minimum=required_actuals,
            )
        )
    result = evaluate_requirements(requirements, now=now).to_dict()
    result["facts"] = {
        "finalized_outcomes_total": int(outcomes.get("finalized_total") or 0),
        "evidence_backed_finalized_outcomes": int(outcomes.get("observed") or 0),
        "prediction_evals_with_actual_raw": int(evals.get("raw_actual") or 0),
        "prediction_evals_with_finite_actual": int(evals.get("finite_actual") or 0),
        "prediction_evals_with_actual": int(evals.get("observed") or 0),
        "distinct_prediction_outcomes_with_verified_actual": int(evals.get("observed") or 0),
        "real_human_feedback": int(feedback.get("observed") or 0),
    }
    if outreach_due > 0 or outreach_coverage.get("status") == "error":
        result["facts"]["outreach_prediction_coverage"] = outreach_coverage
    result["policy"] = {
        "raw_observations_may_be_shown": True,
        "effectiveness_claims_require_ready": True,
        "automatic_business_outcome_creation": False,
        "prediction_eval_requires_human_finalized_outcome_evidence": True,
        "prediction_eval_counts_distinct_outcomes": True,
        "prediction_eval_requires_verified_metric_binding": True,
        "prediction_eval_claim_unit": "distinct_outcome_id",
        "outreach_probability_metric": "brier_score",
        "outreach_wape_is_accuracy": False,
        "outreach_due_denominator_is_exhaustive": True,
        "outreach_unverified_censors_count_as_covered": False,
    }
    return result


__all__ = [
    "DataRequirement",
    "DataReadiness",
    "evaluate_requirements",
    "build_source_readiness",
    "build_learning_readiness",
    "verified_prediction_event_sql",
    "has_observed_outcome_evidence",
    "has_verified_outcome_evidence",
    "outcome_evidence_sql",
    "real_recommendation_feedback_sql",
    "READINESS_VERSION",
    "MIN_FINALIZED_OUTCOMES",
    "MIN_PREDICTION_EVALS",
    "MIN_REAL_FEEDBACK",
    "DEFAULT_MAX_AGE_DAYS",
]
