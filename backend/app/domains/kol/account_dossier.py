"""Read-only account dossier aggregation for KOL Pool items.

This endpoint-level service composes the current KOL profile, video evidence,
analysis cache, deep-analysis rows, and URL crawl history into one KOL-layer
payload. It never calls providers, never enqueues workers, and never writes
V6 Fit fields.
"""
from __future__ import annotations

import json
import urllib.parse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn
from app.domains.kol.llm_deep_analysis import get_kol_llm_deep_analysis


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"


from app.core.coerce import _loads, _text


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _youtube_video_id(url: Any) -> str:
    raw = _text(url)
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        return path_parts[0]
    if "youtube.com" in host:
        query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if query_id:
            return query_id
        for marker in ("shorts", "embed", "live"):
            if marker in path_parts:
                idx = path_parts.index(marker)
                if idx + 1 < len(path_parts):
                    return path_parts[idx + 1]
    return ""


def _video_cache_maps(kol_pool_id: int) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT c.id,
               c.target_id,
               c.derive_method,
               c.status,
               c.model,
               c.cost,
               c.result,
               c.created_at,
               c.updated_at
        FROM vkpi_analysis_cache c
        JOIN vkpi_kol_video_evidence e
          ON e.id::text = c.target_id
        WHERE e.kol_pool_id=?
          AND c.target_type='video'
          AND c.derive_method IN (?, ?)
        ORDER BY c.updated_at DESC, c.id DESC
        """,
        (int(kol_pool_id), FINAL_V1_DERIVE_METHOD, QA_DERIVE_METHOD),
    ).fetchall()
    final_by_evidence: dict[int, dict[str, Any]] = {}
    qa_by_evidence: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["result"] = _loads(item.get("result"), {})
        evidence_id = _int(item.get("target_id"))
        if not evidence_id:
            continue
        target = qa_by_evidence if item.get("derive_method") == QA_DERIVE_METHOD else final_by_evidence
        target.setdefault(evidence_id, _jsonable(item))
    return final_by_evidence, qa_by_evidence


def _score_payload(final_result: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(final_result.get("video_analysis_final_v1")) or final_result
    layer6 = _as_dict(payload.get("layer6_flags_and_scores"))
    scores = _as_dict(layer6.get("scores"))
    marketing = scores.get("marketing_value_score", layer6.get("marketing_value_score"))
    content = scores.get("content_quality_score")
    return {
        "marketing_value_score": marketing,
        "content_quality_score": content,
        "channel_value_score": scores.get("channel_value_score"),
        "asset_reuse_score": scores.get("asset_reuse_score"),
        "product_proof_score": scores.get("product_proof_score"),
        "risk_flags": layer6.get("risk_flags"),
        "final_verdict": layer6.get("final_verdict"),
    }


def _qa_payload(qa_result: dict[str, Any]) -> dict[str, Any]:
    direct = _as_dict(qa_result.get("final_v1_keyframe_qa"))
    if direct:
        return direct
    nested = _as_dict(_as_dict(qa_result.get("video_analysis_final_v1_keyframe_qa")).get("final_v1_keyframe_qa"))
    if nested:
        return nested
    return qa_result if any(key in qa_result for key in ("qa_pass", "checks", "issues", "score_correction")) else {}


def _deep_recommendations(primary: dict[str, Any] | None) -> dict[str, Any]:
    dimensions = _as_dict((primary or {}).get("llm_dimensions_11"))
    recommendations = _as_dict(dimensions.get("recommendations"))
    layer5 = _as_dict(dimensions.get("layer5_recommendations"))
    return recommendations or layer5


def _deep_risk(primary: dict[str, Any] | None) -> dict[str, Any]:
    dimensions = _as_dict((primary or {}).get("llm_dimensions_11"))
    risk = _as_dict(dimensions.get("risk"))
    qa = _as_dict(dimensions.get("qa"))
    return {
        "risk_flags": risk.get("risk_flags"),
        "qa_issues": qa.get("issues"),
        "qa_pass": qa.get("qa_pass"),
    }


def _event_items(
    *,
    videos: list[dict[str, Any]],
    crawl_runs: list[dict[str, Any]],
    deep_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for run in crawl_runs:
        events.append(
            {
                "event_type": "url_crawl",
                "occurred_at": run.get("created_at"),
                "status": run.get("status"),
                "source_url": run.get("source_url"),
                "summary": _as_dict(run.get("result_summary_json")),
            }
        )
    for video in videos:
        events.append(
            {
                "event_type": "video_evidence",
                "occurred_at": video.get("publish_date") or video.get("posted_at") or video.get("created_at"),
                "status": "analyzed" if video.get("analysis", {}).get("has_final_v1") else "evidence_only",
                "source_url": video.get("content_url"),
                "summary": {
                    "title": video.get("title"),
                    "view_count": video.get("view_count"),
                    "has_final_v1": video.get("analysis", {}).get("has_final_v1"),
                    "has_qa": video.get("analysis", {}).get("has_qa"),
                },
            }
        )
    for item in deep_items:
        events.append(
            {
                "event_type": "deep_result",
                "occurred_at": item.get("created_at"),
                "status": item.get("status"),
                "source_url": item.get("source_url"),
                "summary": {
                    "llm_v6_fit": item.get("llm_v6_fit"),
                    "source_evidence_id": item.get("source_evidence_id"),
                    "llm_has_qa": item.get("llm_has_qa"),
                },
            }
        )
    return sorted(events, key=lambda item: _text(item.get("occurred_at")), reverse=True)


def get_kol_account_dossier(
    kol_pool_id: int,
    *,
    video_limit: int = 50,
    event_limit: int = 80,
    deep_limit: int = 20,
) -> dict[str, Any]:
    """Return a read-only account dossier assembled from current local state."""

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    kol = dict(row)
    safe_video_limit = max(1, min(int(video_limit or 50), 200))
    safe_event_limit = max(1, min(int(event_limit or 80), 300))
    safe_deep_limit = max(1, min(int(deep_limit or 20), 50))

    video_rows = conn.execute(
        """
        SELECT
          id AS evidence_id,
          id,
          kol_pool_id,
          content_url,
          platform,
          COALESCE(NULLIF(title, ''), NULLIF(video_title, ''), content_url) AS title,
          video_title,
          thumbnail_url,
          view_count,
          like_count,
          comment_count,
          share_count,
          duration_seconds,
          publish_date,
          posted_at,
          channel_id,
          channel_name,
          scrape_status,
          scrape_source,
          scraped_at,
          metrics_scraped_at,
          metrics_source,
          created_at,
          updated_at
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND is_active IS NOT FALSE
          AND COALESCE(evidence_type, 'video')='video'
        ORDER BY COALESCE(publish_date, posted_at, updated_at, created_at) DESC NULLS LAST,
                 COALESCE(view_count, 0) DESC,
                 id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), safe_video_limit),
    ).fetchall()
    final_by_evidence, qa_by_evidence = _video_cache_maps(int(kol_pool_id))
    videos: list[dict[str, Any]] = []
    platforms = Counter()
    for row in video_rows:
        video = _jsonable(dict(row))
        evidence_id = _int(video.get("evidence_id") or video.get("id"))
        final_entry = final_by_evidence.get(evidence_id)
        qa_entry = qa_by_evidence.get(evidence_id)
        final_result = _as_dict((final_entry or {}).get("result"))
        qa_result = _as_dict((qa_entry or {}).get("result"))
        youtube_id = _youtube_video_id(video.get("content_url")) if _text(video.get("platform")).lower() == "youtube" else ""
        video["youtube_video_id"] = youtube_id
        video["best_thumbnail"] = _text(video.get("thumbnail_url")) or (f"https://img.youtube.com/vi/{youtube_id}/hqdefault.jpg" if youtube_id else "")
        video["analysis"] = {
            "has_final_v1": bool(final_entry and final_entry.get("status") == "ready"),
            "has_qa": bool(qa_entry and qa_entry.get("status") == "ready"),
            "final_cache_id": (final_entry or {}).get("id"),
            "qa_cache_id": (qa_entry or {}).get("id"),
            "scores": _score_payload(final_result) if final_result else {},
            "qa": _qa_payload(qa_result) if qa_result else {},
        }
        platforms[_text(video.get("platform")).lower() or "unknown"] += 1
        videos.append(video)

    crawl_rows = conn.execute(
        """
        SELECT id, kol_pool_id, source_url, url_type, mode, status, dry_run,
               result_summary_json, created_at
        FROM vkpi_kol_url_deep_crawl_runs
        WHERE kol_pool_id=?
        ORDER BY created_at DESC, id DESC
        LIMIT 30
        """,
        (int(kol_pool_id),),
    ).fetchall()
    crawl_runs = []
    for row in crawl_rows:
        item = dict(row)
        item["result_summary_json"] = _loads(item.get("result_summary_json"), {})
        crawl_runs.append(_jsonable(item))

    deep = get_kol_llm_deep_analysis(int(kol_pool_id), limit=safe_deep_limit)
    deep_items = _as_list(deep.get("items"))
    primary_deep = _as_dict(deep.get("primary_result"))
    last_crawl_at = crawl_runs[0].get("created_at") if crawl_runs else kol.get("profile_backfilled_at")
    last_video_at = None
    for video in videos:
        candidate = video.get("publish_date") or video.get("posted_at")
        if candidate and (last_video_at is None or _text(candidate) > _text(last_video_at)):
            last_video_at = candidate

    analyzed_count = sum(1 for video in videos if video.get("analysis", {}).get("has_final_v1"))
    qa_count = sum(1 for video in videos if video.get("analysis", {}).get("has_qa"))
    scores = [
        _num(_as_dict(video.get("analysis", {})).get("scores", {}).get("marketing_value_score", {}).get("score")
             if isinstance(_as_dict(video.get("analysis", {})).get("scores", {}).get("marketing_value_score"), dict)
             else _as_dict(video.get("analysis", {})).get("scores", {}).get("marketing_value_score"))
        for video in videos
    ]
    marketing_scores = [score for score in scores if score is not None]
    # C3(2026-06-16):内容质量均分/峰值,供 KOL 详情「精准分析度」头部一眼看懂(原只聚合了投放价值)。
    cq_raw = [
        _num(_as_dict(video.get("analysis", {})).get("scores", {}).get("content_quality_score", {}).get("score")
             if isinstance(_as_dict(video.get("analysis", {})).get("scores", {}).get("content_quality_score"), dict)
             else _as_dict(video.get("analysis", {})).get("scores", {}).get("content_quality_score"))
        for video in videos
    ]
    content_quality_scores = [score for score in cq_raw if score is not None]
    # 一句话总评:取主深析的建议首句(已有 _deep_recommendations,零 LLM 成本)。
    _rec = _deep_recommendations(primary_deep)
    one_line_verdict = ""
    if isinstance(_rec, str):
        one_line_verdict = _rec.strip()
    elif isinstance(_rec, list) and _rec:
        one_line_verdict = str(_rec[0]).strip()
    one_line_verdict = one_line_verdict[:160]
    # 兜底:无建议文案时用分数合成精准一句话(始终有值,符合「精准分析度」)。
    if not one_line_verdict and (content_quality_scores or marketing_scores):
        _parts = []
        if content_quality_scores:
            _parts.append(f"内容质量均分 {round(sum(content_quality_scores) / len(content_quality_scores))}")
        if marketing_scores:
            _parts.append(f"投放价值均分 {round(sum(marketing_scores) / len(marketing_scores))}")
        if analyzed_count:
            _parts.append(f"已深析 {analyzed_count} 条")
        one_line_verdict = " · ".join(_parts)
    events = _event_items(videos=videos, crawl_runs=crawl_runs, deep_items=deep_items)[:safe_event_limit]
    gaps = []
    if not crawl_runs:
        gaps.append("profile_crawl_history_missing")
    if not videos:
        gaps.append("video_evidence_missing")
    if videos and analyzed_count == 0:
        gaps.append("final_v1_analysis_missing")
    if analyzed_count and not qa_count:
        gaps.append("keyframe_qa_missing")
    if deep.get("status") != "ready":
        gaps.append("llm_deep_result_missing")

    return _jsonable(
        {
            "status": "ready",
            "method": "kol_account_dossier_read_v1",
            "kol_pool_id": int(kol_pool_id),
            "profile": {
                "id": kol.get("id"),
                "platform": kol.get("platform"),
                "handle": kol.get("handle"),
                "display_name": kol.get("display_name"),
                "profile_url": kol.get("profile_url"),
                "avatar_url": kol.get("avatar_url"),
                "bio": kol.get("bio"),
                "followers": kol.get("followers"),
                "avg_views": kol.get("avg_views"),
                "engagement_rate": kol.get("engagement_rate"),
                "country_code": kol.get("country_code"),
                "profile_type": kol.get("profile_type"),
                "candidate_kind": kol.get("candidate_kind"),
                "viltrox_fit_score": kol.get("viltrox_fit_score"),
                "viltrox_fit_reason": kol.get("viltrox_fit_reason"),
                "profile_backfilled_at": kol.get("profile_backfilled_at"),
                "last_video_at": kol.get("last_video_at"),
                "updated_at": kol.get("updated_at"),
            },
            "coverage": {
                "video_evidence_count": len(videos),
                "video_evidence_returned": len(videos),
                "analyzed_final_v1_count": analyzed_count,
                "qa_count": qa_count,
                "deep_result_count": deep.get("count") or 0,
                "crawl_run_count": len(crawl_runs),
                "platform_counts": dict(platforms),
                "last_crawl_at": last_crawl_at,
                "last_video_at": last_video_at,
                "marketing_value_score_avg": round(sum(marketing_scores) / len(marketing_scores), 3) if marketing_scores else None,
                "marketing_value_score_max": max(marketing_scores) if marketing_scores else None,
                "content_quality_score_avg": round(sum(content_quality_scores) / len(content_quality_scores), 3) if content_quality_scores else None,
                "content_quality_score_max": max(content_quality_scores) if content_quality_scores else None,
            },
            "judgment": {
                "primary_llm_v6_fit": primary_deep.get("llm_v6_fit"),
                "primary_source_evidence_id": primary_deep.get("source_evidence_id"),
                "recommendations": _deep_recommendations(primary_deep),
                "risk": _deep_risk(primary_deep),
                "one_line_verdict": one_line_verdict,
            },
            "videos": videos,
            "llm_deep_analysis": deep,
            "crawl_history": crawl_runs,
            "events": events,
            "gaps": gaps,
            "diagnostics": {
                "source": "vkpi_kol_pool + vkpi_kol_video_evidence + vkpi_analysis_cache + vkpi_kol_llm_deep_analysis_results + vkpi_kol_url_deep_crawl_runs",
                "provider_calls": False,
                "llm_calls": False,
                "worker_touched": False,
                "write_db": False,
                "viltrox_fit_score_write": False,
            },
        }
    )
