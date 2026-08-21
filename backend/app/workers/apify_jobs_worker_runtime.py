"""Runtime implementations extracted from :mod:`apify_jobs_worker`.

The caller injects its live module namespace so worker tests and operational
monkeypatches keep controlling the same queue, budget and fencing dependencies.
"""
from __future__ import annotations

from typing import Any, Mapping

import psycopg

def process_job_impl(conn: psycopg.Connection[Any], job: dict[str, Any], namespace: Mapping[str, Any]) -> None:
    GEMINI_VIDEO_DERIVE_METHODS = namespace['GEMINI_VIDEO_DERIVE_METHODS']
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
    _block_job = namespace['_block_job']
    _derive_method = namespace['_derive_method']
    _finish_skipped = namespace['_finish_skipped']
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

    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    if str(job.get("job_type") or "").strip().lower() == "session_advance":
        _process_session_advance(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "smart_search_profile_advance":
        _process_smart_search_profile_advance(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_content_fit_analysis":
        _process_kol_content_fit_analysis(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "account_dossier_extract":
        _process_account_dossier_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "project_contract_extract":
        _process_project_contract_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "project_retrospective_aggregate":
        _process_project_retrospective(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "video_url_resolve":
        _process_video_url_resolve(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_profile_deep_crawl":
        _process_kol_profile_deep_crawl(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_pool_comments_collect":
        _process_kol_pool_comments_collect(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_video_metric_refresh":
        _process_kol_video_metric_refresh(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_audience_stats_refresh":
        _process_kol_audience_stats_refresh(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "official_channel_comments_collect":
        _process_official_channel_comments_collect(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_outreach_draft":
        _process_kol_outreach_draft(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "contract_invoice_extract":
        _process_contract_invoice_extract(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "contract_polish":
        _process_contract_polish(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "logistics_track_sync":
        _process_logistics_track_sync(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_auto_poll":
        _process_kol_auto_poll(conn, job, payload)
        return
    # 未知 job_type 防线(2026-07-11):显式分支簇没接住、又不是合法兜底类型('video')的,
    # 一律 blocked 而非滑进下方 derive_method='mock' 的假成功路径(写 mock cache + done)。
    job_type = str(job.get("job_type") or "").strip().lower()
    if job_type not in TARGET_FALLBACK_JOB_TYPES:
        _block_job(conn, int(job["id"]), "unknown_job_type", {"job_type": job_type})
        return
    target_type, target_id = _target(payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")
    derive_method = _derive_method(payload)
    if derive_method != "mock":
        if target_type not in LLM_TARGET_TYPES:
            _block_job(conn, int(job["id"]), "unsupported_llm_target_type", {"target_type": target_type})
            return
        job_authorization = verify_job_local_evaluation_capability(
            payload,
            job_id=int(job["id"]),
        )
        if job_authorization.get("requested") and not job_authorization.get("valid"):
            _block_job(
                conn,
                int(job["id"]),
                "local_evaluation_capability_blocked",
                {"reason_detail": job_authorization.get("reason")},
            )
            return
        execution_class = str(
            job_authorization.get("execution_class")
            or llm_gateway.PRODUCTION_EXECUTION_CLASS
        )
        cache_derive_method = str(
            job_authorization.get("cache_derive_method") or derive_method
        )
        if execution_class == LOCAL_EVALUATION_EXECUTION_CLASS and (
            derive_method != LOCAL_EVALUATION_DERIVE_METHOD
            or cache_derive_method != LOCAL_EVALUATION_CACHE_DERIVE_METHOD
        ):
            _block_job(
                conn,
                int(job["id"]),
                "local_evaluation_derive_blocked",
                {"derive_method": derive_method},
            )
            return
        target_lock = f"{target_type}:{target_id}:{derive_method}"
        if not _advisory_lock(conn, "vkpi_analysis_worker_target", target_lock):
            _requeue_job(conn, int(job["id"]), "analysis target already in progress", retry_delay_seconds=random.uniform(2.0, 5.0))
            return
        slot = _acquire_llm_slot(conn)
        try:
            if slot is None:
                _requeue_job(conn, int(job["id"]), "llm concurrency limit reached", retry_delay_seconds=random.uniform(5.0, 10.0))
                return
            if _analysis_cache_exists(
                conn,
                target_type,
                target_id,
                cache_derive_method,
            ):
                _finish_skipped(
                    conn,
                    int(job["id"]),
                    "skipped_existing_analysis_cache",
                    evaluation_only=(
                        execution_class == LOCAL_EVALUATION_EXECUTION_CLASS
                    ),
                )
                return
            preflight = _llm_budget_preflight(
                job,
                payload,
                execution_class=execution_class,
            )
            allowed, reason, estimated_cost = _google_allowed(preflight)
            _log_budget_preflight_record_only(
                job=job,
                provider="google",
                allowed=allowed,
                reason=reason,
                estimated_cost=estimated_cost,
                stage=derive_method,
            )
            if not allowed:
                # 护栏② 主线 enforce:撞 cap 拦在 _process_gemini_video 之前(finally 正常释放 slot/lock)
                _block_job(
                    conn,
                    int(job["id"]),
                    "budget_guard_blocked",
                    {
                        "provider": "google",
                        "stage": derive_method,
                        "reason_detail": reason,
                        "estimated_cost_usd": estimated_cost,
                    },
                )
                return
            authorization = _google_execution_authorization(preflight)
            expected_binding = f"google/{WORKER_GEMINI_MODEL}"
            if authorization.get("binding") != expected_binding:
                _block_job(
                    conn,
                    int(job["id"]),
                    "model_binding_mismatch",
                    {
                        "expected_binding": expected_binding,
                        "authorized_binding": authorization.get("binding"),
                        "execution_class": authorization.get("execution_class"),
                    },
                )
                return
            if authorization.get("execution_class") != execution_class:
                _block_job(
                    conn,
                    int(job["id"]),
                    "execution_class_mismatch",
                    {
                        "expected_execution_class": execution_class,
                        "authorized_execution_class": authorization.get("execution_class"),
                    },
                )
                return
            # Capability metadata wins for evaluation identity/cache scope;
            # provider preflight contributes exact binding/readiness/budget.
            payload = {
                **payload,
                "_llm_execution": {
                    **authorization,
                    **(
                        job_authorization
                        if execution_class == LOCAL_EVALUATION_EXECUTION_CLASS
                        else {}
                    ),
                },
            }
            if derive_method in GEMINI_VIDEO_DERIVE_METHODS:
                _respect_gemini_qps(conn)
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
                RETURNING id
                """,
                (target_type, target_id, _json(result), triggered_by_user_id),
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
    _sync_search_session_job(
        conn,
        int(job["id"]),
        raw_status="done",
        analysis_summary=_search_session_analysis_summary_from_result(
            cache_id=cache_id,
            derive_method=derive_method,
            target_type=target_type,
            target_id=target_id,
            evidence={"id": target_id},
            result=result,
            cost=0.0,
        ),
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
