"""把 Apify 用透 · KOL 富集 —— 用现有 scrape_with_apify 抓公开数据,存为富集证据(替代 HypeAuditor)。

env 门控:VKPI_APIFY_ENRICH_ENABLED=1 才真跑(避免意外 Apify 计费);未启用/无 url/Apify 不可用 → 诚实降级。
红线:抓来的公开指标只入 vkpi_kol_enrichment 作证据,绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def _enabled() -> bool:
    return str(os.getenv("VKPI_APIFY_ENRICH_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}


def enrich_kol(kol_pool_id: int, *, force: bool = False) -> dict[str, Any]:
    """对一个 KOL 用 Apify 抓公开档案 → 存富集证据(kind=performance)。

    未启用(且未 force)/无 profile_url/Apify 不可用 → 诚实降级,零成本零写。
    """
    kid = int(kol_pool_id or 0)
    if kid <= 0:
        return {"status": "invalid", "reason": "kol_pool_id_required"}
    if not (force or _enabled()):
        return {"status": "disabled", "note": "设 VKPI_APIFY_ENRICH_ENABLED=1 启用(避免意外 Apify 计费)"}
    from app.platform.apify_budget import current_apify_execution_context

    if current_apify_execution_context() is None:
        return {
            "status": "background_refresh_required",
            "kol_pool_id": kid,
            "provider_calls_performed": False,
        }
    from app.repositories.kol_pool_repo import KolPoolRepository

    d = KolPoolRepository().get_by_id(kid)  # L2:走 repo
    if not d:
        return {"status": "not_found", "kol_pool_id": kid}
    url = str(d.get("profile_url") or d.get("handle") or "").strip()
    platform = str(d.get("platform") or "").strip().lower()
    if not url:
        return {"status": "no_profile_url", "kol_pool_id": kid, "note": "该 KOL 无 profile_url,先补 url 再富集"}
    try:
        import asyncio

        from app.services.scraping import apify as apify_svc

        if not apify_svc._apify_available():
            return {"status": "not_configured", "note": "Apify 不可用(APIFY_TOKEN 未配)"}
        result = asyncio.run(apify_svc.scrape_with_apify(url, platform or "youtube"))
    except Exception:
        logger.warning("apify_enrich.scrape_failed", extra={"kol_pool_id": kid}, exc_info=True)
        return {"status": "error", "kol_pool_id": kid}
    if not result or result.get("error"):
        return {"status": "no_data", "kol_pool_id": kid, "reason": str((result or {}).get("error") or "empty")}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    from app.domains.discovery import enrichment

    eid = enrichment.record_enrichment(
        kid, "apify", "historical",
        {"platform": platform, "url": url, "metrics": metrics, "title": result.get("title", "")},
        confidence=0.6,
    )
    return {"status": "ok", "kol_pool_id": kid, "enrichment_id": eid, "metrics": metrics,
            "note": "Apify 公开数据已存为富集证据(confidence=0.6,独立信号,零触 viltrox_fit_score)。"}


def enrich_candidates(limit: int = 10) -> dict[str, Any]:
    """批量富集 D3 自动轮询候选(收藏/高价值 KOL)。env 门控 + 硬上限,防烧钱。

    未启用 → disabled(零成本)。供 D3 调度 job 调用;每轮最多 limit 个(默认 10)。
    """
    if not _enabled():
        return {"status": "disabled", "enriched": 0, "note": "设 VKPI_APIFY_ENRICH_ENABLED=1 启用"}
    try:
        from app.domains.kol import auto_poll

        cands = auto_poll.select_poll_candidates(limit=max(1, min(int(limit or 10), 30)))
    except Exception:
        logger.warning("apify_enrich.candidates_failed", exc_info=True)
        return {"status": "error", "enriched": 0}
    ok = 0
    for c in cands:
        r = enrich_kol(int(c.get("kol_pool_id") or 0), force=True)  # force:_enabled 已确认
        if r.get("status") == "ok":
            ok += 1
    return {"status": "ok", "candidates": len(cands), "enriched": ok,
            "note": "批量富集收藏/高价值 KOL(Apify 公开数据→证据);硬上限防烧钱;零触 viltrox_fit_score。"}
