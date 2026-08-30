"""KOL Pool 详情/视频证据只读投影(从 pool.py 行为不变搬出)。

纯读端:V6 Fit 投影、视频 evidence 拉取、置信度角标。绝不写 viltrox_fit_score。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.analysis.cache_repo import analysis_cache_read_projection
from app.domains import content_metric_snapshots
from app.domains.kol.metric_truth import project_evidence_item_truth
from app.domains.kol.pool_video_evidence_projection import (
    video_evidence_for_kol as _project_video_evidence_for_kol,
)
from app.domains.kol.pool_common import (
    _bio,  # noqa: F401  (kept available for sibling read-side parity)
    _float_or_none,
    _int_or_none,
    _platform,
)
from app.domains.scoring import ScoringRegistry

from app.core.logging import get_logger

logger = get_logger(__name__)


_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_CACHE_ROUTE_RE = re.compile(
    r"^/api/(?:vkpi-media|admin/vkpi/media)/video-cache/([0-9a-fA-F]{64})/?$"
)
_BATCH_VIDEO_CACHE_PLATFORMS = frozenset({"instagram", "tiktok"})


def _youtube_video_id(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        candidate = path_parts[0]
        return candidate if _YOUTUBE_ID_RE.match(candidate) else ""
    if "youtube.com" in host:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if _YOUTUBE_ID_RE.match(query_id):
            return query_id
        for marker in ("shorts", "embed", "live"):
            if marker in path_parts:
                idx = path_parts.index(marker)
                if idx + 1 < len(path_parts) and _YOUTUBE_ID_RE.match(path_parts[idx + 1]):
                    return path_parts[idx + 1]
    return ""


def _youtube_thumbnail_url(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""


def _video_cache_digest(value: Any) -> str:
    """Return a digest only for one of our authenticated cache routes."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = urllib.parse.urlsplit(raw).path
    except ValueError:
        return ""
    matched = _VIDEO_CACHE_ROUTE_RE.fullmatch(path)
    return matched.group(1).lower() if matched else ""


def _validated_cached_video_url(item: dict[str, Any], platform: str) -> str:
    """Resolve a playable local/R2 cache URL without trusting a stale ledger flag.

    A historical ``status='cached'`` row is only a hint. Internal digest URLs
    must still have local bytes or resolve to a current R2 URL. When that hint
    is stale, the native/evidence identity resolver gets one read-only chance
    to recover a valid cache before the caller falls back to ``content_url``.
    """

    from app.domains.kol.url_deep_crawl_queue import _content_url_video_id
    from app.domains.media.cache import (
        cached_video_file,
        cached_video_redirect_url,
        cached_video_url_for_item,
    )

    raw_url = str(item.get("cached_video_url") or "").strip()
    raw_digest = str(item.get("cached_video_digest") or "").strip().lower()
    if len(raw_digest) != 64 or any(ch not in "0123456789abcdef" for ch in raw_digest):
        raw_digest = ""
    digest = raw_digest or _video_cache_digest(raw_url)

    if digest:
        if cached_video_file(digest):
            return f"/api/vkpi-media/video-cache/{digest}"
        redirected = str(cached_video_redirect_url(digest) or "").strip()
        if redirected:
            return redirected

    # A non-digest URL is already a resolved public cache URL. Digest routes,
    # however, are never returned merely because the ledger labelled them
    # cached: the two checks above must have proved local bytes or R2 playback.
    if raw_url and not digest and not _video_cache_digest(raw_url):
        return raw_url

    candidates = [
        _content_url_video_id(platform, item.get("content_url")),
        str(item.get("id") or item.get("evidence_id") or "").strip(),
    ]
    seen: set[str] = set()
    for video_id in candidates:
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        resolved = str(cached_video_url_for_item(platform, video_id) or "").strip()
        if not resolved:
            continue
        resolved_digest = _video_cache_digest(resolved)
        if not resolved_digest:
            return resolved
        if cached_video_file(resolved_digest):
            return f"/api/vkpi-media/video-cache/{resolved_digest}"
        redirected = str(cached_video_redirect_url(resolved_digest) or "").strip()
        if redirected:
            return redirected
    return ""


