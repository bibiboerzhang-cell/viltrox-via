"""Ops threshold alerting (A4) — read-only ops health → vkpi_alerts.

诚实 by design:本模块只做「读阈值 → emit / clear 告警」,绝不执行运维动作。
- 只 READ 既有真实表(vkpi_provider_budget_caps / apify_jobs / job_execution_ledger),
  把超阈状态以 upsert_alert 写进既有 vkpi_alerts 表(与 domains/alerts/service.py 同款 emit),
  低于阈值则 resolve_open_alert 自动清理。绝不重排队、不重启 worker、不改预算、不付款、
  不裁决任何业务;零触 viltrox_fit_score / rule_v0。
- 阈值表(budget >=80%/100%、队列阻塞数、worker 心跳过期、失败率)全部可经 env 覆盖,
  缺省值保守。相关表缺失(迁移未跑)时安静跳过该规则并清掉自己开过的告警,绝不编造。

四类阈值告警:
  - budget_threshold        预算用量 >=80% (warning) / >=100% (danger),逐 scope。
  - queue_blocked           apify_jobs failed 堆积数 >= 阈值。
  - worker_offline          job_execution_ledger 有 running/processing 任务但 heartbeat 过期。
  - failure_rate            apify_jobs 近 N 小时失败率 >= 阈值(样本量足够时)。

emit 全部走 domains/alerts.service.upsert_alert(→ vkpi_alerts,前端 normalizeAlerts 直接消费),
不另开表、不另发渠道。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.alerts.common import resolve_open_alert as _resolve_open_alert
from app.domains.alerts.service import upsert_alert

logger = get_logger(__name__)

_RULE_KEY = "ops.threshold"


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        return int(default)


# 阈值(env 可覆盖,缺省保守)。
_BUDGET_WARN_RATIO = _env_float("OPS_ALERT_BUDGET_WARN_RATIO", 0.80)
_BUDGET_STOP_RATIO = _env_float("OPS_ALERT_BUDGET_STOP_RATIO", 1.00)
_QUEUE_BLOCKED_MIN = _env_int("OPS_ALERT_QUEUE_BLOCKED_MIN", 10)
_WORKER_STALE_MINUTES = _env_int("OPS_ALERT_WORKER_STALE_MINUTES", 15)
_FAILURE_RATE_WINDOW_HOURS = _env_int("OPS_ALERT_FAILURE_WINDOW_HOURS", 6)
_FAILURE_RATE_MIN_SAMPLE = _env_int("OPS_ALERT_FAILURE_MIN_SAMPLE", 20)
_FAILURE_RATE_RATIO = _env_float("OPS_ALERT_FAILURE_RATE_RATIO", 0.30)

# job_execution_ledger 的「活动中」状态(有这些状态却心跳过期 → worker 疑似离线/卡死)。
_LEDGER_RUNNING_STATUSES = ("running", "processing")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_cutoff(delta: timedelta) -> str:
    return (_utc_now() - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _meta(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def generate_budget_threshold_alerts() -> dict[str, Any]:
    """预算用量 >=80% (warning) / >=100% (danger),逐 provider budget scope。

    读 vkpi_provider_budget_caps(只读);超阈 upsert,回落 resolve。绝不改预算/付款。
    """
    rule_key = f"{_RULE_KEY}.budget"
    if not table_exists("vkpi_provider_budget_caps"):
        return {"alerts": [], "count": 0, "cleared": [], "schema_ready": False, "rule_key": rule_key}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT scope, cap_usd, current_spend
        FROM vkpi_provider_budget_caps
        ORDER BY scope
        """
    ).fetchall()
    created: list[dict[str, Any]] = []
    cleared: list[str] = []
    for row in rows:
        data = dict(row)
        scope_key = str(data.get("scope") or "").strip()
        if not scope_key:
            continue
        cap = float(data.get("cap_usd") or 0)
        spend = float(data.get("current_spend") or 0)
        ratio = (spend / cap) if cap > 0 else 0.0
        alert_key = f"ops-budget-{scope_key}"
        if cap <= 0 or ratio < _BUDGET_WARN_RATIO:
            if _resolve_open_alert(conn, alert_key):
                cleared.append(alert_key)
            continue
        hard = ratio >= _BUDGET_STOP_RATIO
        severity = "danger" if hard else "warning"
        state = "100% reached" if hard else f">={int(_BUDGET_WARN_RATIO * 100)}%"
        created.append(
            upsert_alert(
                alert_key=alert_key,
                severity=severity,
                target_type="budget_scope",
                target_id=None,
                title=f"Budget {state}: {scope_key}",
                body=(
                    f"{scope_key} spend ${spend:.4f} of ${cap:.2f} ({ratio:.0%}). "
                    "Read-only ops alert; review caps or fallback action."
                ),
                rule_key=rule_key,
                metadata_json=_meta(
                    {
                        "scope": scope_key,
                        "cap_usd": cap,
                        "current_spend": spend,
                        "usage_ratio": round(ratio, 4),
                        "warn_ratio": _BUDGET_WARN_RATIO,
                        "stop_ratio": _BUDGET_STOP_RATIO,
                        "hard_stop": hard,
                    }
                ),
            )
        )
    conn.commit()
    return {"alerts": created, "count": len(created), "cleared": cleared, "rule_key": rule_key}


