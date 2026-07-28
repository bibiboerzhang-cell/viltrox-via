"""联邦发现 —— 把成熟外部源(Modash/HypeAuditor/蝉妈妈/Apify)接进来,我们只做归一+fit+落库。

可插拔:自有 internal_pool 现在就跑;商业源用 register_provider 注册适配器后,加 key 即启用,主体零改。
红线:联邦只负责"召回候选";我们的 fit 评分独立(在落库/排序处),外部分数仅作展示信号,绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import (
    db_connection_sync_reusing_scope,
    db_connection_sync_scope,
    get_conn,
    table_exists,
)
from app.platform.apify_budget import current_apify_execution_context

logger = get_logger(__name__)
MAX_DISCOVERY_QUERY_LENGTH = 256

_TABLE = "vkpi_discovery_providers"

# 商业/自定义源适配器注册表:name -> fn(query, limit) -> list[candidate]。
# candidate 统一字段:{source, external_id, name, platform, followers, handle, score(外部分,展示用)}。
_CUSTOM: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {}


def register_provider(name: str, fn: Callable[[str, int], list[dict[str, Any]]]) -> None:
    """注册一个发现源适配器(接 Modash/HypeAuditor 等时调用)。"""
    _CUSTOM[str(name)] = fn


def _local_read_scope():
    """Use the request lease for previews and a short standalone lease in workers."""
    if current_apify_execution_context() is None:
        return db_connection_sync_reusing_scope()
    return db_connection_sync_scope()


def list_providers(kind: str = "") -> list[dict[str, Any]]:
    if not table_exists(_TABLE):
        return []
    try:
        where, params = "", []
        if kind:
            where, params = "WHERE kind = ?", [kind]
        rows = get_conn().execute(
            f"SELECT name, kind, enabled, quota_daily, used_today, priority, note FROM {_TABLE} {where} ORDER BY priority ASC",
            tuple(params),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["enabled"] = bool(d.get("enabled") in (True, 1, "t", "true"))
            d["adapter_ready"] = d["name"] in ("internal_pool", "apify_search") or d["name"] in _CUSTOM
            out.append(d)
        return out
    except Exception:
        logger.debug("federation.list_providers_failed", exc_info=True)
        return []


def _apify_search(query: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    """复用我们的 Apify 做平台搜索(自持、不另花新供应商钱)。

    只允许 durable provider worker 执行；按平台复用既有、已审计的 actor
    输入适配器和统一预算账本。
    """
    # Read/user request paths may inspect the internal pool immediately, but a
    # paid provider run is only legal inside a centrally claimed durable job.
    # Returning a typed deferred status keeps preview/search routes useful and
    # avoids leaking ``durable_execution_context_required`` to the UI.
    if current_apify_execution_context() is None:
        return [], "background_refresh_required"

    # Reuse the reviewed platform-specific discovery adapters.  The previous
    # generic actor loop sent the same ``searchQueries`` payload to every
    # platform, even though TikTok and Instagram require different contracts;
    # production consequently advertised apify_search as enabled while having
    # no executable actor configuration.  The shared adapters already own the
    # actor ids, payload contracts, throttles, normalization, and budget ledger.
    from app.services.intelligence.account_search_discovery import search_platform_content

    platforms = ("youtube", "tiktok", "instagram")
    per = max(1, math.ceil(max(1, int(limit)) / len(platforms)))
    out: list[dict[str, Any]] = []
    statuses: list[str] = []
    for platform in platforms:
        try:
            result = asyncio.run(
                search_platform_content(
                    platform,
                    query,
                    max_results=per,
                )
            )
            status = str(result.get("status") or "error")
            statuses.append(status)
            for item in result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                handle = str(
                    item.get("handle")
                    or item.get("channel_url")
                    or item.get("profile_url")
                    or item.get("source_url")
                    or ""
                ).strip()
                out.append(
                    {
                        "source": "apify_search",
                        "platform": platform,
                        "external_id": str(
                            item.get("channel_id")
                            or item.get("id")
                            or item.get("handle")
                            or handle
                            or ""
                        ),
                        "name": (
                            item.get("channel_name")
                            or item.get("display_name")
                            or item.get("name")
                            or item.get("handle")
                            or ""
                        ),
                        "followers": item.get("followers") or item.get("subscribers"),
                        "handle": handle,
                        "in_pool": False,
                        "provider_status": status,
                    }
                )
        except Exception:
            statuses.append("error")
            logger.warning(
                "federation.apify_search_platform_failed",
                extra={"platform": platform},
                exc_info=True,
            )
    if out:
        return out[:limit], "ok" if all(status in {"done", "ok"} for status in statuses) else "partial"
    if statuses and all(status in {"provider_unavailable", "actor_not_configured", "unsupported_platform"} for status in statuses):
        return [], "not_configured"
    return [], "no_results" if any(status in {"done", "ok"} for status in statuses) else "error"


def _run_provider(
    name: str,
    query: str,
    limit: int,
    *,
    staff: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if name == "internal_pool":
        from app.domains.intelligence import semantic_recall

        # Release the local read lease before a durable worker starts waiting
        # on an external actor.  Otherwise one search can pin a DB connection
        # for the entire provider run.
        with _local_read_scope():
            r = semantic_recall.unified_recall(
                query,
                kinds=("kol",),
                limit=limit,
                staff=staff,
                provider_free=True,
            )
        items = [
            {"source": "internal_pool", "kol_pool_id": x.get("id"), "name": x.get("title"),
             "platform": "", "score": x.get("score"), "in_pool": True}
            for x in r.get("results", [])
        ]
        return items, "ok"
    if name == "apify_search":
        return _apify_search(query, limit)
    fn = _CUSTOM.get(name)
    if fn:
        try:
            return list(fn(query, limit) or []), "ok"
        except Exception:
            logger.warning("federation.provider_failed", extra={"provider": name}, exc_info=True)
            return [], "error"
    return [], "not_configured"  # 商业源待接 key+适配器(诚实)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for x in items:
        key = str(x.get("kol_pool_id") or "") or f"{x.get('platform','')}:{(x.get('name') or '').strip().lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
    return out


def federated_search(
    query: str,
    *,
    limit: int = 20,
    staff: dict[str, Any] | None = None,
    include_external: bool = False,
) -> dict[str, Any]:
    """跨启用源联邦发现 → 归一去重。商业源未配置则 not_configured(不报错)。"""
    q = str(query or "").strip()
    if not q:
        return {"status": "empty_query", "results": [], "sources": {}}
    if len(q) > MAX_DISCOVERY_QUERY_LENGTH:
        raise ValueError(
            f"query must be at most {MAX_DISCOVERY_QUERY_LENGTH} characters"
        )
    # Provider registry reads must finish and release their transaction before
    # a durable worker waits on external networks.  A dedicated short scope
    # also avoids rolling back any request-owned outer transaction.
    with _local_read_scope():
        providers = [p for p in list_providers("discovery") if p["enabled"]]
    if not any(p["name"] == "internal_pool" for p in providers):
        providers.append({"name": "internal_pool"})  # 自有源恒可用兜底
    results: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    external_allowed = bool(
        include_external and current_apify_execution_context() is not None
    )
    for p in providers:
        provider_name = str(p["name"])
        if provider_name != "internal_pool" and not external_allowed:
            sources[provider_name] = {
                "count": 0,
                "status": "background_refresh_required",
            }
            continue
        items, status = _run_provider(
            provider_name,
            q,
            limit,
            staff=staff,
        )
        sources[provider_name] = {"count": len(items), "status": status}
        results.extend(items)
    deduped = _dedupe(results)
    return {
        "status": "ok",
        "query": q,
        "sources": sources,
        "results": deduped[: max(1, min(int(limit or 20), 100))],
        "note": "联邦发现:启用源各自召回→归一去重;商业源未配置=not_configured(诚实);我们的 fit 评分独立,外部分仅展示。",
    }