def _batch_cached_video_urls(
    conn: Any,
    rows: list[Any],
) -> dict[int, str] | None:
    """Resolve Instagram/TikTok cache identities with one DB read.

    ``None`` means the batch read was unavailable (rolling schema/test double),
    in which case the caller preserves the legacy per-item resolver.  A dict,
    including an empty one, is authoritative and prevents per-video DB
    fallback.  Local sidecars/files remain provider-free and are still checked.
    """

    if not is_postgres_runtime():
        return None
    items = [dict(row) for row in rows]
    candidates, pairs, digests = _batch_cache_candidates(items)
    asset_rows = _batch_cache_asset_rows(conn, pairs, digests)
    if asset_rows is None:
        return None
    by_pair, by_digest = _batch_cache_asset_index(asset_rows)
    resolved: dict[int, str] = {}
    for item in items:
        evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
        if evidence_id not in candidates:
            continue
        value = _batch_item_cache_value(item, candidates[evidence_id], by_pair, by_digest)
        if value:
            resolved[evidence_id] = value
    return resolved


def _batch_cache_hex_digest(value: Any) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) == 64 and not any(ch not in "0123456789abcdef" for ch in digest):
        return digest
    return ""


def _batch_cache_candidates(
    items: list[dict[str, Any]],
) -> tuple[dict[int, list[tuple[str, str]]], list[tuple[str, str]], list[str]]:
    candidates: dict[int, list[tuple[str, str]]] = {}
    pairs: list[tuple[str, str]] = []
    digests: list[str] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_digests: set[str] = set()
    for item in items:
        evidence_id = int(item.get("evidence_id") or item.get("id") or 0)
        platform = _platform(item.get("platform") or "")
        if not evidence_id or platform not in _BATCH_VIDEO_CACHE_PLATFORMS:
            continue
        item_pairs = _batch_item_cache_pairs(platform, evidence_id, item.get("content_url"))
        for pair in item_pairs:
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                pairs.append(pair)
        candidates[evidence_id] = item_pairs
        digest = _batch_cache_hex_digest(item.get("cached_video_digest"))
        if digest and digest not in seen_digests:
            seen_digests.add(digest)
            digests.append(digest)
    return candidates, pairs, digests


def _batch_item_cache_pairs(
    platform: str, evidence_id: int, content_url: Any
) -> list[tuple[str, str]]:
    # 懒 import 保持在函数体内(与改前同位):顶层导入会撞 url_deep_crawl_queue 循环。
    from app.domains.kol.url_deep_crawl_queue import _content_url_video_id

    item_pairs: list[tuple[str, str]] = []
    for external_id in (_content_url_video_id(platform, content_url), str(evidence_id)):
        pair = (platform, str(external_id or "").strip())
        if not pair[1] or pair in item_pairs:
            continue
        item_pairs.append(pair)
    return item_pairs


def _batch_cache_asset_rows(
    conn: Any, pairs: list[tuple[str, str]], digests: list[str]
) -> list[Any] | None:
    conditions: list[str] = []
    params: list[Any] = []
    for platform, external_id in pairs:
        conditions.append("(platform=? AND external_id=?)")
        params.extend([platform, external_id])
    if digests:
        conditions.append(f"digest IN ({','.join(['?'] * len(digests))})")
        params.extend(digests)
    if not conditions:
        return []
    try:
        return conn.execute(
            f"""
            SELECT digest, cache_url, storage_backend, r2_key, platform, external_id
            FROM vkpi_media_cache_assets
            WHERE media_kind='video' AND status='cached'
              AND ({' OR '.join(conditions)})
            ORDER BY updated_at DESC, id DESC
            """,
            tuple(params),
        ).fetchall()
    except Exception:
        # ``None`` 是文档化哨兵(批读不可用→调用方回退逐条老路径),非静默吞错。
        return None


