"""单品播放「重新实测」的派活编排(唯一花钱的一步)。

**不另造入队路径**:逐条视频调既有的 ``video_tracking.queue_evidence_refresh``,
既有的行级写围栏(``_assert_target_writable``)、幂等键、预算闸一个不绕。
本模块只做三件事:

1. **重算报价并与客户端回传的指纹比对**——不一致就 409 让操作员重看。
   没有这一步,「确认框写 3 条、实际派 30 条」是完全可能发生的,而且最难被发现。
2. **把服务端硬闸的裁决落到实处**:能派的只有 ``plan["planned"]``,
   超单次上限 / 超每日上限 / 冷却期内 / 共享只读的,一条都不派。
3. **逐条如实回执**:派出去几条、几条是并入已有的、几条没派成(各自的稳定原因码),
   一条不编。门面负责把原因码翻成人话,本模块不产出面向用户的句子。

诚实契约:本模块只返回「已派 / 未派」的事实。它不知道也不假装知道结果何时回来——
``provider_calls_performed`` 在应答里恒为 false,取数由 worker 在后台完成。
``register_tracking=False``:这里是「再测一次」,不顺手把视频塞进长期订阅。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.domains.kol import sku_play_refresh_plan as _plan

logger = get_logger("viltrox.domains.kol.sku_play_refresh_dispatch")


class SkuPlayRefreshError(RuntimeError):
    """稳定错误码 + HTTP 状态,供路由直接转 HTTPException。"""

    def __init__(self, code: str, status_code: int = 409, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = int(status_code)
        self.detail = detail or {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", "")
    if code:
        return str(code)
    return type(exc).__name__


def run_sku_play_refresh(
    conn: Any,
    *,
    staff: dict[str, Any] | None,
    staff_scope_id: int | None,
    sku_code: str,
    evidence_id: int = 0,
    plan_hash: str = "",
    expected_count: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按报价派活。报价对不上一条都不派。"""

    plan = _plan.plan_sku_play_refresh(
        conn,
        staff=staff,
        staff_scope_id=staff_scope_id,
        sku_code=sku_code,
        evidence_id=evidence_id,
        now=now,
    )
    submitted = str(plan_hash or "").strip()
    if not submitted:
        raise SkuPlayRefreshError("sku_play_refresh_plan_required", 400, {"plan": plan})
    if submitted != plan["plan_hash"]:
        # 从看报价到点确认之间,名单 / 冷却 / 每日剩余都可能变。宁可让人重看一眼,
        # 也不能拿旧数字去派新活。
        raise SkuPlayRefreshError("sku_play_refresh_plan_drifted", 409, {"plan": plan})
    if expected_count is not None and _int(expected_count) != _int(plan["planned_count"]):
        raise SkuPlayRefreshError("sku_play_refresh_plan_drifted", 409, {"plan": plan})
    if plan["planned_count"] <= 0:
        return {
            "status": "nothing_to_fetch",
            "plan": plan,
            "queued": [],
            "already_queued": [],
            "failed": [],
            "counts": {"planned": 0, "queued": 0, "already_queued": 0, "failed": 0},
            "provider_calls_performed": False,
        }

    # 单条走交互泳道(操作员在等);批量走 batch 泳道,绝不占交互道。
    queue_lane = "interactive" if plan["planned_count"] == 1 else "batch"

    from app.domains.kol import video_tracking

    queued: list[dict[str, Any]] = []
    already_queued: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in plan["planned"]:
        target_id = _int(item.get("evidence_id"))
        try:
            result = video_tracking.queue_evidence_refresh(
                conn,
                kol_pool_id=_int(item.get("kol_pool_id")),
                evidence_id=target_id,
                staff=staff,
                register_tracking=False,
                refresh_source=_plan.SKU_PLAY_REFRESH_SOURCE,
                queue_lane=queue_lane,
            )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批,但必须如实计入
            logger.warning(
                "sku_play_refresh.enqueue_failed evidence_id=%s code=%s",
                target_id,
                _error_code(exc),
            )
            failed.append({**item, "reason": _error_code(exc)})
            continue
        record = {**item, "job_id": _int(result.get("job_id")) or None}
        if str(result.get("status") or "") == "already_queued":
            already_queued.append(record)
        else:
            queued.append(record)

    return {
        "status": "dispatched",
        "plan": plan,
        "queued": queued,
        "already_queued": already_queued,
        "failed": failed,
        "counts": {
            "planned": plan["planned_count"],
            "queued": len(queued),
            "already_queued": len(already_queued),
            "failed": len(failed),
        },
        "provider_calls_performed": False,
    }


__all__ = ["SkuPlayRefreshError", "run_sku_play_refresh"]
