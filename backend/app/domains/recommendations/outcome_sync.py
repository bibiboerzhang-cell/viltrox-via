"""动作 → outcomes 同步(学习闭环 W-L2·反馈段)。

审计坐实的断点:30 天仅 1 条阶段事件进 outcomes —— pool 看板动作(收藏/提升/拒绝)只落
vkpi_recommendation_feedback,派单阶段(contacted/device_sent/content_posted)与触达记录
从不回写 vkpi_recommendation_outcomes。本模块把三类真实业务行幂等映射成 outcome 节点:

  1. vkpi_recommendation_feedback(shortlist/claim/reject/create_project)→ 对应节点;
  2. vkpi_project_kol_assignments.stage(按 kol_pool_id 桥到最新推荐)→ stage 映射;
  3. vkpi_kol_pool_touches(联系/触达)→ outreach_sent。

全部只读真实业务行、按事件自身时间戳落 COALESCE 时间列、重复跑零写入;缺推荐行诚实计数跳过。
零 LLM、零 provider、零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.recommendations import outcomes as outcome_collector

logger = get_logger(__name__)

# feedback_type → outcome 节点(NODE_COLUMNS 词表)。positive_signal/feedback/snooze 无对应列,不映射。
FEEDBACK_NODE_MAP: dict[str, str] = {
    "shortlist": "shortlisted",
    "claim": "claimed",
    "reject": "rejected",
    "create_project": "project_created",
    "contact": "outreach_sent",
}

# assignment.stage → outcome 节点。送样后词表视为已达成合作意向(agreement_reached),
# 发布后词表视为内容已发布;discovered 等前置阶段不映射。
STAGE_NODE_MAP: dict[str, str] = {
    "contacted": "outreach_sent",
    "replied": "reply_received",
    "negotiating": "reply_received",
    "agreed": "agreement_reached",
    "device_sent": "agreement_reached",
    "shipped": "agreement_reached",
    "arrived": "agreement_reached",
    "received": "agreement_reached",
    "delivered": "agreement_reached",
    "content_posted": "content_published",
    "content_published": "content_published",
    "published": "content_published",
    "posted": "content_published",
    "reviewed": "content_published",
    "measured": "content_published",
}

# 阶段链:到达高阶段时低阶段节点一并置位(device_sent 必然经过 contacted)。
STAGE_IMPLIES: dict[str, tuple[str, ...]] = {
    "reply_received": ("outreach_sent",),
    "agreement_reached": ("outreach_sent",),
    "content_published": ("outreach_sent", "agreement_reached"),
}


def _ts(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _latest_recommendation_for_pool(conn: Any, kol_pool_id: int, *, not_after: Any = None) -> int:
    """该 pool 项最新的推荐行(可选:只认事件时间之前产出的推荐,避免未来推荐吃历史动作)。"""
    pool_id = int(kol_pool_id or 0)
    if pool_id <= 0:
        return 0
    if not_after:
        row = conn.execute(
            "SELECT id FROM vkpi_kol_recommendations WHERE kol_pool_id=? AND created_at <= ? ORDER BY id DESC LIMIT 1",
            (pool_id, _ts(not_after)),
        ).fetchone()
        if row:
            return int(dict(row)["id"])
    row = conn.execute(
        "SELECT id FROM vkpi_kol_recommendations WHERE kol_pool_id=? ORDER BY id DESC LIMIT 1",
        (pool_id,),
    ).fetchone()
    return int(dict(row)["id"]) if row else 0


def _apply_nodes(rec_id: int, nodes: list[str], *, at: Any, context: dict[str, Any]) -> int:
    changed = 0
    for node in nodes:
        if outcome_collector.record_if_missing(rec_id, node, at=_ts(at), context=context):
            changed += 1
    return changed


def sync_feedback_outcomes(limit: int = 2000) -> dict[str, Any]:
    """feedback 行 → outcome 节点(含历史回填)。"""
    if not table_exists("vkpi_recommendation_feedback"):
        return {"status": "table_missing", "scanned": 0, "changed": 0}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT fb.recommendation_id, fb.feedback_type, fb.created_at
        FROM vkpi_recommendation_feedback fb
        INNER JOIN vkpi_kol_recommendations rec ON rec.id = fb.recommendation_id
        ORDER BY fb.id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 2000), 20000))),),
    ).fetchall()
    changed = 0
    unmapped = 0
    for raw in rows:
        row = dict(raw)
        node = FEEDBACK_NODE_MAP.get(str(row.get("feedback_type") or "").strip().lower())
        if not node:
            unmapped += 1
            continue
        changed += _apply_nodes(
            int(row["recommendation_id"]), [node],
            at=row.get("created_at"), context={"source": "feedback_sync", "feedback_type": row.get("feedback_type")},
        )
    return {"status": "ok", "scanned": len(rows), "changed": changed, "unmapped": unmapped}


def sync_assignment_outcomes(limit: int = 2000) -> dict[str, Any]:
    """派单阶段 → outcome 节点。桥:assignment.kol_pool_id → 最新推荐(事件前)。缺推荐诚实跳过计数。"""
    if not table_exists("vkpi_project_kol_assignments"):
        return {"status": "table_missing", "scanned": 0, "changed": 0, "no_recommendation": 0}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, project_id, kol_pool_id, stage, updated_at, created_at
        FROM vkpi_project_kol_assignments
        WHERE COALESCE(stage_status, 'active') != 'deleted'
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 2000), 20000))),),
    ).fetchall()
    changed = 0
    no_recommendation = 0
    unmapped_stage = 0
    for raw in rows:
        row = dict(raw)
        node = STAGE_NODE_MAP.get(str(row.get("stage") or "").strip().lower())
        if not node:
            unmapped_stage += 1
            continue
        event_at = row.get("updated_at") or row.get("created_at")
        rec_id = _latest_recommendation_for_pool(conn, int(row.get("kol_pool_id") or 0), not_after=event_at)
        if rec_id <= 0:
            no_recommendation += 1
            continue
        nodes = [*STAGE_IMPLIES.get(node, ()), node]
        changed += _apply_nodes(
            rec_id, nodes,
            at=event_at,
            context={"source": "assignment_sync", "assignment_id": row.get("id"), "project_id": row.get("project_id"), "stage": row.get("stage")},
        )
    return {"status": "ok", "scanned": len(rows), "changed": changed,
            "no_recommendation": no_recommendation, "unmapped_stage": unmapped_stage}


def sync_touch_outcomes(limit: int = 2000) -> dict[str, Any]:
    """联系/触达记录 → outreach_sent。"""
    if not table_exists("vkpi_kol_pool_touches"):
        return {"status": "table_missing", "scanned": 0, "changed": 0, "no_recommendation": 0}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, kol_pool_id, channel, project_id, touched_at, created_at
        FROM vkpi_kol_pool_touches
        ORDER BY touched_at DESC, id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 2000), 20000))),),
    ).fetchall()
    changed = 0
    no_recommendation = 0
    for raw in rows:
        row = dict(raw)
        event_at = row.get("touched_at") or row.get("created_at")
        rec_id = _latest_recommendation_for_pool(conn, int(row.get("kol_pool_id") or 0), not_after=event_at)
        if rec_id <= 0:
            no_recommendation += 1
            continue
        changed += _apply_nodes(
            rec_id, ["outreach_sent"],
            at=event_at,
            context={"source": "touch_sync", "touch_id": row.get("id"), "channel": row.get("channel"), "project_id": row.get("project_id")},
        )
    return {"status": "ok", "scanned": len(rows), "changed": changed, "no_recommendation": no_recommendation}


def sync_action_outcomes(limit: int = 2000) -> dict[str, Any]:
    """三路同步合集(每路单独吞错计数,互不拖垮)。"""
    result: dict[str, Any] = {"status": "ok"}
    for name, func in (
        ("feedback", sync_feedback_outcomes),
        ("assignments", sync_assignment_outcomes),
        ("touches", sync_touch_outcomes),
    ):
        try:
            result[name] = func(limit)
        except Exception as exc:
            logger.warning("outcome_sync.%s_failed: %s", name, exc, exc_info=True)
            result[name] = {"status": "failed", "error": str(exc), "changed": 0}
    result["changed"] = sum(int((result.get(key) or {}).get("changed") or 0) for key in ("feedback", "assignments", "touches"))
    return result
