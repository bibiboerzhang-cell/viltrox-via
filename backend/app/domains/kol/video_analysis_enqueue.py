"""Single-video final_v1 enqueue helpers for KOL Pool.

This module only writes apify_jobs. It never updates KOL Pool scoring fields.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

from app.db.connection import get_conn
from app.platform import llm_gateway


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "1200"))
ACTIVE_JOB_STATUSES = ("queued", "running", "retrying", "processing")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _platform_from_url(url: str) -> str:
    host = (urlparse(str(url or "")).netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return "unsupported"


def _triggered_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int_or_none(staff.get(key))
        if parsed:
            return parsed
    return None


def _google_budget(preflight: dict[str, Any]) -> dict[str, Any]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    return {
        "allowed": bool(google.get("provider_calls_allowed")),
        "reason": str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked"),
        "estimated_cost_usd": float(google.get("estimated_cost_usd") or 0.0),
        "provider": "google",
        "model": str(google.get("model") or ""),
        "checks": google.get("checks") if isinstance(google.get("checks"), list) else [],
        "preflight": preflight,
    }


def _fit_snapshot(conn: Any, kol_pool_id: int) -> Any:
    row = conn.execute("SELECT viltrox_fit_score FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return dict(row).get("viltrox_fit_score") if row else None


def _load_owned_evidence(conn: Any, *, kol_pool_id: int, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT
            e.id AS evidence_id,
            e.kol_pool_id,
            e.content_url,
            e.platform AS evidence_platform,
            COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
            e.view_count,
            e.duration_seconds,
            COALESCE(kp.handle, kp.display_name, '') AS kol_handle,
            kp.viltrox_fit_score
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
        WHERE e.id=?
          AND e.kol_pool_id=?
          AND e.content_url IS NOT NULL
          AND e.content_url <> ''
          AND e.is_active IS NOT FALSE
        LIMIT 1
        """,
        (int(evidence_id), int(kol_pool_id)),
    ).fetchone()
    return dict(row) if row else None


def _ready_cache(conn: Any, *, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, model, cost, status, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type='video'
          AND target_id=?
          AND derive_method=?
          AND status='ready'
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(evidence_id), FINAL_V1_DERIVE_METHOD),
    ).fetchone()
    return dict(row) if row else None


def _active_job(conn: Any, *, evidence_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE payload->>'target_type'='video'
          AND payload->>'target_id'=?
          AND payload->>'derive_method'=?
          AND status IN ('queued', 'running', 'retrying', 'processing')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(evidence_id), FINAL_V1_DERIVE_METHOD),
    ).fetchone()
    return dict(row) if row else None


def enqueue_final_v1_video_analysis(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue one final_v1 video analysis job after ownership, duplicate, and budget checks."""

    kol_pool_id = int(kol_pool_id)
    evidence_id = int(evidence_id)
    conn = get_conn()
    evidence = _load_owned_evidence(conn, kol_pool_id=kol_pool_id, evidence_id=evidence_id)
    if not evidence:
        raise LookupError("video evidence not found for this KOL")

    platform = _platform_from_url(_text(evidence.get("content_url")))
    if platform == "unsupported":
        return {
            "status": "unsupported_platform",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "provider_calls": False,
            "write_db": False,
            "reason": "unsupported video URL host",
        }

    cache = _ready_cache(conn, evidence_id=evidence_id)
    if cache:
        return {
            "status": "already_analyzed",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "cache": cache,
            "provider_calls": False,
            "write_db": False,
        }

    existing_job = _active_job(conn, evidence_id=evidence_id)
    if existing_job:
        return {
            "status": "already_queued",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "job": existing_job,
            "provider_calls": False,
            "write_db": False,
        }

    prompt = f"final_v1 on_demand video:{evidence_id} {platform}"
    preflight = llm_gateway.budget_preflight(
        prompt,
        purpose="vkpi_analysis_worker",
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        cost_tag=LLM_BUDGET_SCOPE,
    )
    budget = _google_budget(preflight)
    if not budget["allowed"]:
        return {
            "status": "budget_denied",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "budget": budget,
            "provider_calls": False,
            "write_db": False,
        }

    before_fit = _fit_snapshot(conn, kol_pool_id)
    triggered_by_user_id = _triggered_user_id(staff)
    payload = {
        "target_type": "video",
        "target_id": str(evidence_id),
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "platform": platform,
        "platform_by_host": platform,
        "kol_pool_id": kol_pool_id,
        "source": "kol_pool_detail_on_demand",
        "batch": "on_demand",
        "triggered_by_user_id": triggered_by_user_id,
        "prompt": prompt,
        "source_url": evidence.get("content_url"),
        "title": evidence.get("title"),
        "creator_handle": evidence.get("kol_handle"),
    }
    row = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES ('video', ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, created_at, updated_at
        """,
        (json.dumps(payload, ensure_ascii=False, default=str),),
    ).fetchone()
    after_fit = _fit_snapshot(conn, kol_pool_id)
    changed_ids = [kol_pool_id] if before_fit != after_fit else []
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    conn.commit()
    return {
        "status": "queued",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "job": dict(row) if row else {},
        "budget": {key: value for key, value in budget.items() if key != "preflight"},
        "evidence": {
            "platform": platform,
            "title": evidence.get("title"),
            "content_url": evidence.get("content_url"),
            "view_count": evidence.get("view_count"),
            "duration_seconds": evidence.get("duration_seconds"),
        },
        "viltrox_fit_score_changed_ids": changed_ids,
        "provider_calls": False,
        "write_db": True,
        "writes": ["apify_jobs"],
    }
