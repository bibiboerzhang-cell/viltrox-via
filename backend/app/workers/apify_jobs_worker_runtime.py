"""Runtime implementations extracted from :mod:`apify_jobs_worker`.

The caller injects its live module namespace so worker tests and operational
monkeypatches keep controlling the same queue, budget and fencing dependencies.
"""
from __future__ import annotations

from typing import Any, Mapping

import psycopg

# 拒绝的原因不再合并:七种 provider_gate_reason 各回各的码,budget_guard_blocked 只留给真花超。
from app.domains.costs.budget_decision import provider_gate_block
from app.workers.apify_jobs_worker_keyframe_cache import (
    keyframe_qa_cache_reuse_state_for_source,
)


def _cache_reuse_state(
    conn: psycopg.Connection[Any],
    *,
    target_type: str,
    target_id: str,
    derive_method: str,
    payload: dict[str, Any],
    keyframe_derive_method: str,
    keyframe_lookup: Any,
    reuse_lookup: Any,
    legacy_lookup: Any,
) -> dict[str, Any]:
    if derive_method == keyframe_derive_method:
        return keyframe_lookup(
            conn,
            target_type=target_type,
            target_id=target_id,
            derive_method=derive_method,
            payload=payload,
        )
    if callable(reuse_lookup):
        return reuse_lookup(conn, target_type, target_id, derive_method)
    return {
        "exists": bool(legacy_lookup(conn, target_type, target_id, derive_method)),
        "reusable": True,
        "reasons": [],
    }


def _finish_cache_hit(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    derive_method: str,
    cache_state: dict[str, Any],
    evaluation_only: bool,
    scope_checkpoint: Any,
    finish_skipped: Any,
) -> bool:
    if not cache_state.get("exists"):
        return False
    if not scope_checkpoint(
        conn,
        job,
        payload,
        derive_method,
        provider_calls_performed=False,
    ):
        return True
    reason = "skipped_existing_analysis_cache"
    if cache_state.get("reusable") is not True:
        details = ",".join(
            str(item)
            for item in cache_state.get("reasons", [])[:12]
            if str(item).strip()
        )
        reason = "skipped_legacy_cache_unverified"
        if details:
            reason = f"{reason}:{details}"
    finish_skipped(
        conn,
        int(job["id"]),
        reason,
        evaluation_only=evaluation_only,
    )
    return True


_SPECIAL_JOB_HANDLERS = {
    "session_advance": "_process_session_advance",
    "smart_search_profile_advance": "_process_smart_search_profile_advance",
    "kol_content_fit_analysis": "_process_kol_content_fit_analysis",
    "account_dossier_extract": "_process_account_dossier_extract",
    "project_contract_extract": "_process_project_contract_extract",
    "project_retrospective_aggregate": "_process_project_retrospective",
    "video_url_resolve": "_process_video_url_resolve",
    "kol_profile_deep_crawl": "_process_kol_profile_deep_crawl",
    "kol_pool_comments_collect": "_process_kol_pool_comments_collect",
    "kol_video_metric_refresh": "_process_kol_video_metric_refresh",
    "kol_audience_stats_refresh": "_process_kol_audience_stats_refresh",
    "official_channel_comments_collect": "_process_official_channel_comments_collect",
    "kol_outreach_draft": "_process_kol_outreach_draft",
    "contract_invoice_extract": "_process_contract_invoice_extract",
    "contract_polish": "_process_contract_polish",
    "logistics_track_sync": "_process_logistics_track_sync",
    "kol_auto_poll": "_process_kol_auto_poll",
}


def _dispatch_special_job(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    deps: Mapping[str, Any],
) -> bool:
    handler_name = _SPECIAL_JOB_HANDLERS.get(
        str(job.get("job_type") or "").strip().lower()
    )
    if handler_name is None:
        return False
    deps[handler_name](conn, job, payload)
    return True


