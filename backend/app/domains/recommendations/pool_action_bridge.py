"""人工写口 → 训练信号 的 best-effort 桥(波 C·C4,2026-08-23)。

L 车道审计坐实:项目自动收藏 / 加入项目触达 / 分组共享成员 / 派单 stage=contacted /
外联消息 五个真实人工写口都不经 recommendations.actions.record_pool_action_feedback,
只能等每日 outcome_sync 回填——人工动作要过夜才变成训练信号。本模块给这些写口一个
统一、零风险的插桩入口:

- 必须在主写 ``commit()`` **之后**调用(record_pool_action_feedback 自己 commit);
- 绝不抛:任何异常 ``logger.warning`` 后吞掉,并 rollback 掉桥自身的半截事务
  (PG 里任一 SQL 失败会令当前事务进 aborted,不回滚会连累同请求后续写入);
- 幂等由 actions 负责((recommendation_id × feedback_type) 去重,重复点击零写入);
- 动作词表只用 actions._POOL_ACTION_FEEDBACK 闭集(favorite/touch/contact/outreach…),
  不发明新 feedback_type;外联消息按 L 车道 sync_message_outcomes 口径:
  outbound → outreach_sent(即 "outreach" 动作),inbound 无闭集动作,留给每日同步。
- 系统任务拿不到 staff 时传 None,payload 里 ``source`` 说明来源。

零 LLM、零 provider、零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


def _recover_connection() -> None:
    """桥失败后把请求连接从 aborted 态拉回来;主写已提交,回滚只丢桥自身的半截写。"""
    try:
        get_conn().rollback()
    except Exception:
        logger.debug("pool_action_bridge.rollback_failed", exc_info=True)


def bridge_pool_action(
    kol_pool_id: Any,
    action: str,
    *,
    staff: dict[str, Any] | None = None,
    note: str = "",
    payload: dict[str, Any] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """一次人工动作 → 一行推荐反馈(+ outcome 节点)。绝不抛;主写提交后调用。"""
    merged: dict[str, Any] = dict(payload or {})
    if source:
        merged.setdefault("source", source)
    if staff is None:
        merged.setdefault("actor", "system")
    try:
        pool_id = int(kol_pool_id or 0)
    except (TypeError, ValueError):
        pool_id = 0
    if pool_id <= 0:
        return {"linked": False, "reason": "no_pool_id", "action": str(action or "")}
    try:
        from app.domains.recommendations import actions as rec_actions

        return rec_actions.record_pool_action_feedback(pool_id, action, staff=staff, note=note, payload=merged)
    except Exception:
        logger.warning(
            "pool_action_bridge.failed kol_pool_id=%s action=%s source=%s",
            pool_id, action, merged.get("source") or "",
            exc_info=True,
        )
        _recover_connection()
        return {"linked": False, "reason": "bridge_failed", "kol_pool_id": pool_id, "action": str(action or "")}


def bridge_message_outreach(
    *,
    message_id: Any,
    project_id: Any,
    kol_id: Any,
    direction: Any,
    staff: dict[str, Any] | None = None,
    source: str = "message",
) -> list[dict[str, Any]]:
    """外联消息即时桥:outbound 消息 → "outreach"(feedback contact / outcome outreach_sent)。

    kol_id(主 kols 表)经 vkpi_kol_pool.linked_main_kol_id 桥到池;缺 kol_id 时仅当项目
    只派了一个 KOL 才归属(与 outcome_sync.sync_message_outcomes 同口径,多人项目不猜)。
    inbound 消息闭集里没有对应动作,诚实跳过留给每日同步。绝不抛。
    """
    clean_direction = str(direction or "outbound").strip().lower()
    if clean_direction in {"inbound", "in", "reply", "received"}:
        return []
    try:
        from app.domains.recommendations import outcome_sync

        conn = get_conn()
        pool_ids = outcome_sync._pool_ids_for_kol(conn, int(kol_id or 0))
        if not pool_ids:
            sole = outcome_sync._sole_project_pool_id(conn, int(project_id or 0))
            pool_ids = [sole] if sole > 0 else []
    except Exception:
        logger.warning("pool_action_bridge.message_pool_lookup_failed message_id=%s kol_id=%s", message_id, kol_id, exc_info=True)
        _recover_connection()
        return []
    payload = {
        "message_id": message_id,
        "direction": clean_direction,
        "project_id": int(project_id or 0) or None,
        "kol_id": int(kol_id or 0) or None,
    }
    return [
        bridge_pool_action(pool_id, "outreach", staff=staff, payload=dict(payload), source=source)
        for pool_id in pool_ids
    ]
