"""搜索会话创建者 → 真实 staff 行(worker 侧铸围栏用,与 UI 直发同信任级)。

smart-search 的档案代表作深析在 worker 里入队,没有请求上下文:此前 staff=None、
也没有 provider 父围栏,worker 一律 video_analysis_authorization_fence_required(全部 blocked,
会话永远「部分完成」)。会话是操作者发起的,其 created_by(user id)对应的在职 staff
就是这批派生任务的责任人。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def session_creator_staff(conn: Any, search_session_id: Any) -> dict[str, Any] | None:
    """会话 created_by(user id)→ 在职 staff 行 dict;取不到返回 None(调用方自己决定拒绝口径)。"""
    try:
        sid = int(search_session_id or 0)
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None
    try:
        row = conn.execute(
            "SELECT created_by FROM vkpi_kol_search_sessions WHERE id=?", (sid,)
        ).fetchone()
        created_by = int(dict(row).get("created_by") or 0) if row else 0
        if created_by <= 0:
            return None
        staff_row = conn.execute(
            "SELECT * FROM staff WHERE user_id=? AND active=1 ORDER BY id LIMIT 1", (created_by,)
        ).fetchone()
        return dict(staff_row) if staff_row else None
    except Exception as exc:  # noqa: BLE001 — 围栏兜底查询失败按「无身份」处理,不放大
        logger.warning("session_creator_staff_lookup_failed session=%s err=%s", sid, type(exc).__name__)
        return None
