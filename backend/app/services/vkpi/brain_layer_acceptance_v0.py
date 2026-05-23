"""P6.79 read-only brain layer acceptance v0.

This aggregates P6.71-P6.78 artifacts to decide whether the rule-based brain
layer can support human new-launch and KOL planning decisions. It is not a
business sign-off, recommendation engine, model trainer, or sync runner.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACCEPTANCE_VERSION = "brain-layer-acceptance-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"

REQUIRED_REPORTS: list[dict[str, str]] = [
    {
        "key": "p6_71_fire_metrics",
        "phase": "P6.71",
        "label": "Fire metric definitions",
        "pattern": "*p6-71-fire-metric-definitions*.json",
    },
    {
        "key": "p6_72_time_series_anchors",
        "phase": "P6.72",
        "label": "Time-series anchors",
        "pattern": "*p6-72-time-series-anchors*.json",
    },
    {
        "key": "p6_73_trend_detection",
        "phase": "P6.73",
        "label": "Trend detection",
        "pattern": "*p6-73-trend-detection-v0.json",
    },
    {
        "key": "p6_74_new_launch_acceptance",
        "phase": "P6.74",
        "label": "New launch acceptance",
        "pattern": "*p6-74-new-launch-acceptance-v0.json",
    },
    {
        "key": "p6_75_prediction_calibration",
        "phase": "P6.75",
        "label": "Prediction calibration",
        "pattern": "*p6-75-prediction-calibration-v0.json",
    },
    {
        "key": "p6_76_today_new_signals",
        "phase": "P6.76",
        "label": "Today new signals",
        "pattern": "*p6-76-today-new-signals-v0.json",
    },
    {
        "key": "p6_77_weekly_action_plan",
        "phase": "P6.77",
        "label": "Weekly action plan",
        "pattern": "*p6-77-weekly-action-plan-v0.json",
    },
    {
        "key": "p6_78_prediction_accuracy_feedback",
        "phase": "P6.78",
        "label": "Prediction accuracy feedback",
        "pattern": "*p6-78-prediction-accuracy-feedback-v0.json",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_artifact(root: Path, pattern: str) -> Path | None:
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _side_effects_blocked(report: dict[str, Any]) -> bool:
    return not any(
        bool(report.get(key))
        for key in ("provider_calls", "llm_calls", "write_db", "sync_triggered", "task_enqueued", "external_http_calls")
    )


def _module_highlights(key: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = _as_dict(report.get("summary"))
    if key == "p6_71_fire_metrics":
        return {
            "definition_version": report.get("definition_version"),
            "metric_count": len(_as_dict(report.get("metrics"))),
            "misleading_growth_blocked": bool(_as_dict(report.get("checks")).get("misleading_growth_blocked")),
        }
    if key == "p6_72_time_series_anchors":
        return {
            "anchor_count": summary.get("anchor_count"),
            "trend_replay_ready": summary.get("trend_replay_ready"),
            "delta_anchors": summary.get("delta_anchors"),
        }
    if key == "p6_73_trend_detection":
        return {
            "signals_total": summary.get("signals_total"),
            "abnormal_growth_signals": summary.get("abnormal_growth_signals"),
            "market_event_signals": summary.get("market_event_signals"),
        }
    if key == "p6_74_new_launch_acceptance":
        return {
            "sku": summary.get("sku"),
            "candidate_count": summary.get("candidate_count"),
            "strong_candidate_count": summary.get("strong_candidate_count"),
            "market_risk_tier": summary.get("market_risk_tier"),
        }
    if key == "p6_75_prediction_calibration":
        return {
            "calibration_status": summary.get("calibration_status"),
            "accuracy_official": bool(summary.get("accuracy_official")),
            "proxy_precision": summary.get("proxy_precision"),
            "proxy_platform_coverage": summary.get("proxy_platform_coverage"),
        }
    if key == "p6_76_today_new_signals":
        return {
            "trend_signals_24h": summary.get("trend_signals_24h"),
            "abnormal_growth_24h": summary.get("abnormal_growth_24h"),
            "comment_status": summary.get("comment_status"),
            "action_items": summary.get("action_items"),
        }
    if key == "p6_77_weekly_action_plan":
        return {
            "action_count": summary.get("action_count"),
            "action_types": summary.get("action_types"),
            "priorities": summary.get("priorities"),
            "sku": summary.get("sku"),
        }
    if key == "p6_78_prediction_accuracy_feedback":
        return {
            "feedback_status": summary.get("feedback_status"),
            "official_runs": summary.get("official_runs"),
            "smoke_runs": summary.get("smoke_runs"),
            "calibration_allowed": bool(summary.get("calibration_allowed")),
            "auto_tuning_allowed": bool(summary.get("auto_tuning_allowed")),
        }
    return summary


def _module_row(root: Path, spec: dict[str, str]) -> dict[str, Any]:
    path = _latest_artifact(root, spec["pattern"])
    report = _load_json(path) if path else {}
    loaded = bool(path and report)
    return {
        "key": spec["key"],
        "phase": spec["phase"],
        "label": spec["label"],
        "pattern": spec["pattern"],
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "loaded": loaded,
        "mode": report.get("mode"),
        "generated_at": report.get("generated_at"),
        "passed": bool(report.get("passed")) if loaded else False,
        "side_effects_blocked": _side_effects_blocked(report) if loaded else False,
        "highlights": _module_highlights(spec["key"], report) if loaded else {},
    }


def _decision_support_level(modules_by_key: dict[str, dict[str, Any]]) -> str:
    p74 = _as_dict(modules_by_key.get("p6_74_new_launch_acceptance", {}).get("highlights"))
    p77 = _as_dict(modules_by_key.get("p6_77_weekly_action_plan", {}).get("highlights"))
    p78 = _as_dict(modules_by_key.get("p6_78_prediction_accuracy_feedback", {}).get("highlights"))
    candidate_count = _int(p74.get("candidate_count"))
    action_count = _int(p77.get("action_count"))
    action_types = _as_dict(p77.get("action_types"))
    real_action_count = max(0, action_count - _int(action_types.get("review_planning_inputs")))
    if real_action_count > 0 and candidate_count > 0:
        return "actionable_v0_calibration_ready" if p78.get("calibration_allowed") else "actionable_v0_calibration_pending"
    if candidate_count > 0 and action_count > 0:
        return "candidate_review_only"
    return "insufficient_decision_inputs"


def build_brain_layer_acceptance_v0(*, ops_dir: str = DEFAULT_OPS_DIR) -> dict[str, Any]:
    root = Path(ops_dir)
    modules = [_module_row(root, spec) for spec in REQUIRED_REPORTS]
    modules_by_key = {item["key"]: item for item in modules}
    missing = [item["phase"] for item in modules if not item["loaded"]]
    failed = [item["phase"] for item in modules if item["loaded"] and not item["passed"]]
    side_effect_violations = [item["phase"] for item in modules if item["loaded"] and not item["side_effects_blocked"]]
    support_level = _decision_support_level(modules_by_key)
    p78 = _as_dict(modules_by_key.get("p6_78_prediction_accuracy_feedback", {}).get("highlights"))
    p75 = _as_dict(modules_by_key.get("p6_75_prediction_calibration", {}).get("highlights"))
    official_accuracy_pending = not bool(p75.get("accuracy_official")) or _int(p78.get("official_runs")) < 3
    technical_acceptance = not missing and not failed and not side_effect_violations and support_level != "insufficient_decision_inputs"
    checks = {
        "acceptance_version_set": bool(ACCEPTANCE_VERSION),
        "all_required_artifacts_loaded": not missing,
        "all_required_artifacts_passed": not failed,
        "all_side_effects_blocked": not side_effect_violations,
        "decision_inputs_present": support_level != "insufficient_decision_inputs",
        "calibration_guard_present": "p6_78_prediction_accuracy_feedback" in modules_by_key and p78.get("auto_tuning_allowed") is False,
        "official_accuracy_pending_explicit": official_accuracy_pending or bool(p78.get("calibration_allowed")),
        "business_confirmation_separate": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "tasks_blocked": True,
    }
    return {
        "mode": "p6_79_brain_layer_acceptance_v0",
        "generated_at": _now(),
        "acceptance_version": ACCEPTANCE_VERSION,
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
            "required_report_count": len(REQUIRED_REPORTS),
        },
        "summary": {
            "technical_acceptance_passed": technical_acceptance,
            "decision_support_level": support_level,
            "can_assist_new_launch_kol_decision": support_level in {
                "actionable_v0_calibration_pending",
                "actionable_v0_calibration_ready",
                "candidate_review_only",
            },
            "business_confirmed": False,
            "business_confirmation_required": True,
            "official_accuracy_pending": official_accuracy_pending,
            "calibration_allowed": bool(p78.get("calibration_allowed")),
            "missing_phases": missing,
            "failed_phases": failed,
            "side_effect_violations": side_effect_violations,
            "source_scope": "runtime_ops_p6_71_to_p6_78_artifacts_only",
        },
        "modules": modules,
        "policy": {
            "read_only": True,
            "not_a_business_signoff": True,
            "not_a_recommendation_engine": True,
            "human_decision_required": True,
            "no_model_training": True,
            "no_model_weight_update": True,
            "no_sync_or_refresh": True,
        },
        "open_limits": [
            "Official prediction accuracy still needs cross-day P6.75 runs unless calibration_allowed is true.",
            "Business confirmation is intentionally separate from this technical acceptance.",
            "Outputs can guide review/contact planning but must not auto-contact or auto-rank without P3 feedback controls.",
        ],
        "next_steps": [
            "Present the brain-layer v0 report to business users for confirmation.",
            "Continue collecting cross-day calibration artifacts before tuning estimator weights.",
            "Move to P7 agents only after human decision and feedback controls are accepted.",
        ],
    }
