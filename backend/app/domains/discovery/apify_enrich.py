"""把 Apify 用透 · KOL 富集 —— 用现有 scrape_with_apify 抓公开数据,存为富集证据(替代 HypeAuditor)。

env 门控:VKPI_APIFY_ENRICH_ENABLED=1 才真跑(避免意外 Apify 计费);未启用/无 url/Apify 不可用 → 诚实降级。
红线:抓来的公开指标只入 vkpi_kol_enrichment 作证据,绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

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
    row = get_conn().execute("SELECT profile_url, handle, platform FROM vkpi_kol_pool WHERE id = ?", (kid,)).fetchone()
    if not row:
        return {"status": "not_found", "kol_pool_id": kid}
    d = dict(row)
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
