"""Materialize account-level dossier extracts into independent deep results.

This module is pure extraction from local KOL Pool state. It reads the
read-only account dossier aggregate and writes an independent profile_llm row
to vkpi_kol_llm_deep_analysis_results. It never calls providers and never
updates vkpi_kol_pool scoring fields.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.db.connection import get_conn
from app.domains.kol.account_dossier import get_kol_account_dossier


ANALYSIS_KIND = "profile_llm"
METHOD = "kol_account_dossier_extract_v1"
PROVIDER = "local_extract"
JOB_TYPE = "account_dossier_extract"


def _text(value: Any, limit: int = 2000) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
    num = _number(value)
    if num is None:
        return None
    if num < 0:
        num = 0
    if num > 100:
        num = 100
    return Decimal(str(num)).quantize(Decimal("0.001"))


def _confidence(*, analyzed_count: int, qa_count: int, deep_count: int, video_count: int) -> Decimal:
    score = 0.25
    if video_count:
        score += min(0.2, video_count * 0.01)
    if analyzed_count:
        score += min(0.35, analyzed_count * 0.08)
    if qa_count:
        score += min(0.15, qa_count * 0.04)
    if deep_count:
        score += min(0.1, deep_count * 0.02)
    return Decimal(str(min(1.0, score))).quantize(Decimal("0.00001"))


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _video_summary(video: dict[str, Any]) -> dict[str, Any]:
    analysis = _as_dict(video.get("analysis"))
    scores = _as_dict(analysis.get("scores"))
    return {
        "evidence_id": video.get("evidence_id") or video.get("id"),
        "title": video.get("title") or video.get("video_title"),
        "source_url": video.get("content_url"),
        "platform": video.get("platform"),
        "publish_date": video.get("publish_date") or video.get("posted_at"),
        "view_count": video.get("view_count"),
        "like_count": video.get("like_count"),
        "comment_count": video.get("comment_count"),
        "has_final_v1": bool(analysis.get("has_final_v1")),
        "has_qa": bool(analysis.get("has_qa")),
        "marketing_value_score": scores.get("marketing_value_score"),
        "content_quality_score": scores.get("content_quality_score"),
        "risk_flags": scores.get("risk_flags"),
        "final_verdict": scores.get("final_verdict"),
    }


def _extract_text_items(value: Any, limit: int = 8) -> list[str]:
    output: list[str] = []

    def visit(item: Any) -> None:
        if len(output) >= limit:
            return
        if isinstance(item, str):
            text = _text(item, 280)
            if text:
                output.append(text)
            return
        if isinstance(item, dict):
            for key in ("text", "summary", "reason", "rationale", "issue", "name", "label", "value", "final_verdict"):
                if item.get(key):
                    visit(item.get(key))
                    return
            for nested in item.values():
                visit(nested)
                if len(output) >= limit:
                    return
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
                if len(output) >= limit:
                    return

    visit(value)
    deduped: list[str] = []
    for item in output:
        if item not in deduped:
            deduped.append(item)
    return deduped[:limit]


def prepare_account_dossier_extract(kol_pool_id: int) -> dict[str, Any]:
    """Build the profile_llm payload without writing it."""

    dossier = get_kol_account_dossier(int(kol_pool_id), video_limit=120, event_limit=120, deep_limit=50)
    profile = _as_dict(dossier.get("profile"))
    coverage = _as_dict(dossier.get("coverage"))
    judgment = _as_dict(dossier.get("judgment"))
    videos = [_as_dict(item) for item in _as_list(dossier.get("videos"))]
    deep = _as_dict(dossier.get("llm_deep_analysis"))
    deep_items = [_as_dict(item) for item in _as_list(deep.get("items"))]
    events = [_as_dict(item) for item in _as_list(dossier.get("events"))]
    gaps = [str(item) for item in _as_list(dossier.get("gaps")) if item]

    analyzed_count = int(coverage.get("analyzed_final_v1_count") or 0)
    qa_count = int(coverage.get("qa_count") or 0)
    deep_count = int(deep.get("count") or coverage.get("deep_result_count") or 0)
    video_count = int(coverage.get("video_evidence_count") or len(videos))
    if not (video_count or deep_count or events):
        return {
            "status": "skipped",
            "reason": "insufficient_account_dossier_inputs",
            "kol_pool_id": int(kol_pool_id),
        }

    primary_score = _number(judgment.get("primary_llm_v6_fit"))
    score = (
        primary_score
        if primary_score is not None
        else _number(coverage.get("marketing_value_score_avg"))
        or _number(coverage.get("marketing_value_score_max"))
    )
    llm_v6_fit = _decimal_or_none(score)
    source_url = _text(profile.get("profile_url") or profile.get("homepage") or f"kol_pool:{int(kol_pool_id)}")
    analyzed_videos = [video for video in videos if _as_dict(video.get("analysis")).get("has_final_v1")]
    top_videos = sorted(
        videos,
        key=lambda item: (
            bool(_as_dict(item.get("analysis")).get("has_final_v1")),
            _number(item.get("view_count")) or 0,
            _text(item.get("publish_date") or item.get("posted_at")),
        ),
        reverse=True,
    )[:12]
    recommendations = _as_dict(judgment.get("recommendations"))
    risk = _as_dict(judgment.get("risk"))
    strengths = []
    if analyzed_count:
        strengths.append(f"{analyzed_count} 条视频已有 final_v1 深析")
    if qa_count:
        strengths.append(f"{qa_count} 条视频已有关键帧 QA")
    if coverage.get("marketing_value_score_max") is not None:
        strengths.append(f"最高投放价值 {coverage.get('marketing_value_score_max')}")
    strengths.extend(_extract_text_items(recommendations, limit=4))
    weaknesses = list(gaps)
    weaknesses.extend(_extract_text_items(risk.get("risk_flags"), limit=6))
    weaknesses.extend(_extract_text_items(risk.get("qa_issues"), limit=6))
    dimensions = {
        "schema_version": "kol_account_dossier_extract_v1",
        "source": {
            "kol_pool_id": int(kol_pool_id),
            "source_url": source_url,
            "method": METHOD,
            "analysis_kind": ANALYSIS_KIND,
            "provider": PROVIDER,
            "account_dossier_method": dossier.get("method"),
            "last_crawl_at": coverage.get("last_crawl_at"),
            "last_video_at": coverage.get("last_video_at"),
            "llm_calls": False,
            "provider_calls": False,
            "viltrox_fit_score_write": False,
        },
        "profile_snapshot": {
            key: profile.get(key)
            for key in (
                "id",
                "platform",
                "handle",
                "display_name",
                "profile_url",
                "bio",
                "followers",
                "avg_views",
                "engagement_rate",
                "country_code",
                "profile_type",
                "candidate_kind",
                "profile_backfilled_at",
                "last_video_at",
            )
        },
        "coverage": coverage,
        "account_judgment": judgment,
        "strengths": strengths[:10],
        "weaknesses": weaknesses[:12],
        "recommendations": recommendations,
        "risk": risk,
        "videos": {
            "returned_count": len(videos),
            "analyzed_count": analyzed_count,
            "qa_count": qa_count,
            "top": [_video_summary(video) for video in top_videos],
            "analyzed": [_video_summary(video) for video in analyzed_videos[:20]],
        },
        "history": {
            "event_count": len(events),
            "recent_events": events[:20],
            "crawl_history": _as_list(dossier.get("crawl_history"))[:20],
        },
        "gaps": gaps,
        "llm_v6_fit": {
            "score": llm_v6_fit,
            "source": "primary_video_deep_result_or_marketing_score_aggregate",
            "note": "Independent account-level extract; not viltrox_fit_score.",
        },
    }
    confidence = _confidence(
        analyzed_count=analyzed_count,
        qa_count=qa_count,
        deep_count=deep_count,
        video_count=video_count,
    )
    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "source_url": source_url,
        "source_evidence_id": None,
        "analysis_kind": ANALYSIS_KIND,
        "llm_v6_fit": llm_v6_fit,
        "llm_dimensions_11": _json_ready(dimensions),
        "method": METHOD,
        "provider": PROVIDER,
        "confidence": confidence,
        "source_cache_id": None,
        "summary": {
            "video_count": video_count,
            "analyzed_final_v1_count": analyzed_count,
            "qa_count": qa_count,
            "deep_result_count": deep_count,
            "llm_v6_fit": float(llm_v6_fit) if llm_v6_fit is not None else None,
            "confidence": float(confidence),
            "gaps": gaps,
        },
    }


def _fit_snapshot(conn: psycopg.Connection[Any], kol_pool_id: int) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=%s",
            (int(kol_pool_id),),
        )
        row = cur.fetchone() or {}
    return dict(row)


def _existing_result_ids(conn: psycopg.Connection[Any], kol_pool_id: int) -> list[int]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE kol_pool_id=%s
              AND analysis_kind=%s
              AND method=%s
              AND status='ready'
            ORDER BY id DESC
            """,
            (int(kol_pool_id), ANALYSIS_KIND, METHOD),
        )
        return [int(row["id"]) for row in cur.fetchall()]


