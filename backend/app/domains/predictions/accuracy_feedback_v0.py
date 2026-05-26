"""P6.78 read-only prediction accuracy feedback loop v0.

This aggregates P6.75 calibration artifacts into a feedback report. It keeps
same-day smoke checks separate from official cross-day accuracy and never writes
model weights, tasks, sync state, or decisions.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger


FEEDBACK_VERSION = "prediction-accuracy-feedback-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"
DEFAULT_MIN_OFFICIAL_RUNS = 3
CALIBRATION_PATTERN = "*p6-75-prediction-calibration-v0.json"
logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.debug("Failed to load prediction accuracy artifact JSON from %s", path, exc_info=True)
        return {}


def _artifact_paths(ops_dir: str, limit: int) -> list[Path]:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return []
    bounded_limit = max(1, min(500, int(limit or 100)))
    paths = [path for path in root.glob(CALIBRATION_PATTERN) if path.is_file()]
    return sorted(paths, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[:bounded_limit]


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("summary")
    return value if isinstance(value, dict) else {}


def _artifact_row(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    summary = _summary(payload)
    official = bool(summary.get("accuracy_official"))
    status = _text(summary.get("calibration_status")) or ("official_cross_day" if official else "same_day_smoke_or_pending")
    return {
        "artifact_path": str(path),
        "artifact_name": path.name,
        "passed": bool(payload.get("passed")),
        "calibration_status": status,
        "accuracy_official": official,
        "report_generated_at": payload.get("generated_at"),
        "prediction_generated_at": summary.get("prediction_generated_at"),
        "truth_generated_at": summary.get("truth_generated_at"),
        "day_gap": summary.get("day_gap"),
        "predicted_candidate_count": _int(summary.get("predicted_candidate_count")),
        "truth_platform_count": _int(summary.get("truth_platform_count")),
        "truth_abnormal_platform_count": _int(summary.get("truth_abnormal_platform_count")),
        "hit_count": _int(summary.get("hit_count")),
        "proxy_precision": _float(summary.get("proxy_precision")),
        "proxy_platform_coverage": _float(summary.get("proxy_platform_coverage")),
        "prediction_sku": _text(summary.get("prediction_sku")) or "unknown",
        "prediction_market_risk_tier": _text(summary.get("prediction_market_risk_tier")) or "unknown",
    }


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return round(sum(_float(row.get(key)) for row in rows) / len(rows), 4)


def _date_key(value: Any) -> str:
    raw = _text(value)
    if len(raw) >= 10:
        return raw[:10]
    return "unknown"


def _bucket(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_text(row.get(key)) or "unknown"].append(row)
    result: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_rows in sorted(buckets.items()):
        official_rows = [row for row in bucket_rows if row.get("accuracy_official")]
        smoke_rows = [row for row in bucket_rows if not row.get("accuracy_official")]
        latest = max((_text(row.get("report_generated_at")) for row in bucket_rows), default="")
        result[bucket_key] = {
            "runs": len(bucket_rows),
            "official_runs": len(official_rows),
            "smoke_runs": len(smoke_rows),
            "proxy_precision_avg": _avg(bucket_rows, "proxy_precision"),
            "proxy_platform_coverage_avg": _avg(bucket_rows, "proxy_platform_coverage"),
            "latest_report_generated_at": latest,
        }
    return result


def _date_bucket(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[_date_key(row.get("report_generated_at") or row.get("truth_generated_at"))].append(row)
    result: dict[str, dict[str, Any]] = {}
    for bucket_key, bucket_rows in sorted(buckets.items()):
        official_rows = [row for row in bucket_rows if row.get("accuracy_official")]
        result[bucket_key] = {
            "runs": len(bucket_rows),
            "official_runs": len(official_rows),
            "smoke_runs": len(bucket_rows) - len(official_rows),
            "proxy_precision_avg": _avg(bucket_rows, "proxy_precision"),
            "proxy_platform_coverage_avg": _avg(bucket_rows, "proxy_platform_coverage"),
        }
    return result


def build_prediction_accuracy_feedback_v0(
    *,
    ops_dir: str = DEFAULT_OPS_DIR,
    limit: int = 100,
    min_official_runs: int = DEFAULT_MIN_OFFICIAL_RUNS,
) -> dict[str, Any]:
    bounded_min = max(1, min(30, int(min_official_runs or DEFAULT_MIN_OFFICIAL_RUNS)))
    rows = [_artifact_row(path, _load_json(path)) for path in _artifact_paths(ops_dir, limit)]
    rows = [row for row in rows if row.get("artifact_name")]
    official_rows = [row for row in rows if row.get("accuracy_official")]
    smoke_rows = [row for row in rows if not row.get("accuracy_official")]
    calibration_allowed = len(official_rows) >= bounded_min
    status = "no_calibration_artifacts"
    if rows and calibration_allowed:
        status = "calibration_window_ready"
    elif rows:
        status = "pending_cross_day_truth"
    latest_row = rows[0] if rows else {}
    checks = {
        "feedback_version_set": bool(FEEDBACK_VERSION),
        "calibration_artifacts_loaded": bool(rows),
        "official_smoke_split_complete": len(rows) == len(official_rows) + len(smoke_rows),
        "same_day_smoke_not_counted_as_official": all(
            row.get("accuracy_official") or _text(row.get("calibration_status")) != "official_cross_day" for row in smoke_rows
        ),
        "tuning_guard_present": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "tasks_blocked": True,
    }
    return {
        "mode": "p6_78_prediction_accuracy_feedback_v0",
        "generated_at": _now(),
        "feedback_version": FEEDBACK_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "ops_dir": ops_dir,
            "limit": max(1, min(500, int(limit or 100))),
            "min_official_runs": bounded_min,
            "calibration_artifact_pattern": CALIBRATION_PATTERN,
        },
        "summary": {
            "feedback_status": status,
            "artifact_count": len(rows),
            "official_runs": len(official_rows),
            "smoke_runs": len(smoke_rows),
            "min_official_runs": bounded_min,
            "calibration_allowed": calibration_allowed,
            "auto_tuning_allowed": False,
            "weight_update_allowed": False,
            "latest_calibration_status": latest_row.get("calibration_status"),
            "latest_accuracy_official": bool(latest_row.get("accuracy_official")) if latest_row else False,
            "latest_prediction_sku": latest_row.get("prediction_sku"),
            "official_proxy_precision_avg": _avg(official_rows, "proxy_precision"),
            "smoke_proxy_precision_avg": _avg(smoke_rows, "proxy_precision"),
            "official_platform_coverage_avg": _avg(official_rows, "proxy_platform_coverage"),
            "smoke_platform_coverage_avg": _avg(smoke_rows, "proxy_platform_coverage"),
            "source_scope": "runtime_ops_p6_75_artifacts_only",
        },
        "buckets": {
            "by_sku": _bucket(rows, "prediction_sku"),
            "by_risk_tier": _bucket(rows, "prediction_market_risk_tier"),
            "by_report_date": _date_bucket(rows),
        },
        "calibration_artifacts": rows,
        "policy": {
            "read_only": True,
            "official_accuracy_requires_cross_day": True,
            "same_day_smoke_excluded_from_official_accuracy": True,
            "min_official_runs_required": bounded_min,
            "no_model_weight_update": True,
            "no_model_calibration_written": True,
            "human_review_required_before_tuning": True,
        },
        "next_steps": [
            "Wait for cross-day P6.75 runs before treating accuracy as official.",
            "Use same-day smoke metrics only to verify wiring, not to tune weights.",
            "If enough official runs accumulate, review the buckets before proposing any estimator changes.",
        ],
    }
