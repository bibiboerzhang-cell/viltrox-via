#!/usr/bin/env python3
"""灰度阶梯分档脚本 — scheduler_tasks enabled 开关的分档运维 CLI。

照「灰度阶梯 runbook」(GRAY_RELEASE_RUNBOOK.md)落地:把 scheduler_tasks
里的 11 个任务按 risk_level 分成 low / medium / high 三档,逐档放量。

只读/只写 scheduler_tasks 这一张表的 enabled 开关。绝不触碰
viltrox_fit_score / rule_v0 任何既有表(铁律)。

运行(必须用仓库 venv):
    /Users/bibiboer/Documents/V-KPI——marketing/.venv/bin/python scripts/gray_release.py --list
    .venv/bin/python scripts/gray_release.py --tier low            # dry-run,只打印
    .venv/bin/python scripts/gray_release.py --tier low --apply     # 真正写入 enabled=TRUE
    .venv/bin/python scripts/gray_release.py --disable-tier high --apply  # 回滚

默认 dry-run:不带 --apply 只打印将要改的 task_key,绝不 commit。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Bootstrap：把 backend 加进 sys.path 后再 import 应用层(照 scripts/audit_vkpi_runtime_state.py 惯例)。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import close_db_runtime_sync, get_conn  # noqa: E402


# ---------------------------------------------------------------------------
# 档位常量:脚本自带,不从 registry 反推,保证离线/无 import 也可读懂分档。
# 与 migrations/130_vkpi_scheduler_tasks.sql + migrations/141_vkpi_action_inbox.sql
# 的种子分档逐字一致(daily_action_inbox_generate 在 141 后补)。
# ---------------------------------------------------------------------------
TIERS: dict[str, list[str]] = {
    "low": [
        "task_queue_health",
        "kol_search_session_reconcile",
        "project_shipment_sync",
        "official_account_metrics_sync",
        "daily_action_inbox_generate",
    ],
    "medium": [
        "project_content_observation_scan",
        "provider_pressure_retry",
        "kol_profile_incremental_refresh",
    ],
    "high": [
        "batch_video_analysis",
        "deep_result_backfill",
        "failed_pool_recycle",
    ],
}

# risk_level 文本无法直接 ORDER BY 出 low<medium<high,照搬 registry 的显式权重。
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# high 档属 LLM/批量,放量前的红线提醒(脚本层硬编码,不阻断)。
_HIGH_REDLINE = (
    "[RED LINE] high 档为 LLM/批量类任务(batch_video_analysis / deep_result_backfill / "
    "failed_pool_recycle)。放量前确认:\n"
    "  - budget_guard.check_budget 已就位且预算已配置(超预算必拦);\n"
    "  - LLM 批量产物 requires_approval 仍为人审,Action execute 永远人审;\n"
    "  - W0 验收已通过。\n"
    "如已确认,本次 --apply 将照运维显式选择放行 high 档。"
)


def _enabled_label(value: Any) -> str:
    """enabled 读出来是 int 0/1(_normalize_pg_value 归一),映成 OFF/ON。"""
    try:
        return "ON" if int(value) != 0 else "OFF"
    except (TypeError, ValueError):
        return "ON" if str(value).strip().lower() in {"true", "yes", "t", "1"} else "OFF"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")


def cmd_list(conn: Any) -> int:
    """--list:读出全部 11 行 scheduler_tasks,按 risk_level 再 task_key 排序打印。"""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT task_key, risk_level, enabled, last_run_at FROM scheduler_tasks"
        ).fetchall()
    ]
    rows.sort(key=lambda r: (_RISK_ORDER.get(r.get("risk_level"), 99), r.get("task_key") or ""))

    if not rows:
        print("(scheduler_tasks 为空 — 检查 migration 130/141 是否已落地)")
        return 1

    print(f"scheduler_tasks: {len(rows)} 行\n")
    current_tier: str | None = None
    for r in rows:
        tier = r.get("risk_level")
        if tier != current_tier:
            current_tier = tier
            members = TIERS.get(tier or "", [])
            print(f"== {str(tier).upper()} ({len(members)} 任务) ==")
        last_run = r.get("last_run_at")
        last_run_str = "" if last_run in (None, "") else f"  last_run={last_run}"
        print(f"  [{_enabled_label(r.get('enabled')):>3}] {r.get('task_key')}{last_run_str}")
    print()
    return 0


def _current_enabled(conn: Any, task_keys: list[str]) -> dict[str, Any]:
    """读出指定 task_key 现状 enabled,用于 dry-run 展示 before→after。

    逐 key 读,避免 IN 列表占位符方言差异(get_conn 用 '?' 占位)。
    """
    out: dict[str, Any] = {}
    for key in task_keys:
        row = conn.execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key=?", (key,)
        ).fetchone()
        out[key] = None if row is None else dict(row).get("enabled")
    return out


def _apply_tier(conn: Any, tier: str, target_enabled: bool, apply: bool) -> int:
    """对某档执行 enabled 置位。apply=False 为 dry-run,绝不 commit。"""
    task_keys = TIERS[tier]
    target_label = "ON (enabled=TRUE)" if target_enabled else "OFF (enabled=FALSE)"
    before = _current_enabled(conn, task_keys)

    verb = "APPLY" if apply else "DRY-RUN"
    print(f"[{verb}] tier={tier} → {target_label}")
    for key in task_keys:
        cur = _enabled_label(before.get(key)) if before.get(key) is not None else "MISSING"
        print(f"  {key}: {cur} → {_enabled_label(int(target_enabled))}")

    if not apply:
        print("\n(dry-run:未写库。加 --apply 才真正执行。)")
        return 0

    conn.execute(
        "UPDATE scheduler_tasks SET enabled=?, updated_at=? WHERE risk_level=?",
        (target_enabled, _utc_now(), tier),
    )
    conn.commit()
    print(f"\n[OK] tier={tier} 已写入 enabled={'TRUE' if target_enabled else 'FALSE'} 并 commit。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gray_release.py",
        description="scheduler_tasks 灰度阶梯分档开关 (low→medium→high)。默认 dry-run。",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="列各档 task 与现状 enabled (OFF/ON)。")
    g.add_argument("--tier", choices=["low", "medium", "high"], help="把该档 enabled 置 TRUE。")
    g.add_argument(
        "--disable-tier",
        choices=["low", "medium", "high"],
        dest="disable_tier",
        help="把该档 enabled 置 FALSE(回滚)。",
    )
    p.add_argument("--apply", action="store_true", help="真正写库;不带则 dry-run 只打印。")
    p.add_argument("--yes", action="store_true", help="high 档跳过红线交互确认。")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = get_conn()
    try:
        if args.list:
            return cmd_list(conn)

        if args.tier:
            # high 档红线提醒(stderr),不阻断。
            if args.tier == "high" and args.apply:
                print(_HIGH_REDLINE, file=sys.stderr)
                if not args.yes:
                    try:
                        ans = input("继续放量 high 档?输入 yes 确认: ").strip().lower()
                    except EOFError:
                        ans = ""
                    if ans != "yes":
                        print("[ABORT] 未确认,high 档未写入。", file=sys.stderr)
                        return 2
            return _apply_tier(conn, args.tier, target_enabled=True, apply=args.apply)

        if args.disable_tier:
            return _apply_tier(conn, args.disable_tier, target_enabled=False, apply=args.apply)

        return 1
    finally:
        try:
            close_db_runtime_sync()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
