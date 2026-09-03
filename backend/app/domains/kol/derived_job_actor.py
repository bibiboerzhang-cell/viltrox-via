"""派生任务的责任人:从父任务 payload **直通**身份(不经会话)。

worker 里派生出来的子任务没有请求上下文。此前这些派生点一律传 ``staff=None``,
子任务因此不带 ``staff_id`` / ``triggered_by_user_id``;再往下一级的付费视频深析
拿不到任何身份可铸围栏,``apify_jobs_worker_paid_scope.revalidate_paid_job_scope``
只能拒(``video_analysis_authorization_fence_required``,attempts=0 即 blocked)。

**「靠会话反查发起人」这条路已被证伪**(2026-09-03 本地全量核实):这批派生任务的
会话是 worker 在派生之后才现建的,``vkpi_kol_search_sessions.created_by`` 全是 NULL,
``session_actor.session_creator_staff()`` 实跑对 1125~1135 逐个返回 None。所以身份
只能从**父任务 payload 自己带的** ``staff_id`` / ``triggered_by_user_id`` 直通下来
——祖父任务(``smart_search_profile_advance``)的 payload 里身份是齐的。

信任级与 ``session_actor`` 一致:只认在职(``active``)且未停用(``suspended_at`` 空)
的 staff 行;两个 id 互相矛盾时不猜、按无身份处理。取不到 → 返回 ``None``,由调用方
按「**不派生付费动作**、落一条可读原因」处理,而不是静默入队再被 worker 拒。

本模块只读 staff 表,零写入,不碰 ``viltrox_fit_score``,不放宽任何围栏判定。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

# 无身份时的诚实口径:调用方回执用同一组常量,别各写各的。
NO_ACTOR_STATUS = "no_actor"
NO_ACTOR_REASON = "derived_actor_missing"
NO_ACTOR_NOTE = (
    "本次没有派生:这批后台动作找不到可追责的发起人,直接跳过,"
    "而不是排队之后再被授权检查拒掉。从搜索页或 MY KOL 页重新发起即可带上身份。"
)

__all__ = [
    "NO_ACTOR_NOTE",
    "NO_ACTOR_REASON",
    "NO_ACTOR_STATUS",
    "derived_job_staff",
    "is_usable_actor",
    "no_actor_receipt",
]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _truthy(value: Any) -> bool:
    """兼容层把 BOOLEAN/INTEGER 读回成 1/0/'t';``is True`` 会把它们全判成假。"""
    return value in (True, 1, "1", "t", "true", "TRUE", "True")


def is_usable_actor(staff: Any) -> bool:
    """能不能拿去铸围栏:必须是带正 id 的 staff 行。

    ``provider_job_access`` 的 server_owned 返回值形如
    ``{"server_owned": True, "staff_id": None, "user_id": None}`` —— 它不是人,
    这里判 False,让调用方走「不派生付费动作」的诚实分支。
    """
    if not isinstance(staff, dict):
        return False
    return _int(staff.get("id") or staff.get("staff_id")) > 0


def no_actor_receipt(**extra: Any) -> dict[str, Any]:
    """统一的「没身份所以没派生」回执(不是失败,是没事可做)。"""
    return {
        "status": NO_ACTOR_STATUS,
        "reason": NO_ACTOR_REASON,
        "note": NO_ACTOR_NOTE,
        **extra,
    }


def _active_row(row: Any) -> dict[str, Any] | None:
    """staff 行 → 在职才返回;离职/停用一律 None(与 ``_active_actor`` 同口径)。"""
    if not row:
        return None
    staff = dict(row)
    if not _truthy(staff.get("active")):
        return None
    if str(staff.get("suspended_at") or "").strip():
        return None
    return staff


def _by_staff_id(conn: Any, staff_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM staff WHERE id=? LIMIT 1", (int(staff_id),)
    ).fetchone()
    return _active_row(row)


def _by_user_id(conn: Any, user_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM staff WHERE user_id=? AND active=1 ORDER BY id LIMIT 1",
        (int(user_id),),
    ).fetchone()
    return _active_row(row)


def _lookup_staff(conn: Any, staff_id: int, user_id: int) -> dict[str, Any] | None:
    """优先 ``staff_id``,退而用 ``triggered_by_user_id``;两者矛盾时按无身份处理。"""
    staff = _by_staff_id(conn, staff_id) if staff_id > 0 else None
    if staff is None and user_id > 0:
        staff = _by_user_id(conn, user_id)
    if staff is None:
        return None
    if user_id > 0 and _int(staff.get("user_id")) != user_id:
        # payload 自相矛盾(改过归属 / 拼装错):不猜一个人出来背这笔账。
        logger.warning(
            "derived_actor_identity_mismatch staff_id=%s payload_user_id=%s row_user_id=%s",
            staff_id, user_id, staff.get("user_id"),
        )
        return None
    return staff


def derived_job_staff(
    payload: Any,
    *,
    conn: Any = None,
    provider_actor: Any = None,
) -> dict[str, Any] | None:
    """父任务 payload → 可铸围栏的在职 staff 行;取不到返回 ``None``。

    顺序:① 调用方刚复核过的 ``provider_actor``(worker 入口验完的活人,最高信任级、
    零查询)② ``payload["staff_id"]`` ③ ``payload["triggered_by_user_id"]``。

    ``conn`` 留给已经持有兼容层连接的调用方复用;不给就自取一条。
    """
    if is_usable_actor(provider_actor):
        return dict(provider_actor)
    if not isinstance(payload, dict):
        return None
    staff_id = _int(payload.get("staff_id"))
    user_id = _int(payload.get("triggered_by_user_id"))
    if staff_id <= 0 and user_id <= 0:
        return None
    try:
        return _lookup_staff(
            conn if conn is not None else get_conn(), staff_id, user_id
        )
    except Exception as exc:  # noqa: BLE001 — 身份查不到按「无身份」处理,不放大成任务失败
        logger.warning(
            "derived_actor_lookup_failed staff_id=%s user_id=%s error_type=%s",
            staff_id, user_id, type(exc).__name__,
        )
        return None