def generate_queue_blocked_alerts() -> dict[str, Any]:
    """apify_jobs 里 failed(阻塞/卡住)堆积数 >= 阈值 → 一条聚合告警;回落清理。

    只读计数;绝不重排队、不清队列。
    """
    rule_key = f"{_RULE_KEY}.queue_blocked"
    alert_key = "ops-queue-blocked"
    if not table_exists("apify_jobs"):
        return {"alerts": [], "count": 0, "cleared": [], "schema_ready": False, "rule_key": rule_key}
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS blocked FROM apify_jobs WHERE status = 'failed'"
    ).fetchone()
    blocked = int((dict(row) if row else {}).get("blocked") or 0)
    if blocked < _QUEUE_BLOCKED_MIN:
        cleared = [alert_key] if _resolve_open_alert(conn, alert_key) else []
        conn.commit()
        return {"alerts": [], "count": 0, "cleared": cleared, "blocked": blocked, "rule_key": rule_key}
    severity = "danger" if blocked >= max(_QUEUE_BLOCKED_MIN * 2, _QUEUE_BLOCKED_MIN + 10) else "warning"
    alert = upsert_alert(
        alert_key=alert_key,
        severity=severity,
        target_type="job_queue",
        target_id=None,
        title=f"Job queue blocked: {blocked} failed jobs",
        body=(
            f"{blocked} apify_jobs are in 'failed' state (threshold {_QUEUE_BLOCKED_MIN}). "
            "Read-only ops alert; inspect and requeue or clear manually."
        ),
        rule_key=rule_key,
        metadata_json=_meta({"blocked": blocked, "threshold": _QUEUE_BLOCKED_MIN}),
    )
    conn.commit()
    return {"alerts": [alert], "count": 1, "cleared": [], "blocked": blocked, "rule_key": rule_key}


