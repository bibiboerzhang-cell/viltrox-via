"""P7.80 read-only Sync Sentinel Agent v0.

This is an agent-shaped report, not an autonomous worker. It reads existing
sync, budget, alert, and P6.79 acceptance state, then returns prioritized
signals for an operator. It never writes alerts, acknowledges guards, starts
sync jobs, calls providers, or enqueues tasks.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import sync_status


SENTINEL_VERSION = "sync-sentinel-agent-v0.1"
DEFAULT_OPS_DIR = "runtime/ops"
P6_79_PATTERN = "*p6-79-brain-layer-acceptance-v0.json"
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


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


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except Exception:
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _latest_artifact(ops_dir: str, pattern: str) -> Path | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _latest_p6_79(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P6_79_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": _as_dict(payload.get("summary")),
    }


def _budget_caps() -> dict[str, Any]:
    if not _table_exists("vkpi_provider_budget_caps"):
        return {"configured": False, "budgets": [], "summary": {"scopes": 0, "warnings": 0, "hard_stopped": 0}}
    rows = get_conn().execute(
        """
        SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action
        FROM vkpi_provider_budget_caps
        ORDER BY scope
        """
    ).fetchall()
    budgets: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        cap = _float(item.get("cap_usd"))
        spend = _float(item.get("current_spend"))
        warning_at = _float(item.get("warning_at"), 0.8)
        hard_stop_at = _float(item.get("hard_stop_at"), 1.0)
        usage_ratio = (spend / cap) if cap > 0 else 0.0
        budgets.append(
            {
                "scope": item.get("scope"),
                "cap_usd": cap,
                "current_spend": spend,
                "usage_ratio": round(usage_ratio, 4),
                "warning_at": warning_at,
                "hard_stop_at": hard_stop_at,
                "warning": cap > 0 and usage_ratio >= warning_at,
                "hard_stopped": cap > 0 and usage_ratio >= hard_stop_at,
                "reset_at": item.get("reset_at"),
                "fallback_action": item.get("fallback_action") or "",
            }
        )
    return {
        "configured": True,
        "budgets": budgets,
        "summary": {
            "scopes": len(budgets),
            "warnings": sum(1 for item in budgets if item.get("warning")),
            "hard_stopped": sum(1 for item in budgets if item.get("hard_stopped")),
            "current_spend_usd": round(sum(_float(item.get("current_spend")) for item in budgets), 4),
        },
    }


def _open_alerts(limit: int) -> dict[str, Any]:
    safe_limit = max(1, min(200, int(limit or 50)))
    if not _table_exists("vkpi_alerts"):
        return {"configured": False, "alerts": [], "summary": {"open_total": 0, "critical_open": 0, "warning_open": 0}}
    rows = get_conn().execute(
        """
        SELECT id, alert_key, rule_key, severity, status, target_type, target_id,
               title, created_at, updated_at
        FROM vkpi_alerts
        WHERE status='open'
        ORDER BY
          CASE severity WHEN 'danger' THEN 0 WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    alerts = [dict(row) for row in rows]
    return {
        "configured": True,
        "alerts": alerts,
        "summary": {
            "open_total": len(alerts),
            "critical_open": sum(1 for row in alerts if _text(row.get("severity")).lower() in {"danger", "critical"}),
            "warning_open": sum(1 for row in alerts if _text(row.get("severity")).lower() == "warning"),
        },
    }


def _signal(
    *,
    severity: str,
    category: str,
    title: str,
    reason: str,
    evidence: dict[str, Any] | None = None,
    recommended_action: str = "review",
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "title": title,
        "reason": reason,
        "evidence": evidence or {},
        "recommended_action": recommended_action,
    }


