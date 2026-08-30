"""final_v1 enqueue helpers for KOL Pool.

This module only writes apify_jobs. It never updates KOL Pool scoring fields.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.gemini_models import DEFAULT_VIDEO_GEMINI_MODEL
from app.core.video_model_chain import PAYLOAD_CHAIN_KEY, final_v1_model_chain, model_fallback_candidates, ready_model_subchain
from app.db.connection import get_conn
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
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
from app.domains.kol import video_analysis_account_progress as account_progress
from app.domains.kol.video_analysis_account_progress import (
    PROGRESS_ACTIVE_STATES,
    PROGRESS_FAILED_STATES,
)
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)
from app.domains.kol.video_analysis_media import eligible_video_evidence_sql
from app.domains.kol.video_analysis_enqueue_results import (
    ai_disabled_enqueue_result,
    cached_enqueue_result,
    non_video_enqueue_result,
    queued_video_job_result,
)


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
PRODUCTION_VIDEO_MODEL = DEFAULT_VIDEO_GEMINI_MODEL  # 与 worker 同源(env APIFY_WORKER_GEMINI_MODEL)
# C1:预检按整条链(主力预约 + 回退成员就绪)走;payload 只带 ready 子链,worker 只能再收窄。
PRODUCTION_VIDEO_CHAIN = final_v1_model_chain()
# 2026-07-02:默认 1200 会截断分镜 JSON(Extra data 占 unknown 失败桶大头);线上 .env 不随部署 → 代码默认 4096,env 仍可覆盖。
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
    """payload.triggered_by_user_id = **user id**(users.id),绝不退化成 staff id。

    身份类型化(C2,2026-08-22 复盘):此前缺 user_id 时退回 staff.id,让两种 id 在同一个键里
    混用,下游把它当 staff 外键写台账就炸 FK。staff 外键走 payload.staff_id(见 _staff_fk_id)。
    """
    return _int_or_none((staff or {}).get("user_id")) or None


def _staff_fk_id(staff: dict[str, Any] | None) -> int | None:
    """payload.staff_id = **staff 外键**(staff.id);只认 id / staff_id,绝不拿 user_id 凑数。"""
    staff = staff or {}
    return _int_or_none(staff.get("id") or staff.get("staff_id")) or None


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


def _google_budget(preflight: dict[str, Any], chain: list[str] | None = None) -> dict[str, Any]:
    """C1:链逐成员看预检——任一成员 ready 即入队(ready 子链随 payload),全不 ready 才 ai_disabled。"""
    chain = list(chain or PRODUCTION_VIDEO_CHAIN)
    ready, blocked = ready_model_subchain(preflight, chain)
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    head = ready[0] if ready else chain[0]
    google = next((i for i in providers if i.get("provider") == "google" and str(i.get("model") or chain[0]) == head), {})
    return {
        "allowed": bool(ready),
        "reason": str(preflight.get("provider_gate_reason") or google.get("provider_gate_reason") or "provider_calls_blocked"),
        "estimated_cost_usd": float(google.get("estimated_cost_usd") or 0.0),
        "provider": "google",
        "model": str(google.get("model") or head),
        "ready_models": ready,
        "blocked_models": blocked,
        "model_readiness_status": str(preflight.get("model_readiness_status") or google.get("model_readiness_status") or "not_ready"),
        "checks": google.get("checks") if isinstance(google.get("checks"), list) else [],
        "preflight": preflight,
    }


def _ai_analysis_state(
    state: str, *, reason: str = "", gate_reason: str = "", model_readiness_status: str = "", provider_calls_allowed: bool = False
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
        SELECT id, target_type, target_id, model, derive_method, cost, status,
               prompt_version, result, updated_at
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
    if not item["evaluation_only"]:
        item.update(
            canonical_final_v1_cache_reuse(
                item,
                target_type="video",
                target_id=str(evidence_id),
                derive_method=FINAL_V1_DERIVE_METHOD,
            )
        )
    item.pop("result", None)
    return item


def _active_job(conn: Any, *, idempotency_key: str) -> dict[str, Any] | None:
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


def _prepare_enqueue_scope(
    conn: Any, *, kol_pool_id: int, evidence_id: int, staff: dict[str, Any] | None,
    local_evaluation: bool, enforce_target_write: bool, search_session_id: int | None,
    search_session_item_id: int | None, parent_job_id: int | None,
    provider_parent_payload: dict[str, Any] | None,
) -> tuple[int, int, dict[str, Any], dict[str, Any] | None]:
    pool_id, owned_evidence_id = int(kol_pool_id), int(evidence_id)
    if local_evaluation and (
        isinstance(staff, dict) or enforce_target_write or search_session_id
        or search_session_item_id or parent_job_id or isinstance(provider_parent_payload, dict)
    ):
        from app.platform.llm_local_evaluation import LocalEvaluationCapabilityError

        raise LocalEvaluationCapabilityError("local_evaluation_server_scope_required")
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import assert_target_writable

        assert_target_writable(conn, kol_pool_id=pool_id, staff=staff)
    evidence = _load_owned_evidence(conn, kol_pool_id=pool_id, evidence_id=owned_evidence_id)
    if not evidence:
        raise LookupError("video evidence not found for this KOL")
    target_fence = None
    if enforce_target_write:
        from app.domains.kol.my_kol_paid_action_access import build_target_fence

        target_fence = build_target_fence(
            conn, action="video_analysis", kol_pool_id=pool_id, staff=staff,
            evidence_ids=[owned_evidence_id],
        )
    return pool_id, owned_evidence_id, evidence, target_fence


def _evidence_enqueue_gate(
    *, kol_pool_id: int, evidence_id: int, evidence: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    evidence_type = _text(evidence.get("evidence_type")).lower()
    media_kind = _text(evidence.get("media_kind")).lower()
    non_video = (evidence_type and evidence_type != "video") or (
        media_kind and media_kind not in ("video", "reel", "clip", "igtv")
    )
    if non_video:
        return "unsupported", non_video_enqueue_result(
            kol_pool_id=kol_pool_id, evidence_id=evidence_id,
            evidence_type=evidence_type, media_kind=media_kind,
            derive_method=FINAL_V1_DERIVE_METHOD, ai_analysis_state=_ai_analysis_state,
        )
    platform = _platform_from_url(_text(evidence.get("content_url")))
    if platform != "unsupported":
        return platform, None
    return platform, {
        "status": "unsupported_platform", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id,
        "derive_method": FINAL_V1_DERIVE_METHOD, "provider_calls": False, "write_db": False,
        "reason": "unsupported video URL host",
        "ai_analysis": _ai_analysis_state("not_requested", reason="unsupported_platform"),
    }


def _enqueue_budget(*, evidence_id: int, platform: str, local_evaluation: bool) -> tuple[str, str, dict[str, Any]]:
    prompt = f"final_v1 on_demand video:{evidence_id} {platform}"
    execution_class = LOCAL_EVALUATION_EXECUTION_CLASS if local_evaluation else "production"
    preflight = llm_gateway.budget_preflight(
        prompt, purpose="vkpi_analysis_worker", max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
        preferred_provider="google",
        model_override=LOCAL_EVALUATION_MODEL if local_evaluation else PRODUCTION_VIDEO_MODEL,
        model_fallbacks=[] if local_evaluation else model_fallback_candidates(PRODUCTION_VIDEO_CHAIN),
        execution_class=execution_class, cost_tag=LLM_BUDGET_SCOPE, require_configured=False,
    )
    chain = [LOCAL_EVALUATION_MODEL] if local_evaluation else PRODUCTION_VIDEO_CHAIN
    return prompt, execution_class, _google_budget(preflight, chain)


def _authorized_video_payload(
    conn: Any, *, kol_pool_id: int, evidence_id: int, evidence: dict[str, Any], platform: str,
    staff: dict[str, Any] | None, source: str, batch: str, prompt: str,
    budget: dict[str, Any], target_fence: dict[str, Any] | None,
    search_session_id: int | None, search_session_item_id: int | None,
    parent_job_id: int | None, local_evaluation: bool,
    provider_parent_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = with_search_session_lineage(
        {
            "queue_lane": _video_analysis_queue_lane(batch=batch, local_evaluation=bool(local_evaluation)),
            "target_type": "video", "target_id": str(evidence_id),
            "derive_method": FINAL_V1_DERIVE_METHOD, "platform": platform,
            "platform_by_host": platform, "kol_pool_id": kol_pool_id, "source": source,
            "batch": batch, "triggered_by_user_id": _triggered_user_id(staff), "prompt": prompt,
            "source_url": evidence.get("content_url"), "title": evidence.get("title"),
            "creator_handle": evidence.get("kol_handle"), "staff_id": _staff_fk_id(staff),
            PAYLOAD_CHAIN_KEY: list(budget.get("ready_models") or []),
        },
        search_session_id=search_session_id, search_session_item_id=search_session_item_id,
        role="video", parent_job_id=parent_job_id,
    )
    if target_fence is not None:
        from app.domains.kol.my_kol_paid_action_access import FENCE_KEY

        payload[FENCE_KEY] = target_fence
        snapshots = target_fence.get("evidence")
        if isinstance(snapshots, list) and len(snapshots) == 1:
            payload["source_url"] = snapshots[0].get("normalized_url")
    elif not local_evaluation and (
        isinstance(provider_parent_payload, dict) or (search_session_id and isinstance(staff, dict))
    ):
        from app.domains.kol.video_analysis_job_access import authorize_video_analysis_job

        payload = authorize_video_analysis_job(
            conn, payload, evidence=evidence, source_payload=provider_parent_payload, staff=staff,
        )
    if local_evaluation:
        payload = {
            **payload, "local_evaluation": True,
            "execution_class": LOCAL_EVALUATION_EXECUTION_CLASS,
            "model_binding": LOCAL_EVALUATION_BINDING,
        }
    return payload


def _video_job_idempotency_key(payload: dict[str, Any], evidence_id: int, local_evaluation: bool) -> str:
    from app.domains.kol.video_analysis_job_access import video_analysis_authorization_scope

    prefix = "video-final-v1-local-evaluation" if local_evaluation else "video-final-v1"
    return active_job_idempotency_key(
        prefix, evidence_id, video_analysis_authorization_scope(payload),
    )


def _existing_video_job_result(
    conn: Any, *, existing_job: dict[str, Any], lineage_payload: dict[str, Any],
    commit: bool, kol_pool_id: int, evidence_id: int, budget: dict[str, Any],
) -> dict[str, Any]:
    linked_payload = attach_search_session_lineage_to_job(
        conn, existing_job.get("id"), lineage_payload,
    )
    if linked_payload and commit:
        conn.commit()
    return {
        "status": "already_queued", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "job": redact_local_evaluation_capability(existing_job), "provider_calls": False,
        "write_db": bool(linked_payload), "lineage_linked": bool(linked_payload),
        "ai_analysis": _ai_analysis_state(
            "queued", reason="already_queued",
            gate_reason=str(budget.get("reason") or "provider_calls_allowed"),
            model_readiness_status=str(budget.get("model_readiness_status") or "production_ready"),
            provider_calls_allowed=True,
        ),
    }


def _sign_local_evaluation_job(
    conn: Any, *, row: dict[str, Any], payload: dict[str, Any], evidence_id: int,
    local_evaluation: bool, inserted: bool,
) -> dict[str, Any]:
    if not (local_evaluation and inserted):
        return row
    try:
        capability = issue_local_evaluation_capability(
            job_id=int(row.get("id") or 0), target_type="video", target_id=str(evidence_id),
            derive_method=FINAL_V1_DERIVE_METHOD, model_binding=LOCAL_EVALUATION_BINDING,
        )
        signed_payload = {**payload, "_local_evaluation_capability": capability}
        conn.execute(
            "UPDATE apify_jobs SET payload=?::jsonb, updated_at=NOW() WHERE id=?",
            (json.dumps(signed_payload, ensure_ascii=False, default=str), int(row["id"])),
        )
        return {**row, "payload": signed_payload}
    except Exception:
        conn.rollback()
        raise


def _verify_enqueue_fit(conn: Any, *, kol_pool_id: int, before_fit: Any) -> list[int]:
    after_fit = _fit_snapshot(conn, kol_pool_id)
    changed_ids = [kol_pool_id] if before_fit != after_fit else []
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    return changed_ids


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

    kol_pool_id, evidence_id, evidence, target_fence = _prepare_enqueue_scope(
        conn, kol_pool_id=kol_pool_id, evidence_id=evidence_id, staff=staff,
        local_evaluation=local_evaluation, enforce_target_write=enforce_target_write,
        search_session_id=search_session_id, search_session_item_id=search_session_item_id,
        parent_job_id=parent_job_id, provider_parent_payload=provider_parent_payload,
    )

    # 识别闸:图文/轮播帖没视频可下,排了必 media_resolve_failed。
    # 这里统一拦下(批量/URL/手动所有入队路径都过这条),不入队、不当失败。缺省/video 放行。
    # 修复:此前 SELECT 没查 evidence_type,闸恒空转(全盘测速 IG /p/ 帖 3/3 白跑实锤);
    # 现补列并加 media_kind 第二判据(0703 排水同款口径)。media_kind 列存在双向 schema 漂移
    # (线上有列/本地没有),SELECT 用 to_jsonb(e.*)->>'media_kind' 通吃:有列取值、无列得 NULL 不炸。
    platform, evidence_gate = _evidence_enqueue_gate(
        kol_pool_id=kol_pool_id, evidence_id=evidence_id, evidence=evidence,
    )
    if evidence_gate is not None:
        return evidence_gate

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
    cache_gate = cached_enqueue_result(
        kol_pool_id=kol_pool_id, evidence_id=evidence_id, cache=cache,
        derive_method=FINAL_V1_DERIVE_METHOD, ai_analysis_state=_ai_analysis_state,
    )
    if cache_gate is not None:
        return cache_gate
    prompt, execution_class, budget = _enqueue_budget(
        evidence_id=evidence_id, platform=platform, local_evaluation=local_evaluation,
    )

    # A durable AI job is only useful when the exact model chain is authorized
    # now.  Previously these rows were queued despite a failed production
    # readiness gate, so the worker could only mark them blocked and URL/search
    # sessions remained partial forever.  Existing profile/video evidence is
    # still returned; the optional AI stage terminates honestly as not requested.
    if not budget["allowed"]:
        return ai_disabled_enqueue_result(
            kol_pool_id=kol_pool_id, evidence_id=evidence_id, evidence=evidence,
            platform=platform, budget=budget, execution_class=execution_class,
            derive_method=FINAL_V1_DERIVE_METHOD, ai_analysis_state=_ai_analysis_state,
        )

    # Source-level queue contract guard: helper payload retains
    # "target_type", "target_id", "derive_method", and "kol_pool_id".
    before_fit = _fit_snapshot(conn, kol_pool_id)
    payload = _authorized_video_payload(
        conn, kol_pool_id=kol_pool_id, evidence_id=evidence_id, evidence=evidence,
        platform=platform, staff=staff, source=source, batch=batch, prompt=prompt,
        budget=budget, target_fence=target_fence, search_session_id=search_session_id,
        search_session_item_id=search_session_item_id, parent_job_id=parent_job_id,
        local_evaluation=local_evaluation, provider_parent_payload=provider_parent_payload,
    )
    idempotency_key = _video_job_idempotency_key(payload, evidence_id, local_evaluation)
    existing_job = _active_job(conn, idempotency_key=idempotency_key)
    if existing_job:
        return _existing_video_job_result(
            conn, existing_job=existing_job, lineage_payload=lineage_payload, commit=commit,
            kol_pool_id=kol_pool_id, evidence_id=evidence_id, budget=budget,
        )

    row, inserted = enqueue_active_apify_job(
        conn,
        job_type="video",
        payload=payload,
        idempotency_key=idempotency_key,
    )
    row = _sign_local_evaluation_job(
        conn, row=row, payload=payload, evidence_id=evidence_id,
        local_evaluation=local_evaluation, inserted=inserted,
    )
    changed_ids = _verify_enqueue_fit(conn, kol_pool_id=kol_pool_id, before_fit=before_fit)
    if commit:
        conn.commit()
    return queued_video_job_result(
        row=row, inserted=inserted, kol_pool_id=kol_pool_id, evidence_id=evidence_id,
        budget=budget, execution_class=execution_class, local_evaluation=local_evaluation,
        evidence=evidence, platform=platform, changed_ids=changed_ids,
        derive_method=FINAL_V1_DERIVE_METHOD, ai_analysis_state=_ai_analysis_state,
        redact_job=redact_local_evaluation_capability,
    )


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
    legacy_unverified = 0
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
                if result.get("effective_status") == "legacy_unverified":
                    legacy_unverified += 1
        except LookupError as exc:
            errors += 1
            results.append({"status": "not_found", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id, "reason": str(exc)})
        except Exception as exc:
            errors += 1
            results.append({"status": "error", "kol_pool_id": kol_pool_id, "evidence_id": evidence_id, "reason": str(exc)})
    batch_status = "partial" if not queued and legacy_unverified else "completed"
    batch_state = "queued" if queued else batch_status
    return {
        "status": batch_status,
        "state": batch_state,
        "terminal": queued == 0,
        **(
            {
                "effective_status": "legacy_unverified",
                "cache_reuse_status": "legacy_unverified",
                "revalidation_required": True,
                "claim_status": "descriptive_only",
            }
            if not queued and legacy_unverified
            else {}
        ),
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "requested": len(normalized),
        "queued": queued,
        "skipped": skipped,
        "ai_disabled": ai_disabled,
        "legacy_unverified_count": legacy_unverified,
        "errors": errors,
        "budget_gate": "enforced_at_enqueue",
        "ai_analysis": _ai_analysis_state(
            "queued" if queued else "partial" if legacy_unverified else "not_requested",
            reason="analysis_queued" if queued else "legacy_cache_requires_explicit_revalidation" if legacy_unverified else "ai_disabled" if ai_disabled else "no_eligible_video",
            provider_calls_allowed=queued > 0,
        ),
        "items": results,
        "provider_calls": False,
        "write_db": queued > 0,
        "writes": ["apify_jobs"] if queued else [],
    }


def list_kol_all_evidence_ids(conn: Any, kol_pool_id: int) -> list[int]:
    """全部活跃视频证据 id；排除已确认非视频并兼容旧表缺列。"""
    eligible_sql = eligible_video_evidence_sql(conn)
    rows = conn.execute(
        f"""
        SELECT e.id AS evidence_id
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id = ?
          AND (e.is_active IS NULL OR e.is_active = TRUE)
          AND {eligible_sql}
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


# ── 账号级进度(只读;实现拆至兄弟模块以守住行数门禁)────────────────────────
def _video_concurrency_hint() -> int:
    return account_progress.video_concurrency_hint()


def _recent_final_v1_duration_p50_ms(conn: Any) -> tuple[int | None, str]:
    return account_progress.recent_final_v1_duration_p50_ms(conn)


def account_video_analysis_progress(
    conn: Any,
    kol_pool_id: int,
    *,
    limit: int = KOL_DEEP_ANALYSIS_VIDEO_LIMIT,
    include_items: bool = True,
) -> dict[str, Any]:
    return account_progress.account_video_analysis_progress(
        conn,
        kol_pool_id,
        limit=limit,
        include_items=include_items,
        list_evidence_ids=list_kol_all_evidence_ids,
    )
