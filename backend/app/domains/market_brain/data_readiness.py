"""Shared data-readiness gates for Market Brain claims.

The gate separates an implemented capability from enough recent, observed
business evidence to make an effectiveness claim. It is read-only and never
creates outcomes, evaluations, or feedback rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

READINESS_VERSION = "market_brain_data_readiness_v1"
MIN_FINALIZED_OUTCOMES = 5
MIN_PREDICTION_EVALS = 5
MIN_REAL_FEEDBACK = 5
DEFAULT_MAX_AGE_DAYS = 30
_NON_EVIDENCE_STATUSES = {
    "pending",
    "missing",
    "no_data",
    "unknown",
    "no_kol_linked",
    "unavailable",
}


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


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


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


def has_observed_outcome_evidence(row: dict[str, Any]) -> bool:
    """Return whether an outcome row contains observed evidence, not a placeholder."""
    actual = _json_dict(row.get("actual_result"))
    actual_status = str(actual.get("status") or "").strip().lower()
    if actual and actual_status not in _NON_EVIDENCE_STATUSES:
        return True
    for key in ("window_7d", "window_14d", "window_28d"):
        window = _json_dict(row.get(key))
        if not window:
            continue
        status = str(window.get("status") or "").strip().lower()
        # Legacy manually-entered window payloads did not always include status.
        if not status or status == "filled":
            return True
    return False


def outcome_evidence_sql(prefix: str = "") -> str:
    """Postgres predicate matching ``has_observed_outcome_evidence``."""
    qualifier = f"{prefix}." if prefix else ""

    def nonempty(column: str) -> str:
        ref = f"{qualifier}{column}"
        return f"{ref} IS NOT NULL AND {ref} <> '{{}}'::jsonb AND {ref} <> 'null'::jsonb"

    actual = f"""(
        {nonempty('actual_result')}
        AND LOWER(COALESCE({qualifier}actual_result->>'status', ''))
            NOT IN ('pending', 'missing', 'no_data', 'unknown', 'no_kol_linked', 'unavailable')
    )"""
    windows = [
        f"""(
            {nonempty(column)}
            AND LOWER(COALESCE({qualifier}{column}->>'status', 'filled')) = 'filled'
        )"""
        for column in ("window_7d", "window_14d", "window_28d")
    ]
    return " OR ".join([actual, *windows])


@dataclass(frozen=True)
class DataRequirement:
    key: str
    observed: int
    minimum: int
    freshest_at: Any = None
    max_age_days: int | None = None
    label: str = ""


@dataclass(frozen=True)
class DataReadiness:
    status: str
    ready: bool
    claimable: bool
    claim_level: str
    checks: dict[str, dict[str, Any]]
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": READINESS_VERSION,
            "status": self.status,
            "ready": self.ready,
            "claimable": self.claimable,
            "claim_level": self.claim_level,
            "checks": self.checks,
            "blockers": list(self.blockers),
            "note": (
                "Effectiveness claims require every sample and freshness check to pass; "
                "otherwise values are descriptive observations only."
            ),
        }


def evaluate_requirements(
    requirements: Iterable[DataRequirement],
    *,
    now: datetime | None = None,
) -> DataReadiness:
    """Evaluate sample and freshness requirements without touching a database."""
    current = _now(now)
    checks: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    has_stale = False
    has_insufficient = False

    for requirement in requirements:
        observed = max(0, int(requirement.observed or 0))
        minimum = max(0, int(requirement.minimum or 0))
        max_age_days = (
            max(0, int(requirement.max_age_days))
            if requirement.max_age_days is not None
            else None
        )
        parsed = _parse_ts(requirement.freshest_at)
        age_days = None
        freshness_status = "not_required"
        if max_age_days is not None:
            if parsed is None:
                freshness_status = "unknown"
            else:
                age_days = round(max(0.0, (current - parsed).total_seconds() / 86400.0), 2)
                freshness_status = "fresh" if age_days <= max_age_days else "stale"

        if observed < minimum:
            status = "insufficient"
            has_insufficient = True
            blockers.append(f"{requirement.key}:sample<{minimum}")
        elif freshness_status == "unknown":
            status = "freshness_unknown"
            has_insufficient = True
            blockers.append(f"{requirement.key}:freshness_unknown")
        elif freshness_status == "stale":
            status = "stale"
            has_stale = True
            blockers.append(f"{requirement.key}:stale>{max_age_days}d")
        else:
            status = "ready"

        checks[requirement.key] = {
            "label": requirement.label or requirement.key,
            "status": status,
            "observed": observed,
            "minimum": minimum,
            "freshest_at": parsed.isoformat() if parsed is not None else None,
            "age_days": age_days,
            "max_age_days": max_age_days,
            "sample_ready": observed >= minimum,
            "freshness_status": freshness_status,
        }

    ready = bool(checks) and not blockers
    if ready:
        status = "ready"
    elif has_insufficient:
        status = "insufficient"
    elif has_stale:
        status = "stale"
    else:
        status = "insufficient"
    return DataReadiness(
        status=status,
        ready=ready,
        claimable=ready,
        claim_level="validated" if ready else "descriptive_only",
        checks=checks,
        blockers=tuple(blockers),
    )


def build_source_readiness(
    source_key: str,
    *,
    observed: int,
    freshest_at: Any,
    minimum: int = 1,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    return evaluate_requirements(
        [
            DataRequirement(
                key=source_key,
                label=f"{source_key} observed source rows",
                observed=observed,
                minimum=minimum,
                freshest_at=freshest_at,
                max_age_days=max_age_days,
            )
        ],
        now=now,
    ).to_dict()


def _row(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    try:
        raw = conn.execute(sql, params).fetchone()
        return dict(raw) if raw else {}
    except Exception:
        logger.warning("market_brain.data_readiness.query_failed", exc_info=True)
        return {}


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
    observed_outcome_sql = outcome_evidence_sql()
    outcomes = {}
    evals = {}
    feedback = {}

    if table_exists("vkpi_gtm_outcomes"):
        outcomes = _row(
            db,
            f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE decision <> 'open'
                      AND decided_at IS NOT NULL
                      AND decided_by IS NOT NULL
                ) AS finalized_total,
                COUNT(*) FILTER (
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
            FROM vkpi_gtm_outcomes
            """,
        )

    if table_exists("vkpi_prediction_evals") and table_exists("vkpi_gtm_outcomes"):
        eval_outcome_evidence_sql = outcome_evidence_sql("o")
        finite_actual_sql = """(
            e.actual_value IS NOT NULL
            AND LOWER(e.actual_value::text) NOT IN ('nan', 'infinity', '-infinity')
        )"""
        verified_binding_sql = """(
            COALESCE(e.actual_json->>'binding_status', '') = 'verified_against_outcome'
            AND COALESCE(e.actual_json->>'outcome_id', '') = e.outcome_id::text
            AND COALESCE(e.actual_json->>'evidence_field', '')
                IN ('actual_result', 'window_7d', 'window_14d', 'window_28d')
            AND COALESCE(e.actual_json->>'metric_path', '') <> ''
            AND CASE
                WHEN COALESCE(e.actual_json->>'value', '')
                    ~ '^-?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?$'
                THEN (e.actual_json->>'value')::double precision = e.actual_value
                ELSE FALSE
            END
        )"""
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
                      AND o.id IS NOT NULL
                      AND o.decision <> 'open'
                      AND o.decided_at IS NOT NULL
                      AND o.decided_by IS NOT NULL
                      AND ({eval_outcome_evidence_sql})
                ) AS freshest_at
            FROM vkpi_prediction_evals e
            LEFT JOIN vkpi_gtm_outcomes o ON o.id = e.outcome_id
            """,
        )

    if table_exists("vkpi_recommendation_feedback"):
        feedback = _row(
            db,
            """
            SELECT COUNT(*) FILTER (
                       WHERE created_by_staff_id IS NOT NULL
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                   ) AS observed,
                   MAX(created_at) FILTER (
                       WHERE created_by_staff_id IS NOT NULL
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(note, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                         AND LOWER(COALESCE(metadata_json::text, '')) NOT LIKE ?
                   ) AS freshest_at
            FROM vkpi_recommendation_feedback
            """,
            (
                "%demo%", "%synthetic%", "%fixture%",
                "%demo%", "%synthetic%", "%fixture%",
                '%"environment": "test"%', '%"source": "test"%',
                '%"is_test": true%', "%gtm_weight_feedback%",
            ) * 2,
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
    result["policy"] = {
        "raw_observations_may_be_shown": True,
        "effectiveness_claims_require_ready": True,
        "automatic_business_outcome_creation": False,
        "prediction_eval_requires_human_finalized_outcome_evidence": True,
        "prediction_eval_counts_distinct_outcomes": True,
        "prediction_eval_requires_verified_metric_binding": True,
        "prediction_eval_claim_unit": "distinct_outcome_id",
    }
    return result


__all__ = [
    "DataRequirement",
    "DataReadiness",
    "evaluate_requirements",
    "build_source_readiness",
    "build_learning_readiness",
    "has_observed_outcome_evidence",
    "outcome_evidence_sql",
    "READINESS_VERSION",
    "MIN_FINALIZED_OUTCOMES",
    "MIN_PREDICTION_EVALS",
    "MIN_REAL_FEEDBACK",
    "DEFAULT_MAX_AGE_DAYS",
]
