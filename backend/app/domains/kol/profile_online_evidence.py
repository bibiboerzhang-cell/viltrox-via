"""QueryCell and bounded content evidence adaptation for online KOL search.

Raw provider text is used only in memory. Public projections contain evidence
coordinates and matched terms, never source profile or transcript bodies.
"""
from __future__ import annotations

from collections.abc import Callable
import math
import re
from typing import Any

from app.domains.kol import (
    profile_online_facets,
    profile_online_identity,
    targeted_search_contract,
)
from app.domains.kol.profile_recall_match_evidence import (
    CAPABILITY_USE_EVIDENCE_SOURCE,
    CONTROLLED_ALIAS_EVIDENCE_SOURCE,
    candidate_facets,
)
from app.domains.kol.search_sessions_serde import (
    project_public_asset_url,
    project_public_profile_text,
)


_MAX_MATCHED_QUERY_CELLS = 8
_CONTENT_EVIDENCE_LIMITS = {
    "title": 500,
    "description": 2_000,
    "caption": 2_000,
    "transcript": 6_000,
    "subtitles": 6_000,
}
_CONTENT_FIELD_ALIASES = {
    "description": ("sample_description", "content_description", "description"),
    "caption": ("sample_caption", "caption"),
    "transcript": ("sample_transcript", "transcript"),
    "subtitles": ("subtitles",),
}
_ONLINE_PUBLIC_EVIDENCE_FIELDS = frozenset({
    "handle",
    "display_name",
    "bio",
    "primary_topic",
    "content_style",
    "secondary_topics_json",
    "profile_text",
    "type_reason",
    "representative_evidence.title",
    "representative_evidence.description",
    "representative_evidence.caption",
    "representative_evidence.transcript",
    "representative_evidence.subtitles",
})
_PRIVATE_EVIDENCE_TERM_RE = re.compile(r"@|(?:^|\D)\+?\d(?:[\s().-]*\d){6,}(?:\D|$)")
_SAFE_QUALITY_LABELS = frozenset({
    "poor", "fair", "good", "excellent", "low", "medium", "high",
})

def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_observed_score(value: Any) -> float | str | None:
    """Project only numeric or controlled-label aggregate quality values."""

    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().lower() in _SAFE_QUALITY_LABELS:
        return value.strip().lower()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, 6)


def _safe_distribution(value: Any) -> dict[str, float] | None:
    """Allow aggregate audience shares while rejecting arbitrary provider blobs."""

    if not isinstance(value, dict):
        return None
    output: dict[str, float] = {}
    for raw_key, raw_share in list(value.items())[:64]:
        key = " ".join(str(raw_key or "").split()).strip().lower()[:80]
        if not key or _PRIVATE_EVIDENCE_TERM_RE.search(key):
            continue
        try:
            share = float(raw_share)
        except (TypeError, ValueError):
            continue
        if math.isfinite(share) and share >= 0:
            output[key] = round(share, 6)
    return output or None


def _safe_growth_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    """Strict allowlist for online audience/content-execution score inputs."""

    output: dict[str, Any] = {}
    for key in (
        "audience_fit_score",
        "audience_match_score",
        "target_audience_fit",
        "content_execution_score",
        "production_quality_score",
        "production_quality",
        "posting_consistency_score",
        "content_consistency_score",
        "posting_consistency",
        "posting_consistency_percentile",
        "recent_content_consistency_percentile",
        "originality_score",
        "originality",
    ):
        value = _safe_observed_score(raw.get(key))
        if value is not None:
            output[key] = value
    for key in (
        "audience_market_distribution",
        "audience_country_distribution",
        "audience_language_distribution",
    ):
        value = _safe_distribution(raw.get(key))
        if value:
            output[key] = value
    return output


def _profile_url(raw: dict[str, Any]) -> str:
    return _text(profile_online_identity.stable_creator_identity(raw).get("profile_url"))


def _looks_like_video_url(value: Any) -> bool:
    from app.domains.kol.search_platform_policy import STRICT_DISCOVERY_PLATFORMS
    for platform in STRICT_DISCOVERY_PLATFORMS:
        if profile_online_identity.is_platform_video_url(value, platform=platform):
            return True
    return False