def _batch_cache_asset_index(
    asset_rows: list[Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    by_digest: dict[str, dict[str, Any]] = {}
    for raw in asset_rows:
        asset = dict(raw)
        pair = (_platform(asset.get("platform") or ""), str(asset.get("external_id") or "").strip())
        digest = str(asset.get("digest") or "").strip().lower()
        if pair[0] and pair[1]:
            by_pair.setdefault(pair, asset)
        if digest:
            by_digest.setdefault(digest, asset)
    return by_pair, by_digest


def _batch_resolve_cache_asset(asset: dict[str, Any] | None) -> str:
    if not asset:
        return ""
    # 懒 import 保持在函数体内(与改前同位),monkeypatch 走模块属性路径不受影响。
    from app.domains.media import cache
    from app.domains.media.cache_core import _resolved_cached_asset_row

    digest = str(asset.get("digest") or "").strip().lower()
    if digest and cache.cached_video_file(digest):
        return f"/api/vkpi-media/video-cache/{digest}"
    return str(_resolved_cached_asset_row(asset) or "").strip()


def _batch_item_hint_value(item: dict[str, Any], by_digest: dict[str, dict[str, Any]]) -> str:
    from app.domains.media import cache

    raw_url = str(item.get("cached_video_url") or "").strip()
    digest = _batch_cache_hex_digest(item.get("cached_video_digest"))
    value = ""
    if digest:
        if cache.cached_video_file(digest):
            value = f"/api/vkpi-media/video-cache/{digest}"
        if not value:
            projected_asset = {
                "digest": digest,
                "cache_url": item.get("cached_video_url"),
                "storage_backend": item.get("cached_video_storage_backend"),
                "r2_key": item.get("cached_video_r2_key"),
            }
            value = _batch_resolve_cache_asset(projected_asset) or _batch_resolve_cache_asset(by_digest.get(digest))
    elif raw_url and not _video_cache_digest(raw_url):
        value = raw_url
    return value


def _batch_item_cache_value(
    item: dict[str, Any],
    item_pairs: list[tuple[str, str]],
    by_pair: dict[tuple[str, str], dict[str, Any]],
    by_digest: dict[str, dict[str, Any]],
) -> str:
    from app.domains.media import cache

    value = _batch_item_hint_value(item, by_digest)
    for pair in item_pairs:
        if value:
            break
        try:
            value = str(cache.cached_video_url_for_item(*pair, allow_db_fallback=False) or "").strip()
        except TypeError:
            # A rolling test double may expose the older two-argument
            # signature; the batched DB projection below remains valid.
            value = ""
        if not value:
            value = _batch_resolve_cache_asset(by_pair.get(pair))
    return value


def _v6_breakdown_for_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Project persisted V6 Fit into the drawer's read-only breakdown shape.

    vkpi_kol_pool only persists viltrox_fit_score/reason today. The current
    rule_v0 score is additive, while the drawer has older multiplier labels.
    Keep those legacy multiplier slots neutral and expose the real additive
    components under components so the UI can evolve without a write migration.
    """

    persisted_score = _float_or_none(item.get("viltrox_fit_score"))
    if persisted_score is None:
        return None
    platform = _platform(item.get("platform") or "")
    engagement = _float_or_none(item.get("engagement_rate"))
    engagement_ratio = (engagement / 100.0) if engagement is not None and engagement > 1 else engagement
    try:
        scoring = ScoringRegistry.get("rule_v0").score(
            {
                "platform": platform,
                "followers": _int_or_none(item.get("followers")),
                "posts_count": _int_or_none(item.get("posts_count")),
                "avg_views": _int_or_none(item.get("avg_views")),
                "engagement_rate": engagement_ratio,
                "primary_topic": item.get("primary_topic") or item.get("bio") or "",
                "sync_status": item.get("sync_status") or "",
            },
            {"product_name": "Viltrox lens", "category": "camera lens", "target_platforms": [platform]},
        )
        components = dict(scoring.breakdown or {})
        projected_score = float(scoring.score)
        strengths = list(scoring.strengths or [])
        concerns = list(scoring.concerns or [])
    except Exception:
        components = {}
        projected_score = persisted_score
        strengths = []
        concerns = []

    return {
        "source": "rule_v0_read_projection",
        "formula": "additive_rule_v0_projected_to_legacy_multiplier_slots",
        "base": round(float(persisted_score), 3),
        "industry": 1.0,
        "upgrade": 1.0,
        "geo_match": 1.0,
        "real_er": 1.0,
        "loyalty": 1.0,
        "trend": 1.0,
        "platform_native": 1.0,
        "price_match": 1.0,
        "network": 1.0,
        "competitor_decay": 0.0,
        "components": components,
        "projected_rule_v0_score": round(projected_score, 3),
        "persisted_viltrox_fit_score": round(float(persisted_score), 3),
        "reason": item.get("viltrox_fit_reason"),
        "strengths": strengths,
        "concerns": concerns,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "viltrox_fit_score_write": False,
    }


def _video_evidence_for_kol(
    kol_pool_id: int,
    *,
    limit: int = 3,
    only_with_cache: bool = False,
    include_inactive: bool = False,
    stable_order: bool = False,
    before: tuple[str | None, int] | None = None,
) -> list[dict[str, Any]]:
    """Return the legacy video-evidence DTO through a low-complexity projector.

    Dependencies remain resolved from this module on every call so existing
    monkeypatches, rolling-runtime replacements, and imports through
    app.domains.kol.pool keep the same boundary.
    """

    return _project_video_evidence_for_kol(
        kol_pool_id,
        limit=limit,
        only_with_cache=only_with_cache,
        include_inactive=include_inactive,
        stable_order=stable_order,
        before=before,
        get_conn=get_conn,
        is_postgres_runtime=is_postgres_runtime,
        metric_trends_for_evidence=content_metric_snapshots.metric_trends_for_evidence,
        unavailable_tracking=content_metric_snapshots.unavailable_tracking,
        batch_video_cache_platforms=_BATCH_VIDEO_CACHE_PLATFORMS,
        batch_cached_video_urls=_batch_cached_video_urls,
        validated_cached_video_url=_validated_cached_video_url,
        normalize_platform=_platform,
        youtube_video_id=_youtube_video_id,
        youtube_thumbnail_url=_youtube_thumbnail_url,
        project_evidence_item_truth=project_evidence_item_truth,
        logger=logger,
    )


def _final_analysis_cache_projection(
    entry: dict[str, Any] | None,
    *,
    target_id: Any = None,
) -> tuple[dict[str, Any] | None, str, str | None, dict[str, Any]]:
    """Project one final-v1 cache row without promoting quality triage."""

    projection = analysis_cache_read_projection(
        entry,
        target_type="video",
        target_id=target_id or (entry or {}).get("target_id"),
        derive_method="video_analysis_final_v1",
    )
    state = str(projection.get("state") or "not_requested")
    if state == "ready":
        return entry, state, None, projection
    if state == "quality_incomplete":
        return None, state, "final_v1_quality_incomplete", projection
    if state == "legacy_unverified":
        return None, state, "final_v1_cache_legacy_unverified", projection
    return None, "not_requested", "analysis_not_requested", projection


def _confidence_badge_from_dims(dimensions: dict[str, Any]) -> dict[str, Any]:
    """从持久化 dimensions_11_json 抽独立置信度/数据完整度角标(只读,绝不进 fit)。"""
    conf = dimensions.get("confidence") if isinstance(dimensions.get("confidence"), dict) else {}
    present = sum(
        1
        for k in ("block1_content", "block2_performance", "block3_business", "block4_specialty")
        if isinstance(conf.get(k), (int, float)) and float(conf.get(k) or 0) > 0
    )
    return {
        "overall": float(conf.get("overall") or 0),
        "data_completeness": float(conf.get("data_completeness")) if conf.get("data_completeness") is not None else round(present / 4.0, 3),
        "per_block": {
            k: float(conf.get(k) or 0)
            for k in ("block1_content", "block2_performance", "block3_business", "block4_specialty")
        },
        "persisted": bool(dimensions.get("persisted")),
        "note": "独立置信度/数据完整度角标,绝不参与 viltrox_fit_score。",
    }
