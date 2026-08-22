"""new_launch_match 的影子重排序接线(W-L2),从主文件拆出以守千行/800 行卫兵。

P4 确定性评分 ``score`` 与 ``score_breakdown`` 永不改;这里只给每条 item 挂
``rerank_adjustment`` / ``rerank_reason_codes``(后端内部字段),treatment arm 才按
score+adjustment 稳定重排并重排 rank。cron 刷新无 staff 身份 → 恒 control/off,
只记录影子量供拟合,不动线上次序。零 LLM、零 provider、零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.recommendations import rerank_shadow

logger = get_logger(__name__)

ENGINE = "new_launch_match"


def apply_to_preview(
    eligible: list[dict[str, Any]],
    *,
    pool_map: dict[str, dict[str, Any]],
    staff: Any = None,
) -> dict[str, Any]:
    """在 eligible 已按确定性分排好序之后调用;返回 rerank_policy 摘要。失败退化为零调整。"""
    pool_by_id: dict[int, dict[str, Any]] = {}
    for pool in (pool_map or {}).values():
        try:
            pool_by_id[int(pool.get("id") or 0)] = pool
        except (TypeError, ValueError, AttributeError):
            continue
    arm = rerank_shadow.arm_for_staff(staff)
    try:
        model = rerank_shadow.load_active_model()
    except Exception:
        logger.warning("new_launch_match.rerank_model_unavailable", exc_info=True)
        model = None
    try:
        return rerank_shadow.apply_shadow_rerank(
            eligible,
            arm=arm,
            model=model,
            engine=ENGINE,
            profile_of=lambda item: pool_by_id.get(int(item.get("kol_pool_id") or 0), {}),
            breakdown_of=lambda item: item.get("score_breakdown") or {},
        )
    except Exception:
        logger.warning("new_launch_match.rerank_apply_failed", exc_info=True)
        for item in eligible:
            item.setdefault("rerank_adjustment", 0.0)
            item.setdefault("rerank_reason_codes", [])
        return {"arm": arm, "applied": False, "model_version": "", "candidates_adjusted": 0,
                "display_note": "", "provider_calls": False, "error": "rerank_apply_failed"}


def persist_snapshots(
    *,
    items: list[dict[str, Any]],
    rec_ids: dict[int, int],
    policy: dict[str, Any],
    run_id: int,
) -> dict[str, int]:
    """整批 commit 之后落特征快照。rec_ids: index(item 在 items 中的位置) → recommendation_id。"""
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        rows.append({
            "recommendation_id": rec_ids.get(idx, 0),
            "kol_pool_id": item.get("kol_pool_id"),
            "score": item.get("score"),
            "rerank_vector": item.get("rerank_vector") or {},
            "rerank_adjustment": item.get("rerank_adjustment"),
            "rerank_reason_codes": item.get("rerank_reason_codes") or [],
        })
    return rerank_shadow.write_snapshots_for_items(
        rows,
        engine=ENGINE,
        arm=str(policy.get("arm") or rerank_shadow.ARM_OFF),
        applied=bool(policy.get("applied")),
        model_version=str(policy.get("model_version") or ""),
        staff_id=None,
        run_id=run_id or None,
        launch_id=None,
        rec_id_of=lambda row: row.get("recommendation_id"),
    )


def strip_internal_vectors(items: list[dict[str, Any]]) -> None:
    """特征向量只进快照表,不进响应/落盘 JSON(体积 + 内部字段不外露)。"""
    for item in items:
        item.pop("rerank_vector", None)