def generate_worker_offline_alerts() -> dict[str, Any]:
    """有 running/processing 任务但 heartbeat_at 过期(> N 分钟)→ worker 疑似离线/卡死。

    读 job_execution_ledger(只读);绝不重启/取消/接管任务。heartbeat_at 列由迁移 056 引入,
    缺列/缺表时安静跳过并清理自己开过的告警(诚实)。
    """
    rule_key = f"{_RULE_KEY}.worker_offline"
    alert_key = "ops-worker-offline"
    if not table_exists("job_execution_ledger"):
        return {"alerts": [], "count": 0, "cleared": [], "schema_ready": False, "rule_key": rule_key}
    conn = get_conn()
    cutoff = _iso_cutoff(timedelta(minutes=max(1, _WORKER_STALE_MINUTES)))
    status_placeholders = ",".join(["?"] * len(_LEDGER_RUNNING_STATUSES))
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS stale,
                   MIN(heartbeat_at) AS oldest_heartbeat
            FROM job_execution_ledger
            WHERE status IN ({status_placeholders})
              AND heartbeat_at IS NOT NULL
              AND heartbeat_at < ?
            """,
            (*_LEDGER_RUNNING_STATUSES, cutoff),
        ).fetchone()
    except Exception:
        # heartbeat_at 列缺失(迁移 056 未跑)→ 诚实跳过,清掉旧告警。
        logger.debug("ops.alerting: worker heartbeat read failed", exc_info=True)
        cleared = [alert_key] if _resolve_open_alert(conn, alert_key) else []
        conn.commit()
        return {"alerts": [], "count": 0, "cleared": cleared, "schema_ready": False, "rule_key": rule_key}
    data = dict(row) if row else {}
    stale = int(data.get("stale") or 0)
    if stale <= 0:
        cleared = [alert_key] if _resolve_open_alert(conn, alert_key) else []
        conn.commit()
        return {"alerts": [], "count": 0, "cleared": cleared, "stale": 0, "rule_key": rule_key}
    alert = upsert_alert(
        alert_key=alert_key,
        severity="danger",
        target_type="worker",
        target_id=None,
        title=f"Worker offline: {stale} job(s) with stale heartbeat",
        body=(
            f"{stale} running/processing job(s) have no heartbeat in {_WORKER_STALE_MINUTES} min "
            f"(oldest {data.get('oldest_heartbeat')}). Read-only ops alert; check worker liveness."
        ),
        rule_key=rule_key,
        metadata_json=_meta(
            {
                "stale_jobs": stale,
                "stale_minutes": _WORKER_STALE_MINUTES,
                "oldest_heartbeat": data.get("oldest_heartbeat"),
            }
        ),
    )
    conn.commit()
    return {"alerts": [alert], "count": 1, "cleared": [], "stale": stale, "rule_key": rule_key}


def generate_failure_rate_alerts() -> dict[str, Any]:
    """apify_jobs 近 N 小时失败率 >= 阈值(样本量足够时)→ 一条聚合告警;回落清理。

    只读计算;绝不重排队/改任务。样本不足时不告警(诚实,避免少量样本噪声)。
    """
    rule_key = f"{_RULE_KEY}.failure_rate"
    alert_key = "ops-failure-rate"
    if not table_exists("apify_jobs"):
        return {"alerts": [], "count": 0, "cleared": [], "schema_ready": False, "rule_key": rule_key}
    conn = get_conn()
    cutoff = _iso_cutoff(timedelta(hours=max(1, _FAILURE_RATE_WINDOW_HOURS)))
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done
        FROM apify_jobs
        WHERE status IN ('failed', 'done')
          AND updated_at >= ?
        """,
        (cutoff,),
    ).fetchone()
    data = dict(row) if row else {}
    total = int(data.get("total") or 0)
    failed = int(data.get("failed") or 0)
    if total < _FAILURE_RATE_MIN_SAMPLE:
        # 样本不足:不告警,清掉旧告警(避免噪声;诚实)。
        cleared = [alert_key] if _resolve_open_alert(conn, alert_key) else []
        conn.commit()
        return {
            "alerts": [],
            "count": 0,
            "cleared": cleared,
            "total": total,
            "failed": failed,
            "below_min_sample": True,
            "rule_key": rule_key,
        }
    rate = failed / total if total else 0.0
    if rate < _FAILURE_RATE_RATIO:
        cleared = [alert_key] if _resolve_open_alert(conn, alert_key) else []
        conn.commit()
        return {"alerts": [], "count": 0, "cleared": cleared, "total": total, "failed": failed, "rate": round(rate, 4), "rule_key": rule_key}
    severity = "danger" if rate >= min(1.0, _FAILURE_RATE_RATIO * 2) else "warning"
    alert = upsert_alert(
        alert_key=alert_key,
        severity=severity,
        target_type="job_queue",
        target_id=None,
        title=f"High job failure rate: {rate:.0%}",
        body=(
            f"{failed}/{total} apify_jobs failed in the last {_FAILURE_RATE_WINDOW_HOURS}h "
            f"({rate:.0%}, threshold {_FAILURE_RATE_RATIO:.0%}). Read-only ops alert; investigate root cause."
        ),
        rule_key=rule_key,
        metadata_json=_meta(
            {
                "total": total,
                "failed": failed,
                "done": int(data.get("done") or 0),
                "failure_rate": round(rate, 4),
                "window_hours": _FAILURE_RATE_WINDOW_HOURS,
                "min_sample": _FAILURE_RATE_MIN_SAMPLE,
                "threshold": _FAILURE_RATE_RATIO,
            }
        ),
    )
    conn.commit()
    return {"alerts": [alert], "count": 1, "cleared": [], "total": total, "failed": failed, "rate": round(rate, 4), "rule_key": rule_key}


def generate_ops_alerts() -> dict[str, Any]:
    """跑全部 ops 阈值规则;每条规则自带 try 兜底,单条失败不拖垮其余(诚实部分成功)。"""
    results: dict[str, Any] = {}
    alerts: list[dict[str, Any]] = []
    for name, fn in (
        ("budget_threshold", generate_budget_threshold_alerts),
        ("queue_blocked", generate_queue_blocked_alerts),
        ("worker_offline", generate_worker_offline_alerts),
        ("failure_rate", generate_failure_rate_alerts),
    ):
        try:
            res = fn()
        except Exception:
            logger.exception("ops.alerting.%s_failed", name)
            res = {"alerts": [], "count": 0, "error": True, "rule_key": f"{_RULE_KEY}.{name}"}
        results[name] = res
        alerts.extend(res.get("alerts") or [])
    return {"alerts": alerts, "count": len(alerts), **results}
