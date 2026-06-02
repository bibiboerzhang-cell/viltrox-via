"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from app.core.config import DB_RUNTIME_URL
from app.core.logging import get_logger
from app.db.connection import close_db_runtime_sync, db_connection_sync_scope
from app.domains.costs import budget_guard
from app.platform import llm_gateway
from app.services.media.video_download import download_direct_video_url
from app.services.media.video_keyframes import temporary_keyframes
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from app.services.scraping import apify as apify_scraper


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_CONCURRENCY_LIMIT = max(1, min(2, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "1"))))
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
LLM_TARGET_TYPES = {"video", "contract"}
GEMINI_VIDEO_V2_DERIVE_METHODS = {
    "gemini_video_v2",
    "gemini_video_v2_pro_single",
    "gemini_video_v2_flash_pro_judge",
    "gemini_video_v2_flash_gpt55_judge",
}
GEMINI_VIDEO_DERIVE_METHODS = {"gemini", *GEMINI_VIDEO_V2_DERIVE_METHODS}
WORKER_GEMINI_MODEL = os.environ.get("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
_stop_event = threading.Event()


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _mock_result(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mock": True,
        "note": "placeholder analysis result; no LLM or network call was made",
        "job_id": job["id"],
        "job_type": job.get("job_type"),
        "target_type": payload.get("target_type"),
        "target_id": str(payload.get("target_id")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _derive_method(payload: dict[str, Any]) -> str:
    return str(payload.get("derive_method") or payload.get("analysis_method") or "mock").strip().lower() or "mock"


def _target(payload: dict[str, Any]) -> tuple[str, str]:
    return str(payload.get("target_type") or "").strip(), str(payload.get("target_id") or "").strip()


def _url_host(url: str) -> str:
    try:
        return urlparse(str(url or "")).netloc.lower()
    except Exception:
        return ""


def _platform_from_content_url(url: str) -> str:
    host = _url_host(url)
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "unsupported"


def _resolve_video_media(evidence: dict[str, Any]) -> dict[str, Any]:
    content_url = str(evidence.get("content_url") or "").strip()
    platform = _platform_from_content_url(content_url)
    output = {
        "ok": False,
        "platform": platform,
        "source_url_host": _url_host(content_url),
        "direct_video_url": "",
        "direct_video_url_host": "",
        "reason": "",
        "scraped_ok": False,
    }
    if platform == "unsupported":
        output["reason"] = "unsupported_platform"
        output["status"] = "blocked"
        return output
    if platform == "youtube":
        output["ok"] = True
        output["reason"] = "youtube_direct_url_path"
        output["status"] = "ready"
        return output
    if not os.environ.get("APIFY_TOKEN", "").strip():
        output["reason"] = "apify_not_configured"
        output["status"] = "blocked"
        return output
    scraped = asyncio.run(apify_scraper.scrape_with_apify(content_url, platform))
    output["scraped_ok"] = bool(scraped.get("scraped_ok"))
    direct_video_url = str(scraped.get("video_url") or "").strip()
    if not direct_video_url:
        output["reason"] = str(scraped.get("error") or "media_resolve_failed")
        output["status"] = "failed"
        return output
    output.update(
        {
            "ok": True,
            "direct_video_url": direct_video_url,
            "direct_video_url_host": _url_host(direct_video_url),
            "reason": "media_resolved",
            "status": "ready",
        }
    )
    return output


def _advisory_lock(conn: psycopg.Connection[Any], scope: str, key: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS locked", (scope, key))
        row = cur.fetchone() or {}
        return bool(row.get("locked"))


def _advisory_unlock(conn: psycopg.Connection[Any], scope: str, key: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))", (scope, key))


def _acquire_llm_slot(conn: psycopg.Connection[Any]) -> str | None:
    for index in range(LLM_CONCURRENCY_LIMIT):
        slot = str(index)
        if _advisory_lock(conn, "vkpi_analysis_worker_llm_slot", slot):
            return slot
    return None


def _analysis_cache_exists(conn: psycopg.Connection[Any], target_type: str, target_id: str, derive_method: str) -> bool:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT 1
            FROM vkpi_analysis_cache
            WHERE target_type=%s AND target_id=%s AND derive_method=%s AND status='ready'
            LIMIT 1
            """,
            (target_type, target_id, derive_method),
        )
        return cur.fetchone() is not None


def _finish_skipped(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
            )


def _block_job(conn: psycopg.Connection[Any], job_id: int, reason: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"reason": reason, **(detail or {})}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='blocked', last_error=%s, updated_at=NOW() WHERE id=%s",
                (_json(payload)[:2000], job_id),
            )


def _requeue_job(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='queued', last_error=%s, updated_at=NOW() WHERE id=%s",
                (reason[:2000], job_id),
            )


def _llm_budget_preflight(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id}")
    with db_connection_sync_scope():
        return llm_gateway.budget_preflight(
            prompt,
            purpose="vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider="google",
            cost_tag=LLM_BUDGET_SCOPE,
        )


def _google_allowed(preflight: dict[str, Any]) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    reason = str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(google.get("provider_calls_allowed")), reason, float(google.get("estimated_cost_usd") or 0.0)


def _provider_allowed(preflight: dict[str, Any], provider_name: str) -> tuple[bool, str, float]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    provider = next((item for item in providers if item.get("provider") == provider_name), {})
    reason = str(preflight.get("provider_gate_reason") or provider.get("provider_gate_reason") or "provider_calls_blocked")
    return bool(provider.get("provider_calls_allowed")), reason, float(provider.get("estimated_cost_usd") or 0.0)


def _provider_budget_preflight(job: dict[str, Any], payload: dict[str, Any], provider: str) -> dict[str, Any]:
    target_type, target_id = _target(payload)
    prompt = str(payload.get("prompt") or f"{job.get('job_type') or 'analysis'} {target_type}:{target_id} {provider}")
    with db_connection_sync_scope():
        return llm_gateway.budget_preflight(
            prompt,
            purpose="vkpi_analysis_worker",
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            preferred_provider=provider,
            cost_tag=LLM_BUDGET_SCOPE,
        )


def _load_video_evidence(conn: psycopg.Connection[Any], target_id: str) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              e.id,
              e.content_url,
              COALESCE(e.video_title, e.title, '') AS title,
              e.platform,
              e.view_count,
              e.like_count,
              e.comment_count,
              e.share_count,
              e.duration_seconds,
              e.publish_date,
              e.metrics_source,
              e.metrics_scraped_at,
              e.project_id,
              e.kol_pool_id,
              p.project_name,
              p.product_name,
              COALESCE(kp.handle, '') AS creator_handle,
              COALESCE(kp.display_name, '') AS creator_name,
              kp.followers,
              kp.avg_views,
              kp.engagement_rate
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_kol_pool kp ON kp.id = e.kol_pool_id
            LEFT JOIN vkpi_projects p ON p.id = e.project_id
            WHERE e.id = %s
            LIMIT 1
            """,
            (_int_or_none(target_id) or 0,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"video evidence not found: {target_id}")
    if not str(row.get("content_url") or "").strip():
        raise ValueError(f"video evidence has no content_url: {target_id}")
    return dict(row)


def _usage_count(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            parsed = _int_or_none(value)
            return parsed or 0
    return 0


def _gemini_input_cost_usd(model: str, metadata: dict[str, Any], tokens_in: int) -> float:
    model_key = str(model or "").lower()
    details = metadata.get("prompt_tokens_details")
    if not isinstance(details, list):
        details = []
    modality_counts: dict[str, int] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        modality = str(item.get("modality") or "").upper()
        modality_counts[modality] = modality_counts.get(modality, 0) + (_int_or_none(item.get("token_count")) or 0)
    if "gemini-3.1-pro" in model_key:
        rate = 4.0 if tokens_in > 200_000 else 2.0
        return tokens_in * rate / 1_000_000
    if "gemini-3-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.50 + audio * 1.00) / 1_000_000
    if "gemini-2.5-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.30 + audio * 1.00) / 1_000_000
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return tokens_in * float(config.get("input_cents_per_million") or 0) / 100_000_000


def _gemini_output_rate_usd_per_mtok(model: str, tokens_in: int) -> float:
    model_key = str(model or "").lower()
    if "gemini-3.1-pro" in model_key:
        return 18.0 if tokens_in > 200_000 else 12.0
    if "gemini-3-flash" in model_key:
        return 3.0
    if "gemini-2.5-flash" in model_key:
        return 2.50
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return float(config.get("output_cents_per_million") or 0) / 100


def _gemini_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "prompt_token_count", "promptTokenCount")
    tokens_out = _usage_count(metadata, "candidates_token_count", "candidatesTokenCount")
    tokens_out += _usage_count(metadata, "thoughts_token_count", "thoughtsTokenCount")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "")
        cost = _gemini_input_cost_usd(model, metadata, tokens_in)
        cost += tokens_out * _gemini_output_rate_usd_per_mtok(model, tokens_in) / 1_000_000
        return round(max(0.0, cost), 6), "gemini_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _openai_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "input_tokens", "prompt_tokens")
    tokens_out = _usage_count(metadata, "output_tokens", "completion_tokens")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "").lower()
        if "gpt-5.5" in model:
            cost = (tokens_in * 5.0 + tokens_out * 30.0) / 1_000_000
        else:
            config = llm_gateway.PROVIDER_CONFIG.get("openai") or {}
            input_cents = float(config.get("input_cents_per_million") or 0)
            output_cents = float(config.get("output_cents_per_million") or 0)
            cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "openai_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _low_scores(scores: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, (int, float)) and value <= 6:
            output.append({"dimension": key, "score": value})
    return output[:8]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _rate(numerator: Any, denominator: Any) -> float | None:
    top = _int_or_none(numerator)
    bottom = _int_or_none(denominator)
    if top is None or bottom is None or bottom <= 0:
        return None
    return round(top / bottom, 6)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _video_performance_context(evidence: dict[str, Any]) -> dict[str, Any]:
    views = _int_or_none(evidence.get("view_count"))
    return {
        "view_count": views,
        "like_count": _int_or_none(evidence.get("like_count")),
        "comment_count": _int_or_none(evidence.get("comment_count")),
        "share_count": _int_or_none(evidence.get("share_count")),
        "like_rate": _rate(evidence.get("like_count"), views),
        "comment_rate": _rate(evidence.get("comment_count"), views),
        "duration_seconds": _int_or_none(evidence.get("duration_seconds")),
        "publish_date": _iso_or_none(evidence.get("publish_date")),
        "metrics_source": evidence.get("metrics_source"),
        "metrics_scraped_at": _iso_or_none(evidence.get("metrics_scraped_at")),
        "account_baseline": {
            "followers": _int_or_none(evidence.get("followers")),
            "avg_views": _int_or_none(evidence.get("avg_views")),
            "engagement_rate": _float_or_none(evidence.get("engagement_rate")),
        },
        "relative_to_account_baseline_allowed": False,
        "relative_baseline_note": "followers/avg_views are often missing; use absolute performance only.",
    }


def _select_keyframe_requests(layer1: dict[str, Any], limit: int = 6) -> list[dict[str, str]]:
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    candidates = [
        {"timestamp": str(item.get("timestamp") or ""), "reason": str(item.get("what") or "")}
        for item in timeline
        if isinstance(item, dict) and item.get("timestamp")
    ]
    if not candidates:
        return [{"timestamp": ts, "reason": "fallback keyframe"} for ts in ["00:00", "00:15", "00:45", "01:30", "02:30", "04:30"]]
    if len(candidates) <= limit:
        return candidates
    indexes = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
    output: list[dict[str, str]] = []
    seen: set[int] = set()
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        output.append(candidates[index])
    return output[:limit]


def _download_youtube_for_keyframes(url: str, output_dir: str) -> dict[str, Any]:
    output: dict[str, Any] = {"success": False, "path": None, "error": None, "bytes": 0}
    out_tmpl = str(Path(output_dir) / "youtube_keyframes.%(ext)s")
    cmd = [
        "yt-dlp",
        "--quiet",
        "--no-progress",
        "-f",
        "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/best[height<=720]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        out_tmpl,
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        output["error"] = "youtube keyframe download timed out"
        return output
    if proc.returncode != 0:
        output["error"] = (proc.stderr or proc.stdout or "youtube keyframe download failed")[-500:]
        return output
    candidates = sorted(Path(output_dir).glob("youtube_keyframes.*"))
    if not candidates:
        output["error"] = "youtube keyframe download produced no file"
        return output
    video_path = candidates[0]
    if not video_path.exists() or video_path.stat().st_size <= 0:
        output["error"] = "youtube keyframe download produced empty file"
        return output
    output.update({"success": True, "path": str(video_path), "bytes": video_path.stat().st_size})
    return output


@contextmanager
def _gemini_worker_overrides(payload: dict[str, Any]):
    model_override = str(payload.get("gemini_model") or WORKER_GEMINI_MODEL).strip()
    skip_subtitles = _truthy(
        payload.get("skip_subtitles", payload.get("gemini_skip_subtitles", os.environ.get("APIFY_WORKER_GEMINI_SKIP_SUBTITLES")))
    )
    with ExitStack() as stack:
        if skip_subtitles:
            stack.enter_context(patch.object(gemini_video_analyzer, "fetch_youtube_subtitles", lambda *_args, **_kwargs: ""))
        if model_override and getattr(gemini_video_analyzer, "gemini_client", None):
            original_generate = gemini_video_analyzer.gemini_client.models.generate_content

            def _forced_generate_content(*args: Any, **kwargs: Any):
                kwargs["model"] = model_override
                return original_generate(*args, **kwargs)

            stack.enter_context(patch.object(gemini_video_analyzer.gemini_client.models, "generate_content", _forced_generate_content))
        yield model_override


def _shape_gemini_result(
    *,
    job: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> dict[str, Any]:
    if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS:
        v2 = raw.get("video_analysis_v2") if isinstance(raw.get("video_analysis_v2"), dict) else {}
        layer3 = dict(v2.get("layer3_integrated_judgment") or {})
        layer3["performance_metrics"] = _video_performance_context(evidence)
        model_name = str(raw.get("model") or raw.get("method") or "gemini_video")
        segments = raw.get("cost_segments") if isinstance(raw.get("cost_segments"), list) else None
        return {
            "schema_version": "video_analysis_v2",
            "mock": False,
            "analysis_method": derive_method,
            "job_id": job.get("id"),
            "target_type": "video",
            "target_id": str(evidence.get("id")),
            "source": {
                "url": evidence.get("content_url"),
                "title": evidence.get("title"),
                "platform": evidence.get("platform"),
                "creator_handle": evidence.get("creator_handle"),
                "creator_name": evidence.get("creator_name"),
                "project_id": evidence.get("project_id"),
                "project_name": evidence.get("project_name"),
                "product_name": evidence.get("product_name"),
                "kol_pool_id": evidence.get("kol_pool_id"),
            },
            "layer1_visual_content": v2.get("layer1_visual_content") or {},
            "layer2_video_scores": v2.get("layer2_video_scores") or {},
            "layer3_integrated_judgment": layer3,
            "cost": {
                "recorded_cost_usd": cost,
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "segments": segments
                or [
                    {
                        "stage": "single_pass",
                        "provider": "gemini",
                        "model": model_name,
                        "cost_usd": cost,
                    }
                ],
                "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
                "latency_ms": latency_ms,
            },
            "raw_gemini_video": raw,
            "frame_extraction": raw.get("frame_extraction") if isinstance(raw.get("frame_extraction"), dict) else {},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    quality_scores = raw.get("quality_scores") if isinstance(raw.get("quality_scores"), dict) else {}
    return {
        "mock": False,
        "analysis_method": "gemini",
        "job_id": job.get("id"),
        "target_type": "video",
        "target_id": str(evidence.get("id")),
        "source": {
            "url": evidence.get("content_url"),
            "title": evidence.get("title"),
            "platform": evidence.get("platform"),
            "creator_handle": evidence.get("creator_handle"),
            "project_id": evidence.get("project_id"),
            "kol_pool_id": evidence.get("kol_pool_id"),
        },
        "platform_algorithm_rules": {
            "content_genre": raw.get("content_genre"),
            "target_audience": raw.get("target_audience"),
            "hook_analysis": raw.get("hook_analysis"),
            "marketing_potential": raw.get("marketing_potential"),
            "brand_integration_depth": raw.get("brand_integration_depth"),
            "community_value": raw.get("community_value"),
            "quality_scores": quality_scores,
        },
        "weak_performance_reasons": {
            "quality_summary": raw.get("quality_summary"),
            "vertical_quality_notes": raw.get("vertical_quality_notes"),
            "marketing_notes": raw.get("marketing_notes"),
            "tech_floor": raw.get("tech_floor"),
            "low_scores": _low_scores(quality_scores),
        },
        "improvement_suggestions": raw.get("improvements") if isinstance(raw.get("improvements"), list) else [],
        "raw_gemini_video": raw,
        "cost": {
            "recorded_cost_usd": cost,
            "cost_basis": cost_basis,
            "preflight_estimated_cost_usd": preflight_cost,
            "usage_metadata": raw.get("usage_metadata") if isinstance(raw.get("usage_metadata"), dict) else {},
            "latency_ms": latency_ms,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _record_gemini_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="gemini",
            model_name=str(raw.get("model") or raw.get("method") or "gemini_video"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": raw.get("error") or "",
            },
            extra_scopes=["monthly_total", "single_call", "provider:gemini"],
        )


def _record_openai_cost(
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    preflight_cost: float,
) -> dict[str, Any]:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    with db_connection_sync_scope():
        return budget_guard.record_cost(
            scope=LLM_BUDGET_SCOPE,
            cron_task="vkpi_analysis_worker",
            ai_provider="openai",
            model_name=str(raw.get("model") or raw.get("method") or "gpt-5.5"),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            metadata={
                "status": "success" if raw.get("analyzed") else "provider_error",
                "job_id": job.get("id"),
                "target_type": payload.get("target_type"),
                "target_id": str(payload.get("target_id") or ""),
                "cost_basis": cost_basis,
                "preflight_estimated_cost_usd": preflight_cost,
                "latency_ms": latency_ms,
                "triggered_by_user_id": triggered_by,
                "error": raw.get("error") or "",
            },
            extra_scopes=["monthly_total", "single_call", "provider:openai"],
        )


def _write_gemini_cache(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    raw: dict[str, Any],
    cost: float,
    cost_basis: str,
    preflight_cost: float,
    latency_ms: int,
    derive_method: str,
) -> None:
    target_type, target_id = _target(payload)
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                """,
                (
                    target_type,
                    target_id,
                    str(raw.get("model") or raw.get("method") or "gemini_video"),
                    derive_method,
                    _json(shaped),
                    cost,
                    _int_or_none(triggered_by),
                ),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _process_gemini_video_flash_pro_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_pro_judge currently supports YouTube only")
    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": "gemini-3-flash-preview"}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = "gemini-3-flash-preview"
        visual_raw["method"] = "gemini_direct_gemini-3-flash-preview"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    keyframe_requests = _select_keyframe_requests(layer1, limit=6)
    with tempfile.TemporaryDirectory(prefix="vkpi-scheme2-video-") as tmpdir:
        download = _download_youtube_for_keyframes(str(evidence.get("content_url") or ""), tmpdir)
        if not download.get("success") or not download.get("path"):
            raise RuntimeError(f"youtube_keyframe_download_failed: {download.get('error')}")
        with temporary_keyframes(str(download["path"]), keyframe_requests) as frames:
            if not frames:
                raise RuntimeError("keyframe extraction produced no frames")
            judgment_raw = asyncio.run(
                gemini_video_analyzer.analyze_v2_judgment_with_keyframes(
                    layer1_visual_content=layer1,
                    keyframes=frames,
                    title=str(evidence.get("title") or ""),
                    performance_context=performance,
                    model_name="gemini-3.1-pro-preview",
                )
            )
            frame_meta = [
                {"timestamp": frame.get("timestamp"), "reason": frame.get("reason")}
                for frame in frames
            ]
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _gemini_cost(judgment_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"Gemini keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_pro_judge",
        "model": "gemini-3-flash-preview+gemini-3.1-pro-preview",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": "gemini-3-flash-preview",
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "gemini",
                "model": "gemini-3.1-pro-preview",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_pro_judge",
    )


def _process_gemini_video_flash_gpt55_judge(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    evidence: dict[str, Any],
    preflight_cost: float,
) -> None:
    if _platform_from_content_url(str(evidence.get("content_url") or "")) != "youtube":
        raise RuntimeError("gemini_video_v2_flash_gpt55_judge currently supports YouTube only")
    openai_preflight = _provider_budget_preflight(job, payload, "openai")
    openai_allowed, openai_reason, openai_estimated_cost = _provider_allowed(openai_preflight, "openai")
    if not openai_allowed:
        _block_job(
            conn,
            int(job["id"]),
            openai_reason,
            {"estimated_cost_usd": openai_estimated_cost, "budget_scope": LLM_BUDGET_SCOPE, "provider": "openai"},
        )
        return

    started = time.monotonic()
    performance = _video_performance_context(evidence)
    visual_payload = {**payload, "gemini_model": "gemini-3-flash-preview"}
    with _gemini_worker_overrides(visual_payload):
        visual_raw = asyncio.run(
            gemini_video_analyzer.analyze_youtube_with_gemini(
                str(evidence.get("content_url") or ""),
                str(evidence.get("title") or ""),
                str(evidence.get("creator_handle") or ""),
                schema_version="v2",
                performance_context=performance,
            )
        )
    if visual_raw.get("analyzed"):
        visual_raw["model"] = "gemini-3-flash-preview"
        visual_raw["method"] = "gemini_direct_gemini-3-flash-preview"
    visual_cost, visual_basis, visual_tokens_in, visual_tokens_out = _gemini_cost(visual_raw, preflight_cost)
    _record_gemini_cost(
        job=job,
        payload=payload,
        raw=visual_raw,
        cost=visual_cost,
        cost_basis=visual_basis,
        tokens_in=visual_tokens_in,
        tokens_out=visual_tokens_out,
        latency_ms=0,
        preflight_cost=preflight_cost,
    )
    if not visual_raw.get("analyzed"):
        raise RuntimeError(f"Gemini visual pass failed: {visual_raw.get('error') or 'not_analyzed'}")

    v2 = visual_raw.get("video_analysis_v2") if isinstance(visual_raw.get("video_analysis_v2"), dict) else {}
    layer1 = v2.get("layer1_visual_content") if isinstance(v2.get("layer1_visual_content"), dict) else {}
    keyframe_requests = _select_keyframe_requests(layer1, limit=6)
    with tempfile.TemporaryDirectory(prefix="vkpi-scheme3a-video-") as tmpdir:
        download = _download_youtube_for_keyframes(str(evidence.get("content_url") or ""), tmpdir)
        if not download.get("success") or not download.get("path"):
            raise RuntimeError(f"youtube_keyframe_download_failed: {download.get('error')}")
        with temporary_keyframes(str(download["path"]), keyframe_requests) as frames:
            if not frames:
                raise RuntimeError("keyframe extraction produced no frames")
            judgment_raw = asyncio.run(
                gemini_video_analyzer.analyze_v2_judgment_with_openai_keyframes(
                    layer1_visual_content=layer1,
                    keyframes=frames,
                    title=str(evidence.get("title") or ""),
                    performance_context=performance,
                    model_name="gpt-5.5",
                )
            )
            frame_meta = [
                {"timestamp": frame.get("timestamp"), "reason": frame.get("reason")}
                for frame in frames
            ]
    judgment_cost, judgment_basis, judgment_tokens_in, judgment_tokens_out = _openai_cost(judgment_raw, openai_estimated_cost)
    _record_openai_cost(
        job=job,
        payload=payload,
        raw=judgment_raw,
        cost=judgment_cost,
        cost_basis=judgment_basis,
        tokens_in=judgment_tokens_in,
        tokens_out=judgment_tokens_out,
        latency_ms=0,
        preflight_cost=openai_estimated_cost,
    )
    if not judgment_raw.get("analyzed"):
        raise RuntimeError(f"OpenAI keyframe judgment failed: {judgment_raw.get('error') or 'not_analyzed'}")

    latency_ms = int((time.monotonic() - started) * 1000)
    total_cost = round(visual_cost + judgment_cost, 6)
    raw = {
        **judgment_raw,
        "method": "gemini_flash_gpt55_judge",
        "model": "gemini-3-flash-preview+gpt-5.5",
        "visual_pass": visual_raw,
        "cost_segments": [
            {
                "stage": "visual_video_pass",
                "provider": "gemini",
                "model": "gemini-3-flash-preview",
                "cost_usd": visual_cost,
                "cost_basis": visual_basis,
                "usage_metadata": visual_raw.get("usage_metadata") if isinstance(visual_raw.get("usage_metadata"), dict) else {},
            },
            {
                "stage": "judgment_pass",
                "provider": "openai",
                "model": "gpt-5.5",
                "cost_usd": judgment_cost,
                "cost_basis": judgment_basis,
                "usage_metadata": judgment_raw.get("usage_metadata") if isinstance(judgment_raw.get("usage_metadata"), dict) else {},
            },
        ],
        "frame_extraction": {
            "requested": keyframe_requests,
            "extracted_count": len(frame_meta),
            "frames": frame_meta,
            "download_bytes": int(download.get("bytes") or 0),
            "temporary_files_cleaned": True,
        },
    }
    _write_gemini_cache(
        conn,
        job=job,
        payload=payload,
        evidence=evidence,
        raw=raw,
        cost=total_cost,
        cost_basis="gemini_openai_usage_metadata_segmented_model_rate",
        preflight_cost=preflight_cost + openai_estimated_cost,
        latency_ms=latency_ms,
        derive_method="gemini_video_v2_flash_gpt55_judge",
    )


def _process_gemini_video(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    preflight_cost: float,
) -> None:
    target_type, target_id = _target(payload)
    derive_method = _derive_method(payload)
    if target_type != "video":
        _block_job(conn, int(job["id"]), "unsupported_gemini_target_type", {"target_type": target_type})
        return
    evidence = _load_video_evidence(conn, target_id)
    platform = _platform_from_content_url(str(evidence.get("content_url") or ""))
    if platform == "unsupported":
        _block_job(conn, int(job["id"]), "unsupported_platform", {"source_url_host": _url_host(str(evidence.get("content_url") or ""))})
        return
    if platform in {"instagram", "tiktok"} and derive_method not in GEMINI_VIDEO_V2_DERIVE_METHODS:
        _block_job(conn, int(job["id"]), "unsupported_media_derive_method", {"platform": platform, "derive_method": derive_method})
        return
    if derive_method == "gemini_video_v2_flash_pro_judge":
        _process_gemini_video_flash_pro_judge(conn, job, payload, evidence, preflight_cost)
        return
    if derive_method == "gemini_video_v2_flash_gpt55_judge":
        _process_gemini_video_flash_gpt55_judge(conn, job, payload, evidence, preflight_cost)
        return
    logger.info(
        "apify_jobs gemini video start | job_id=%s target_id=%s url=%s",
        job.get("id"),
        target_id,
        str(evidence.get("content_url") or "")[:120],
    )
    started = time.monotonic()
    with _gemini_worker_overrides(payload) as model_override:
        if platform in {"instagram", "tiktok"}:
            resolved = _resolve_video_media(evidence)
            if str(resolved.get("status") or "") == "blocked":
                _block_job(conn, int(job["id"]), str(resolved.get("reason") or "media_resolve_blocked"), resolved)
                return
            if not resolved.get("ok"):
                raise RuntimeError(f"media_resolve_failed: {resolved.get('reason') or platform}")
            with tempfile.TemporaryDirectory(prefix="vkpi-analysis-video-") as tmpdir:
                download = download_direct_video_url(
                    str(resolved.get("direct_video_url") or ""),
                    tmpdir,
                    referer=str(evidence.get("content_url") or ""),
                )
                if not download.get("success") or not download.get("path"):
                    raise RuntimeError(f"direct_video_download_failed: {download.get('error') or platform}")
                raw = asyncio.run(
                    gemini_video_analyzer.analyze_local_video_with_gemini(
                        str(download["path"]),
                        str(evidence.get("title") or ""),
                        str(evidence.get("creator_handle") or ""),
                        schema_version="v2",
                        performance_context=_video_performance_context(evidence),
                    )
                )
        else:
            raw = asyncio.run(
                gemini_video_analyzer.analyze_youtube_with_gemini(
                    str(evidence.get("content_url") or ""),
                    str(evidence.get("title") or ""),
                    str(evidence.get("creator_handle") or ""),
                    schema_version="v2" if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS else "legacy",
                    performance_context=_video_performance_context(evidence) if derive_method in GEMINI_VIDEO_V2_DERIVE_METHODS else None,
                )
            )
    if model_override and raw.get("analyzed"):
        raw["model"] = model_override
        method = str(raw.get("method") or "")
        if method.startswith("gemini_direct_"):
            raw["method"] = f"gemini_direct_{model_override}"
        elif method.startswith("gemini_fileapi_"):
            raw["method"] = f"gemini_fileapi_{model_override}"
        elif method.startswith("gemini_local_fileapi_"):
            raw["method"] = f"gemini_local_fileapi_{model_override}"
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "apify_jobs gemini video returned | job_id=%s analyzed=%s method=%s latency_ms=%s",
        job.get("id"),
        bool(raw.get("analyzed")),
        raw.get("method"),
        latency_ms,
    )
    cost, cost_basis, tokens_in, tokens_out = _gemini_cost(raw, preflight_cost)
    ledger = _record_gemini_cost(
        job=job,
        payload=payload,
        raw=raw,
        cost=cost,
        cost_basis=cost_basis,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        preflight_cost=preflight_cost,
    )
    if not raw.get("analyzed"):
        raise RuntimeError(f"Gemini video analysis failed: {raw.get('error') or 'not_analyzed'}")
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = _int_or_none(triggered_by)
    shaped = _shape_gemini_result(
        job=job,
        evidence=evidence,
        raw={**raw, "ledger": ledger},
        cost=cost,
        cost_basis=cost_basis,
        preflight_cost=preflight_cost,
        latency_ms=latency_ms,
        derive_method=derive_method,
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                """,
                (
                    target_type,
                    target_id,
                    str(raw.get("model") or raw.get("method") or "gemini_video"),
                    derive_method,
                    _json(shaped),
                    cost,
                    triggered_by_user_id,
                ),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id, job_type, payload
                FROM apify_jobs
                WHERE status = 'queued'
                ORDER BY created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                "UPDATE apify_jobs SET status='running', updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )
            return dict(job)


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    target_type, target_id = _target(payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    derive_method = _derive_method(payload)
    if derive_method != "mock":
        if target_type not in LLM_TARGET_TYPES:
            _block_job(conn, int(job["id"]), "unsupported_llm_target_type", {"target_type": target_type})
            return
        target_lock = f"{target_type}:{target_id}:{derive_method}"
        if not _advisory_lock(conn, "vkpi_analysis_worker_target", target_lock):
            _requeue_job(conn, int(job["id"]), "analysis target already in progress")
            return
        slot = _acquire_llm_slot(conn)
        try:
            if slot is None:
                _requeue_job(conn, int(job["id"]), "llm concurrency limit reached")
                return
            if _analysis_cache_exists(conn, target_type, target_id, derive_method):
                _finish_skipped(conn, int(job["id"]), "skipped_existing_analysis_cache")
                return
            preflight = _llm_budget_preflight(job, payload)
            allowed, reason, estimated_cost = _google_allowed(preflight)
            if not allowed:
                _block_job(conn, int(job["id"]), reason, {"estimated_cost_usd": estimated_cost, "budget_scope": LLM_BUDGET_SCOPE})
                return
            if derive_method in GEMINI_VIDEO_DERIVE_METHODS:
                _process_gemini_video(conn, job, payload, estimated_cost)
                return
            _block_job(conn, int(job["id"]), "unsupported_llm_derive_method", {"derive_method": derive_method})
            return
        finally:
            if slot is not None:
                _advisory_unlock(conn, "vkpi_analysis_worker_llm_slot", slot)
            _advisory_unlock(conn, "vkpi_analysis_worker_target", target_lock)
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = int(triggered_by) if triggered_by not in (None, "") else None
    result = _mock_result(job, payload)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_analysis_cache (
                  target_type, target_id, model, derive_method, result, cost,
                  status, triggered_by_user_id, created_at, updated_at
                )
                VALUES (%s, %s, 'mock', 'mock', %s::jsonb, 0, 'ready', %s, NOW(), NOW())
                ON CONFLICT (target_type, target_id, derive_method)
                DO UPDATE SET
                  model = EXCLUDED.model,
                  result = EXCLUDED.result,
                  cost = EXCLUDED.cost,
                  status = 'ready',
                  triggered_by_user_id = EXCLUDED.triggered_by_user_id,
                  updated_at = NOW()
                """,
                (target_type, target_id, _json(result), triggered_by_user_id),
            )
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (job["id"],),
            )


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='failed', attempts=attempts+1, last_error=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (message, job_id),
            )


def run_worker() -> None:
    if not DB_RUNTIME_URL:
        raise RuntimeError("DATABASE_URL is required for apify_jobs worker")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info("apify_jobs mock worker started | poll_seconds=%s", POLL_SECONDS)
    try:
        with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
            while not _stop_event.is_set():
                job = _claim_job(conn)
                if not job:
                    _stop_event.wait(POLL_SECONDS)
                    continue
                try:
                    _process_job(conn, job)
                    logger.info("apify_jobs mock job done | id=%s", job["id"])
                except Exception as exc:
                    logger.exception("apify_jobs mock job failed | id=%s", job.get("id"))
                    _fail_job(conn, int(job["id"]), exc)
    finally:
        close_db_runtime_sync()
        logger.info("apify_jobs mock worker stopped")


if __name__ == "__main__":
    run_worker()
