"""P13 分享/撤销审计:谁对哪个 project/event 把谁加为什么角色、何时撤销。

best-effort —— 审计写入失败绝不影响主流程(分享/撤销本身);软引用 target_id(无 FK),
成员行被删后审计史仍在。台账见迁移 137 vkpi_share_audit。
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


def _int_or_none(value) -> int | None:
    try:
        n = int(value)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def record(
    *,
    target_type: str,
    target_id,
    action: str,
    member_staff_id=None,
    role: str | None = None,
    actor_staff_id=None,
) -> None:
    """记一条分享审计。action ∈ {share, revoke, role_change}。"""
    try:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO vkpi_share_audit
                (target_type, target_id, action, member_staff_id, role, actor_staff_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(target_type),
                str(target_id),
                str(action),
                _int_or_none(member_staff_id),
                str(role) if role else None,
                _int_or_none(actor_staff_id),
            ),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — 审计失败不阻塞分享/撤销
        logger.warning("vkpi.share_audit.record_failed", exc_info=True)


def list_for_target(target_type: str, target_id, *, limit: int = 100) -> list[dict]:
    """某 project/event 的分享/撤销历史(倒序)。"""
    rows = get_conn().execute(
        """
        SELECT id, target_type, target_id, action, member_staff_id, role, actor_staff_id, created_at
        FROM vkpi_share_audit
        WHERE target_type = ? AND target_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (str(target_type), str(target_id), max(1, min(int(limit or 100), 500))),
    ).fetchall()
    return [dict(r) for r in rows]
