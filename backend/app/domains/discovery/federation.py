"""联邦发现 —— 把成熟外部源(Modash/HypeAuditor/蝉妈妈/Apify)接进来,我们只做归一+fit+落库。

可插拔:自有 internal_pool 现在就跑;商业源用 register_provider 注册适配器后,加 key 即启用,主体零改。
红线:联邦只负责"召回候选";我们的 fit 评分独立(在落库/排序处),外部分数仅作展示信号,绝不并入 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_TABLE = "vkpi_discovery_providers"

# 商业/自定义源适配器注册表:name -> fn(query, limit) -> list[candidate]。
# candidate 统一字段:{source, external_id, name, platform, followers, handle, score(外部分,展示用)}。
_CUSTOM: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {}


def register_provider(name: str, fn: Callable[[str, int], list[dict[str, Any]]]) -> None:
    """注册一个发现源适配器(接 Modash/HypeAuditor 等时调用)。"""
    _CUSTOM[str(name)] = fn


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
            d["adapter_ready"] = (d["name"] == "internal_pool") or (d["name"] in _CUSTOM)
            out.append(d)
        return out
    except Exception:
        logger.debug("federation.list_providers_failed", exc_info=True)
        return []


def _run_provider(name: str, query: str, limit: int) -> tuple[list[dict[str, Any]], str]:
    if name == "internal_pool":
        from app.domains.intelligence import semantic_recall

        r = semantic_recall.unified_recall(query, kinds=("kol",), limit=limit)
        items = [
            {"source": "internal_pool", "kol_pool_id": x.get("id"), "name": x.get("title"),
             "platform": "", "score": x.get("score"), "in_pool": True}
            for x in r.get("results", [])
        ]
        return items, "ok"
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


def federated_search(query: str, *, limit: int = 20, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """跨启用源联邦发现 → 归一去重。商业源未配置则 not_configured(不报错)。"""
    del staff
    q = str(query or "").strip()
    if not q:
        return {"status": "empty_query", "results": [], "sources": {}}
    providers = [p for p in list_providers("discovery") if p["enabled"]]
    if not any(p["name"] == "internal_pool" for p in providers):
        providers.append({"name": "internal_pool"})  # 自有源恒可用兜底
    results: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for p in providers:
        items, status = _run_provider(p["name"], q, limit)
        sources[p["name"]] = {"count": len(items), "status": status}
        results.extend(items)
    deduped = _dedupe(results)
    return {
        "status": "ok",
        "query": q,
        "sources": sources,
        "results": deduped[: max(1, min(int(limit or 20), 100))],
        "note": "联邦发现:启用源各自召回→归一去重;商业源未配置=not_configured(诚实);我们的 fit 评分独立,外部分仅展示。",
    }
