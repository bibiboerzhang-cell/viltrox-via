#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据飞轮七任务开闸(scheduler_tasks.enabled),默认只列现状。

七任务(config-gate,迁移种子默认 OFF):
  vkpi_kol_video_metric_refresh   每小时:按 hot/warm/cold 期限把到期订阅入队(worker 才调 provider)
  vkpi_kol_content_monitoring     每小时:只扫显式 active 的最近内容订阅
  vkpi_forecast_outcomes_refresh  每日 04:50 CN:满窗 pending 预测回查实测播放 → 写回 actual/outcome
  vkpi_prediction_weekly_rollup   每周一 07:10 CN:已裁决流水补账 evals + WAPE/带内率/方向命中/FVA
  vkpi_baseline_forecast_daily    每日:补预测对照基线
  vkpi_drift_monitor              每周:有样本才计算漂移
  vkpi_gtm_windows_refresh        每日:只回填 7/14/28 天证据,不自动裁决

用法:
  PYTHONPATH=backend .venv/bin/python scripts/ops/enable_tracking_schedulers.py            # 只看
  PYTHONPATH=backend .venv/bin/python scripts/ops/enable_tracking_schedulers.py --apply    # UPDATE enabled=TRUE
  PYTHONPATH=backend .venv/bin/python scripts/ops/enable_tracking_schedulers.py --apply --disable  # 关回
  PYTHONPATH=backend .venv/bin/python scripts/ops/enable_tracking_schedulers.py --apply --run-rollup
      # 额外立即跑一次预测真值 rollup(写回 actual + 产出 evals + WAPE/FVA),不必等周一
prod 由用户亲自跑。--apply 之外零写入;调度器进程读 scheduler_tasks 每次触发时现查,无需重启。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
for _extra in (REPO / "scripts", REPO / "backend"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from stdout_utils import out, out_json  # noqa: E402

TASK_KEYS = (
    "vkpi_kol_video_metric_refresh",
    "vkpi_kol_content_monitoring",
    "vkpi_forecast_outcomes_refresh",
    "vkpi_prediction_weekly_rollup",
    "vkpi_baseline_forecast_daily",
    "vkpi_drift_monitor",
    "vkpi_gtm_windows_refresh",
)


class SchedulerTaskUpdateIncomplete(RuntimeError):
    """The reviewed seven-row scheduler scope changed during an apply."""

    def __init__(self, updated: int) -> None:
        self.updated = int(updated)
        super().__init__(f"scheduler task update incomplete: {self.updated}/{len(TASK_KEYS)}")


def _truthy(value: Any) -> bool:
    return value in (True, 1, "1", "t", "true", "TRUE")


def task_status(conn: Any) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in TASK_KEYS)
    rows = conn.execute(
        f"""
        SELECT task_key, enabled, max_daily_runs, max_daily_cost_cents, owner, risk_level,
               last_run_at, last_success_at, last_error
        FROM scheduler_tasks
        WHERE task_key IN ({placeholders})
        """,
        tuple(TASK_KEYS),
    ).fetchall()
    by_key = {str(dict(row)["task_key"]): dict(row) for row in rows}
    result = []
    for key in TASK_KEYS:
        row = by_key.get(key)
        if row is None:
            result.append({"task_key": key, "registered": False, "enabled": False})
            continue
        result.append({
            "task_key": key,
            "registered": True,
            "enabled": _truthy(row.get("enabled")),
            "max_daily_runs": row.get("max_daily_runs"),
            "owner": row.get("owner"),
            "risk_level": row.get("risk_level"),
            "last_run_at": str(row.get("last_run_at") or ""),
            "last_success_at": str(row.get("last_success_at") or ""),
            "last_error": str(row.get("last_error") or "")[:160],
        })
    return result


