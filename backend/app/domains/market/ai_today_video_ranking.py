"""Pure ranking runtime for AI Today external-video evidence."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable


_ANALYSIS_KEYS = (
    "content_summary",
    "content_topic",
    "why_compelling",
    "marketing_notes",
    "marketing_potential",
    "viltrox_detected",
)


@dataclass(frozen=True)
class RankedVideoCandidate:
    score: float
    row: dict[str, Any]
    analysis: dict[str, str]


def _brief_text(content: dict[str, Any]) -> str:
    return " ".join(
        [str(content.get("headline") or "")]
        + [str(value) for value in (content.get("shooting_plans") or [])]
        + [str(value) for value in (content.get("hot_topics") or [])]
    ).lower()


def _desired_terms(
    content: dict[str, Any],
    topic_terms: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...],
) -> set[str]:
    brief_text = _brief_text(content)
    desired: set[str] = set()
    for triggers, terms in topic_terms:
        if any(trigger in brief_text for trigger in triggers):
            desired.update(terms)
    if not desired:
        desired.update(
            ("lens", "camera", "photography", "video", "镜头", "摄影")
        )
    return desired


def _candidate_pool(
    rows: list[dict[str, Any]],
    *,
    max_recommended_videos: int,
    video_content_origin: Callable[[dict[str, Any]], str],
) -> list[dict[str, Any]]:
    external_rows = [
        row for row in rows if video_content_origin(row) == "external"
    ]
    media_rows = [
        row
        for row in external_rows
        if row.get("cached_thumbnail_url")
        or row.get("thumbnail_url")
        or row.get("cached_video_url")
    ]
    return (
        media_rows
        if len(media_rows) >= max_recommended_videos
        else external_rows
    )


def _analysis_fields(
    row: dict[str, Any],
    analysis_value: Callable[[Any, str], str],
) -> dict[str, str]:
    return {
        key: analysis_value(row.get("analysis_result"), key)
        for key in _ANALYSIS_KEYS
    }


def _match_terms(
    row: dict[str, Any],
    analysis: dict[str, str],
    desired: set[str],
) -> list[str]:
    searchable = " ".join(
        str(value or "")
        for value in (
            row.get("title"),
            row.get("channel_name"),
            row.get("handle"),
            *analysis.values(),
        )
    ).lower()
    return sorted(
        term for term in desired if term and term in searchable
    )


def _media_bonus(row: dict[str, Any]) -> int:
    platform_name = str(row.get("platform") or "").lower()
    if row.get("cached_video_url"):
        return 40
    if row.get("cached_thumbnail_url"):
        return 32
    if row.get("thumbnail_url") and "youtube" in platform_name:
        return 22
    if row.get("thumbnail_url"):
        return 6
    return 0


def _freshness_penalty(
    row: dict[str, Any],
    *,
    datetime_type: Any,
    timezone_value: Any,
) -> float:
    published_ref = row.get("publish_date") or row.get("posted_at")
    if published_ref is None:
        return 0.0
    try:
        published_dt = (
            datetime_type.fromisoformat(
                published_ref.replace("Z", "+00:00")
            )
            if isinstance(published_ref, str)
            else published_ref
        )
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(tzinfo=timezone_value.utc)
        age_days = max(
            0.0,
            (
                datetime_type.now(timezone_value.utc) - published_dt
            ).total_seconds()
            / 86400,
        )
        return min(20.0, age_days / 9.0)
    except (TypeError, ValueError):
        return 0.0


def _score_candidate(
    row: dict[str, Any],
    *,
    desired: set[str],
    analysis_value: Callable[[Any, str], str],
    datetime_type: Any,
    timezone_value: Any,
) -> RankedVideoCandidate:
    analysis = _analysis_fields(row, analysis_value)
    matches = _match_terms(row, analysis, desired)
    views = max(0, int(row.get("view_count") or 0))
    fit = float(
        row.get("llm_v6_fit") or row.get("viltrox_fit_score") or 0
    )
    score = (
        len(matches) * 14
        + (12 if row.get("deep_result_id") else 0)
        + (8 if row.get("analysis_cache_id") else 0)
        + _media_bonus(row)
        + (
            8
            if analysis["viltrox_detected"].lower()
            not in ("", "false", "none", "null", "0")
            else 0
        )
        + min(16, math.log10(views + 1) * 3)
        + min(10, max(0, fit) / 10)
        - _freshness_penalty(
            row,
            datetime_type=datetime_type,
            timezone_value=timezone_value,
        )
    )
    return RankedVideoCandidate(
        score=score,
        row=row,
        analysis=analysis | {"matches": " / ".join(matches[:3])},
    )


def _sorted_candidates(
    rows: list[dict[str, Any]],
    *,
    desired: set[str],
    analysis_value: Callable[[Any, str], str],
    datetime_type: Any,
    timezone_value: Any,
) -> list[RankedVideoCandidate]:
    scored = [
        _score_candidate(
            row,
            desired=desired,
            analysis_value=analysis_value,
            datetime_type=datetime_type,
            timezone_value=timezone_value,
        )
        for row in rows
    ]
    return sorted(
        scored,
        key=lambda item: (
            item.score,
            int(item.row.get("view_count") or 0),
        ),
        reverse=True,
    )


def _creator_key(row: dict[str, Any]) -> str:
    return str(
        row.get("kol_pool_id")
        or row.get("handle")
        or row.get("channel_name")
        or ""
    )


def _thumbnail_url(
    row: dict[str, Any],
    *,
    platform_video_id: str,
) -> str:
    thumbnail_url = str(
        row.get("cached_thumbnail_url") or row.get("thumbnail_url") or ""
    )
    if (
        not thumbnail_url
        and platform_video_id
        and "youtube" in str(row.get("platform") or "").lower()
    ):
        return (
            f"https://img.youtube.com/vi/{platform_video_id}/hqdefault.jpg"
        )
    return thumbnail_url


def _recommendation_reason(
    row: dict[str, Any],
    analysis: dict[str, str],
    fit: Any,
) -> str:
    reason = (
        analysis.get("marketing_notes")
        or analysis.get("why_compelling")
        or analysis.get("content_summary")
    )
    if reason:
        return str(reason)
    facts = ["已完成视频深析"]
    if fit is not None:
        facts.append(f"Fit {float(fit):.1f}")
    if row.get("view_count") is not None:
        facts.append(f"播放 {int(row.get('view_count') or 0):,}")
    return " · ".join(facts)


def _playback_source(row: dict[str, Any], playback_url: str) -> str:
    if not playback_url:
        return ""
    storage_backend = str(
        row.get("video_storage_backend") or ""
    ).strip().lower()
    if storage_backend == "r2" or str(row.get("video_r2_key") or "").strip():
        return "r2"
    if playback_url.startswith("/api/vkpi-media/"):
        return "local_cache"
    return "media_cache"


def _source_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "table": "vkpi_kol_video_evidence",
            "id": row.get("evidence_id"),
        },
        *(
            [
                {
                    "table": "vkpi_analysis_cache",
                    "id": row.get("analysis_cache_id"),
                }
            ]
            if row.get("analysis_cache_id")
            else []
        ),
        *(
            [
                {
                    "table": "vkpi_kol_llm_deep_analysis_results",
                    "id": row.get("deep_result_id"),
                }
            ]
            if row.get("deep_result_id")
            else []
        ),
    ]


def _project_candidate(
    candidate: RankedVideoCandidate,
    *,
    platform_video_id_for: Callable[[Any, Any], str],
) -> dict[str, Any]:
    row = candidate.row
    analysis = candidate.analysis
    url = str(row.get("content_url") or "").strip()
    fit = (
        row.get("llm_v6_fit")
        if row.get("llm_v6_fit") is not None
        else row.get("viltrox_fit_score")
    )
    platform_video_id = platform_video_id_for(row.get("platform"), url)
    playback_url = str(row.get("cached_video_url") or "")
    return {
        "evidence_id": row.get("evidence_id"),
        "kol_pool_id": row.get("kol_pool_id"),
        "analysis_cache_id": row.get("analysis_cache_id"),
        "deep_result_id": row.get("deep_result_id"),
        "platform": str(row.get("platform") or ""),
        "platform_video_id": platform_video_id,
        "title": str(row.get("title") or "未命名视频")[:240],
        "creator_handle": str(
            row.get("handle") or row.get("channel_name") or ""
        ),
        "creator_name": str(
            row.get("display_name")
            or row.get("channel_name")
            or row.get("handle")
            or ""
        ),
        "content_url": url,
        "source_url": url,
        "source_platform": str(row.get("platform") or "").strip().lower(),
        "content_origin": "external",
        "thumbnail_url": _thumbnail_url(
            row,
            platform_video_id=platform_video_id,
        ),
        "playback_url": playback_url,
        "playback_source": _playback_source(row, playback_url),
        "view_count": row.get("view_count"),
        "like_count": row.get("like_count"),
        "comment_count": row.get("comment_count"),
        "duration_seconds": row.get("duration_seconds"),
        "published_at": row.get("publish_date") or row.get("posted_at"),
        "fit_score": fit,
        "match_terms": [
            part
            for part in str(analysis.get("matches") or "").split(" / ")
            if part
        ],
        "why_recommended": _recommendation_reason(row, analysis, fit)[:500],
        "content_summary": analysis.get("content_summary") or "",
        "content_topic": analysis.get("content_topic") or "",
        "rank_score": round(candidate.score, 2),
        "source_refs": _source_refs(row),
    }


def rank_video_candidates(
    rows: list[dict[str, Any]],
    content: dict[str, Any],
    *,
    max_recommended_videos: int,
    topic_terms: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...],
    video_content_origin: Callable[[dict[str, Any]], str],
    analysis_value: Callable[[Any, str], str],
    platform_video_id_for: Callable[[Any, Any], str],
    datetime_type: Any,
    timezone_value: Any,
) -> list[dict[str, Any]]:
    desired = _desired_terms(content, topic_terms)
    candidates = _sorted_candidates(
        _candidate_pool(
            rows,
            max_recommended_videos=max_recommended_videos,
            video_content_origin=video_content_origin,
        ),
        desired=desired,
        analysis_value=analysis_value,
        datetime_type=datetime_type,
        timezone_value=timezone_value,
    )
    results: list[dict[str, Any]] = []
    used_creators: set[str] = set()
    for candidate in candidates:
        creator_key = _creator_key(candidate.row)
        if creator_key and creator_key in used_creators:
            continue
        url = str(candidate.row.get("content_url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        if creator_key:
            used_creators.add(creator_key)
        results.append(
            _project_candidate(
                candidate,
                platform_video_id_for=platform_video_id_for,
            )
        )
        if len(results) >= max_recommended_videos:
            break
    return results


__all__ = ["rank_video_candidates"]
