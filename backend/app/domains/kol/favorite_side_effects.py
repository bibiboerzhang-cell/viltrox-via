"""收藏即登记(波 C·C5):收藏成功后把该 KOL 的视频证据幂等登记进指标追踪。

此前「关注 = 进 MY KOL」,但指标追踪(vkpi_kol_video_metric_tracking)要等运维跑
scripts/ops/enroll_metric_tracking.py 才登记,用户看不到跟进进度。这里把同一套
登记逻辑(video_tracking_enroll.enroll_my_kol_evidence)挂到收藏写口之后:

- best-effort:主写(收藏行)已经 commit;这里任何失败只 logger.warning,绝不回传 5xx,
  绝不静默吞掉。
- 受 metric_tracking 月闸(video_tracking_budget.budget_gate,默认 $30/月):
  闸关(本月花满 / scope 未补种 = fail-closed)时不登记,理由原样回给前端。
  登记本身零 provider 调用、零入队,真正花钱的入队在 scheduler 那边还有同一道闸。
- 幂等:已 active / paused 的订阅一律不动(ON CONFLICT DO NOTHING)。
- 执行者:优先收藏人本人(能过 scheduler 复核的),兜底就是当前操作者。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol import video_tracking_budget, video_tracking_enroll


logger = get_logger("viltrox.domains.kol.favorite_side_effects")

RESPONSE_KEYS = (
    "tracking_enrolled",
    "tracking_candidates",
    "tracking_already_active",
    "tracking_skipped",
    "tracking_enroll_reason",
)


def _empty(reason: str) -> dict[str, Any]:
    return {
        "tracking_enrolled": 0,
        "tracking_candidates": 0,
        "tracking_already_active": 0,
        "tracking_skipped": {},
        "tracking_enroll_reason": reason,
    }


def enroll_tracking_after_favorite(kol_pool_id: int, *, staff: dict[str, Any] | None) -> dict[str, Any]:
    """收藏成功后调用;返回值直接并进收藏响应(键见 RESPONSE_KEYS)。永不抛异常。"""

    pool_id = int(kol_pool_id or 0)
    actor = scope.actor_staff_id(staff)
    if pool_id <= 0 or actor <= 0:
        return _empty("staff_identity_required")
    try:
        conn = get_conn()
        gate = video_tracking_budget.budget_gate(conn, sync_spend=False)
        if not gate.get("allowed"):
            reason = str(gate.get("reason") or "budget_blocked")
            logger.warning(
                "favorite.tracking_enroll_skipped | kol_pool_id=%s staff_id=%s reason=%s spend=%s cap=%s",
                pool_id, actor, reason, gate.get("spend_usd"), gate.get("cap_usd"),
            )
            return _empty(reason)
        summary = video_tracking_enroll.enroll_my_kol_evidence(
            conn, apply=True, kol_pool_ids=[pool_id], fallback_staff_id=actor,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort 副作用,主写已落库,只记日志
        logger.warning(
            "favorite.tracking_enroll_failed | kol_pool_id=%s staff_id=%s error=%s",
            pool_id, actor, f"{type(exc).__name__}: {exc}",
        )
        try:
            get_conn().rollback()
        except Exception:  # noqa: BLE001
            logger.debug("favorite.tracking_enroll_rollback_failed", exc_info=True)
        return _empty("enroll_failed")
    inserted = int(summary.get("inserted") or 0)
    candidates = int(summary.get("candidates") or 0)
    if candidates == 0:
        reason: str | None = "no_video_evidence"
    elif inserted == 0 and int(summary.get("already_active") or 0) > 0:
        reason = "already_enrolled"
    elif inserted == 0:
        reason = "nothing_eligible"
    else:
        reason = None
    return {
        "tracking_enrolled": inserted,
        "tracking_candidates": candidates,
        "tracking_already_active": int(summary.get("already_active") or 0),
        "tracking_skipped": dict(summary.get("skipped") or {}),
        "tracking_enroll_reason": reason,
    }


__all__ = ["RESPONSE_KEYS", "enroll_tracking_after_favorite"]
