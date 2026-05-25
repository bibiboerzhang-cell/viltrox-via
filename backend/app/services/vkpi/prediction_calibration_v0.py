"""P6.75 read-only prediction calibration v0.

This compares a saved P6.74 new-launch estimate with current trend truth
proxies. Same-day comparisons are marked as smoke checks; official calibration
requires a prediction generated before the truth day.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.domains.trends import trend_detection_use_case as trend_detection_v0


CALIBRATION_VERSION = "prediction-calibration-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except Exception:
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raw = _text(value)
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d")
            except Exception:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_json(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_prediction_path(ops_dir: str = DEFAULT_OPS_DIR) -> str:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return ""
    candidates = sorted(root.glob("*p6-74-new-launch-acceptance-v0.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(candidates[0]) if candidates else ""


def _truth_platforms(truth_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    platforms: dict[str, dict[str, Any]] = {}
    for signal in truth_report.get("signals") or []:
        entity = signal.get("entity") if isinstance(signal.get("entity"), dict) else {}
        platform = _text(entity.get("platform")).lower()
        if not platform:
            continue
        bucket = platforms.setdefault(platform, {"signals": 0, "abnormal_growth": 0, "max_score": 0.0, "examples": []})
        bucket["signals"] = int(bucket["signals"]) + 1
        bucket["max_score"] = max(_float(bucket.get("max_score")), _float(signal.get("score")))
        if signal.get("is_abnormal_growth"):
            bucket["abnormal_growth"] = int(bucket["abnormal_growth"]) + 1
        if len(bucket["examples"]) < 3:
            bucket["examples"].append(
                {
                    "rule_key": signal.get("rule_key"),
                    "score": signal.get("score"),
                    "is_abnormal_growth": bool(signal.get("is_abnormal_growth")),
                    "entity": {
                        "account_handle": entity.get("account_handle"),
                        "post_uid": entity.get("post_uid"),
                        "brand": entity.get("brand"),
                    },
                }
            )
    return platforms


def _candidate_rows(prediction_report: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    candidates = list(prediction_report.get("top_candidates") or [])[: max(1, min(50, int(top_n or 12)))]
    return [item for item in candidates if isinstance(item, dict)]


def build_prediction_calibration_from_reports(
    *,
    prediction_report: dict[str, Any],
    truth_report: dict[str, Any],
    prediction_path: str = "",
    top_n: int = 12,
) -> dict[str, Any]:
    candidates = _candidate_rows(prediction_report, top_n)
    truth_by_platform = _truth_platforms(truth_report)
    abnormal_platforms = {platform for platform, item in truth_by_platform.items() if int(item.get("abnormal_growth") or 0) > 0}
    candidate_results: list[dict[str, Any]] = []
    for candidate in candidates:
        platform = _text(candidate.get("platform")).lower()
        truth = truth_by_platform.get(platform, {"signals": 0, "abnormal_growth": 0, "max_score": 0, "examples": []})
        hit = platform in abnormal_platforms
        candidate_results.append(
            {
                "kol_pool_id": candidate.get("kol_pool_id"),
                "platform": candidate.get("platform"),
                "handle": candidate.get("handle"),
                "predicted_score": candidate.get("acceptance_score"),
                "predicted_tier": candidate.get("acceptance_tier"),
                "truth_platform_signals": truth.get("signals", 0),
                "truth_platform_abnormal_growth": truth.get("abnormal_growth", 0),
                "truth_platform_max_score": truth.get("max_score", 0),
                "hit": hit,
                "truth_examples": truth.get("examples", []),
            }
        )
    hit_count = sum(1 for item in candidate_results if item.get("hit"))
    predicted_platforms = {_text(item.get("platform")).lower() for item in candidate_results if _text(item.get("platform"))}
    hit_platforms = predicted_platforms & abnormal_platforms
    prediction_dt = _parse_datetime(prediction_report.get("generated_at"))
    truth_dt = _parse_datetime(truth_report.get("generated_at"))
    day_gap = None
    if prediction_dt and truth_dt:
        day_gap = (truth_dt.date() - prediction_dt.date()).days
    official_accuracy = bool(day_gap is not None and day_gap >= 1)
    calibration_status = "official_cross_day" if official_accuracy else "same_day_smoke_or_pending"
    checks = {
        "calibration_version_set": bool(CALIBRATION_VERSION),
        "prediction_report_loaded": bool(prediction_report),
        "truth_report_loaded": bool(truth_report),
        "prediction_candidates_loaded": bool(candidates),
        "truth_platforms_loaded": bool(truth_by_platform),
        "accuracy_labeled_official_or_smoke": calibration_status in {"official_cross_day", "same_day_smoke_or_pending"},
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p6_75_prediction_calibration_v0",
        "generated_at": _now(),
        "calibration_version": CALIBRATION_VERSION,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "parameters": {
            "prediction_path": prediction_path,
            "top_n": top_n,
        },
        "summary": {
            "calibration_status": calibration_status,
            "accuracy_official": official_accuracy,
            "prediction_generated_at": prediction_report.get("generated_at"),
            "truth_generated_at": truth_report.get("generated_at"),
            "day_gap": day_gap,
            "predicted_candidate_count": len(candidate_results),
            "truth_platform_count": len(truth_by_platform),
            "truth_abnormal_platform_count": len(abnormal_platforms),
            "hit_count": hit_count,
            "proxy_precision": round(hit_count / len(candidate_results), 4) if candidate_results else 0.0,
            "proxy_platform_coverage": round(len(hit_platforms) / len(abnormal_platforms), 4) if abnormal_platforms else 0.0,
            "prediction_sku": prediction_report.get("summary", {}).get("sku"),
            "prediction_market_risk_tier": prediction_report.get("summary", {}).get("market_risk_tier"),
        },
        "truth_platforms": truth_by_platform,
        "candidate_results": candidate_results,
        "policy": {
            "read_only": True,
            "proxy_truth_only": True,
            "official_accuracy_requires_cross_day": True,
            "no_model_calibration_written": True,
        },
        "next_steps": [
            "Run again after the next daily metrics snapshot to get official cross-day accuracy.",
            "Persist prediction snapshots only after the calibration contract is accepted.",
            "Do not tune estimator weights from same-day smoke checks.",
        ],
    }


def build_prediction_calibration_v0(
    *,
    prediction_json: str = "",
    truth_json: str = "",
    ops_dir: str = DEFAULT_OPS_DIR,
    top_n: int = 12,
) -> dict[str, Any]:
    prediction_path = prediction_json or _latest_prediction_path(ops_dir)
    prediction_report = _load_json(prediction_path)
    truth_report = _load_json(truth_json) if truth_json else trend_detection_v0.build_trend_detection_v0(lookback_days=14, limit=5000, top_signals=100)
    return build_prediction_calibration_from_reports(
        prediction_report=prediction_report,
        truth_report=truth_report,
        prediction_path=prediction_path,
        top_n=top_n,
    )