def _prepare_llm_context(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    target_type: str,
    target_id: str,
    derive_method: str,
    deps: Mapping[str, Any],
) -> dict[str, Any] | None:
    if target_type not in deps["LLM_TARGET_TYPES"]:
        deps["_block_job"](
            conn,
            int(job["id"]),
            "unsupported_llm_target_type",
            {"target_type": target_type},
        )
        return None
    job_authorization = deps["verify_job_local_evaluation_capability"](
        payload,
        job_id=int(job["id"]),
    )
    if job_authorization.get("requested") and not job_authorization.get("valid"):
        deps["_block_job"](
            conn,
            int(job["id"]),
            "local_evaluation_capability_blocked",
            {"reason_detail": job_authorization.get("reason")},
        )
        return None
    execution_class = str(
        job_authorization.get("execution_class")
        or deps["llm_gateway"].PRODUCTION_EXECUTION_CLASS
    )
    cache_derive_method = str(
        job_authorization.get("cache_derive_method") or derive_method
    )
    if execution_class == deps["LOCAL_EVALUATION_EXECUTION_CLASS"] and (
        derive_method != deps["LOCAL_EVALUATION_DERIVE_METHOD"]
        or cache_derive_method != deps["LOCAL_EVALUATION_CACHE_DERIVE_METHOD"]
    ):
        deps["_block_job"](
            conn,
            int(job["id"]),
            "local_evaluation_derive_blocked",
            {"derive_method": derive_method},
        )
        return None
    cache_state = _cache_reuse_state(
        conn,
        target_type=target_type,
        target_id=target_id,
        derive_method=cache_derive_method,
        payload=payload,
        keyframe_derive_method=deps["FINAL_V1_KEYFRAME_QA_DERIVE_METHOD"],
        keyframe_lookup=deps["_keyframe_qa_cache_reuse_state_for_source"],
        reuse_lookup=deps["_analysis_cache_reuse_decision"],
        legacy_lookup=deps["_analysis_cache_exists"],
    )
    if _finish_cache_hit(
        conn,
        job=job,
        payload=payload,
        derive_method=derive_method,
        cache_state=cache_state,
        evaluation_only=execution_class
        == deps["LOCAL_EVALUATION_EXECUTION_CLASS"],
        scope_checkpoint=deps["_final_v1_scope_checkpoint"],
        finish_skipped=deps["_finish_skipped"],
    ):
        return None
    return {
        "job_authorization": job_authorization,
        "execution_class": execution_class,
        "cache_derive_method": cache_derive_method,
    }


def _preflight_state(
    preflight: dict[str, Any], deps: Mapping[str, Any]
) -> dict[str, Any]:
    execution_plan = (
        preflight.get("worker_model_execution")
        if isinstance(preflight.get("worker_model_execution"), dict)
        else {}
    )
    ready_models = [
        str(model).strip()
        for model in execution_plan.get("ready_models", [])
        if str(model).strip()
    ]
    if execution_plan:
        allowed = bool(ready_models)
        blocked_models = (
            execution_plan.get("blocked_models")
            if isinstance(execution_plan.get("blocked_models"), dict)
            else {}
        )
        reason = str(
            preflight.get("provider_gate_reason")
            or next(iter(blocked_models.values()), "")
            or ("provider_calls_allowed" if allowed else "provider_calls_blocked")
        )
        estimated_cost = float(execution_plan.get("estimated_cost_usd") or 0.0)
    else:
        allowed, reason, estimated_cost = deps["_google_allowed"](preflight)
    return {
        "execution_plan": execution_plan,
        "ready_models": ready_models,
        "allowed": allowed,
        "reason": reason,
        "estimated_cost": estimated_cost,
    }


def _expected_model(
    payload: dict[str, Any],
    derive_method: str,
    ready_models: list[str],
    deps: Mapping[str, Any],
) -> str:
    if ready_models:
        return ready_models[0]
    if derive_method == deps["FINAL_V1_KEYFRAME_QA_DERIVE_METHOD"]:
        return str(
            payload.get("final_v1_qa_model")
            or deps["FINAL_V1_KEYFRAME_QA_MODEL"]
        ).strip()
    return deps["WORKER_GEMINI_MODEL"]


