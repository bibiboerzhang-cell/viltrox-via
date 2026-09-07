"""人工操作 → 运营偏好反馈的 best-effort 桥,不证明真实消息收发。

- 必须在主写 ``commit()`` **之后**调用(record_pool_action_feedback 自己 commit);
- 绝不抛:任何异常 ``logger.warning`` 后吞掉,并 rollback 掉桥自身的半截事务
  (PG 里任一 SQL 失败会令当前事务进 aborted,不回滚会连累同请求后续写入);
- 幂等由 actions 负责((recommendation_id × feedback_type) 去重,重复点击零写入);
- 动作词表只用 actions._POOL_ACTION_FEEDBACK 闭集(favorite/touch/contact/outreach…);
  contact/touch/outreach 仅保留 contact 偏好,不能置位 outreach_sent/reply_received。
- 手工消息兼容入口不产生反馈;当前 raw vkpi_messages 不提供可验证收发凭据。
- 系统任务拿不到 staff 时传 None,payload 里 ``source`` 说明来源。

零 LLM、零 provider、零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.recommendations import actions as rec_actions

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
    """一次人工动作 → 推荐偏好反馈,不证明投递。绝不抛;主写提交后调用。"""
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
    """Compatibility no-op: recording a message is not transport evidence.

    Current raw vkpi_messages have no verifiable provider receipt. Neither a
    direction/source label nor arbitrary message metadata proves send/reply.
    Keep callers compatible without a DB lookup or a training fact; a future
    trusted observation requires a dedicated server-owned receipt entry point.
    """
    return []
