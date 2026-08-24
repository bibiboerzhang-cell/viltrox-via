"""Truth-gated rollups for prediction evaluations.

Raw metrics remain useful diagnostics, but claimable metrics require one
immutable, human-verified evaluation per distinct business outcome.  Binary
outreach probabilities additionally require the due-run coverage gate.

A third, weaker tier is *measured*: KOL view forecasts (``vkpi_forecast_log``)
whose actual is the median of snapshot-tracked view counts observed after the
forecast.  Those are instrument-measured, not human-verified, so they never
enter the verified rollup; they get their own block with a higher sample floor
(:data:`MIN_MEASURED_CLAIMABLE_EVALS`) and are what the Dashboard accuracy card
reports as ``n/20``.
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

MIN_BINARY_CLAIMABLE_EVALS = 50
MIN_MEASURED_CLAIMABLE_EVALS = 20
MEASURED_BINDING_STATUS = "measured_from_snapshots"
FORECAST_LOG_RUN_PREFIX = "fclog_"
FORECAST_LOG_TASK_TYPE = "kol_views"
_RESOLVED_OUTCOMES = ("hit_in_band", "below", "above")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "t", "1", "yes"}:
        return True
    if text in {"false", "f", "0", "no"}:
        return False
    return None


def binary_brier_rollup(
    rows: list[dict[str, Any]], *, outreach_coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return Brier score for one verified Bernoulli actual per outcome."""
    binary_rows = [
        row for row in rows
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability"
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    run_counts: dict[str, int] = {}
    invalid_n = 0
    for row in binary_rows:
        run_id = str(row.get("run_id") or "").strip()
        outcome_id = str(row.get("outcome_id") or "").strip()
        if not run_id or not outcome_id:
            invalid_n += 1
            continue
        groups.setdefault(outcome_id, []).append(row)
        run_counts[run_id] = run_counts.get(run_id, 0) + 1
    squared_errors: list[float] = []
    for grouped in groups.values():
        if len(grouped) != 1:
            invalid_n += len(grouped)
            continue
        row = grouped[0]
        if run_counts.get(str(row.get("run_id") or "").strip()) != 1:
            invalid_n += 1
            continue
        probability = _number(row.get("p50"))
        actual = _number(row.get("actual_value"))
        if (
            probability is None or not 0.0 <= probability <= 1.0
            or actual not in {0.0, 1.0}
            or _boolean(row.get("verified_actual")) is not True
        ):
            invalid_n += 1
            continue
        squared_errors.append((probability - actual) ** 2)
    coverage = outreach_coverage or {}
    coverage_claimable = bool(coverage.get("claimable"))
    n = len(squared_errors)
    score = round(sum(squared_errors) / n, 6) if n else None
    claimable = n >= MIN_BINARY_CLAIMABLE_EVALS and coverage_claimable
    return {
        "task_type": "kol_outreach_reply_probability",
        "metric": "brier_score",
        "brier_score": score,
        "n": n,
        "invalid_n": invalid_n,
        "coverage": coverage,
        "coverage_claimable": coverage_claimable,
        "minimum_verified_outcomes": MIN_BINARY_CLAIMABLE_EVALS,
        "claimable": claimable,
        "claim_level": "validated" if claimable else "descriptive_only",
    }


def verified_nonbinary_rollup(
    rows: list[dict[str, Any]], *, minimum: int,
) -> dict[str, Any]:
    """Aggregate one verified row per distinct outcome; reject duplicates."""
    groups: dict[str, list[dict[str, Any]]] = {}
    invalid_n = 0
    for row in rows:
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability":
            continue
        outcome_id = str(row.get("outcome_id") or "").strip()
        if not outcome_id or _boolean(row.get("verified_actual")) is not True:
            invalid_n += 1
            continue
        groups.setdefault(outcome_id, []).append(row)

    verified: list[dict[str, Any]] = []
    for grouped in groups.values():
        if len(grouped) != 1:
            invalid_n += len(grouped)
            continue
        row = grouped[0]
        if _number(row.get("actual_value")) is None or _number(row.get("error_abs")) is None:
            invalid_n += 1
            continue
        verified.append(row)

    err_sum = sum(abs(_number(row.get("error_abs")) or 0.0) for row in verified)
    act_sum = sum(abs(_number(row.get("actual_value")) or 0.0) for row in verified)
    interval = [
        value for value in (_boolean(row.get("interval_hit")) for row in verified)
        if value is not None
    ]
    direction = [
        value for value in (_boolean(row.get("direction_hit")) for row in verified)
        if value is not None
    ]
    n = len(verified)
    claimable = n >= minimum
    return {
        "n": n,
        "invalid_n": invalid_n,
        "wape": round(err_sum / act_sum, 4) if n and act_sum > 0 else None,
        "interval_coverage": round(sum(interval) / len(interval), 4) if interval else None,
        "direction_hit_rate": round(sum(direction) / len(direction), 4) if direction else None,
        "interval_n": len(interval),
        "direction_n": len(direction),
        "claimable": claimable,
    }


def measured_nonbinary_rollup(
    rows: list[dict[str, Any]], *, minimum: int = MIN_MEASURED_CLAIMABLE_EVALS,
) -> dict[str, Any]:
    """Aggregate snapshot-measured KOL view evals (one per forecast-log row).

    Rows qualify when their ``actual_json.binding_status`` is
    :data:`MEASURED_BINDING_STATUS` and at least one successful metric snapshot
    backs the measured sample.  A row that claims the measured binding without
    a positive ``snapshot_backed_count`` is retained in ``invalid_n`` instead
    of inflating the claimable sample.  Verified outcome-bound rows are handled
    by :func:`verified_nonbinary_rollup` and are excluded here, so the two tiers
    never double count.
    """
    measured: list[dict[str, Any]] = []
    invalid_n = 0
    for row in rows:
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability":
            continue
        payload = _actual_json(row.get("actual_json"))
        if payload.get("binding_status") != MEASURED_BINDING_STATUS:
            continue
        if (_int(payload.get("snapshot_backed_count")) or 0) <= 0:
            invalid_n += 1
            continue
        if _number(row.get("actual_value")) is None or _number(row.get("error_abs")) is None:
            invalid_n += 1
            continue
        measured.append(row)
    err_sum = sum(abs(_number(row.get("error_abs")) or 0.0) for row in measured)
    act_sum = sum(abs(_number(row.get("actual_value")) or 0.0) for row in measured)
    interval = [
        value for value in (_boolean(row.get("interval_hit")) for row in measured)
        if value is not None
    ]
    direction = [
        value for value in (_boolean(row.get("direction_hit")) for row in measured)
        if value is not None
    ]
    n = len(measured)
    return {
        "binding_status": MEASURED_BINDING_STATUS,
        "n": n,
        "invalid_n": invalid_n,
        "minimum": minimum,
        "wape": round(err_sum / act_sum, 4) if n and act_sum > 0 else None,
        "interval_coverage": round(sum(interval) / len(interval), 4) if interval else None,
        "direction_hit_rate": round(sum(direction) / len(direction), 4) if direction else None,
        "interval_n": len(interval),
        "direction_n": len(direction),
        "claimable": n >= minimum,
        "claim_level": "measured" if n >= minimum else "insufficient_sample",
        "sample_label": f"{n}/{minimum}",
    }


def _actual_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace(" ", "T", 1).replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ── forecast_log → measured actual → eval → weekly metrics ────────────────


def backfill_forecast_log_actuals(
    conn: Any, *, min_age_days: int = 30, limit: int = 500,
) -> dict[str, Any]:
    """Write measured actuals back to ``vkpi_forecast_log`` through its one writer.

    ``learning.forecast_feedback.refresh_forecast_outcomes`` owns the redline on
    the three target columns (actual_views/actual_at/outcome); this only routes
    the weekly rollup through it with the caller's connection.
    """
    from app.domains.learning import forecast_feedback

    return forecast_feedback.refresh_forecast_outcomes(min_age_days, limit, conn=conn)


def _measured_sample(
    conn: Any, *, kol_pool_id: int, window_from: datetime, window_to: datetime,
) -> dict[str, Any]:
    """Snapshot-backed evidence behind one measured actual (diagnostic payload)."""
    rows = conn.execute(
        """
        SELECT e.id, e.view_count, e.posted_at,
               (
                   SELECT COUNT(*) FROM vkpi_content_metric_snapshots s
                   WHERE s.evidence_id=e.id AND s.status='success'
               ) AS success_snapshots
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id=? AND e.posted_at IS NOT NULL AND e.is_active IS NOT FALSE
          AND e.view_count > 0
        """,
        (int(kol_pool_id),),
    ).fetchall()
    evidence_ids: list[int] = []
    snapshot_backed: list[int] = []
    views: list[float] = []
    for raw in rows:
        row = dict(raw)
        posted = _ts(row.get("posted_at"))
        if posted is None or posted < window_from or posted > window_to:
            continue
        evidence_id = _int(row.get("id"))
        if evidence_id is None:
            continue
        evidence_ids.append(evidence_id)
        views.append(float(row.get("view_count") or 0))
        if _int(row.get("success_snapshots")):
            snapshot_backed.append(evidence_id)
    return {
        "sample_count": len(evidence_ids),
        "snapshot_backed_count": len(snapshot_backed),
        "evidence_ids": evidence_ids[:50],
        "median_views": int(round(statistics.median(views))) if views else None,
    }


def record_forecast_log_evals(
    conn: Any, *, scan_limit: int = 500, organization_id: str | None = None,
) -> dict[str, Any]:
    """UPSERT one measured eval per resolved forecast-log row (idempotent).

    Uses the caller's connection (unlike ``prediction_ledger.record_eval``) so
    the weekly rollup is one transaction.  The quantiles come from the
    ``fclog_<id>`` ledger run when it exists; forecasts older than the ledger
    dual-write carry the same p10/p50/p90 on the forecast-log row itself, so
    those are evaluated from the row (``prediction_source='forecast_log'``)
    rather than dropped — the row is the immutable prediction record.
    """
    from app.domains.market_brain import prediction_ledger

    org = organization_id or prediction_ledger.DEFAULT_ORG
    result = {
        "scanned": 0, "recorded": 0, "updated": 0,
        "from_ledger_run": 0, "from_forecast_log": 0, "skipped_no_band": 0, "errors": 0,
    }
    rows = conn.execute(
        f"""
        SELECT f.id, f.kol_pool_id, f.created_at, f.actual_views, f.actual_at, f.outcome,
               f.p10 AS log_p10, f.p50 AS log_p50, f.p90 AS log_p90,
               r.p10, r.p50, r.p90, r.id AS run_pk
        FROM vkpi_forecast_log f
        LEFT JOIN {prediction_ledger.RUNS_TABLE} r
            ON r.organization_id=? AND r.run_id=('{FORECAST_LOG_RUN_PREFIX}' || CAST(f.id AS TEXT))
        WHERE f.outcome IN ('hit_in_band', 'below', 'above') AND f.actual_views IS NOT NULL
        ORDER BY f.id DESC
        LIMIT ?
        """,
        (org, max(1, min(int(scan_limit), 5000))),
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        result["scanned"] += 1
        log_id = _int(row.get("id"))
        if log_id is None:
            continue
        run_id = f"{FORECAST_LOG_RUN_PREFIX}{log_id}"
        actual = _number(row.get("actual_views"))
        if actual is None:
            continue
        if row.get("run_pk") is not None:
            band = (row.get("p10"), row.get("p50"), row.get("p90"))
            prediction_source = "prediction_runs"
        else:
            band = (row.get("log_p10"), row.get("log_p50"), row.get("log_p90"))
            prediction_source = "forecast_log"
        if _number(band[1]) is None:
            result["skipped_no_band"] += 1
            continue
        result["from_ledger_run" if prediction_source == "prediction_runs" else "from_forecast_log"] += 1
        metrics = prediction_ledger.compute_eval_metrics(band[0], band[1], band[2], actual)
        window_from = _ts(row.get("created_at")) or datetime.now(timezone.utc)
        window_to = _ts(row.get("actual_at")) or datetime.now(timezone.utc)
        sample = _measured_sample(
            conn,
            kol_pool_id=int(row.get("kol_pool_id") or 0),
            window_from=window_from,
            window_to=window_to,
        )
        actual_json = json.dumps({
            "binding_status": MEASURED_BINDING_STATUS,
            "source": "prediction_rollup_truth.record_forecast_log_evals",
            "prediction_source": prediction_source,
            "p10": _number(band[0]), "p50": _number(band[1]), "p90": _number(band[2]),
            "forecast_log_id": log_id,
            "kol_pool_id": _int(row.get("kol_pool_id")),
            "outcome": str(row.get("outcome") or ""),
            "actual_at": window_to.isoformat(),
            **sample,
        }, ensure_ascii=False)
        try:
            existing = conn.execute(
                f"""
                SELECT id FROM {prediction_ledger.EVALS_TABLE}
                WHERE organization_id=? AND run_id=? AND outcome_id IS NULL
                """,
                (org, run_id),
            ).fetchone()
            if existing:
                conn.execute(
                    f"""
                    UPDATE {prediction_ledger.EVALS_TABLE} SET
                        actual_value=?, actual_json=?::jsonb, error_abs=?, error_pct=?,
                        interval_hit=?, direction_hit=?, evaluated_at=NOW(), notes=?
                    WHERE id=?
                    """,
                    (
                        actual, actual_json, metrics["error_abs"], metrics["error_pct"],
                        metrics["interval_hit"], metrics["direction_hit"],
                        "forecast_log_rollup", int(dict(existing)["id"]),
                    ),
                )
                result["updated"] += 1
            else:
                conn.execute(
                    f"""
                    INSERT INTO {prediction_ledger.EVALS_TABLE} (
                        organization_id, run_id, outcome_id, actual_value, actual_json,
                        error_abs, error_pct, interval_hit, direction_hit, notes
                    ) VALUES (?, ?, NULL, ?, ?::jsonb, ?, ?, ?, ?, ?)
                    """,
                    (
                        org, run_id, actual, actual_json, metrics["error_abs"],
                        metrics["error_pct"], metrics["interval_hit"],
                        metrics["direction_hit"], "forecast_log_rollup",
                    ),
                )
                result["recorded"] += 1
        except Exception:
            result["errors"] += 1
            logger.warning("forecast_log eval upsert failed run_id=%s", run_id, exc_info=True)
            raise
    return result


def forecast_log_weekly_metrics(
    conn: Any, *, days: int = 7, limit: int = 2000, organization_id: str | None = None,
) -> dict[str, Any]:
    """Recent evals joined with runs → ``prediction_ledger.weekly_rollup`` (WAPE/FVA)."""
    from app.domains.market_brain import prediction_ledger

    org = organization_id or prediction_ledger.DEFAULT_ORG
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    rows = conn.execute(
        f"""
        SELECT e.run_id, e.outcome_id, e.actual_value, e.actual_json, e.error_abs,
               e.interval_hit, e.direction_hit,
               r.task_type, r.p50, r.source_step, r.product_sku, r.market, r.channel,
               r.baseline_value
        FROM {prediction_ledger.EVALS_TABLE} e
        LEFT JOIN {prediction_ledger.RUNS_TABLE} r
            ON r.organization_id=e.organization_id AND r.run_id=e.run_id
        WHERE e.organization_id=? AND e.evaluated_at >= ?
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (org, cutoff, max(1, min(int(limit), 5000))),
    ).fetchall()
    return prediction_ledger.weekly_rollup([dict(r) for r in rows])


def rollup_forecast_log_truth(
    conn: Any,
    *,
    min_age_days: int = 30,
    backfill_limit: int = 500,
    scan_limit: int = 500,
    commit: bool = True,
) -> dict[str, Any]:
    """Weekly truth rollup: measured actuals → forecast_log → evals → WAPE/FVA.

    Only ``vkpi_forecast_log`` (three outcome columns, via its owning writer)
    and ``vkpi_prediction_evals`` are written.  Never touches fit scores.
    """
    result: dict[str, Any] = {"status": "ok"}
    result["backfill"] = backfill_forecast_log_actuals(
        conn, min_age_days=min_age_days, limit=backfill_limit,
    )
    result["evals"] = record_forecast_log_evals(conn, scan_limit=scan_limit)
    if commit:
        conn.commit()
    weekly = forecast_log_weekly_metrics(conn)
    result["weekly"] = weekly
    measured = weekly.get("measured_nonbinary") or {}
    result["metrics"] = {
        "wape": measured.get("wape"),
        "interval_coverage": measured.get("interval_coverage"),
        "direction_hit_rate": measured.get("direction_hit_rate"),
        "fva": {
            "n_groups": (weekly.get("fva") or {}).get("n_groups"),
            "mean_delta": (weekly.get("fva") or {}).get("mean_delta"),
            "model_better_share": (weekly.get("fva") or {}).get("model_better_share"),
        },
        "measured_n": measured.get("n"),
        "measured_minimum": measured.get("minimum"),
        "measured_claimable": measured.get("claimable"),
    }
    return result


__all__ = [
    "FORECAST_LOG_RUN_PREFIX",
    "MEASURED_BINDING_STATUS",
    "MIN_BINARY_CLAIMABLE_EVALS",
    "MIN_MEASURED_CLAIMABLE_EVALS",
    "backfill_forecast_log_actuals",
    "binary_brier_rollup",
    "forecast_log_weekly_metrics",
    "measured_nonbinary_rollup",
    "record_forecast_log_evals",
    "rollup_forecast_log_truth",
    "verified_nonbinary_rollup",
]
