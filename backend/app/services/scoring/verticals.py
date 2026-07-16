"""
services/scoring/verticals.py — 垂类权重学习（Ridge 回归）
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from typing import Any

from pydantic import BaseModel

from app.core.constants import TECH_DIMS, MARKETING_DIMS, VERTICAL_WEIGHTS
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

VERTICAL_TRAINING_GATE_VERSION = "vertical_ridge_training_gate_v1"
HARD_MIN_VALID_SAMPLES = 30
MIN_TRAIN_SAMPLES = 24
MIN_HOLDOUT_SAMPLES = 6
MIN_DISTINCT_TARGETS = 5
MIN_POSITIVE_TARGETS = 5
MIN_PARTITION_DISTINCT_TARGETS = 2
MIN_TIME_SPAN_HOURS = 24
HOLDOUT_FRACTION = 0.20

# ``VERTICAL_WEIGHTS`` is mutated in place when a learned artifact is applied.
# Keep an immutable-by-convention snapshot so a rejected/legacy artifact can
# actively restore rule_v0 instead of leaving a previously loaded bad weight set
# resident in the worker process.
_RULE_V0_VERTICAL_WEIGHTS = deepcopy(VERTICAL_WEIGHTS)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_nonnegative_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _build_training_sample(row: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Convert one DB row into explicit features and an observed outcome label."""

    created_at = _parse_timestamp(_row_value(row, "created_at"))
    if created_at is None:
        return None, "invalid_created_at"

    raw_analysis = _row_value(row, "video_analysis", "{}")
    try:
        analysis = raw_analysis if isinstance(raw_analysis, dict) else json.loads(str(raw_analysis or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, "invalid_video_analysis"
    quality_scores = analysis.get("quality_scores") if isinstance(analysis, dict) else None
    if not isinstance(quality_scores, dict):
        return None, "missing_quality_scores"

    features: dict[str, float] = {}
    for dimension in (*TECH_DIMS, *MARKETING_DIMS):
        if dimension not in quality_scores:
            return None, "incomplete_quality_scores"
        score = _as_nonnegative_number(quality_scores.get(dimension))
        if score is None or score > 10:
            return None, "invalid_quality_score"
        features[dimension] = score

    views = _as_nonnegative_number(_row_value(row, "views", 0))
    likes = _as_nonnegative_number(_row_value(row, "likes", 0))
    comments = _as_nonnegative_number(_row_value(row, "comments", 0))
    shares = _as_nonnegative_number(_row_value(row, "shares", 0))
    if views is None or views <= 0 or likes is None or comments is None or shares is None:
        return None, "invalid_engagement_metrics"

    # Predict an observed engagement-rate outcome instead of raw reach. Including
    # views as a positive term (the legacy behavior) mostly taught audience size,
    # not whether the scored content dimensions explained engagement.
    weighted_interactions = likes + comments * 2.0 + shares * 3.0
    outcome = math.log1p((weighted_interactions / views) * 1000.0)

    return {
        "source_id": _row_value(row, "id"),
        "created_at": created_at,
        "tech": [features[dimension] for dimension in TECH_DIMS],
        "mkt": [features[dimension] for dimension in MARKETING_DIMS],
        "target": outcome,
    }, None


def _distinct_count(values: list[float], *, precision: int = 12) -> int:
    return len({round(float(value), precision) for value in values})


def _feature_variants(samples: list[dict[str, Any]], axis: str) -> int:
    return len({tuple(float(value) for value in sample[axis]) for sample in samples})


def evaluate_vertical_training_readiness(
    samples: list[dict[str, Any]],
    *,
    vertical: str,
    min_samples: int = HARD_MIN_VALID_SAMPLES,
    raw_samples: int | None = None,
    rejected_reasons: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Return a pure, auditable chronological training-gate decision."""

    required_samples = max(HARD_MIN_VALID_SAMPLES, int(min_samples or 0))
    ordered = sorted(samples, key=lambda item: (item["created_at"], str(item.get("source_id") or "")))
    valid_samples = len(ordered)
    holdout_samples = max(MIN_HOLDOUT_SAMPLES, math.ceil(valid_samples * HOLDOUT_FRACTION))
    train_samples = max(0, valid_samples - holdout_samples)
    split_index = train_samples
    train = ordered[:split_index]
    holdout = ordered[split_index:]
    targets = [float(sample["target"]) for sample in ordered]
    train_targets = [float(sample["target"]) for sample in train]
    holdout_targets = [float(sample["target"]) for sample in holdout]
    positive_targets = sum(1 for target in targets if target > 0)
    reasons: list[str] = []

    if valid_samples < required_samples:
        reasons.append(f"valid_samples<{required_samples}")
    if train_samples < MIN_TRAIN_SAMPLES:
        reasons.append(f"train_samples<{MIN_TRAIN_SAMPLES}")
    if len(holdout) < MIN_HOLDOUT_SAMPLES:
        reasons.append(f"holdout_samples<{MIN_HOLDOUT_SAMPLES}")
    if _distinct_count(targets) < MIN_DISTINCT_TARGETS:
        reasons.append(f"distinct_targets<{MIN_DISTINCT_TARGETS}")
    if positive_targets < MIN_POSITIVE_TARGETS:
        reasons.append(f"positive_targets<{MIN_POSITIVE_TARGETS}")
    if train and _distinct_count(train_targets) < MIN_PARTITION_DISTINCT_TARGETS:
        reasons.append(f"train_distinct_targets<{MIN_PARTITION_DISTINCT_TARGETS}")
    if holdout and _distinct_count(holdout_targets) < MIN_PARTITION_DISTINCT_TARGETS:
        reasons.append(f"holdout_distinct_targets<{MIN_PARTITION_DISTINCT_TARGETS}")
    if ordered:
        time_span_hours = (ordered[-1]["created_at"] - ordered[0]["created_at"]).total_seconds() / 3600.0
    else:
        time_span_hours = 0.0
    if time_span_hours < MIN_TIME_SPAN_HOURS:
        reasons.append(f"time_span_hours<{MIN_TIME_SPAN_HOURS}")
    strict_time_split = bool(train and holdout and train[-1]["created_at"] < holdout[0]["created_at"])
    if not strict_time_split:
        reasons.append("chronological_split_not_strict")
    if _feature_variants(train, "tech") < 2:
        reasons.append("train_tech_feature_variants<2")
    if _feature_variants(train, "mkt") < 2:
        reasons.append("train_mkt_feature_variants<2")

    return {
        "version": VERTICAL_TRAINING_GATE_VERSION,
        "status": "ready" if not reasons else "blocked",
        "claimable": not reasons,
        "vertical": str(vertical or ""),
        "reasons": reasons,
        "policy": {
            "hard_min_valid_samples": HARD_MIN_VALID_SAMPLES,
            "requested_min_samples": int(min_samples or 0),
            "effective_min_valid_samples": required_samples,
            "min_train_samples": MIN_TRAIN_SAMPLES,
            "min_holdout_samples": MIN_HOLDOUT_SAMPLES,
            "holdout_fraction": HOLDOUT_FRACTION,
            "min_distinct_targets": MIN_DISTINCT_TARGETS,
            "min_positive_targets": MIN_POSITIVE_TARGETS,
            "min_partition_distinct_targets": MIN_PARTITION_DISTINCT_TARGETS,
            "min_time_span_hours": MIN_TIME_SPAN_HOURS,
            "split_strategy": "chronological_created_at",
            "target": "log1p(1000 * (likes + 2*comments + 3*shares) / views)",
            "target_kind": "continuous_observed_engagement_rate",
            "eligibility_status": "confirmed (filter only; never used as a training label)",
            "promotion_metric": "each_axis_holdout_mae_not_worse_than_train_mean_baseline",
        },
        "facts": {
            "raw_samples": int(raw_samples if raw_samples is not None else valid_samples),
            "valid_samples": valid_samples,
            "rejected_samples": max(0, int(raw_samples if raw_samples is not None else valid_samples) - valid_samples),
            "rejected_reasons": dict(sorted((rejected_reasons or {}).items())),
            "train_samples": train_samples,
            "holdout_samples": len(holdout),
            "distinct_targets": _distinct_count(targets),
            "positive_targets": positive_targets,
            "zero_targets": valid_samples - positive_targets,
            "train_distinct_targets": _distinct_count(train_targets),
            "holdout_distinct_targets": _distinct_count(holdout_targets),
            "train_tech_feature_variants": _feature_variants(train, "tech"),
            "train_mkt_feature_variants": _feature_variants(train, "mkt"),
            "time_span_hours": round(time_span_hours, 3),
            "strict_time_split": strict_time_split,
            "train_end_at": train[-1]["created_at"].isoformat() if train else None,
            "holdout_start_at": holdout[0]["created_at"].isoformat() if holdout else None,
        },
        "split_index": split_index,
    }


def _skipped_result(vertical: str, audit: dict[str, Any], reason_code: str = "training_gate_blocked") -> dict[str, Any]:
    result = {
        "status": "skipped",
        "trained": False,
        "vertical": str(vertical or ""),
        "reason_code": reason_code,
        "training_audit": audit,
    }
    logger.info("vertical learn skipped | audit=%s", json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


# ──────────────────────────────────────────────
# Vertical weight learning (runs monthly)
# Reads DB signals, updates VERTICAL_WEIGHTS in-place
# Requires: scikit-learn (pip install scikit-learn)
# ──────────────────────────────────────────────
def learn_vertical_weights(
    vertical: str,
    min_samples: int = HARD_MIN_VALID_SAMPLES,
    *,
    return_audit: bool = False,
) -> dict | None:
    """
    Learn which scored dimensions predict observed engagement rate.

    The legacy contract is preserved: a skipped run returns ``None``. Set
    ``return_audit=True`` to receive the structured skip decision and reasons.
    Successful runs always include their training and holdout audit.
    """
    try:
        import numpy as np
        conn = get_conn()

        rows = conn.execute("""
            SELECT id, created_at, video_analysis, views, likes, comments, shares,
                   detection_status, tech_score, marketing_score
            FROM submissions
            WHERE vertical_category = ?
              AND detection_status = 'confirmed'
              AND tech_score > 0
              AND views > 0
            ORDER BY created_at ASC, id ASC
        """, (vertical,)).fetchall()

        samples: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for row in rows:
            sample, reason = _build_training_sample(row)
            if sample is None:
                rejected[str(reason or "invalid_sample")] += 1
            else:
                samples.append(sample)
        samples.sort(key=lambda item: (item["created_at"], str(item.get("source_id") or "")))

        audit = evaluate_vertical_training_readiness(
            samples,
            vertical=vertical,
            min_samples=min_samples,
            raw_samples=len(rows),
            rejected_reasons=dict(rejected),
        )
        if not audit["claimable"]:
            skipped = _skipped_result(vertical, audit)
            return skipped if return_audit else None

        from sklearn.linear_model import Ridge
        split_index = int(audit["split_index"])
        train = samples[:split_index]
        holdout = samples[split_index:]
        X_tech_train = np.asarray([sample["tech"] for sample in train], dtype=float)
        X_mkt_train = np.asarray([sample["mkt"] for sample in train], dtype=float)
        y_train = np.asarray([sample["target"] for sample in train], dtype=float)
        X_tech_holdout = np.asarray([sample["tech"] for sample in holdout], dtype=float)
        X_mkt_holdout = np.asarray([sample["mkt"] for sample in holdout], dtype=float)
        y_holdout = np.asarray([sample["target"] for sample in holdout], dtype=float)

        # Fit only the earlier partition, then evaluate the later holdout.
        reg_tech = Ridge(alpha=1.0, positive=True)
        reg_tech.fit(X_tech_train, y_train)

        reg_mkt = Ridge(alpha=1.0, positive=True)
        reg_mkt.fit(X_mkt_train, y_train)

        baseline = np.full(y_holdout.shape, float(np.mean(y_train)), dtype=float)

        def _mae(actual: Any, predicted: Any) -> float:
            return float(np.mean(np.abs(np.asarray(actual) - np.asarray(predicted))))

        baseline_mae = _mae(y_holdout, baseline)
        tech_mae = _mae(y_holdout, reg_tech.predict(X_tech_holdout))
        mkt_mae = _mae(y_holdout, reg_mkt.predict(X_mkt_holdout))
        validation_reasons: list[str] = []
        if not math.isfinite(tech_mae) or tech_mae > baseline_mae + 1e-12:
            validation_reasons.append("tech_holdout_mae_worse_than_baseline")
        if not math.isfinite(mkt_mae) or mkt_mae > baseline_mae + 1e-12:
            validation_reasons.append("mkt_holdout_mae_worse_than_baseline")
        if float(np.maximum(reg_tech.coef_, 0).sum()) <= 0:
            validation_reasons.append("tech_coefficients_have_no_positive_signal")
        if float(np.maximum(reg_mkt.coef_, 0).sum()) <= 0:
            validation_reasons.append("mkt_coefficients_have_no_positive_signal")

        audit["holdout"] = {
            "status": "passed" if not validation_reasons else "blocked",
            "reasons": validation_reasons,
            "baseline_mae": round(baseline_mae, 8),
            "tech_mae": round(tech_mae, 8),
            "mkt_mae": round(mkt_mae, 8),
            "tech_improvement_vs_baseline": round((baseline_mae - tech_mae) / baseline_mae, 8) if baseline_mae else None,
            "mkt_improvement_vs_baseline": round((baseline_mae - mkt_mae) / baseline_mae, 8) if baseline_mae else None,
        }
        if validation_reasons:
            audit["status"] = "blocked"
            audit["claimable"] = False
            audit["reasons"] = [*audit["reasons"], *validation_reasons]
            skipped = _skipped_result(vertical, audit, "holdout_validation_blocked")
            return skipped if return_audit else None

        # The fixed policy passed holdout validation; refit on all eligible rows
        # before deriving the promoted weights.
        X_tech_all = np.asarray([sample["tech"] for sample in samples], dtype=float)
        X_mkt_all = np.asarray([sample["mkt"] for sample in samples], dtype=float)
        y_all = np.asarray([sample["target"] for sample in samples], dtype=float)
        reg_tech.fit(X_tech_all, y_all)
        reg_mkt.fit(X_mkt_all, y_all)

        def _to_weights(coefs: np.ndarray, dims: list, current: dict,
                        max_shift: float = 0.20) -> dict:
            """Normalize coefficients to percentages, apply max_shift constraint."""
            coefs = np.maximum(coefs, 0)
            total = coefs.sum()
            if total == 0:
                return current
            raw = {d: round(float(c / total * 100)) for d, c in zip(dims, coefs)}
            # Constraint: no weight moves more than max_shift from current
            result = {}
            for d in dims:
                cur = current.get(d, 10)
                new = raw.get(d, cur)
                delta = new - cur
                clamped = cur + max(min(delta, cur * max_shift), -cur * max_shift)
                result[d] = max(1, round(clamped))
            # Renormalize to 100
            s = sum(result.values())
            for d in result:
                result[d] = round(result[d] / s * 100)
            return result

        cur_vw   = VERTICAL_WEIGHTS.get(vertical, VERTICAL_WEIGHTS["default"])
        new_tech = _to_weights(reg_tech.coef_, TECH_DIMS,    cur_vw["tech"])
        new_mkt  = _to_weights(reg_mkt.coef_,  MARKETING_DIMS, cur_vw["mkt"])

        result = {
            "status":       "trained_not_persisted",
            "trained":      True,
            "promoted":     False,
            "persisted":    False,
            "vertical":     vertical,
            "samples":      len(samples),
            "tech":         new_tech,
            "mkt":          new_mkt,
            "learned_at":   _utcnow(),
            "training_audit": audit,
        }

        # Persist learned weights to DB
        try:
            conn2 = get_conn()
            persisted_result = {
                **result,
                "status": "trained",
                "promoted": True,
                "persisted": True,
            }
            conn2.execute(
                "INSERT INTO insights_cache(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (f"learned_weights_{vertical}",
                 json.dumps(persisted_result, ensure_ascii=False),
                 result["learned_at"])
            )
            conn2.commit()
            result = persisted_result
        except Exception as e:
            logger.exception("vertical learn save error | vertical=%s | error=%s", vertical, e)

        logger.info(
            "vertical learn completed | vertical=%s | samples=%s | persisted=%s | audit=%s",
            vertical,
            len(samples),
            result["persisted"],
            json.dumps(audit, ensure_ascii=False, sort_keys=True),
        )
        return result

    except ImportError:
        logger.warning("vertical learn unavailable because numpy/scikit-learn is not installed")
        if return_audit:
            return _skipped_result(
                vertical,
                {
                    "version": VERTICAL_TRAINING_GATE_VERSION,
                    "status": "blocked",
                    "claimable": False,
                    "vertical": str(vertical or ""),
                    "reasons": ["training_dependency_unavailable"],
                },
                "training_dependency_unavailable",
            )
        return None
    except Exception as e:
        logger.exception("vertical learn error | vertical=%s | error=%s", vertical, e)
        if return_audit:
            return _skipped_result(
                vertical,
                {
                    "version": VERTICAL_TRAINING_GATE_VERSION,
                    "status": "blocked",
                    "claimable": False,
                    "vertical": str(vertical or ""),
                    "reasons": ["training_internal_error"],
                    "error_type": type(e).__name__,
                },
                "training_internal_error",
            )
        return None


def load_learned_weights(vertical: str) -> dict | None:
    """Load previously learned weights from cache, if any."""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT value FROM insights_cache WHERE key=?",
            (f"learned_weights_{vertical}",)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        logger.exception("failed to load learned weights | vertical=%s", vertical)
    return None


def _artifact_gate_failure(reason: str, **checks: Any) -> dict[str, Any]:
    return {
        "accepted": False,
        "reason": str(reason or "artifact_invalid"),
        "checks": checks,
    }


def _artifact_has_blockers(value: Any) -> bool:
    return value not in (None, "", [], (), {})


def _validated_weight_axis(
    value: Any,
    dimensions: list[str],
    *,
    axis: str,
) -> tuple[dict[str, float] | None, dict[str, Any] | None]:
    if not isinstance(value, Mapping):
        return None, _artifact_gate_failure(f"{axis}_weights_missing_or_invalid")
    expected = set(dimensions)
    actual = {str(key) for key in value}
    if actual != expected:
        return None, _artifact_gate_failure(
            f"{axis}_weights_dimensions_mismatch",
            expected=sorted(expected),
            actual=sorted(actual),
        )
    cleaned: dict[str, float] = {}
    for dimension in dimensions:
        try:
            weight = float(value[dimension])
        except (KeyError, TypeError, ValueError):
            return None, _artifact_gate_failure(
                f"{axis}_weight_invalid",
                dimension=dimension,
            )
        if not math.isfinite(weight) or weight <= 0 or weight > 100:
            return None, _artifact_gate_failure(
                f"{axis}_weight_out_of_range",
                dimension=dimension,
            )
        cleaned[dimension] = weight
    if not math.isclose(sum(cleaned.values()), 100.0, abs_tol=1.0):
        return None, _artifact_gate_failure(
            f"{axis}_weights_not_normalized",
            total=round(sum(cleaned.values()), 6),
        )
    return cleaned, None


def validate_learned_weights_artifact(learned: Any) -> dict[str, Any]:
    """Fail-closed acceptance decision for one runtime weight artifact.

    Only the schema emitted by the hardened trainer is accepted.  In
    particular, a legacy cache row with just ``tech``/``mkt`` can no longer
    silently alter live scoring.
    """

    if not isinstance(learned, Mapping):
        return _artifact_gate_failure("artifact_missing_or_invalid")
    if learned.get("trained") is not True:
        return _artifact_gate_failure("artifact_not_trained")
    if learned.get("persisted") is not True:
        return _artifact_gate_failure("artifact_not_persisted")
    if learned.get("promoted") is not True:
        return _artifact_gate_failure("artifact_not_promoted")
    if str(learned.get("status") or "") != "trained":
        return _artifact_gate_failure("artifact_status_not_trained")

    audit = learned.get("training_audit")
    if not isinstance(audit, Mapping):
        return _artifact_gate_failure("training_audit_missing_or_invalid")
    if str(audit.get("version") or "") != VERTICAL_TRAINING_GATE_VERSION:
        return _artifact_gate_failure("training_audit_version_mismatch")
    if str(audit.get("status") or "") != "ready" or audit.get("claimable") is not True:
        return _artifact_gate_failure("training_audit_not_claimable")
    if _artifact_has_blockers(audit.get("reasons")):
        return _artifact_gate_failure("training_audit_has_blockers")

    policy = audit.get("policy")
    facts = audit.get("facts")
    holdout = audit.get("holdout")
    if not isinstance(policy, Mapping):
        return _artifact_gate_failure("training_policy_missing_or_invalid")
    if not isinstance(facts, Mapping):
        return _artifact_gate_failure("training_facts_missing_or_invalid")
    if not isinstance(holdout, Mapping):
        return _artifact_gate_failure("holdout_audit_missing_or_invalid")
    if (
        str(holdout.get("status") or "") != "passed"
        or _artifact_has_blockers(holdout.get("reasons"))
    ):
        return _artifact_gate_failure("holdout_not_passed")

    try:
        effective_min_valid_samples = int(
            policy.get("effective_min_valid_samples") or HARD_MIN_VALID_SAMPLES
        )
    except (TypeError, ValueError):
        return _artifact_gate_failure("training_policy_minimum_invalid")
    numeric_floors = {
        "valid_samples": max(
            HARD_MIN_VALID_SAMPLES,
            effective_min_valid_samples,
        ),
        "train_samples": MIN_TRAIN_SAMPLES,
        "holdout_samples": MIN_HOLDOUT_SAMPLES,
        "distinct_targets": MIN_DISTINCT_TARGETS,
        "positive_targets": MIN_POSITIVE_TARGETS,
        "train_distinct_targets": MIN_PARTITION_DISTINCT_TARGETS,
        "holdout_distinct_targets": MIN_PARTITION_DISTINCT_TARGETS,
        "train_tech_feature_variants": 2,
        "train_mkt_feature_variants": 2,
        "time_span_hours": MIN_TIME_SPAN_HOURS,
    }
    for fact_key, minimum in numeric_floors.items():
        try:
            observed = float(facts.get(fact_key))
        except (TypeError, ValueError):
            return _artifact_gate_failure("training_fact_missing_or_invalid", fact=fact_key)
        if not math.isfinite(observed) or observed < float(minimum):
            return _artifact_gate_failure(
                "training_fact_below_gate",
                fact=fact_key,
                observed=observed,
                minimum=minimum,
            )
    if facts.get("strict_time_split") is not True:
        return _artifact_gate_failure("chronological_split_not_strict")

    holdout_metrics: dict[str, float] = {}
    for metric_key in ("baseline_mae", "tech_mae", "mkt_mae"):
        try:
            metric = float(holdout.get(metric_key))
        except (TypeError, ValueError):
            return _artifact_gate_failure("holdout_metric_missing_or_invalid", metric=metric_key)
        if not math.isfinite(metric) or metric < 0:
            return _artifact_gate_failure("holdout_metric_missing_or_invalid", metric=metric_key)
        holdout_metrics[metric_key] = metric
    baseline_mae = holdout_metrics["baseline_mae"]
    if (
        holdout_metrics["tech_mae"] > baseline_mae + 1e-12
        or holdout_metrics["mkt_mae"] > baseline_mae + 1e-12
    ):
        return _artifact_gate_failure("holdout_worse_than_baseline")

    tech, tech_error = _validated_weight_axis(learned.get("tech"), TECH_DIMS, axis="tech")
    if tech_error:
        return tech_error
    mkt, mkt_error = _validated_weight_axis(learned.get("mkt"), MARKETING_DIMS, axis="mkt")
    if mkt_error:
        return mkt_error
    return {
        "accepted": True,
        "reason": "accepted",
        "checks": {
            "training_audit_version": VERTICAL_TRAINING_GATE_VERSION,
            "persisted": True,
            "promoted": True,
            "holdout": "passed",
        },
        "weights": {"tech": tech, "mkt": mkt},
    }


def _restore_rule_v0_weights(vertical: str) -> None:
    base = _RULE_V0_VERTICAL_WEIGHTS.get(vertical) or _RULE_V0_VERTICAL_WEIGHTS["default"]
    VERTICAL_WEIGHTS[vertical] = deepcopy(base)


def apply_learned_weights(vertical: str) -> dict[str, Any]:
    """Apply a promoted artifact, otherwise actively restore rule_v0 weights."""
    learned = load_learned_weights(vertical)
    decision = validate_learned_weights_artifact(learned)
    if not decision["accepted"]:
        _restore_rule_v0_weights(vertical)
        result = {
            "status": "rule_v0_fallback",
            "applied": False,
            "vertical": str(vertical or ""),
            "reason": decision["reason"],
            "artifact_gate": decision,
        }
        logger.warning(
            "learned weights rejected; rule_v0 restored | vertical=%s | reason=%s",
            vertical,
            decision["reason"],
        )
        return result

    weights = decision["weights"]
    base = _RULE_V0_VERTICAL_WEIGHTS.get(vertical) or _RULE_V0_VERTICAL_WEIGHTS["default"]
    VERTICAL_WEIGHTS[vertical] = deepcopy(base)
    VERTICAL_WEIGHTS[vertical]["tech"] = dict(weights["tech"])
    VERTICAL_WEIGHTS[vertical]["mkt"] = dict(weights["mkt"])
    try:
        samples = max(0, int(learned.get("samples") or 0))
    except (TypeError, ValueError):
        samples = 0
    result = {
        "status": "applied",
        "applied": True,
        "vertical": str(vertical or ""),
        "reason": "promoted_artifact_accepted",
        "samples": samples,
        "learned_at": str(learned.get("learned_at") or ""),
        "artifact_gate": decision,
    }
    logger.info(
        "applied promoted learned weights | vertical=%s | samples=%s | learned_at=%s",
        vertical,
        result["samples"],
        result["learned_at"],
    )
    return result






# ──────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────
class MetricsInput(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0


class HintsInput(BaseModel):
    logo: bool = False
    product: bool = False
    voice: bool = False
    review: bool = False


class UploadedVideoInput(BaseModel):
    video_id: str = ""
    filename: str = ""
    mime_type: str = ""
    size_mb: float = 0.0
    path: str = ""


class AuditRequest(BaseModel):
    url: str = ""