def upsert_account_dossier_extract(conn: psycopg.Connection[Any], kol_pool_id: int) -> dict[str, Any]:
    """Upsert one independent account-level profile_llm extract row."""

    prepared = prepare_account_dossier_extract(int(kol_pool_id))
    if prepared.get("status") != "ready":
        return prepared
    before = _fit_snapshot(conn, int(kol_pool_id))
    existing_ids = _existing_result_ids(conn, int(kol_pool_id))
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            params = {
                "kol_pool_id": int(kol_pool_id),
                "source_url": prepared["source_url"],
                "source_evidence_id": None,
                "analysis_kind": ANALYSIS_KIND,
                "llm_v6_fit": prepared["llm_v6_fit"],
                "llm_dimensions_11": Jsonb(prepared["llm_dimensions_11"]),
                "method": METHOD,
                "provider": PROVIDER,
                "confidence": prepared["confidence"],
                "source_cache_id": None,
                "status": "ready",
            }
            if existing_ids:
                cur.execute(
                    """
                    UPDATE vkpi_kol_llm_deep_analysis_results
                    SET source_url=%(source_url)s,
                        source_evidence_id=%(source_evidence_id)s,
                        llm_v6_fit=%(llm_v6_fit)s,
                        llm_dimensions_11=%(llm_dimensions_11)s,
                        provider=%(provider)s,
                        confidence=%(confidence)s,
                        source_cache_id=%(source_cache_id)s,
                        status=%(status)s,
                        created_at=NOW()
                    WHERE id=%(id)s
                    RETURNING id
                    """,
                    {**params, "id": existing_ids[0]},
                )
                action = "updated"
                if len(existing_ids) > 1:
                    cur.execute(
                        """
                        UPDATE vkpi_kol_llm_deep_analysis_results
                        SET status='void'
                        WHERE id = ANY(%s)
                        """,
                        (existing_ids[1:],),
                    )
            else:
                cur.execute(
                    """
                    INSERT INTO vkpi_kol_llm_deep_analysis_results (
                        kol_pool_id,
                        source_url,
                        source_evidence_id,
                        analysis_kind,
                        llm_v6_fit,
                        llm_dimensions_11,
                        method,
                        provider,
                        confidence,
                        source_cache_id,
                        status
                    ) VALUES (
                        %(kol_pool_id)s,
                        %(source_url)s,
                        %(source_evidence_id)s,
                        %(analysis_kind)s,
                        %(llm_v6_fit)s,
                        %(llm_dimensions_11)s,
                        %(method)s,
                        %(provider)s,
                        %(confidence)s,
                        %(source_cache_id)s,
                        %(status)s
                    )
                    RETURNING id
                    """,
                    params,
                )
                action = "inserted"
            row = cur.fetchone() or {}
            after = _fit_snapshot(conn, int(kol_pool_id))
            if before != after:
                raise RuntimeError(f"viltrox_fit_score_changed_ids={[int(kol_pool_id)]}; rolled back")
    return {
        "status": "ready",
        "action": action,
        "deep_result_id": int(row["id"]) if row.get("id") is not None else None,
        "kol_pool_id": int(kol_pool_id),
        "analysis_kind": ANALYSIS_KIND,
        "method": METHOD,
        "llm_v6_fit": float(prepared["llm_v6_fit"]) if prepared.get("llm_v6_fit") is not None else None,
        "confidence": float(prepared["confidence"]) if prepared.get("confidence") is not None else None,
        "summary": prepared.get("summary"),
        "viltrox_fit_score_changed_ids": [],
    }


