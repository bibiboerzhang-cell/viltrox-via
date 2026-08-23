"""动作 → outcomes 同步(学习闭环 W-L2·反馈段)。

审计坐实的断点:30 天仅 1 条阶段事件进 outcomes —— pool 看板动作(收藏/提升/拒绝)只落
vkpi_recommendation_feedback,派单阶段(contacted/device_sent/content_posted)与触达记录
从不回写 vkpi_recommendation_outcomes。本模块把三类真实业务行幂等映射成 outcome 节点:

  1. vkpi_recommendation_feedback(shortlist/claim/reject/create_project)→ 对应节点;
  2. vkpi_project_kol_assignments.stage(按 kol_pool_id 桥到最新推荐)→ stage 映射;
  3. vkpi_kol_pool_touches(联系/触达)→ outreach_sent;
  4. vkpi_messages(外联消息:outbound → outreach_sent / inbound → reply_received;
     kol_id 经 vkpi_kol_pool.linked_main_kol_id 桥到池,缺 kol_id 时仅当项目只派了一个 KOL 才归属)
     —— L 车道 2026-08-23 补:外联写口(evidence/messages.py、workflow_evidence_project_writes)不调反馈桥;
  5. sync_favorite_feedback:收藏 / MY KOL 勾选成员 两个写口不经 actions.record_pool_action_feedback
     (pool_favorites.py / vkpi_my_kol.py / staff_groups 直写表)→ 这里按 (recommendation_id x 'shortlist')
     幂等补一行 feedback(带 staff),让人工动作真正进训练信号。

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


def _pool_ids_for_kol(conn: Any, kol_id: int) -> list[int]:
    if int(kol_id or 0) <= 0:
        return []
    rows = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE linked_main_kol_id=? ORDER BY id ASC LIMIT 5",
        (int(kol_id),),
    ).fetchall()
    return [int(dict(r)["id"]) for r in rows]


def _sole_project_pool_id(conn: Any, project_id: int) -> int:
    """项目只派了一个 KOL 时才把项目级消息归属给它;多人项目诚实不猜。"""
    if int(project_id or 0) <= 0 or not table_exists("vkpi_project_kol_assignments"):
        return 0
    rows = conn.execute(
        """
        SELECT kol_pool_id FROM vkpi_project_kol_assignments
        WHERE project_id=? AND COALESCE(stage_status, 'active') != 'deleted'
        ORDER BY id ASC LIMIT 2
        """,
        (int(project_id),),
    ).fetchall()
    return int(dict(rows[0])["kol_pool_id"]) if len(rows) == 1 else 0


def sync_message_outcomes(limit: int = 2000) -> dict[str, Any]:
    """外联消息 → outreach_sent(outbound)/ reply_received(inbound,含 outreach_sent 隐含)。"""
    if not table_exists("vkpi_messages"):
        return {"status": "table_missing", "scanned": 0, "changed": 0, "no_recommendation": 0, "ambiguous": 0}
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, project_id, kol_id, direction, captured_at, created_at
        FROM vkpi_messages
        ORDER BY captured_at DESC, id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 2000), 20000))),),
    ).fetchall()
    changed = 0
    no_recommendation = 0
    ambiguous = 0
    for raw in rows:
        row = dict(raw)
        direction = str(row.get("direction") or "outbound").strip().lower()
        node = "reply_received" if direction in {"inbound", "in", "reply", "received"} else "outreach_sent"
        event_at = row.get("captured_at") or row.get("created_at")
        pool_ids = _pool_ids_for_kol(conn, int(row.get("kol_id") or 0))
        if not pool_ids:
            sole = _sole_project_pool_id(conn, int(row.get("project_id") or 0))
            if sole <= 0:
                ambiguous += 1
                continue
            pool_ids = [sole]
        rec_id = 0
        for pool_id in pool_ids:
            rec_id = _latest_recommendation_for_pool(conn, pool_id, not_after=event_at)
            if rec_id > 0:
                break
        if rec_id <= 0:
            no_recommendation += 1
            continue
        nodes = [*STAGE_IMPLIES.get(node, ()), node]
        changed += _apply_nodes(
            rec_id, nodes,
            at=event_at,
            context={"source": "message_sync", "message_id": row.get("id"), "direction": direction, "project_id": row.get("project_id")},
        )
    return {"status": "ok", "scanned": len(rows), "changed": changed,
            "no_recommendation": no_recommendation, "ambiguous": ambiguous}


def sync_favorite_feedback(limit: int = 2000) -> dict[str, Any]:
    """收藏 / 勾选成员 → feedback 'shortlist'(recommendation_id x feedback_type 幂等,带 staff)。"""
    from app.domains.recommendations import actions as rec_actions

    result: dict[str, Any] = {"status": "ok", "scanned": 0, "inserted": 0, "no_recommendation": 0, "changed": 0}
    conn = get_conn()
    cap = int(max(1, min(int(limit or 2000), 20000)))
    inserted = 0
    for table, pool_action in (("vkpi_kol_pool_favorites", "favorite"), ("vkpi_kol_pool_members", "member")):
        if not table_exists(table):
            continue
        rows = conn.execute(
            f"SELECT id, kol_pool_id, staff_id, created_at FROM {table} ORDER BY id DESC LIMIT ?",
            (cap,),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            result["scanned"] += 1
            rec_id = _latest_recommendation_for_pool(conn, int(row.get("kol_pool_id") or 0), not_after=row.get("created_at"))
            if rec_id <= 0:
                result["no_recommendation"] += 1
                continue
            staff = {"id": int(row.get("staff_id") or 0)} if row.get("staff_id") else None
            if rec_actions._record_action_feedback_once(
                rec_id, "shortlist",
                {"kol_pool_id": int(row.get("kol_pool_id") or 0), "pool_action": pool_action, "sync": "favorite_feedback", "row_id": row.get("id")},
                staff=staff, note="",
            ):
                inserted += 1
                changed = outcome_collector.record_if_missing(
                    rec_id, "shortlisted", at=_ts(row.get("created_at")),
                    context={"source": "favorite_sync", "pool_action": pool_action},
                )
                result["changed"] += 1 if changed else 0
    if inserted:
        conn.commit()
    result["inserted"] = inserted
    return result


_SYNC_ROUTES: tuple[tuple[str, Any], ...] = (
    ("feedback", sync_feedback_outcomes),
    ("assignments", sync_assignment_outcomes),
    ("touches", sync_touch_outcomes),
    ("messages", sync_message_outcomes),
    ("favorites", sync_favorite_feedback),
)


def sync_action_outcomes(limit: int = 2000) -> dict[str, Any]:
    """五路同步合集(每路单独吞错计数,互不拖垮);由 outcomes.refresh_open_outcomes(run_sync=True)
    在每日 job_vkpi_recommendation_outcomes(04:40)链头调用。"""
    result: dict[str, Any] = {"status": "ok"}
    for name, func in _SYNC_ROUTES:
        try:
            result[name] = func(limit)
        except Exception as exc:
            logger.warning("outcome_sync.%s_failed: %s", name, exc, exc_info=True)
            result[name] = {"status": "failed", "error": str(exc), "changed": 0}
    result["changed"] = sum(int((result.get(key) or {}).get("changed") or 0) for key, _ in _SYNC_ROUTES)
    return result
