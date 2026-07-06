"""final_v1 enqueue helpers for KOL Pool.

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
# 2026-07-02:默认 1200 会截断分镜 JSON(Extra data 解析失败占 unknown 失败桶大头),
# 且本地靠 .env 覆盖 4096 而线上 .env 不随部署 → 代码默认直接提到 4096,env 仍可覆盖。
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "4096"))
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
            e.evidence_type,
            e.metadata_json,
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


def _enqueue_final_v1_video_analysis(
    conn: Any,
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
    source: str = "kol_pool_detail_on_demand",
    batch: str = "on_demand",
    commit: bool = True,
) -> dict[str, Any]:
    """Enqueue one final_v1 job after ownership and duplicate checks.

    Budget preflight is retained as telemetry only; it must not block user-triggered analysis.
    """

    kol_pool_id = int(kol_pool_id)
    evidence_id = int(evidence_id)
    evidence = _load_owned_evidence(conn, kol_pool_id=kol_pool_id, evidence_id=evidence_id)
    if not evidence:
        raise LookupError("video evidence not found for this KOL")

    # 识别闸:图文/轮播帖没视频可下,排了必 media_resolve_failed。
    # 这里统一拦下(批量/URL/手动所有入队路径都过这条),不入队、不当失败。缺省/video 放行。
    # 修复:此前 SELECT 没查 evidence_type,闸恒空转(全盘测速 IG /p/ 帖 3/3 白跑实锤);
    # 现补列并加 metadata.media_kind 第二判据(0703 排水同款口径)。
    _etype = _text(evidence.get("evidence_type")).lower()
    _mkind = ""
    try:
        _mkind = _text((json.loads(_text(evidence.get("metadata_json")) or "{}") or {}).get("media_kind")).lower()
    except (TypeError, ValueError):
        _mkind = ""
    _non_video = (_etype and _etype != "video") or (_mkind and _mkind not in ("video", "reel", "clip", "igtv"))
    if _non_video:
        return {
            "status": "skipped_non_video",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "provider_calls": False,
            "write_db": False,
            "reason": f"evidence_type={_etype or '-'} media_kind={_mkind or '-'}(图文/轮播,不跑视频深析)",
        }

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

    before_fit = _fit_snapshot(conn, kol_pool_id)
    triggered_by_user_id = _triggered_user_id(staff)
    payload = {
        "target_type": "video",
        "target_id": str(evidence_id),
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "platform": platform,
        "platform_by_host": platform,
        "kol_pool_id": kol_pool_id,
        "source": source,
        "batch": batch,
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
    if commit:
        conn.commit()
    return {
        "status": "queued",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "job": dict(row) if row else {},
        "budget": {key: value for key, value in budget.items() if key != "preflight"},
        "budget_gate": "record_only",
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


def enqueue_final_v1_video_analysis(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    return _enqueue_final_v1_video_analysis(
        conn,
        kol_pool_id=int(kol_pool_id),
        evidence_id=int(evidence_id),
        staff=staff,
    )


def list_kols_needing_video_analysis(limit: int = 50) -> dict[str, Any]:
    """库内有视频证据、但还没有 ready 深析结果的 KOL,各带一个代表 evidence_id(供批量入队)。
    2026-06-16:为「待分析列表 + 批量入队」提供数据源;只读,不碰 fit/评分。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 50), 200))
    rows = conn.execute(
        """
        SELECT p.id AS kol_pool_id, p.handle, p.platform, p.display_name, p.avatar_url, p.followers,
               (SELECT e.id FROM vkpi_kol_video_evidence e WHERE e.kol_pool_id = p.id ORDER BY e.id DESC LIMIT 1) AS evidence_id,
               (SELECT COUNT(*) FROM vkpi_kol_video_evidence e WHERE e.kol_pool_id = p.id) AS evidence_count
        FROM vkpi_kol_pool p
        WHERE p.duplicate_of_id IS NULL
          AND EXISTS (SELECT 1 FROM vkpi_kol_video_evidence e WHERE e.kol_pool_id = p.id)
          AND NOT EXISTS (
              SELECT 1 FROM vkpi_kol_llm_deep_analysis_results d
              WHERE d.kol_pool_id = p.id AND d.status = 'ready'
          )
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "count": len(items)}


def enqueue_final_v1_video_analysis_batch(
    *,
    items: list[dict[str, Any]],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue multiple final_v1 jobs, one evidence per item, without touching V6 Fit."""

    normalized: list[dict[str, int]] = []
    for item in items or []:
        kol_pool_id = _int_or_none((item or {}).get("kol_pool_id"))
        evidence_id = _int_or_none((item or {}).get("evidence_id"))
        if not kol_pool_id or not evidence_id:
            normalized.append({"kol_pool_id": int(kol_pool_id or 0), "evidence_id": int(evidence_id or 0)})
            continue
        normalized.append({"kol_pool_id": kol_pool_id, "evidence_id": evidence_id})
    if not normalized:
        raise ValueError("items required")

    conn = get_conn()
    results: list[dict[str, Any]] = []
    queued = 0
    skipped = 0
    errors = 0
    for item in normalized:
        kol_pool_id = item.get("kol_pool_id")
        evidence_id = item.get("evidence_id")
        if not kol_pool_id or not evidence_id:
            errors += 1
            results.append({"status": "invalid_item", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id})
            continue
        try:
            result = _enqueue_final_v1_video_analysis(
                conn,
                kol_pool_id=kol_pool_id,
                evidence_id=evidence_id,
                staff=staff,
                source="kol_pool_detail_batch_on_demand",
                batch="on_demand_batch",
                commit=True,
            )
            results.append(result)
            if result.get("status") == "queued":
                queued += 1
            else:
                skipped += 1
        except LookupError as exc:
            errors += 1
            results.append({"status": "not_found", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id, "reason": str(exc)})
        except Exception as exc:
            errors += 1
            results.append({"status": "error", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id, "reason": str(exc)})
    return {
        "status": "completed",
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "requested": len(normalized),
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
        "budget_gate": "record_only",
        "items": results,
        "write_db": queued > 0,
        "writes": ["apify_jobs"] if queued else [],
    }


def list_kol_all_evidence_ids(conn: Any, kol_pool_id: int) -> list[int]:
    """该 KOL 全部活跃视频证据 id(去重、按时间降序)。只读。"""
    rows = conn.execute(
        """
        SELECT e.id AS evidence_id
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id = ?
          AND (e.is_active IS NULL OR e.is_active = TRUE)
        ORDER BY e.id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    out: list[int] = []
    seen: set[int] = set()
    for row in rows:
        eid = _int_or_none(dict(row).get("evidence_id"))
        if eid and eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


# 「KOL深度分析理解」每号最多分析的视频条数(2026-06-16 裁令:全视频→最近20条,控成本+队列)。
KOL_DEEP_ANALYSIS_VIDEO_LIMIT = 20


def enqueue_all_kol_videos(
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None = None,
    limit: int = KOL_DEEP_ANALYSIS_VIDEO_LIMIT,
) -> dict[str, Any]:
    """「KOL深度分析理解」:该 KOL 最近 N 条(默认20)视频证据各入队一条 final_v1,
    供发完后综合评估(账号档案 worker 链路会聚合已分析视频)。已 ready / 在队的自动跳过。
    红线:只写 apify_jobs,零触 viltrox_fit_score。"""
    pool_id = _int_or_none(kol_pool_id)
    if not pool_id:
        raise ValueError("kol_pool_id required")
    conn = get_conn()
    # 取证按 e.id DESC,切最近 N 条(用户裁令:全视频→最近20条)。
    cap = max(1, int(limit or KOL_DEEP_ANALYSIS_VIDEO_LIMIT))
    evidence_ids = list_kol_all_evidence_ids(conn, pool_id)[:cap]
    if not evidence_ids:
        return {
            "status": "no_evidence",
            "kol_pool_id": pool_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "requested": 0,
            "queued": 0,
            "skipped": 0,
            "errors": 0,
            "reason": "该 KOL 暂无视频证据;需先发现/抓取视频(account_deep 模式)再全视频分析。",
            "items": [],
            "write_db": False,
            "writes": [],
        }
    items = [{"kol_pool_id": pool_id, "evidence_id": eid} for eid in evidence_ids]
    result = enqueue_final_v1_video_analysis_batch(items=items, staff=staff)
    result["kol_pool_id"] = pool_id
    result["mode"] = "all_videos"
    result["evidence_total"] = len(evidence_ids)
    return result