def enqueue_account_dossier_extract_job(
    kol_pool_id: int,
    *,
    body: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Queue one account dossier extract materialization job."""

    body = body or {}
    conn = get_conn()
    row = conn.execute(
        "SELECT id, handle, display_name, profile_url FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    kol = dict(row)
    active = conn.execute(
        """
        SELECT id, job_type, status, payload, created_at, updated_at
        FROM apify_jobs
        WHERE job_type=?
          AND status IN ('queued', 'running')
          AND payload->>'target_type'='kol_pool'
          AND payload->>'target_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (JOB_TYPE, str(int(kol_pool_id))),
    ).fetchone()
    if active:
        item = dict(active)
        return {
            "status": "already_queued" if item.get("status") == "queued" else "already_running",
            "kol_pool_id": int(kol_pool_id),
            "job": item,
            "diagnostics": {
                "provider_calls": False,
                "llm_calls": False,
                "worker_touched": False,
                "write_viltrox_fit_score": False,
            },
        }
    preview = prepare_account_dossier_extract(int(kol_pool_id))
    if preview.get("status") != "ready" and not bool(body.get("force")):
        return {
            "status": "skipped",
            "reason": preview.get("reason") or "account_dossier_extract_not_ready",
            "kol_pool_id": int(kol_pool_id),
            "preview": preview,
            "diagnostics": {
                "provider_calls": False,
                "llm_calls": False,
                "worker_touched": False,
                "write_viltrox_fit_score": False,
            },
        }
    staff = staff or {}
    payload = {
        "target_type": "kol_pool",
        "target_id": str(int(kol_pool_id)),
        "derive_method": METHOD,
        "analysis_kind": ANALYSIS_KIND,
        "source": body.get("source") or "kol_account_dossier_extract",
        "trigger": body.get("trigger") or "manual",
        "query_text": body.get("query_text") or kol.get("handle") or kol.get("display_name") or f"kol_pool:{int(kol_pool_id)}",
        "source_url": body.get("source_url") or kol.get("profile_url") or preview.get("source_url"),
        "triggered_by_user_id": staff.get("user_id") or staff.get("id"),
        "staff_id": staff.get("staff_id"),
        "preview_summary": preview.get("summary"),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id, job_type, status, payload, created_at, updated_at
        """,
        (JOB_TYPE, json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {
        "status": "queued",
        "kol_pool_id": int(kol_pool_id),
        "job": dict(job) if job else None,
        "preview": {
            "status": preview.get("status"),
            "summary": preview.get("summary"),
        },
        "diagnostics": {
            "provider_calls": False,
            "llm_calls": False,
            "worker_touched": False,
            "write_viltrox_fit_score": False,
        },
    }
