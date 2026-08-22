"""final_v1 enqueue helpers for KOL Pool.

This module only writes apify_jobs. It never updates KOL Pool scoring fields.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.db.connection import get_conn
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.domains.tasks.search_session_lineage import (
    attach_search_session_lineage_to_job,
    with_search_session_lineage,
)
from app.platform import llm_gateway
from app.platform.llm_local_evaluation import (
    LOCAL_EVALUATION_BINDING,
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
    LOCAL_EVALUATION_EXECUTION_CLASS,
    LOCAL_EVALUATION_MODEL,
    issue_local_evaluation_capability,
    redact_local_evaluation_capability,
)
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
PRODUCTION_VIDEO_MODEL = os.environ.get("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
# 2026-07-02:默认 1200 会截断分镜 JSON(Extra data 解析失败占 unknown 失败桶大头),
# 且本地靠 .env 覆盖 4096 而线上 .env 不随部署 → 代码默认直接提到 4096,env 仍可覆盖。
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "4096"))
ACTIVE_JOB_STATUSES = ("queued", "running", "retrying", "processing")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


from app.core.coerce import _text


def _platform_from_url(url: str) -> str:
    try:
        return parse_supported_video_url(url).platform
    except VideoUrlIdentityError:
        return "unsupported"


def _triggered_user_id(staff: dict[str, Any] | None) -> int | None:
    staff = staff or {}
    for key in ("user_id", "id", "staff_id"):
        parsed = _int_or_none(staff.get(key))
        if parsed:
            return parsed
    return None


def _video_analysis_queue_lane(*, batch: str, local_evaluation: bool) -> str:
    """Reserve interactive capacity for explicit single-video requests.

    ``on_demand`` is the default for the single-video endpoint, while every
    other non-empty marker belongs to a batch/background wrapper.  An explicit
    local evaluation is always user-triggered and must not wait behind bulk
    work, even when reached through a URL-flow wrapper with a custom marker.
    """

    marker = str(batch or "").strip().lower()
    if local_evaluation or marker in {"", "on_demand"}:
        return "interactive"
    return "batch"


def _google_budget(preflight: dict[str, Any]) -> dict[str, Any]:
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    google = next((item for item in providers if item.get("provider") == "google"), {})
    return {
        "allowed": bool(google.get("provider_calls_allowed")),
        "reason": str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked"),
        "estimated_cost_usd": float(google.get("estimated_cost_usd") or 0.0),
        "provider": "google",
        "model": str(google.get("model") or ""),
        "model_readiness_status": str(
            preflight.get("model_readiness_status")
            or google.get("model_readiness_status")
            or "not_ready"
        ),
        "checks": google.get("checks") if isinstance(google.get("checks"), list) else [],
        "preflight": preflight,
    }


def _ai_analysis_state(
    state: str,
    *,
    reason: str = "",
    gate_reason: str = "",
    model_readiness_status: str = "",
    provider_calls_allowed: bool = False,
) -> dict[str, Any]:
    """Stable, frontend-safe AI-stage contract for URL/profile/text flows."""

    return {
        "state": str(state or "not_requested"),
        "reason": str(reason or ""),
        "gate_reason": str(gate_reason or ""),
        "model_readiness_status": str(model_readiness_status or "not_ready"),
        "provider_calls_allowed": bool(provider_calls_allowed),
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
            to_jsonb(e.*)->>'media_kind' AS media_kind,
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


def _ready_cache(
    conn: Any,
    *,
    evidence_id: int,
    include_local_evaluation: bool = False,
) -> dict[str, Any] | None:
    methods = (
        (FINAL_V1_DERIVE_METHOD, LOCAL_EVALUATION_CACHE_DERIVE_METHOD)
        if include_local_evaluation
        else (FINAL_V1_DERIVE_METHOD,)
    )
    placeholders = ", ".join("?" for _ in methods)
    row = conn.execute(
        f"""
        SELECT id, model, derive_method, cost, status, result, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type='video'
          AND target_id=?
          AND derive_method IN ({placeholders})
          AND status='ready'
        ORDER BY
          CASE WHEN derive_method=? THEN 0 ELSE 1 END,
          updated_at DESC,
          id DESC
        LIMIT 1
        """,
        (str(evidence_id), *methods, FINAL_V1_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["evaluation_only"] = item.get("derive_method") == LOCAL_EVALUATION_CACHE_DERIVE_METHOD
    item.pop("result", None)
    return item


def _active_job(
    conn: Any,
    *,
    idempotency_key: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, job_type, status, created_at, updated_at
        FROM apify_jobs
        WHERE idempotency_key=?
          AND status IN ('queued', 'running', 'retrying', 'processing')
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (str(idempotency_key),),
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
    search_session_id: int | None = None,
    search_session_item_id: int | None = None,
    parent_job_id: int | None = None,
    local_evaluation: bool = False,
    enforce_target_write: bool = False,
    provider_parent_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enqueue one final_v1 job after ownership and duplicate checks.

    Production readiness is checked before any durable paid-AI row is inserted.
    A blocked optional AI stage returns ``ai_disabled`` while preserving base evidence.
    """

    kol_pool_id = int(kol_pool_id)
    evidence_id = int(evidence_id)
    if local_evaluation and (
        isinstance(staff, dict)
        or enforce_target_write
        or search_session_id
        or search_session_item_id
        or parent_job_id
        or isinstance(provider_parent_payload, dict)
    ):
        from app.platform.llm_local_evaluation import LocalEvaluationCapabilityError

        raise LocalEvaluationCapabilityError("local_evaluation_server_scope_required")
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import assert_target_writable

        assert_target_writable(
            conn,
            kol_pool_id=kol_pool_id,
            staff=staff,
        )
    evidence = _load_owned_evidence(conn, kol_pool_id=kol_pool_id, evidence_id=evidence_id)
    if not evidence:
        raise LookupError("video evidence not found for this KOL")
    target_fence: dict[str, Any] | None = None
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import build_target_fence

        target_fence = build_target_fence(
            conn,
            action="video_analysis",
            kol_pool_id=kol_pool_id,
            staff=staff,
            evidence_ids=[evidence_id],
        )

    # 识别闸:图文/轮播帖没视频可下,排了必 media_resolve_failed。
    # 这里统一拦下(批量/URL/手动所有入队路径都过这条),不入队、不当失败。缺省/video 放行。
    # 修复:此前 SELECT 没查 evidence_type,闸恒空转(全盘测速 IG /p/ 帖 3/3 白跑实锤);
    # 现补列并加 media_kind 第二判据(0703 排水同款口径)。media_kind 列存在双向 schema 漂移
    # (线上有列/本地没有),SELECT 用 to_jsonb(e.*)->>'media_kind' 通吃:有列取值、无列得 NULL 不炸。
    _etype = _text(evidence.get("evidence_type")).lower()
    _mkind = _text(evidence.get("media_kind")).lower()
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
            "ai_analysis": _ai_analysis_state(
                "not_requested",
                reason="non_video_evidence",
            ),
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
            "ai_analysis": _ai_analysis_state(
                "not_requested",
                reason="unsupported_platform",
            ),
        }

    lineage_payload = with_search_session_lineage(
        {},
        search_session_id=search_session_id,
        search_session_item_id=search_session_item_id,
        role="video",
        parent_job_id=parent_job_id,
    )

    cache = _ready_cache(
        conn,
        evidence_id=evidence_id,
        include_local_evaluation=bool(local_evaluation),
    )
    if cache:
        return {
            "status": (
                "already_evaluated"
                if cache.get("evaluation_only")
                else "already_analyzed"
            ),
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "cache": cache,
            "provider_calls": False,
            "write_db": False,
            "ai_analysis": _ai_analysis_state(
                "ready",
                reason="cached_analysis",
                model_readiness_status="ready_cache",
            ),
        }

    prompt = f"final_v1 on_demand video:{evidence_id} {platform}"
    execution_class = (
        LOCAL_EVALUATION_EXECUTION_CLASS if local_evaluation else "production"
    )
    preflight = llm_gateway.budget_preflight(
        prompt,
        purpose="vkpi_analysis_worker",
        max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        model_override=(LOCAL_EVALUATION_MODEL if local_evaluation else PRODUCTION_VIDEO_MODEL),
        model_fallbacks=[],
        execution_class=execution_class,
        cost_tag=LLM_BUDGET_SCOPE,
        require_configured=False,
    )
    budget = _google_budget(preflight)

    # A durable AI job is only useful when the exact model chain is authorized
    # now.  Previously these rows were queued despite a failed production
    # readiness gate, so the worker could only mark them blocked and URL/search
    # sessions remained partial forever.  Existing profile/video evidence is
    # still returned; the optional AI stage terminates honestly as not requested.
    if not budget["allowed"]:
        gate_reason = str(budget.get("reason") or "provider_calls_blocked")
        return {
            "status": "ai_disabled",
            "state": "not_requested",
            "stage": "ai_disabled",
            "terminal": True,
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "reason": "ai_disabled",
            "provider_gate_reason": gate_reason,
            "budget": {key: value for key, value in budget.items() if key != "preflight"},
            "execution_class": execution_class,
            "claim_status": "descriptive_only",
            "model_readiness_status": str(budget.get("model_readiness_status") or "not_ready"),
            "ai_analysis": _ai_analysis_state(
                "not_requested",
                reason="ai_disabled",
                gate_reason=gate_reason,
                model_readiness_status=str(budget.get("model_readiness_status") or "not_ready"),
                provider_calls_allowed=False,
            ),
            "evidence": {
                "platform": platform,
                "title": evidence.get("title"),
                "content_url": evidence.get("content_url"),
                "view_count": evidence.get("view_count"),
                "duration_seconds": evidence.get("duration_seconds"),
            },
            "viltrox_fit_score_changed_ids": [],
            "provider_calls": False,
            "write_db": False,
            "writes": [],
        }

    before_fit = _fit_snapshot(conn, kol_pool_id)
    triggered_by_user_id = _triggered_user_id(staff)
    payload = with_search_session_lineage(
        {
            "queue_lane": _video_analysis_queue_lane(
                batch=batch,
                local_evaluation=bool(local_evaluation),
            ),
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
            "staff_id": _int_or_none(
                (staff or {}).get("id") or (staff or {}).get("staff_id")
            ),
        },
        search_session_id=search_session_id,
        search_session_item_id=search_session_item_id,
        role="video",
        parent_job_id=parent_job_id,
    )
    if target_fence is not None:
        from app.domains.kol.my_kol_paid_action_access import FENCE_KEY

        payload[FENCE_KEY] = target_fence
        snapshots = target_fence.get("evidence")
        if isinstance(snapshots, list) and len(snapshots) == 1:
            payload["source_url"] = snapshots[0].get("normalized_url")
    elif not local_evaluation and (
        isinstance(provider_parent_payload, dict)
        or (search_session_id and isinstance(staff, dict))
    ):
        from app.domains.kol.video_analysis_job_access import authorize_video_analysis_job

        payload = authorize_video_analysis_job(
            conn,
            payload,
            evidence=evidence,
            source_payload=provider_parent_payload,
            staff=staff,
        )
    if local_evaluation:
        payload = {
            **payload,
            "local_evaluation": True,
            "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
            "model_binding": LOCAL_EVALUATION_BINDING,
        }
    from app.domains.kol.video_analysis_job_access import video_analysis_authorization_scope

    idempotency_key = active_job_idempotency_key(
        "video-final-v1-local-evaluation" if local_evaluation else "video-final-v1",
        evidence_id,
        video_analysis_authorization_scope(payload),
    )
    existing_job = _active_job(conn, idempotency_key=idempotency_key)
    if existing_job:
        linked_payload = attach_search_session_lineage_to_job(
            conn,
            existing_job.get("id"),
            lineage_payload,
        )
        if linked_payload and commit:
            conn.commit()
        return {
            "status": "already_queued",
            "kol_pool_id": kol_pool_id,
            "evidence_id": evidence_id,
            "derive_method": FINAL_V1_DERIVE_METHOD,
            "job": redact_local_evaluation_capability(existing_job),
            "provider_calls": False,
            "write_db": bool(linked_payload),
            "lineage_linked": bool(linked_payload),
            "ai_analysis": _ai_analysis_state(
                "queued",
                reason="already_queued",
                gate_reason=str(budget.get("reason") or "provider_calls_allowed"),
                model_readiness_status=str(budget.get("model_readiness_status") or "production_ready"),
                provider_calls_allowed=True,
            ),
        }

    row, inserted = enqueue_active_apify_job(
        conn,
        job_type="video",
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if local_evaluation and inserted:
        # Sign only after PostgreSQL assigned the durable job id.  The update is
        # in the same transaction as the insert, so a worker can never observe
        # the unsigned intermediate row.  Copying this capability to another
        # queue row (even for the same target) fails the worker's job-id check.
        try:
            capability = issue_local_evaluation_capability(
                job_id=int(row.get("id") or 0),
                target_type="video",
                target_id=str(evidence_id),
                derive_method=FINAL_V1_DERIVE_METHOD,
                model_binding=LOCAL_EVALUATION_BINDING,
            )
            payload = {**payload, "_local_evaluation_capability": capability}
            conn.execute(
                "UPDATE apify_jobs SET payload=?::jsonb, updated_at=NOW() WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, default=str), int(row["id"])),
            )
            row = {**row, "payload": payload}
        except Exception:
            # Never leave an unsigned local-evaluation row pending in the
            # caller's connection.  The worker would fail closed, but the
            # operator would also be unable to retry because of idempotency.
            conn.rollback()
            raise
    after_fit = _fit_snapshot(conn, kol_pool_id)
    changed_ids = [kol_pool_id] if before_fit != after_fit else []
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    if commit:
        conn.commit()
    return {
        "status": "queued" if inserted else "already_queued",
        "kol_pool_id": kol_pool_id,
        "evidence_id": evidence_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "job": redact_local_evaluation_capability(row),
        "budget": {key: value for key, value in budget.items() if key != "preflight"},
        "budget_gate": "enforced_at_enqueue",
        "execution_class": execution_class,
        "evaluation_only": bool(local_evaluation),
        "claim_status": "descriptive_only",
        "model_readiness_status": (
            "evaluation_only_not_production_ready"
            if local_evaluation
            else str(budget.get("model_readiness_status") or "production_ready")
        ),
        "ai_analysis": _ai_analysis_state(
            "queued",
            reason="analysis_queued",
            gate_reason=str(budget.get("reason") or "provider_calls_allowed"),
            model_readiness_status=str(budget.get("model_readiness_status") or "production_ready"),
            provider_calls_allowed=True,
        ),
        "evidence": {
            "platform": platform,
            "title": evidence.get("title"),
            "content_url": evidence.get("content_url"),
            "view_count": evidence.get("view_count"),
            "duration_seconds": evidence.get("duration_seconds"),
        },
        "viltrox_fit_score_changed_ids": changed_ids,
        "provider_calls": False,
        "write_db": inserted,
        "writes": ["apify_jobs"] if inserted else [],
    }


def enqueue_final_v1_video_analysis(
    *,
    kol_pool_id: int,
    evidence_id: int,
    staff: dict[str, Any] | None = None,
    local_evaluation: bool = False,
    enforce_target_write: bool = False,
) -> dict[str, Any]:
    conn = get_conn()
    return _enqueue_final_v1_video_analysis(
        conn,
        kol_pool_id=int(kol_pool_id),
        evidence_id=int(evidence_id),
        staff=staff,
        local_evaluation=bool(local_evaluation),
        enforce_target_write=bool(enforce_target_write),
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
    enforce_target_write: bool = False,
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
    ai_disabled = 0
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
                enforce_target_write=bool(enforce_target_write),
            )
            results.append(result)
            if result.get("status") == "queued":
                queued += 1
            else:
                skipped += 1
                if result.get("status") == "ai_disabled":
                    ai_disabled += 1
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
        "ai_disabled": ai_disabled,
        "errors": errors,
        "budget_gate": "enforced_at_enqueue",
        "ai_analysis": _ai_analysis_state(
            "queued" if queued else "not_requested",
            reason="analysis_queued" if queued else "ai_disabled" if ai_disabled else "no_eligible_video",
            provider_calls_allowed=queued > 0,
        ),
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
    enforce_target_write: bool = False,
) -> dict[str, Any]:
    """「KOL深度分析理解」:该 KOL 最近 N 条(默认20)视频证据各入队一条 final_v1,
    供发完后综合评估(账号档案 worker 链路会聚合已分析视频)。已 ready / 在队的自动跳过。
    红线:只写 apify_jobs,零触 viltrox_fit_score。"""
    pool_id = _int_or_none(kol_pool_id)
    if not pool_id:
        raise ValueError("kol_pool_id required")
    conn = get_conn()
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import assert_target_writable

        assert_target_writable(conn, kol_pool_id=pool_id, staff=staff)
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
    result = enqueue_final_v1_video_analysis_batch(
        items=items,
        staff=staff,
        enforce_target_write=bool(enforce_target_write),
    )
    result["kol_pool_id"] = pool_id
    result["mode"] = "all_videos"
    result["evidence_total"] = len(evidence_ids)
    return result


