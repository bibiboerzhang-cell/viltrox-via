"""C3 · KOL 数字孪生 —— 一份"合作判断档案"(只读,聚合既有情报,零新数据)。

把散落的智能(推荐卡 / provenance / ROI / 学习权重 / 结果回写)聚成一个统一画像:
身份 + 为什么记住 + 数据等级 + 历史表现 + 学习信号 + 合作建议。
红线:全程只读;llm_v6_fit 等独立展示,绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception:
        logger.debug("twin.section_failed", exc_info=True)
        return default


def _suggestion(grade: str, weight: float | None, outcomes: dict[str, Any], roi_status: str) -> str:
    """据 数据等级 + 学习权重 + 结果回写 合成一句合作建议(不含臆造数值)。"""
    succ = int(outcomes.get("success") or 0)
    fail = int(outcomes.get("fail") or 0)
    if fail > succ and (succ + fail) >= 2:
        return "谨慎:历史合作结果偏负,建议复盘失败原因后再定"
    if weight is not None and weight >= 0.6:
        return "强推荐:历史表现好 + 学习权重高,优先续约/加投"
    if grade in ("A", "B") and (succ + fail) == 0:
        return "建议首次合作:数据完整度高、暂无历史,可试投一轮观察"
    if grade in ("C", "D"):
        return "数据不足:建议先补深析/评论证据再判断"
    return "可合作:综合信号中性,按常规流程推进"


def get_kol_twin(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """KOL 合作判断档案(只读,聚合既有情报)。不存在 → status='not_found'。"""
    kid = int(kol_pool_id or 0)
    if kid <= 0:
        return {"status": "not_found"}

    from app.domains.kol import recommendation_card, roi_aggregate
    from app.domains.memory import agent_memory_writer, provenance

    card = _safe(lambda: recommendation_card.get_recommendation_card(kid, staff=staff), {"status": "error"})
    if card.get("status") == "not_found":
        return {"status": "not_found", "kol_pool_id": kid}

    prov = _safe(lambda: provenance.get_kol_provenance(kid, staff=staff, video_limit=5), {})
    roi = _safe(lambda: roi_aggregate.get_kol_roi_summary(kid, staff=staff), {"status": "no_projects"})
    weight = _safe(lambda: roi_aggregate.compute_next_recommendation_weight(kid), None)
    outcomes = _safe(lambda: agent_memory_writer.recent_outcome_stats("kol", kid, lookback=20),
                     {"total": 0, "success": 0, "fail": 0})
    enrichment = _safe(lambda: _enrichment_summary(kid), {})

    grade = str(card.get("data_grade") or "")
    suggestion = _suggestion(grade, weight if isinstance(weight, (int, float)) else None, outcomes, str(roi.get("status") or ""))

    return {
        "status": "ok",
        "kol_pool_id": kid,
        "identity": {
            "name": card.get("name"),
            "platform": card.get("platform"),
            "followers": card.get("followers"),
        },
        "data_grade": grade,
        "why_recommended": card.get("why_recommended"),
        "signals": card.get("signals"),
        "why_remembered": prov.get("why_remembered"),
        "citations": prov.get("citations"),
        "performance": {
            "roi_summary": roi,
            "recommendation_weight": weight,
            "recent_outcomes": outcomes,
        },
        "enrichment": enrichment,
        "cooperation_suggestion": suggestion,
        "note": "合作判断档案:聚合既有推荐卡/provenance/ROI/学习信号/外部富集;只读,零触 viltrox_fit_score。",
    }


def _enrichment_summary(kid: int) -> dict[str, Any]:
    """外部富集证据摘要(来源 + 各 kind 有无),给孪生卡展示。零触 viltrox_fit_score。"""
    from app.domains.discovery import enrichment as enr

    e = enr.get_enrichment(kid)
    by_kind = e.get("by_kind") or {}
    return {
        "available": bool(by_kind),
        "kinds": list(by_kind.keys()),
        "sources": sorted({str(v.get("source") or "") for v in by_kind.values() if v.get("source")}),
    }