def _authorized_execution_payload(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    derive_method: str,
    preflight: dict[str, Any],
    state: dict[str, Any],
    context: dict[str, Any],
    deps: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not state["allowed"]:
        deps["_block_job"](conn, int(job["id"]), *provider_gate_block(
            preflight, provider="google", stage=derive_method,
            gate_reason=state["reason"], estimated_cost_usd=state["estimated_cost"],
        ))
        return None
    execution_plan = state["execution_plan"]
    ready_models = state["ready_models"]
    authorizations_by_model = (
        execution_plan.get("authorizations_by_model")
        if isinstance(execution_plan.get("authorizations_by_model"), dict)
        else {}
    )
    authorizations_by_binding = (
        execution_plan.get("authorizations_by_binding")
        if isinstance(execution_plan.get("authorizations_by_binding"), dict)
        else {}
    )
    expected_model = _expected_model(payload, derive_method, ready_models, deps)
    model_authorization = authorizations_by_model.get(expected_model)
    authorization = (
        model_authorization
        if isinstance(model_authorization, dict)
        else deps["_google_execution_authorization"](preflight)
    )
    expected_binding = f"google/{expected_model}"
    if authorization.get("binding") != expected_binding:
        deps["_block_job"](
            conn,
            int(job["id"]),
            "model_binding_mismatch",
            {
                "expected_binding": expected_binding,
                "authorized_binding": authorization.get("binding"),
                "execution_class": authorization.get("execution_class"),
            },
        )
        return None
    execution_class = context["execution_class"]
    if authorization.get("execution_class") != execution_class:
        deps["_block_job"](
            conn,
            int(job["id"]),
            "execution_class_mismatch",
            {
                "expected_execution_class": execution_class,
                "authorized_execution_class": authorization.get("execution_class"),
            },
        )
        return None
    return {
        **payload,
        **(
            {"gemini_final_v1_models": list(ready_models)}
            if derive_method == "video_analysis_final_v1" and ready_models
            else {}
        ),
        "_llm_execution": {
            **authorization,
            "requested_model_chain": list(
                execution_plan.get("requested_models") or ready_models
            ),
            "ready_model_chain": list(ready_models or [expected_model]),
            "execution_authorizations_by_model": authorizations_by_model,
            "execution_authorizations_by_binding": authorizations_by_binding,
            **(
                context["job_authorization"]
                if execution_class == deps["LOCAL_EVALUATION_EXECUTION_CLASS"]
                else {}
            ),
        },
    }


def _run_locked_llm(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    target_type: str,
    target_id: str,
    derive_method: str,
    target_lock: str,
    context: dict[str, Any],
    deps: Mapping[str, Any],
) -> None:
    slot = None
    try:
        cache_state = _cache_reuse_state(
            conn,
            target_type=target_type,
            target_id=target_id,
            derive_method=context["cache_derive_method"],
            payload=payload,
            keyframe_derive_method=deps["FINAL_V1_KEYFRAME_QA_DERIVE_METHOD"],
            keyframe_lookup=deps["_keyframe_qa_cache_reuse_state_for_source"],
            reuse_lookup=deps["_analysis_cache_reuse_decision"],
            legacy_lookup=deps["_analysis_cache_exists"],
        )
        if _finish_cache_hit(
            conn,
            job=job,
            payload=payload,
            derive_method=derive_method,
            cache_state=cache_state,
            evaluation_only=context["execution_class"]
            == deps["LOCAL_EVALUATION_EXECUTION_CLASS"],
            scope_checkpoint=deps["_final_v1_scope_checkpoint"],
            finish_skipped=deps["_finish_skipped"],
        ):
            return
        slot = deps["_acquire_llm_slot"](conn)
        if slot is None:
            deps["_requeue_job"](
                conn,
                int(job["id"]),
                "llm concurrency limit reached",
                retry_delay_seconds=deps["random"].uniform(5.0, 10.0),
            )
            return
        preflight = deps["_llm_budget_preflight"](
            job,
            payload,
            execution_class=context["execution_class"],
        )
        state = _preflight_state(preflight, deps)
        deps["_log_budget_preflight_record_only"](
            job=job,
            provider="google",
            allowed=state["allowed"],
            reason=state["reason"],
            estimated_cost=state["estimated_cost"],
            stage=derive_method,
        )
        authorized_payload = _authorized_execution_payload(
            conn,
            job=job,
            payload=payload,
            derive_method=derive_method,
            preflight=preflight,
            state=state,
            context=context,
            deps=deps,
        )
        if authorized_payload is None:
            return
        if derive_method in deps["GEMINI_VIDEO_DERIVE_METHODS"]:
            deps["_respect_gemini_qps"](conn)
            deps["_process_gemini_video"](
                conn,
                job,
                authorized_payload,
                state["estimated_cost"],
            )
            return
        deps["_block_job"](
            conn,
            int(job["id"]),
            "unsupported_llm_derive_method",
            {"derive_method": derive_method},
        )
    finally:
        if slot is not None:
            deps["_advisory_unlock"](
                conn, "vkpi_analysis_worker_llm_slot", slot
            )
        deps["_advisory_unlock"](
            conn, "vkpi_analysis_worker_target", target_lock
        )


def _process_llm_job(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    target_type: str,
    target_id: str,
    derive_method: str,
    deps: Mapping[str, Any],
) -> None:
    context = _prepare_llm_context(
        conn,
        job=job,
        payload=payload,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
        deps=deps,
    )
    if context is None:
        return
    target_lock = f"{target_type}:{target_id}:{derive_method}"
    if not deps["_advisory_lock"](
        conn, "vkpi_analysis_worker_target", target_lock
    ):
        deps["_requeue_job"](
            conn,
            int(job["id"]),
            "analysis target already in progress",
            retry_delay_seconds=deps["random"].uniform(2.0, 5.0),
        )
        return
    _run_locked_llm(
        conn,
        job=job,
        payload=payload,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
        target_lock=target_lock,
        context=context,
        deps=deps,
    )


def _process_mock_job(
    conn: psycopg.Connection[Any],
    *,
    job: dict[str, Any],
    payload: dict[str, Any],
    target_type: str,
    target_id: str,
    derive_method: str,
    deps: Mapping[str, Any],
) -> None:
    triggered_by = payload.get("triggered_by_user_id", payload.get("user_id"))
    triggered_by_user_id = int(triggered_by) if triggered_by not in (None, "") else None
    result = deps["_mock_result"](job, payload)
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
                RETURNING id
                """,
                (target_type, target_id, deps["_json"](result), triggered_by_user_id),
            )
            cache_row = cur.fetchone()
            cache_id = int(cache_row[0]) if cache_row else None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job["id"],),
            )
    deps["_sync_search_session_job"](
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=deps["_search_session_analysis_summary_from_result"](
            cache_id=cache_id,
            derive_method=derive_method,
            target_type=target_type,
            target_id=target_id,
            evidence={"id": target_id},
            result=result,
            cost=0.0,
        ),
    )


def _runtime_dependencies(namespace: Mapping[str, Any]) -> dict[str, Any]:
    GEMINI_VIDEO_DERIVE_METHODS = namespace['GEMINI_VIDEO_DERIVE_METHODS']
    FINAL_V1_KEYFRAME_QA_DERIVE_METHOD = namespace['FINAL_V1_KEYFRAME_QA_DERIVE_METHOD']
    FINAL_V1_KEYFRAME_QA_MODEL = namespace['FINAL_V1_KEYFRAME_QA_MODEL']
    LLM_TARGET_TYPES = namespace['LLM_TARGET_TYPES']
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD = namespace['LOCAL_EVALUATION_CACHE_DERIVE_METHOD']
    LOCAL_EVALUATION_DERIVE_METHOD = namespace['LOCAL_EVALUATION_DERIVE_METHOD']
    LOCAL_EVALUATION_EXECUTION_CLASS = namespace['LOCAL_EVALUATION_EXECUTION_CLASS']
    TARGET_FALLBACK_JOB_TYPES = namespace['TARGET_FALLBACK_JOB_TYPES']
    WORKER_GEMINI_MODEL = namespace['WORKER_GEMINI_MODEL']
    _acquire_llm_slot = namespace['_acquire_llm_slot']
    _advisory_lock = namespace['_advisory_lock']
    _advisory_unlock = namespace['_advisory_unlock']
    _analysis_cache_exists = namespace['_analysis_cache_exists']
    _analysis_cache_reuse_decision = namespace.get('_analysis_cache_reuse_decision')
    _keyframe_qa_cache_reuse_state_for_source = namespace.get(
        '_keyframe_qa_cache_reuse_state_for_source',
        keyframe_qa_cache_reuse_state_for_source,
    )
    _block_job = namespace['_block_job']
    _derive_method = namespace['_derive_method']
    _finish_skipped = namespace['_finish_skipped']
    _final_v1_scope_checkpoint = namespace['_final_v1_scope_checkpoint']
    _google_allowed = namespace['_google_allowed']
    _google_execution_authorization = namespace['_google_execution_authorization']
    _json = namespace['_json']
    _llm_budget_preflight = namespace['_llm_budget_preflight']
    _log_budget_preflight_record_only = namespace['_log_budget_preflight_record_only']
    _mock_result = namespace['_mock_result']
    _process_account_dossier_extract = namespace['_process_account_dossier_extract']
    _process_contract_invoice_extract = namespace['_process_contract_invoice_extract']
    _process_contract_polish = namespace['_process_contract_polish']
    _process_gemini_video = namespace['_process_gemini_video']
    _process_kol_audience_stats_refresh = namespace['_process_kol_audience_stats_refresh']
    _process_kol_auto_poll = namespace['_process_kol_auto_poll']
    _process_kol_content_fit_analysis = namespace['_process_kol_content_fit_analysis']
    _process_kol_outreach_draft = namespace['_process_kol_outreach_draft']
    _process_kol_pool_comments_collect = namespace['_process_kol_pool_comments_collect']
    _process_kol_video_metric_refresh = namespace['_process_kol_video_metric_refresh']
    _process_kol_profile_deep_crawl = namespace['_process_kol_profile_deep_crawl']
    _process_logistics_track_sync = namespace['_process_logistics_track_sync']
    _process_official_channel_comments_collect = namespace['_process_official_channel_comments_collect']
    _process_project_contract_extract = namespace['_process_project_contract_extract']
    _process_project_retrospective = namespace['_process_project_retrospective']
    _process_video_url_resolve = namespace['_process_video_url_resolve']
    _process_session_advance = namespace['_process_session_advance']
    _process_smart_search_profile_advance = namespace['_process_smart_search_profile_advance']
    _requeue_job = namespace['_requeue_job']
    _respect_gemini_qps = namespace['_respect_gemini_qps']
    _search_session_analysis_summary_from_result = namespace['_search_session_analysis_summary_from_result']
    _sync_search_session_job = namespace['_sync_search_session_job']
    _target = namespace['_target']
    llm_gateway = namespace['llm_gateway']
    random = namespace['random']
    verify_job_local_evaluation_capability = namespace['verify_job_local_evaluation_capability']
    return locals()


def process_job_impl(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    namespace: Mapping[str, Any],
) -> None:
    deps = _runtime_dependencies(namespace)
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    if _dispatch_special_job(conn, job, payload, deps):
        return
    job_type = str(job.get("job_type") or "").strip().lower()
    if job_type not in deps["TARGET_FALLBACK_JOB_TYPES"]:
        deps["_block_job"](
            conn,
            int(job["id"]),
            "unknown_job_type",
            {"job_type": job_type},
        )
        return
    target_type, target_id = deps["_target"](payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    derive_method = deps["_derive_method"](payload)
    if derive_method != "mock":
        _process_llm_job(
            conn,
            job=job,
            payload=payload,
            target_type=target_type,
            target_id=target_id,
            derive_method=derive_method,
            deps=deps,
        )
        return
    _process_mock_job(
        conn,
        job=job,
        payload=payload,
        target_type=target_type,
        target_id=target_id,
        derive_method=derive_method,
        deps=deps,
    )

def fail_job_impl(conn: psycopg.Connection[Any], job_id: int, exc: Exception, namespace: Mapping[str, Any]) -> None:
    ApifyBudgetBlocked = namespace['ApifyBudgetBlocked']
    ApifyProviderReplayBlocked = namespace['ApifyProviderReplayBlocked']
    MAX_JOB_ATTEMPTS = namespace['MAX_JOB_ATTEMPTS']
    PROVIDER_RETRY_MAX_ATTEMPTS = namespace['PROVIDER_RETRY_MAX_ATTEMPTS']
    _error_category = namespace['_error_category']
    _failure_disposition = namespace['_failure_disposition']
    _block_job = namespace['_block_job']
    _provider_retry_delay_seconds = namespace['_provider_retry_delay_seconds']
    _provider_retry_reason = namespace['_provider_retry_reason']
    _redact_sensitive_text = namespace['_redact_sensitive_text']
    _sync_search_session_job = namespace['_sync_search_session_job']
    dict_row = namespace['dict_row']
    logger = namespace['logger']

    if isinstance(exc, ApifyBudgetBlocked):
        _block_job(conn, job_id, exc.code, {"provider": "apify"})
        return
    if isinstance(exc, ApifyProviderReplayBlocked):
        _block_job(conn, job_id, exc.code, {"provider": "apify"})
        return

    if str(exc).strip() == "gemini_call_timeout":
        message = "gemini_call_timeout"
    else:
        message = _redact_sensitive_text(f"{type(exc).__name__}: {exc}")
    category = _error_category(message)
    # 10E 失败池分流:把细类映射成处置动作。'retry' 类(timeout/proxy/限流/媒体解析/被回收)
    # 在仍有重试预算时重新 queued;'triage' 类(no_data/auth/下架/代码错)直接标 status='triage'
    # 待人工;unknown → 落 'failed'。last_error_category 始终写明确细类,不再留 null。
    #
    # 早退缺口修复(失败池排水):此前「retry 类但重试预算耗尽」会掉进最后的 else 落 'failed',
    # 永远绕过 triage 引擎,死在死信池里(审计:192 download + 152 media_resolve 全卡这条路)。
    # 改为:retry 类一旦耗尽,不再落 'failed',而是和 triage 类一样停 status='triage' —— 让所有
    # 「值得再看一眼」的失败都汇入同一个 triage 面待裁决(人工放量 / 离线排水重置后再跑),
    # 而非分裂成两套死信池。只有 unknown / 不认识的类别仍保守落 'failed'(可能藏永久错)。
    disposition = _failure_disposition(category)
    # provider_pressure 保留它专属的更宽重试预算(5 次 + 指数退避);其余可重试类用通用预算闸。
    if category == "provider_pressure":
        retry_budget = PROVIDER_RETRY_MAX_ATTEMPTS
    else:
        retry_budget = MAX_JOB_ATTEMPTS
    raw_status = "failed"
    sync_reason = message
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT attempts FROM apify_jobs WHERE id=%s FOR UPDATE", (job_id,))
            row = cur.fetchone() or {}
            next_attempt = int(row.get("attempts") or 0) + 1
            # retry 类预算耗尽 → 走 triage(而非 failed);triage 类与「耗尽的 retry 类」共用 triage 落点。
            send_to_triage = disposition == "triage" or (disposition == "retry" and next_attempt >= retry_budget)
            if disposition == "retry" and next_attempt < retry_budget:
                delay_seconds = _provider_retry_delay_seconds(next_attempt)
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='queued',
                        attempts=%s,
                        last_error=%s,
                        last_error_category=%s,
                        next_retry_at=NOW() + make_interval(secs => %s),
                        updated_at=NOW()
                    WHERE id=%s
                    RETURNING next_retry_at
                    """,
                    (next_attempt, message, category, delay_seconds, job_id),
                )
                retry_row = cur.fetchone() or {}
                raw_status = "queued"
                sync_reason = _provider_retry_reason(message, next_retry_at=retry_row.get("next_retry_at"))
                logger.warning(
                    "apify_jobs failure requeued | id=%s category=%s attempt=%s delay_seconds=%s next_retry_at=%s",
                    job_id,
                    category,
                    next_attempt,
                    delay_seconds,
                    retry_row.get("next_retry_at"),
                )
            elif send_to_triage:
                # 两类汇入 triage：①不可重试类(凭证/改代码/确认下架);②可重试类但预算耗尽
                # （再自动跑没用，但仍可由人工/离线排水重置 attempts 后放量）。都停 status='triage'
                # 待裁决，不再消耗重试预算。
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='triage',
                        attempts=%s,
                        last_error=%s,
                        last_error_category=%s,
                        next_retry_at=NULL,
                        updated_at=NOW()
                    WHERE id=%s
                    """,
                    (next_attempt, message, category, job_id),
                )
                raw_status = "failed"
                logger.warning(
                    "apify_jobs failure sent to triage | id=%s category=%s disposition=%s attempt=%s budget=%s exhausted=%s",
                    job_id,
                    category,
                    disposition,
                    next_attempt,
                    retry_budget,
                    disposition == "retry",
                )
            else:
                # 只剩 unknown / 不认识的类别落到这里:保守标 failed(可能藏永久错),
                # 留给离线排水层重新派生类别后再定夺。retry 类已不会再走到这条分支。
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET status='failed',
                        attempts=%s,
                        last_error=%s,
                        last_error_category=%s,
                        next_retry_at=NULL,
                        updated_at=NOW()
                    WHERE id=%s
                    """,
                    (next_attempt, message, category, job_id),
                )
                logger.warning(
                    "apify_jobs failure marked failed | id=%s category=%s disposition=%s attempt=%s budget=%s",
                    job_id,
                    category,
                    disposition,
                    next_attempt,
                    retry_budget,
                )
    _sync_search_session_job(conn, job_id, raw_status=raw_status, reason=sync_reason)