def _latest_video_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    return profile_online_identity.latest_video_evidence(raw)


def _bounded_content_text(value: Any, *, limit: int) -> str:
    """Flatten provider text shapes without serialising arbitrary objects."""

    parts: list[str] = []

    def collect(raw: Any, *, depth: int = 0) -> None:
        if depth > 3 or sum(len(part) for part in parts) >= limit:
            return
        if isinstance(raw, str):
            text = " ".join(raw.split()).strip()
            if text:
                parts.append(text)
            return
        if isinstance(raw, list):
            for item in raw[:32]:
                collect(item, depth=depth + 1)
            return
        if isinstance(raw, dict):
            for key in ("text", "content", "caption", "description", "body", "value"):
                if key in raw:
                    collect(raw.get(key), depth=depth + 1)

    collect(value)
    output = " ".join(dict.fromkeys(parts))
    return output[: max(0, int(limit))]


def _representative_content_evidence(
    raw: dict[str, Any],
    *,
    latest: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Build bounded in-memory content fields and a non-text availability status."""

    sources: list[dict[str, Any]] = [raw]
    latest_raw = raw.get("latest_real_video")
    if isinstance(latest_raw, dict):
        sources.append(latest_raw)
    for key in ("representative_evidence", "video_evidence", "recent_videos"):
        values = raw.get(key)
        if isinstance(values, list):
            sources.extend(item for item in values[:8] if isinstance(item, dict))

    record: dict[str, str] = {}
    title = _bounded_content_text(
        latest.get("title") or raw.get("sample_title") or raw.get("title"),
        limit=_CONTENT_EVIDENCE_LIMITS["title"],
    )
    if title:
        record["title"] = title
    for field, aliases in _CONTENT_FIELD_ALIASES.items():
        chunks: list[str] = []
        for source in sources:
            for alias in aliases:
                value = _bounded_content_text(
                    source.get(alias),
                    limit=_CONTENT_EVIDENCE_LIMITS[field],
                )
                if value and value not in chunks:
                    chunks.append(value)
        if chunks:
            record[field] = " ".join(chunks)[:_CONTENT_EVIDENCE_LIMITS[field]]

    locator_values = [
        source.get(key)
        for source in sources
        for key in ("content_url", "source_url", "video_url", "post_url")
    ]
    has_content_locator = any(_looks_like_video_url(value) for value in locator_values if value)
    detail_fields = sorted(set(record).intersection(_CONTENT_FIELD_ALIASES))
    return ([record] if record else []), {
        "has_content_locator": has_content_locator,
        "available_fields": sorted(record),
        "detail_fields": detail_fields,
        "detail_text_available": bool(detail_fields),
        "text_exposed": False,
    }


def _safe_query_cell(raw: Any, *, fallback_query: str = "") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    primary_query = _text(raw.get("primary_query") or raw.get("query_cell_query") or fallback_query)[:500]
    cell_id = _text(raw.get("query_cell_id"))[:120]
    if not primary_query:
        return None
    required_groups = raw.get("required_evidence_groups")
    locked_term_groups = targeted_search_contract.rebuild_locked_term_groups_for_cell(raw)
    return {
        "query_cell_id": cell_id or "legacy_single_query",
        "objective": _text(raw.get("objective"))[:80],
        "segment": _text(raw.get("segment") or raw.get("query_cell_segment"))[:120],
        "segment_label": _text(raw.get("segment_label"))[:240],
        "primary_query": primary_query,
        "required_evidence_groups": [
            _text(value)[:80]
            for value in (required_groups if isinstance(required_groups, list) else [])[:8]
            if _text(value)
        ],
        "required_scene_terms": [
            _text(value)[:120]
            for value in (raw.get("required_scene_terms") or [])[:6]
            if _text(value)
        ],
        "scene_match_mode": "all" if raw.get("scene_match_mode") == "all" else "any",
        "required_role_terms": [
            _text(value)[:120]
            for value in (raw.get("required_role_terms") or [])[:4]
            if _text(value)
        ],
        "role_match_mode": "all" if raw.get("role_match_mode") == "all" else "any",
        "product_evidence_required": raw.get("product_evidence_required") is not False,
        "product_evidence_basis": _text(raw.get("product_evidence_basis"))[:40],
        "brand_or_model_required": raw.get("brand_or_model_required") is True,
        "brand_or_model_ranking_weight": raw.get("brand_or_model_ranking_weight"),
        **({"locked_term_groups": locked_term_groups} if locked_term_groups else {}),
    }


def _candidate_query_cells(raw: dict[str, Any], *, query_text: str) -> list[dict[str, Any]]:
    """Read matched cell provenance only from a JSON-list, with legacy fallback."""

    value = raw.get("matched_query_cells")
    cells: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if isinstance(value, list):
        for candidate in value[:_MAX_MATCHED_QUERY_CELLS]:
            cell = _safe_query_cell(candidate)
            if not cell:
                continue
            key = (cell["query_cell_id"], cell["primary_query"].casefold())
            if key not in seen:
                seen.add(key)
                cells.append(cell)
    if cells:
        return cells

    targeted = raw.get("targeted_search") if isinstance(raw.get("targeted_search"), dict) else {}
    fallback = {
        **targeted,
        "query_cell_id": raw.get("query_cell_id") or targeted.get("query_cell_id"),
        "query_cell_segment": raw.get("query_cell_segment") or targeted.get("segment"),
        "query_cell_query": (
            raw.get("query_cell_query")
            or raw.get("discovery_query")
            or raw.get("search_query")
            or targeted.get("primary_query")
            or query_text
        ),
    }
    cell = _safe_query_cell(fallback, fallback_query=query_text)
    return [cell] if cell else []


def _merge_match_evidence(*values: Any) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for value in values:
        for raw in value if isinstance(value, list) else []:
            if not isinstance(raw, dict):
                continue
            field = _text(raw.get("field"))
            term = _text(raw.get("term"))
            source = _text(raw.get("source")) or "server_profile_evidence"
            canonical_term = _text(raw.get("canonical_term"))
            observed_term = _text(raw.get("observed_term"))
            evidence_group = _text(raw.get("evidence_group"))
            key = (field, term, source, canonical_term, observed_term)
            if field and term and key not in seen:
                seen.add(key)
                item = {"field": field, "term": term, "source": source}
                if source == CONTROLLED_ALIAS_EVIDENCE_SOURCE:
                    item.update({
                        "canonical_term": canonical_term,
                        "observed_term": observed_term,
                        "evidence_group": evidence_group,
                        "evidence_relation": _text(raw.get("evidence_relation")),
                    })
                elif source == CAPABILITY_USE_EVIDENCE_SOURCE:
                    item.update({
                        "canonical_term": canonical_term,
                        "observed_term": observed_term,
                        "evidence_group": evidence_group,
                        "evidence_relation": _text(raw.get("evidence_relation")),
                    })
                output.append(item)
    return output[:24]


def _project_online_match_evidence(value: Any) -> list[dict[str, str]]:
    """Project evidence coordinates and query terms, never the source text."""

    projected: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        field = _text(raw.get("field"))
        term = _text(raw.get("term"))[:120]
        source = _text(raw.get("source"))
        if (
            field not in _ONLINE_PUBLIC_EVIDENCE_FIELDS
            or not term
            or _PRIVATE_EVIDENCE_TERM_RE.search(term)
            or (
                source
                and source not in {
                    "server_profile_evidence",
                    CONTROLLED_ALIAS_EVIDENCE_SOURCE,
                    CAPABILITY_USE_EVIDENCE_SOURCE,
                }
            )
        ):
            continue
        evidence = {"field": field, "term": term}
        if source:
            evidence["source"] = source
        if source in {CONTROLLED_ALIAS_EVIDENCE_SOURCE, CAPABILITY_USE_EVIDENCE_SOURCE}:
            canonical_term = _text(raw.get("canonical_term"))[:120]
            observed_term = _text(raw.get("observed_term"))[:120]
            evidence_group = _text(raw.get("evidence_group"))[:80]
            if (
                not canonical_term
                or not observed_term
                or evidence_group not in {
                    "product_use_fit", "segment_use_case", "people_role",
                }
            ):
                continue
            evidence.update({
                "canonical_term": canonical_term,
                "observed_term": observed_term,
                "evidence_group": evidence_group,
                "evidence_relation": _text(raw.get("evidence_relation"))[:80],
            })
        projected.append(evidence)
    return _merge_match_evidence(projected)[:12]


def _candidate_row(raw: dict[str, Any]) -> dict[str, Any]:
    identity = profile_online_identity.stable_creator_identity(raw)
    platform = _text(identity.get("platform"))
    handle = _text(identity.get("handle"))
    display_name = project_public_profile_text(
        raw.get("display_name") or raw.get("channel_name") or raw.get("name"),
        limit=240,
    )
    country = raw.get("country")
    country_source = _text(raw.get("country_source") or raw.get("market_source")).lower()
    # An online provider's bare country label is not equivalent to the pool's
    # legacy declared-country column.  Mark its missing provenance untrusted.
    if country and not country_source:
        country_source = "online_provider_unverified"
    language_evidence = profile_online_facets.adapt_language(raw)
    profile_type_evidence = profile_online_facets.adapt_profile_type(raw)
    followers = next(
        (
            raw.get(field)
            for field in ("followers", "subscriber_count", "follower_count")
            if raw.get(field) not in (None, "")
        ),
        None,
    )
    return {
        "platform": platform,
        "handle": handle,
        "display_name": display_name,
        "profile_url": identity.get("profile_url"),
        "avatar_url": project_public_asset_url(raw.get("avatar_url") or raw.get("avatar")),
        "followers": followers,
        "country": country,
        "country_source": country_source,
        "language": language_evidence.get("value"),
        "language_source": language_evidence.get("source"),
        "profile_type": profile_type_evidence.get("value"),
        "profile_type_source": profile_type_evidence.get("source"),
        "facet_evidence": {
            "language": language_evidence,
            "profile_type": profile_type_evidence,
        },
        "bio": _text(raw.get("bio") or raw.get("description"))[:1000],
        "primary_topic": _text(raw.get("primary_topic"))[:300],
        "content_style": _text(raw.get("content_style"))[:300],
        "secondary_topics_json": raw.get("secondary_topics_json") or [],
        "profile_text": _text(raw.get("profile_text"))[:1000],
        "type_reason": _text(raw.get("type_reason"))[:300],
        # Never feed arbitrary provider blobs into market qualification.
        "raw_platform_data": "{}",
        "identity_projection_passed": identity.get("passed") is True,
    }


def adapt_candidates(
    candidates: list[dict[str, Any]],
    *,
    query_text: str,
    cell_match_evidence: Callable[..., list[dict[str, str]]],
) -> tuple[
    list[dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, list[dict[str, Any]]],
]:
    adapted: list[dict[str, Any]] = []
    rows_by_id: dict[int, dict[str, Any]] = {}
    evidence_by_id: dict[int, dict[str, Any]] = {}
    source_by_id: dict[int, dict[str, Any]] = {}
    cell_inputs_by_id: dict[int, list[dict[str, Any]]] = {}
    for index, raw in enumerate(candidates):
        synthetic_id = 2_000_000_000 + index
        row = _candidate_row(raw)
        native_ids = profile_online_identity.safe_native_identity(raw, platform=row.get("platform"))
        latest = _latest_video_evidence(raw)
        representative, content_status = _representative_content_evidence(raw, latest=latest)
        evidence = {
            "latest_real_video": latest,
            "representative_evidence": representative,
        }
        cells = _candidate_query_cells(raw, query_text=query_text)
        cell_inputs: list[dict[str, Any]] = []
        for cell in cells:
            cell_query = _text(cell.get("primary_query")) or query_text
            cell_inputs.append({
                "query_cell": cell,
                "match_evidence": cell_match_evidence(
                    row,
                    evidence,
                    query_text=cell_query,
                    query_cell=cell,
                ),
            })
        match_evidence = _merge_match_evidence(
            *(entry["match_evidence"] for entry in cell_inputs)
        )
        primary_cell = cells[0] if cells else {}
        candidate_query = _text(primary_cell.get("primary_query")) or query_text
        profile_type = _text(row.get("profile_type")).lower()
        exact_single_sample = _text(raw.get("activation_metrics_scope")) == "exact_query_hit_45d"
        item = {
            "kol_pool_id": synthetic_id,
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "display_name": row.get("display_name"),
            "profile_url": row.get("profile_url"),
            "avatar_url": row.get("avatar_url"),
            "followers": row.get("followers"),
            "avg_views": raw.get("avg_views") if raw.get("avg_views") is not None else (
                None if exact_single_sample else raw.get("views")
            ),
            "avg_likes": raw.get("avg_likes") if raw.get("avg_likes") is not None else (
                None if exact_single_sample else raw.get("likes")
            ),
            "avg_comments": raw.get("avg_comments") if raw.get("avg_comments") is not None else (
                None if exact_single_sample else raw.get("comments")
            ),
            "engagement_rate": raw.get("engagement_rate"),
            # Safe, aggregate provenance for descriptive activation metrics.
            # No raw provider payload or private text is admitted here.
            "avg_views_source": _text(raw.get("avg_views_source"))[:120] or None,
            "avg_views_scope": _text(raw.get("avg_views_scope"))[:80] or None,
            "channel_total_views": raw.get("channel_total_views"),
            "channel_video_count": raw.get("channel_video_count"),
            "channel_lifetime_views": raw.get("channel_lifetime_views"),
            "channel_public_video_count": raw.get("channel_public_video_count"),
            "channel_lifetime_views_per_public_video": raw.get(
                "channel_lifetime_views_per_public_video"
            ),
            "representative_video_views": raw.get("representative_video_views"),
            "representative_video_likes": raw.get("representative_video_likes"),
            "representative_video_comments": raw.get("representative_video_comments"),
            "representative_video_published_at": _text(
                raw.get("representative_video_published_at")
            )[:80] or None,
            "representative_video_duration": _text(
                raw.get("representative_video_duration")
            )[:40] or None,
            "representative_video_duration_seconds": raw.get(
                "representative_video_duration_seconds"
            ),
            "activation_sample_count": raw.get("activation_sample_count"),
            "activation_metrics_source": _text(raw.get("activation_metrics_source"))[:120] or None,
            "activation_metrics_scope": _text(raw.get("activation_metrics_scope"))[:80] or None,
            "activation_evidence_status": _text(raw.get("activation_evidence_status"))[:80] or None,
            **_safe_growth_inputs(raw),
            "views": raw.get("views"),
            "likes": raw.get("likes"),
            "comments": raw.get("comments"),
            "language": row.get("language"),
            "profile_type": row.get("profile_type"),
            "country": row.get("country"),
            **native_ids,
            "facet_evidence": row.get("facet_evidence"),
            "bucket": "reviewer" if profile_type == "reviewer" else "creator",
            "match_evidence": match_evidence,
            "candidate_facets": candidate_facets(row, evidence),
            "display_rank_score": raw.get("display_rank_score") or raw.get("relevance_score") or raw.get("score"),
            "recall_rank_score": raw.get("recall_rank_score") or raw.get("relevance_score") or raw.get("score"),
            "query_cell_id": _text(raw.get("query_cell_id")) or None,
            "query_cell_segment": _text(raw.get("query_cell_segment")) or None,
            "query_cell_query": candidate_query or None,
            "matched_query_cells": cells,
            "content_evidence_status": content_status,
        }
        adapted.append(item)
        rows_by_id[synthetic_id] = row
        evidence_by_id[synthetic_id] = evidence
        source_by_id[synthetic_id] = raw
        cell_inputs_by_id[synthetic_id] = cell_inputs
    return adapted, rows_by_id, evidence_by_id, source_by_id, cell_inputs_by_id


def identity_probe(raw: dict[str, Any]) -> dict[str, Any]:
    row = _candidate_row(raw)
    return {
        **row,
        **profile_online_identity.safe_native_identity(raw, platform=row.get("platform")),
    }


__all__ = [
    "adapt_candidates",
    "identity_probe",
]