# ── 账号级进度(只读;供前端 account_deep 进度条/预计剩余时间接线)──────────────────────
# 一个账号 N 条视频各是一条独立 apify_jobs(幂等键 video-final-v1:<evidence_id>:<scope>、目标锁
# video:<id>:final_v1 都按视频粒度,批量入队走 batch 车道、SKIP LOCKED 天然散到多车道),
# 此前没有任何地方把 N 条的整体状态汇总起来。本函数零写、零 LLM、零 fit。
PROGRESS_ACTIVE_STATES = ("queued", "running")
PROGRESS_FAILED_STATES = ("failed", "blocked", "triage")


def _video_concurrency_hint() -> int:
    """视频槽位数(与 worker 的 APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY 同源;解析失败按 1)。"""

    raw = str(os.environ.get("APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY", "1") or "1").strip()
    try:
        return max(1, min(16, int(raw)))
    except ValueError:
        return 1


def _recent_final_v1_duration_p50_ms(conn: Any) -> tuple[int | None, str]:
    """最近 done 的 final_v1 任务 wall time 中位数(先 24h,再 7d);没有样本返回 (None, 'no_sample')。"""

    for hours, basis in ((24, "done_jobs_24h_p50"), (24 * 7, "done_jobs_7d_p50")):
        row = conn.execute(
            """
            SELECT percentile_cont(0.5) WITHIN GROUP (
                     ORDER BY EXTRACT(EPOCH FROM (updated_at - started_at)) * 1000
                   ) AS p50_ms,
                   COUNT(*) AS n
            FROM apify_jobs
            WHERE job_type='video'
              AND status='done'
              AND payload->>'derive_method'=?
              AND started_at IS NOT NULL
              AND updated_at >= NOW() - make_interval(hours => ?)
              AND updated_at > started_at
            """,
            (FINAL_V1_DERIVE_METHOD, int(hours)),
        ).fetchone()
        data = dict(row) if row else {}
        if _int_or_none(data.get("n")) and data.get("p50_ms") is not None:
            return int(float(data["p50_ms"])), basis
    return None, "no_sample"