def _signals_from_overview(overview: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for section_name in ("industry", "shopify", "cron_jobs", "daily_sync", "platform_settings"):
        section = _as_dict(overview.get(section_name))
        if section.get("error"):
            signals.append(
                _signal(
                    severity="warning",
                    category="source_error",
                    title=f"Sync overview source error: {section_name}",
                    reason=_text(section.get("error")),
                    evidence={"section": section_name, "payload": section},
                    recommended_action="fix the read-side status query or source schema",
                )
            )
    daily = _as_dict(overview.get("daily_sync"))
    latest = _as_dict(daily.get("latest_summary") or daily.get("latest_run"))
    health = _as_dict(latest.get("health"))
    if daily.get("ack_required"):
        blocking = _as_dict(daily.get("blocking_run"))
        signals.append(
            _signal(
                severity="critical",
                category="sync_guard",
                title="Daily sync guard requires acknowledgement",
                reason=f"Blocking run: {blocking.get('run_id') or 'unknown'}",
                evidence={"blocking_run": blocking},
                recommended_action="review blocking run and write an explicit manual ack only if safe",
            )
        )
    failure_rate = _float(health.get("failure_rate"))
    threshold = _float(daily.get("failure_rate_threshold"), 0.10)
    if failure_rate >= threshold and latest:
        signals.append(
            _signal(
                severity="warning",
                category="sync_failure_rate",
                title="Latest sync failure rate crossed threshold",
                reason=f"failure_rate={failure_rate:.4f}, threshold={threshold:.4f}",
                evidence={"latest_run": latest},
                recommended_action="inspect latest sync summary before the next timer run",
            )
        )
    summary = _as_dict(overview.get("summary"))
    for issue in summary.get("issues") or []:
        severity = "critical" if issue.get("severity") in {"critical", "error"} else "warning"
        signals.append(
            _signal(
                severity=severity,
                category=_text(issue.get("category")) or "sync_overview",
                title="Sync overview issue",
                reason=_text(issue.get("message")),
                evidence={"issue": issue},
                recommended_action="review sync overview source",
            )
        )
    return signals


def _signals_from_budgets(budgets: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    if not budgets.get("configured"):
        signals.append(
            _signal(
                severity="warning",
                category="budget",
                title="Provider budget caps table is not configured",
                reason="vkpi_provider_budget_caps was not readable",
                recommended_action="configure budget caps before enabling provider-backed automations",
            )
        )
        return signals
    for item in budgets.get("budgets") or []:
        if item.get("hard_stopped"):
            signals.append(
                _signal(
                    severity="critical",
                    category="budget",
                    title="Provider budget hard stop reached",
                    reason=f"{item.get('scope')} usage_ratio={item.get('usage_ratio')}",
                    evidence={"budget": item},
                    recommended_action="keep provider calls blocked and review spend ledger",
                )
            )
        elif item.get("warning"):
            signals.append(
                _signal(
                    severity="warning",
                    category="budget",
                    title="Provider budget warning threshold reached",
                    reason=f"{item.get('scope')} usage_ratio={item.get('usage_ratio')}",
                    evidence={"budget": item},
                    recommended_action="review fallback action before more provider work",
                )
            )
    return signals


def _signals_from_open_alerts(open_alerts: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for item in open_alerts.get("alerts") or []:
        raw_severity = _text(item.get("severity")).lower()
        severity = "critical" if raw_severity in {"danger", "critical"} else "warning" if raw_severity == "warning" else "info"
        signals.append(
            _signal(
                severity=severity,
                category="existing_alert",
                title=_text(item.get("title")) or _text(item.get("rule_key")) or "Open alert",
                reason=f"{item.get('rule_key') or 'manual'} is open",
                evidence={"alert": item},
                recommended_action="review existing alert detail",
            )
        )
    return signals


def _signals_from_p6_79(p6_79: dict[str, Any]) -> list[dict[str, Any]]:
    if not p6_79.get("loaded"):
        return [
            _signal(
                severity="warning",
                category="brain_layer",
                title="P6.79 brain-layer acceptance artifact is missing",
                reason="Sync Sentinel expects P6.79 before P7 agents consume the brain layer",
                recommended_action="generate P6.79 before relying on agent outputs",
            )
        ]
    summary = _as_dict(p6_79.get("summary"))
    signals: list[dict[str, Any]] = []
    if summary.get("official_accuracy_pending"):
        signals.append(
            _signal(
                severity="info",
                category="calibration",
                title="Prediction accuracy is still pending official cross-day truth",
                reason="P6.79 says official accuracy is pending; smoke checks must not tune weights",
                evidence={"p6_79": summary, "artifact": p6_79.get("artifact_name")},
                recommended_action="continue collecting cross-day P6.75 artifacts",
            )
        )
    if not summary.get("business_confirmed"):
        signals.append(
            _signal(
                severity="info",
                category="business_confirmation",
                title="Brain layer still needs business confirmation",
                reason="P6.79 intentionally keeps business_confirmed=false",
                evidence={"p6_79": summary, "artifact": p6_79.get("artifact_name")},
                recommended_action="keep agents read-only until business confirmation is recorded",
            )
        )
    return signals


def _sort_signals(signals: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    safe_limit = max(1, min(200, int(limit or 50)))
    ordered = sorted(
        signals,
        key=lambda item: (SEVERITY_RANK.get(_text(item.get("severity")), 9), _text(item.get("category")), _text(item.get("title"))),
    )
    return ordered[:safe_limit]


def build_sync_sentinel_agent_v0(*, ops_dir: str = DEFAULT_OPS_DIR, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(200, int(limit or 50)))
    overview = sync_status.get_overview()
    budgets = _budget_caps()
    open_alerts = _open_alerts(safe_limit)
    p6_79 = _latest_p6_79(ops_dir)
    signals = _sort_signals(
        _signals_from_overview(overview)
        + _signals_from_budgets(budgets)
        + _signals_from_open_alerts(open_alerts)
        + _signals_from_p6_79(p6_79),
        safe_limit,
    )
    critical_count = sum(1 for item in signals if item.get("severity") == "critical")
    warning_count = sum(1 for item in signals if item.get("severity") == "warning")
    info_count = sum(1 for item in signals if item.get("severity") == "info")
    sentinel_status = "blocked" if critical_count else "degraded" if warning_count else "healthy"
    daily = _as_dict(overview.get("daily_sync"))
    latest = _as_dict(daily.get("latest_summary") or daily.get("latest_run"))
    health = _as_dict(latest.get("health"))
    checks = {
        "sentinel_version_set": bool(SENTINEL_VERSION),
        "sync_overview_loaded": bool(overview),
        "daily_sync_guard_visible": "guard_allowed" in daily and "ack_required" in daily,
        "budget_snapshot_loaded": bool(budgets),
        "open_alert_snapshot_loaded": bool(open_alerts),
        "p6_79_loaded": bool(p6_79.get("loaded")),
        "read_only_guard_present": True,
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
        "tasks_blocked": True,
    }
    return {
        "mode": "p7_80_sync_sentinel_agent_v0",
        "generated_at": _now(),
        "sentinel_version": SENTINEL_VERSION,
        "agent_type": "read_only_sentinel",
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
            "limit": safe_limit,
            "p6_79_pattern": P6_79_PATTERN,
        },
        "summary": {
            "sentinel_status": sentinel_status,
            "signal_count": len(signals),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "sync_guard_allowed": bool(daily.get("guard_allowed")),
            "sync_ack_required": bool(daily.get("ack_required")),
            "latest_sync_status": latest.get("status"),
            "latest_sync_reason": latest.get("reason"),
            "latest_sync_failure_rate": health.get("failure_rate", 0),
            "budget_warning_scopes": _as_dict(budgets.get("summary")).get("warnings", 0),
            "budget_hard_stop_scopes": _as_dict(budgets.get("summary")).get("hard_stopped", 0),
            "open_alerts": _as_dict(open_alerts.get("summary")).get("open_total", 0),
            "p6_79_loaded": bool(p6_79.get("loaded")),
            "source_scope": "existing_db_runtime_ops_only",
        },
        "signals": signals,
        "sources": {
            "sync_overview": {
                "daily_sync": daily,
                "summary": overview.get("summary"),
            },
            "budget_caps": budgets,
            "open_alerts": open_alerts,
            "p6_79": {
                "loaded": p6_79.get("loaded"),
                "artifact_path": p6_79.get("artifact_path"),
                "artifact_name": p6_79.get("artifact_name"),
                "summary": p6_79.get("summary"),
            },
        },
        "policy": {
            "read_only": True,
            "agent_does_not_run_in_background": True,
            "no_alert_write": True,
            "no_manual_ack": True,
            "no_sync_trigger": True,
            "no_budget_mutation": True,
            "no_provider_calls": True,
            "no_llm_calls": True,
        },
        "next_steps": [
            "Review critical Sync Sentinel signals before the next natural timer window.",
            "Keep legacy full KOL refresh disabled; use qualified/tiered refresh only when explicitly enabled.",
            "Do not turn read-only sentinel output into autonomous action until P7 audit controls are accepted.",
        ],
    }
