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
from app.domains.kol.identity import canonical_creator_aliases

logger = get_logger(__name__)
MAX_DISCOVERY_QUERY_LENGTH = 256

_TABLE = "vkpi_discovery_providers"

# 商业/自定义源适配器注册表:name -> fn(query, limit) -> list[candidate]。
# candidate 统一字段:{source, external_id(仅明确创作者ID), content_id(内容证据),
# name, platform, followers, handle, score(外部分,展示用)}。
_CUSTOM: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {}


def _provider_creator_id_projection(item: dict[str, Any]) -> dict[str, str]:
    """Project only explicitly creator-scoped ids from a provider item."""
    field_map = (
        ("channel_id", ("channel_id", "channelId"), "channel_id"),
        ("account_id", ("account_id", "accountId"), "account_id"),
        (
            "platform_user_id",
            ("platform_user_id", "platformUserId"),
            "user_id",
        ),
        ("user_id", ("user_id", "userId"), "user_id"),
        ("native_id", ("native_id", "nativeId"), "native_id"),
    )
    projected: dict[str, str] = {}
    external_id = ""
    external_kind = ""
    for output_field, source_fields, kind in field_map:
        value = next(
            (
                str(item.get(field) or "").strip()
                for field in source_fields
                if str(item.get(field) or "").strip()
            ),
            "",
        )
        if not value:
            continue
        projected[output_field] = value
        if not external_id:
            external_id, external_kind = value, kind
    projected["external_id"] = external_id
    projected["external_id_kind"] = external_kind
    return projected


def _provider_content_id(item: dict[str, Any]) -> str:
    """Keep ambiguous provider ids as content evidence, never creator identity."""
    for field in ("content_id", "video_id", "post_id", "media_id", "aweme_id"):
        value = str(item.get(field) or "").strip()
        if value:
            return value
    ambiguous = item.get("id")
    if isinstance(ambiguous, dict):
        for field in ("videoId", "postId", "mediaId", "id"):
            value = str(ambiguous.get(field) or "").strip()
            if value:
                return value
        return ""
    return str(ambiguous or "").strip()


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
    from app.domains.kol.contact_system import project_public_profile_url
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
                creator_ids = _provider_creator_id_projection(item)
                handle = str(
                    item.get("handle")
                    or item.get("channel_handle")
                    or item.get("username")
                    or creator_ids.get("channel_id")
                    or creator_ids.get("account_id")
                    or creator_ids.get("platform_user_id")
                    or creator_ids.get("user_id")
                    or creator_ids.get("native_id")
                    or ""
                ).strip()
                profile_url = project_public_profile_url(
                    item.get("profile_url") or item.get("channel_url")
                )
                avatar_url = str(item.get("avatar_url") or "").strip()
                avatar_url_status = str(
                    item.get("avatar_url_status")
                    or ("unverified" if avatar_url else "missing")
                ).strip().lower()[:40]
                # Content thumbnail is kept as separate evidence and is never
                # promoted into the account avatar slot when an avatar is
                # missing/expired.
                thumbnail_url = str(
                    item.get("thumbnail_url") or item.get("thumbnail") or ""
                ).strip()
                out.append(
                    {
                        "source": "apify_search",
                        "platform": platform,
                        **creator_ids,
                        "content_id": _provider_content_id(item),
                        "name": (
                            item.get("channel_name")
                            or item.get("display_name")
                            or item.get("name")
                            or item.get("handle")
                            or ""
                        ),
                        "followers": item.get("followers") or item.get("subscribers"),
                        "handle": handle,
                        "profile_url": profile_url,
                        "channel_url": profile_url,
                        "avatar_url": avatar_url,
                        "avatar_url_status": avatar_url_status,
                        "thumbnail_url": thumbnail_url,
                        "source_url": str(item.get("source_url") or "").strip(),
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
    groups: list[dict[str, Any]] = []
    for x in items:
        try:
            pool_id = int(x.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            pool_id = 0
        aliases = canonical_creator_aliases(x)
        key = str(x.get("kol_pool_id") or "") or f"{x.get('platform','')}:{(x.get('handle') or x.get('name') or '').strip().lower()}"
        matching = [
            index
            for index, group in enumerate(groups)
            if (pool_id and pool_id in group["pool_ids"])
            or (aliases and aliases.intersection(group["aliases"]))
            or (not aliases and key in group["fallback_keys"])
        ]
        if not matching:
            groups.append({
                "item": x,
                "aliases": set(aliases),
                "pool_ids": {pool_id} if pool_id else set(),
                "fallback_keys": {key},
            })
            continue
        # One bridge observation can connect two groups already emitted in
        # this pass (UC-only, @handle-only, then UC+handle). Merge every
        # matching group rather than discarding the bridge and leaving two
        # physical cards behind.
        target = groups[matching[0]]
        target["aliases"].update(aliases)
        target["fallback_keys"].add(key)
        if pool_id:
            target["pool_ids"].add(pool_id)
        for index in reversed(matching[1:]):
            other = groups.pop(index)
            target["aliases"].update(other["aliases"])
            target["pool_ids"].update(other["pool_ids"])
            target["fallback_keys"].update(other["fallback_keys"])
    return [group["item"] for group in groups]


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