def account_video_analysis_progress(
    conn: Any,
    kol_pool_id: int,
    *,
    limit: int = KOL_DEEP_ANALYSIS_VIDEO_LIMIT,
    include_items: bool = True,
) -> dict[str, Any]:
    """account_deep 一个账号 N 条视频 final_v1 的整体进度(已完成/进行中/失败/预计剩余)。

    口径:范围 = 该 KOL 最近 ``limit`` 条活跃视频证据(与 enqueue_all_kol_videos 同一切片)。
    每条状态优先级:ready(有 ready cache)> running/queued(最新任务活跃)> failed/blocked/triage
    (最新任务终态且无 cache)> not_requested。ETA = ceil(进行中 / 视频槽位) × 最近 done 任务 p50。
    返回形状见 README 注释与 tests/test_video_analysis_account_progress.py。
    """

    pool_id = _int_or_none(kol_pool_id)
    if not pool_id:
        raise ValueError("kol_pool_id required")
    cap = max(1, int(limit or KOL_DEEP_ANALYSIS_VIDEO_LIMIT))
    all_ids = list_kol_all_evidence_ids(conn, pool_id)
    scope_ids = all_ids[:cap]
    counts = {"ready": 0, "running": 0, "queued": 0, "failed": 0, "blocked": 0, "triage": 0, "not_requested": 0}
    items: list[dict[str, Any]] = []
    if scope_ids:
        placeholders = ", ".join("?" for _ in scope_ids)
        id_params = tuple(int(eid) for eid in scope_ids)
        text_params = tuple(str(eid) for eid in scope_ids)
        evidence_rows = conn.execute(
            f"""
            SELECT id, content_url, platform,
                   COALESCE(NULLIF(title, ''), NULLIF(video_title, ''), '') AS title
            FROM vkpi_kol_video_evidence
            WHERE id IN ({placeholders})
            """,
            id_params,
        ).fetchall()
        evidence_by_id = {int(dict(r)["id"]): dict(r) for r in evidence_rows}
        cache_rows = conn.execute(
            f"""
            SELECT target_id, updated_at
            FROM vkpi_analysis_cache
            WHERE target_type='video'
              AND derive_method=?
              AND status='ready'
              AND target_id IN ({placeholders})
            """,
            (FINAL_V1_DERIVE_METHOD, *text_params),
        ).fetchall()
        cache_by_id = {str(dict(r)["target_id"]): dict(r) for r in cache_rows}
        job_rows = conn.execute(
            f"""
            SELECT DISTINCT ON (payload->>'target_id')
                   id, status, attempts, last_error_category, created_at, started_at, updated_at,
                   payload->>'target_id' AS target_id
            FROM apify_jobs
            WHERE job_type='video'
              AND payload->>'derive_method'=?
              AND payload->>'target_type'='video'
              AND payload->>'target_id' IN ({placeholders})
            ORDER BY payload->>'target_id', created_at DESC, id DESC
            """,
            (FINAL_V1_DERIVE_METHOD, *text_params),
        ).fetchall()
        job_by_id = {str(dict(r)["target_id"]): dict(r) for r in job_rows}
        for eid in scope_ids:
            key = str(eid)
            cache = cache_by_id.get(key)
            job = job_by_id.get(key) or {}
            job_status = str(job.get("status") or "").lower()
            if cache:
                state = "ready"
            elif job_status in PROGRESS_ACTIVE_STATES:
                state = job_status
            elif job_status in PROGRESS_FAILED_STATES:
                state = job_status
            else:
                state = "not_requested"
            counts[state] += 1
            if include_items:
                evidence = evidence_by_id.get(int(eid), {})
                items.append(
                    {
                        "evidence_id": int(eid),
                        "content_url": evidence.get("content_url"),
                        "platform": evidence.get("platform"),
                        "title": evidence.get("title"),
                        "state": state,
                        "job_id": _int_or_none(job.get("id")),
                        "job_status": job_status or None,
                        "attempts": _int_or_none(job.get("attempts")),
                        "last_error_category": job.get("last_error_category"),
                        "cache_updated_at": cache.get("updated_at") if cache else None,
                        "job_updated_at": job.get("updated_at"),
                    }
                )
    completed = counts["ready"]
    in_progress = counts["running"] + counts["queued"]
    failed = counts["failed"] + counts["blocked"] + counts["triage"]
    scope_total = len(scope_ids)
    if scope_total == 0:
        state = "no_evidence"
    elif in_progress > 0:
        state = "running"
    elif completed == scope_total:
        state = "done"
    elif completed + failed == scope_total:
        state = "partial_failed"
    elif completed == 0 and failed == 0:
        state = "idle"
    else:
        state = "partial"
    p50_ms, basis = _recent_final_v1_duration_p50_ms(conn) if in_progress else (None, "not_needed")
    parallelism = _video_concurrency_hint()
    eta_seconds: int | None = None
    if in_progress and p50_ms:
        waves = -(-in_progress // max(1, parallelism))  # ceil
        eta_seconds = int(round(waves * p50_ms / 1000.0))
    return {
        "kol_pool_id": pool_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "scope": {"limit": cap, "evidence_total": len(all_ids), "scope_total": scope_total},
        "state": state,
        "completed": completed,
        "in_progress": in_progress,
        "failed": failed,
        "not_requested": counts["not_requested"],
        "percent": int(round(100.0 * completed / scope_total)) if scope_total else 0,
        "counts": counts,
        "eta": {
            "remaining": in_progress,
            "recent_p50_ms": p50_ms,
            "basis": basis,
            "effective_parallelism": min(parallelism, in_progress) if in_progress else 0,
            "estimated_remaining_seconds": eta_seconds,
        },
        "items": items if include_items else [],
        "write_db": False,
        "provider_calls": False,
    }
