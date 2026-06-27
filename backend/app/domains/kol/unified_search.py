"""段2 · 统一 KOL 搜索响应模型(facade)—— 收口"联邦/平台/历史池召回"成一个后端模型。

字段统一:source / status / cost_gate / provider_status / candidate_ids / history_match / results。
前端单一体验:任何搜索路径同一响应形,杜绝"这个页能搜那个页不能解释来源"。
红线:自有池召回免费即时;外部抓取(Apify/商业源)烧钱 → cost_gate 显式 + 默认 off;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _history_match(query: str) -> dict[str, Any]:
    """历史搜索匹配:这个 query 之前搜过没(search_sessions)。"""
    if not table_exists("vkpi_kol_search_sessions"):
        return {"available": False, "prior_sessions": 0, "searched_before": False}
    try:
        row = get_conn().execute(
            "SELECT COUNT(*) AS n FROM vkpi_kol_search_sessions WHERE query_text ILIKE ?",
            (f"%{str(query).strip()}%",),
        ).fetchone()
        n = int(dict(row).get("n") or 0) if row else 0
        return {"available": True, "prior_sessions": n, "searched_before": n > 0}
    except Exception:
        logger.debug("unified_search.history_failed", exc_info=True)
        return {"available": False, "prior_sessions": 0, "searched_before": False}


def unified_search(query: str, *, include_external: bool = False, limit: int = 20,
                   staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """统一搜索:自有池召回 + 联邦(可选外部)+ 历史匹配 + 成本闸,产出统一响应模型。"""
    q = str(query or "").strip()
    if not q:
        return {"status": "empty_query", "query": q, "results": [], "source": "unified"}

    from app.domains.discovery import federation

    fed = federation.federated_search(q, limit=limit, staff=staff)
    results = fed.get("results", []) if fed.get("status") == "ok" else []
    provider_status = fed.get("sources", {})

    # 候选:已在池的 pool id(可直接看档案/勾选)
    candidate_ids = [r.get("kol_pool_id") for r in results if r.get("kol_pool_id")]

    # 成本闸:外部源是否就绪 + 本次是否会烧钱
    providers = federation.list_providers("discovery")
    external_ready = [p["name"] for p in providers
                      if p.get("enabled") and p.get("adapter_ready") and p["name"] != "internal_pool"]
    cost_gate = {
        "external_providers_ready": external_ready,
        "external_search_enabled": bool(external_ready),
        "incurs_cost": bool(external_ready) and bool(include_external),
        "note": "自有池召回免费;外部抓取(Apify/商业源)烧钱——配 actor + include_external 才跑,默认 off。",
    }

    return {
        "status": "ok",
        "query": q,
        "source": "unified",
        "provider_status": provider_status,
        "results": results,
        "candidate_ids": candidate_ids,
        "history_match": _history_match(q),
        "cost_gate": cost_gate,
        "note": "统一搜索响应模型(段2):联邦/平台/历史池召回收口为一个形;前端单一体验;零触 viltrox_fit_score。",
    }
