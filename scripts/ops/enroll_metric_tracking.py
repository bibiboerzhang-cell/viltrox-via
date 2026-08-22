#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标追踪点火:把 MY KOL 收藏集下的视频证据幂等登记进 vkpi_kol_video_metric_tracking。

采样日程按证据发布年龄分层(与 scheduler 同一套口径,video_metric_schedule.TIER_CADENCES):
  hot  ≤7 天  → 每 6h 采样
  warm ≤30 天 → 每 24h
  cold >30 天 → 每 7d
登记只写订阅表,绝不调 provider、绝不入队;实际入队由 scheduler_tasks.vkpi_kol_video_metric_refresh
(用 scripts/ops/enable_tracking_schedulers.py 开闸)逐小时按期分批。

同时幂等补种预算 scope 行 vkpi_provider_budget_caps.scope='metric_tracking'(cost_tag=metric_tracking,
月封顶取 env VKPI_METRIC_TRACKING_MONTHLY_CAP_USD,默认 30;缺行 = scheduler 入队闸 fail-closed)。

幂等:ON CONFLICT (evidence_id) DO NOTHING;已 active / 已 paused 的行一律不动。
执行者:每条订阅带一个能过 scheduler 复核(authorize_video_metric_refresh_actor)的 staff;
收藏人过不了(pending 用户等)时用 --actor-staff-id <id|auto> 指定管理员兜底,否则该证据跳过并计数。

用法(默认 dry-run,零写入):
  PYTHONPATH=backend .venv/bin/python scripts/ops/enroll_metric_tracking.py
  PYTHONPATH=backend .venv/bin/python scripts/ops/enroll_metric_tracking.py --actor-staff-id auto
  PYTHONPATH=backend .venv/bin/python scripts/ops/enroll_metric_tracking.py --apply --limit 200
  PYTHONPATH=backend .venv/bin/python scripts/ops/enroll_metric_tracking.py --apply --actor-staff-id 84 --json
prod 由用户亲自跑(DATABASE_URL 指向哪就写哪)。
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


def _auto_fallback_actor(conn: Any) -> int | None:
    """First active owner/admin whose user account is approved/active."""
    from app.domains.kol.video_metric_refresh import authorize_video_metric_refresh_actor

    rows = conn.execute(
        """
        SELECT s.id AS staff_id,
               (
                   SELECT f.kol_pool_id FROM vkpi_kol_pool_favorites f
                   ORDER BY f.id LIMIT 1
               ) AS sample_kol
        FROM staff s
        JOIN users u ON u.id=s.user_id
        WHERE COALESCE(s.active, 0)=1 AND s.suspended_at IS NULL
          AND (COALESCE(s.is_owner, 0)=1 OR LOWER(COALESCE(s.role, ''))='admin')
        ORDER BY CASE WHEN COALESCE(s.is_owner, 0)=1 THEN 0 ELSE 1 END, s.id
        LIMIT 20
        """
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        sample_kol = row.get("sample_kol")
        if sample_kol is None:
            return None
        actor, _err = authorize_video_metric_refresh_actor(
            conn, staff_id=int(row["staff_id"]), kol_pool_id=int(sample_kol),
        )
        if actor is not None:
            return int(row["staff_id"])
    return None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="真写入(默认 dry-run 只出计划)")
    parser.add_argument("--limit", type=int, default=None, help="最多扫描多少条候选证据(按 evidence id 升序)")
    parser.add_argument(
        "--kol-pool-id", type=int, action="append", default=None,
        help="只处理这些 KOL(可重复)",
    )
    parser.add_argument(
        "--actor-staff-id", default=None,
        help="收藏人过不了复核时的兜底执行者 staff id;'auto' 取第一个可用 owner/admin",
    )
    parser.add_argument("--skip-budget", action="store_true", help="不补种 metric_tracking 预算行")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from app.db.connection import get_conn
    from app.domains.kol import video_tracking_budget
    from app.domains.kol.video_tracking_enroll import enroll_my_kol_evidence

    conn = get_conn()
    fallback: int | None = None
    fallback_note = "none"
    if args.actor_staff_id:
        if str(args.actor_staff_id).strip().lower() == "auto":
            fallback = _auto_fallback_actor(conn)
            fallback_note = f"auto -> {fallback}" if fallback else "auto -> no owner/admin passes revalidation"
        else:
            fallback = int(args.actor_staff_id)
            fallback_note = f"explicit {fallback}"

    budget: dict[str, Any] = {"action": "skipped"}
    if not args.skip_budget:
        existing = video_tracking_budget.load_scope(conn)
        budget = {
            "scope": video_tracking_budget.BUDGET_SCOPE,
            "cost_tag": video_tracking_budget.COST_TAG,
            "cap_usd": video_tracking_budget.configured_monthly_cap_usd(),
            "cap_env": video_tracking_budget.CAP_ENV,
            "existing_cap_usd": float(existing["cap_usd"]) if existing else None,
            "action": "would_insert" if existing is None else "would_update",
        }
        if args.apply:
            budget.update(video_tracking_budget.ensure_budget_scope(conn))

    summary = enroll_my_kol_evidence(
        conn,
        apply=bool(args.apply),
        limit=args.limit,
        kol_pool_ids=args.kol_pool_id,
        fallback_staff_id=fallback,
    )
    summary["fallback_actor"] = fallback_note
    summary["budget"] = budget
    if args.apply:
        conn.commit()
    else:
        conn.rollback()

    if args.json:
        out_json(summary, ensure_ascii=False, default=str)
        return 0
    out(f"mode={summary['mode']} candidates={summary['candidates']} to_register={summary['to_register']} "
        f"inserted={summary['inserted']} conflicts={summary['conflicts']} "
        f"already_active={summary['already_active']} already_paused={summary['already_paused']}")
    out(f"tiers={json.dumps(summary['tiers'])} cadence_hours={json.dumps(summary['cadence_hours'])}")
    out(f"platforms={json.dumps(summary['platforms'])} actors={json.dumps(summary['actors'])} "
        f"fallback_actor={fallback_note}")
    if summary["skipped"]:
        out(f"skipped={json.dumps(summary['skipped'], ensure_ascii=False)}")
        if summary["skipped"].get("no_authorized_actor"):
            out("hint: 收藏人未通过复核(多为 pending 用户);加 --actor-staff-id <owner/admin id|auto> 兜底登记。")
    out(f"budget={json.dumps(budget, ensure_ascii=False, default=str)}")
    if not args.apply:
        out("dry-run: 未写入任何行;加 --apply 真登记。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