def readiness(conn: Any) -> dict[str, Any]:
    """Key inputs the seven tasks depend on, so an operator sees why a run is empty."""
    from app.domains.kol import video_tracking_budget

    def scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = conn.execute(sql, params).fetchone()
        return list(dict(row).values())[0] if row else None

    scope = video_tracking_budget.load_scope(conn)
    return {
        "tracking_active": scalar(
            "SELECT COUNT(*) AS n FROM vkpi_kol_video_metric_tracking WHERE status='active'"
        ),
        "tracking_paused": scalar(
            "SELECT COUNT(*) AS n FROM vkpi_kol_video_metric_tracking WHERE status<>'active'"
        ),
        "content_subscriptions": {
            "active": scalar(
                "SELECT COUNT(*) AS n FROM vkpi_kol_content_monitoring_subscriptions WHERE status='active'"
            ),
            "paused": scalar(
                "SELECT COUNT(*) AS n FROM vkpi_kol_content_monitoring_subscriptions WHERE status='paused'"
            ),
        },
        "budget_scope": None if scope is None else {
            "cap_usd": float(scope.get("cap_usd") or 0),
            "current_spend": float(scope.get("current_spend") or 0),
            "month_spend_from_ledger": video_tracking_budget.month_spend_usd(conn),
        },
        "forecast_pending_over_30d": scalar(
            """
            SELECT COUNT(*) AS n FROM vkpi_forecast_log
            WHERE outcome='pending' AND created_at < NOW() - INTERVAL '30 days'
            """
        ),
        "forecast_resolved": scalar(
            "SELECT COUNT(*) AS n FROM vkpi_forecast_log WHERE outcome IN ('hit_in_band','below','above')"
        ),
        "prediction_evals": scalar("SELECT COUNT(*) AS n FROM vkpi_prediction_evals"),
        "prediction_evals_measured": scalar(
            """
            SELECT COUNT(*) AS n FROM vkpi_prediction_evals
            WHERE COALESCE(actual_json->>'binding_status', '')='measured_from_snapshots'
            """
        ),
    }


def set_enabled(conn: Any, *, enabled: bool) -> dict[str, int]:
    placeholders = ", ".join("?" for _ in TASK_KEYS)
    cursor = conn.execute(
        f"UPDATE scheduler_tasks SET enabled=?, updated_at=NOW() WHERE task_key IN ({placeholders})",
        (bool(enabled), *TASK_KEYS),
    )
    updated = int(getattr(cursor, "rowcount", 0) or 0)
    if updated != len(TASK_KEYS):
        raise SchedulerTaskUpdateIncomplete(updated)
    return {"updated": updated}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真 UPDATE scheduler_tasks(默认只列现状)")
    parser.add_argument("--disable", action="store_true", help="与 --apply 连用:关闸而非开闸")
    parser.add_argument(
        "--run-rollup", action="store_true",
        help="与 --apply 连用:立即跑一次 prediction_rollup_truth.rollup_forecast_log_truth",
    )
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app.db.connection import get_conn

    conn = get_conn()
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "before": task_status(conn),
        "readiness": readiness(conn),
        "update": None,
        "after": None,
        "rollup": None,
        "missing_task_keys": [],
        "error": None,
    }
    report["missing_task_keys"] = [
        row["task_key"] for row in report["before"] if not row.get("registered")
    ]
    exit_code = 0
    if args.apply:
        if report["missing_task_keys"]:
            conn.rollback()
            report["error"] = {
                "code": "scheduler_tasks_missing",
                "expected": len(TASK_KEYS),
                "registered": len(TASK_KEYS) - len(report["missing_task_keys"]),
                "missing_task_keys": list(report["missing_task_keys"]),
            }
            exit_code = 2
        else:
            try:
                report["update"] = set_enabled(conn, enabled=not args.disable)
                conn.commit()
                report["after"] = task_status(conn)
                if args.run_rollup:
                    from app.domains.market_brain import prediction_rollup_truth

                    rollup = prediction_rollup_truth.rollup_forecast_log_truth(conn)
                    report["rollup"] = {
                        "backfill": rollup.get("backfill"),
                        "evals": rollup.get("evals"),
                        "metrics": rollup.get("metrics"),
                    }
            except SchedulerTaskUpdateIncomplete as exc:
                conn.rollback()
                report["error"] = {
                    "code": "scheduler_task_update_incomplete",
                    "expected": len(TASK_KEYS),
                    "updated": exc.updated,
                }
                exit_code = 2
    else:
        conn.rollback()

    if args.json:
        out_json(report, ensure_ascii=False, default=str)
        return exit_code
    out(f"mode={report['mode']}")
    for row in report["before"]:
        out(f"  {row['task_key']}: registered={row['registered']} enabled={row['enabled']} "
            f"last_run={row.get('last_run_at') or '-'} last_error={row.get('last_error') or '-'}")
    out(f"readiness={json.dumps(report['readiness'], ensure_ascii=False, default=str)}")
    if report["error"] is not None:
        out(f"error={json.dumps(report['error'], ensure_ascii=False, default=str)}")
    if report["update"] is not None:
        out(f"update={json.dumps(report['update'])} -> enabled={not args.disable}")
        for row in report["after"] or []:
            out(f"  {row['task_key']}: enabled={row['enabled']}")
    if report["rollup"] is not None:
        out(f"rollup={json.dumps(report['rollup'], ensure_ascii=False, default=str)}")
    if not args.apply:
        out("dry-run: 未改 scheduler_tasks;加 --apply 开闸(--disable 关闸)。")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
