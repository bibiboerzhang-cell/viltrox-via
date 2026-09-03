"""单一 job_type 处理簇(session_advance / 各 _process_* 调度入口),从 apify_jobs_worker.py
整簇 move 出来。函数体逐字不变 → 行为必然不变;原文件 re-export 兜住所有调用点。

这些 handler 互不依赖原文件留下的名字(其域内 import 均为函数内 lazy import),故本模块
顶层直接 import 依赖,无循环导入风险。红线:本簇零 fit 写。
"""
from __future__ import annotations

import asyncio
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.db.connection import db_connection_sync_scope
from app.domains.kol.account_dossier_extract import upsert_account_dossier_extract
from app.domains.projects import contracts as project_contracts
from app.domains.projects import retrospective_aggregate as project_retrospective
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.kol import search_sessions as kol_search_sessions
from app.domains.kol.provider_job_access import (
    SESSION_ADVANCE,
    SMART_SEARCH_PROFILE_ADVANCE,
    authorize_provider_job_before_execution,
    guard_provider_job_before_execution,
)
from app.workers.apify_jobs_worker_helpers import (
    _int_or_none,
    _json,
)
from app.workers import apify_jobs_worker_deep_crawl as deep_crawl_worker
from app.workers.apify_jobs_worker_session_convergence import session_max_running_seconds


logger = get_logger(__name__)


def _process_session_advance(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    if not guard_provider_job_before_execution(conn, job, payload, expected_action=SESSION_ADVANCE):
        return
    session_id = _int_or_none(payload.get("search_session_id") or payload.get("target_id"))
    if not session_id:
        raise ValueError("session_advance payload must include search_session_id")
    try:
        kol_search_sessions.update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={
                "profile_batch_advance_job": {
                    "status": "running",
                    "job_id": int(job["id"]),
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        kol_search_sessions.mark_items_profile_running(
            int(session_id),
            job_id=int(job["id"]),
            reason="session_advance_worker_claimed",
        )
        result = kol_profile_discovery.advance_search_session_items(
            session_id=int(session_id),
            body={**payload, "execute": True},
        )
    except Exception as exc:
        try:
            kol_search_sessions.update_session_result_summary(
                int(session_id),
                status="failed",
                summary_patch={
                    "profile_batch_advance_job": {
                        "status": "failed",
                        "job_id": int(job["id"]),
                        "error": str(exc)[:1000],
                        "viltrox_fit_score_untouched": True,
                    }
                },
            )
        except Exception as inner_exc:
            logger.warning("session_advance failure summary update failed | job_id=%s error=%s", job.get("id"), inner_exc)
        raise

    job_status = "failed" if result.get("status") == "failed" else "done"
    last_error = "" if job_status == "done" else str(result.get("status") or "session_advance_failed")
    payload["session_advance_result"] = {
        "status": result.get("status"),
        "selected": result.get("selected"),
        "eligible": result.get("eligible"),
        "overflow": result.get("overflow"),
        "counts": result.get("counts"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    payload["search_session_last_job_status"] = job_status
    payload["search_session_last_error"] = last_error
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _process_smart_search_profile_advance(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    provider_actor = authorize_provider_job_before_execution(
        conn,
        job,
        payload,
        expected_action=SMART_SEARCH_PROFILE_ADVANCE,
    )
    if provider_actor is None:
        return
    session_id = _int_or_none(payload.get("search_session_id") or payload.get("target_id"))
    if not session_id:
        raise ValueError("smart_search_profile_advance payload must include search_session_id")
    try:
        kol_search_sessions.update_session_result_summary(
            int(session_id),
            status="running",
            summary_patch={
                "smart_search_profile_advance_job": {
                    "status": "running",
                    "job_id": int(job["id"]),
                    "query_text": payload.get("query_text"),
                    # 终态判定预算:子任务排队/被拦超过该秒数,会话按「部分完成」收敛(见 *_session_convergence)。
                    "max_running_sec": session_max_running_seconds(),
                    "viltrox_fit_score_untouched": True,
                }
            },
        )
        result = asyncio.run(
            kol_profile_discovery.execute_smart_search_profile_advance_pipeline(
                session_id=int(session_id),
                payload={**payload, "job_id": int(job["id"])},
                provider_actor=provider_actor,
            )
        )
    except Exception as exc:
        try:
            kol_search_sessions.update_session_result_summary(
                int(session_id),
                status="failed",
                summary_patch={
                    "smart_search_profile_advance_job": {
                        "status": "failed",
                        "job_id": int(job["id"]),
                        "query_text": payload.get("query_text"),
                        "error": str(exc)[:1000],
                        "viltrox_fit_score_untouched": True,
                    }
                },
            )
        except Exception as inner_exc:
            logger.warning("smart_search_profile_advance failure summary update failed | job_id=%s error=%s", job.get("id"), inner_exc)
        raise

    job_status = "failed" if result.get("status") == "failed" else "done"
    last_error = "" if job_status == "done" else str(result.get("status") or "smart_search_profile_advance_failed")
    advance = result.get("advance") if isinstance(result.get("advance"), dict) else {}
    new_discovery = result.get("new_discovery") if isinstance(result.get("new_discovery"), dict) else {}
    payload["smart_search_profile_advance_result"] = {
        "status": result.get("status"),
        "session_id": result.get("session_id"),
        "query": result.get("query"),
        "recall": result.get("recall"),
        "new_discovery_status": new_discovery.get("status") if new_discovery else "not_requested",
        "new_discovery_counts": new_discovery.get("counts") if new_discovery else None,
        "advance_status": advance.get("status"),
        "advance_selected": advance.get("selected"),
        "advance_counts": advance.get("counts"),
        # 诚实信号:LLM planner('llm_plan')vs rule_v0 兜底('rule_v0_fallback'),源自 pipeline 返回。
        "query_plan_source": result.get("query_plan_source"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids"),
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
    }
    payload["search_session_last_job_status"] = job_status
    payload["search_session_last_error"] = last_error
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _process_account_dossier_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    kol_pool_id = _int_or_none(payload.get("target_id") or payload.get("kol_pool_id"))
    if not kol_pool_id:
        raise ValueError("account_dossier_extract payload must include target_id")
    result = upsert_account_dossier_extract(conn, int(kol_pool_id))
    job_status = "done" if result.get("status") == "ready" else "blocked"
    last_error = "" if job_status == "done" else str(result.get("reason") or result.get("status") or "account_dossier_extract_not_ready")
    payload["account_dossier_extract_result"] = result
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _resolve_job_staff(conn: psycopg.Connection[Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Build the FK-safe staff identity for worker-run domain calls."""
    return deep_crawl_worker.resolve_job_staff(
        conn,
        payload,
        int_or_none=_int_or_none,
        row_factory=dict_row,
    )


def _process_project_contract_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    contract_id = _int_or_none(payload.get("target_id"))
    project_id = _int_or_none(payload.get("project_id"))
    if not contract_id or not project_id:
        raise ValueError("project_contract_extract payload must include target_id and project_id")
    staff = _resolve_job_staff(conn, payload)
    # contracts.py uses sqlite-compat '?' via its own get_conn(); run inside the sync scope so
    # placeholders are translated and never touch this worker's raw psycopg connection.
    # R2 ordering: the core writes domain state (extraction_status='ready'/'failed' + cache)
    # and commits BEFORE we mark the job done; on failure it re-raises to _fail_job. Re-runs
    # are idempotent (cache upsert + contract row UPDATE overwrite with the same result).
    with db_connection_sync_scope():
        project_contracts.run_contract_extraction_for_job(int(project_id), int(contract_id), staff=staff)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, updated_at=NOW() WHERE id=%s",
                (int(job["id"]),),
            )


def _process_kol_profile_deep_crawl(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """队列铁律(2026-06-12):账号深爬 execute 走队列,泳道可见;内核与 HTTP execute 同一条。"""
    from app.domains.kol import url_deep_crawl as kol_url_deep_crawl
    from app.domains.kol import url_deep_crawl_queue
    from app.domains.kol.video_tracking import VideoTrackingError

    staff = _resolve_job_staff(conn, payload)
    try:
        with db_connection_sync_scope():
            result = kol_url_deep_crawl.run_profile_deep_crawl_for_job(payload, staff=staff)
    except VideoTrackingError as exc:
        # A durable My-KOL actor/target fence is an authorization decision, not
        # a transient provider failure.  Terminalize it as blocked so the
        # global worker retry path cannot spend later after permission or URL
        # state has already been revoked/drifted.
        if deep_crawl_worker.terminalize_write_fence_error(
            conn,
            job,
            payload,
            exc,
            terminal_codes=url_deep_crawl_queue.TARGET_WRITE_FENCE_TERMINAL_CODES,
            db_connection_sync_scope=db_connection_sync_scope,
            json_dump=_json,
            logger=logger,
        ):
            return
        raise
    ok, status = deep_crawl_worker.crawl_outcome(result)
    # search_session_id 回写 payload:queue_view 据此输出 search_session,
    # 泳道「最近完成」才会保留该任务并支持点开会话详情(一闪而过案)。
    session_id = _int_or_none((result or {}).get("search_session_id"))
    if session_id:
        payload["search_session_id"] = int(session_id)
    deep_crawl_worker.persist_crawl_outcome(
        conn,
        job,
        payload,
        ok=ok,
        status=status,
        json_dump=_json,
    )
    deep_crawl_worker.record_monitor_terminal(
        job,
        payload,
        ok=ok,
        db_connection_sync_scope=db_connection_sync_scope,
        logger=logger,
    )
    # 账号深爬完成后只进入新 durable L0 编排。该编排读取已经落库的 raw/bio，绝不在
    # 此处追加 provider、网站抓取或发送；需要外部动作的状态交给后续人工授权流程。
    kol_pool_id = deep_crawl_worker.crawl_kol_pool_id(
        payload,
        result,
        int_or_none=_int_or_none,
    )
    if ok and kol_pool_id:
        deep_crawl_worker.run_success_followups(
            int(kol_pool_id),
            payload,
            staff,
            db_connection_sync_scope=db_connection_sync_scope,
            logger=logger,
        )


def _process_logistics_track_sync(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """17track 物流同步(2026-06-12):注册+拉取轨迹写回 shipping 元数据,泳道可见。"""
    from app.domains.logistics import seventeen_track

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = seventeen_track.run_logistics_sync_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    payload["logistics_sync_result"] = {k: v for k, v in (result or {}).items() if k != "results"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "logistics_sync_failed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_kol_auto_poll(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """D3 关注KOL轻量轮询(metadata_light):为该 KOL 触发一次轻量 profile 刷新。

    治本:此前 worker 无此 handler → kol_auto_poll job 落 _target 兜底炸
    ValueError("payload must include target_type and target_id") → 入队 50 条全 failed。
    payload 带 kol_pool_id(+handle/platform);取 profile_url → 入轻量 profile 抓取队列
    (max_posts=1,只刷档案,不跑视频/不烧 LLM)。无 kol_pool_id / 无 url → 诚实 done(原因落 payload)。
    红线:绝不触 viltrox_fit_score。SELECT/enqueue 走 compat get_conn(?),job 终态走 worker conn(%s)。
    """
    kid = _int_or_none(payload.get("kol_pool_id"))
    note = "auto_poll_no_kol_id"
    enqueue_res: Any = None
    if kid:
        from app.db.connection import get_conn
        from app.domains.kol import url_deep_crawl

        with db_connection_sync_scope():
            row = get_conn().execute(
                "SELECT profile_url FROM vkpi_kol_pool WHERE id = ?", (int(kid),)
            ).fetchone()
            url = str(((dict(row).get("profile_url")) if row else "") or "").strip()
            if not url:
                note = "auto_poll_no_profile_url"
            else:
                enqueue_res = url_deep_crawl.enqueue_profile_deep_crawl_job(
                    url, kol_pool_id=int(kid), max_posts=1, staff=None, queue_lane="batch"
                )
                note = "metadata_light_refresh_enqueued"
    payload["auto_poll_result"] = {"note": note, "kol_pool_id": kid, "enqueue": enqueue_res}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status='done', last_error=NULL, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (_json(payload), int(job["id"])),
            )


def _process_kol_outreach_draft(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """联系草稿(2026-06-12 裁令):LLM 经队列生成外联消息,产物落 cache(kol_outreach_draft_v1)。"""
    from app.domains.kol import outreach_draft as kol_outreach_draft

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = kol_outreach_draft.run_outreach_draft_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "outreach_draft_failed"))[:300],
                    int(job["id"]),
                ),
            )


def _process_kol_content_fit_analysis(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """收口路①-2:内容契合深析(「思考中」)。读该 KOL 视频画面/故事 + 评论 → openai(GPT)+3重试
    → 落独立 cache(target_type='kol'/derive_method='content_fit_v1')。

    红线:零触 viltrox_fit_score;LLM/重试/cache 全由 content_fit_analysis 域内负责,本 handler
    只做调度与状态回写。无视频证据 → status='insufficient_evidence',标 blocked(不烧 LLM,诚实)。
    """
    from app.domains.kol import content_fit_analysis as kol_content_fit

    kol_pool_id = _int_or_none(payload.get("kol_pool_id") or payload.get("target_id"))
    if not kol_pool_id:
        raise ValueError("kol_content_fit_analysis payload must include kol_pool_id")
    staff = _resolve_job_staff(conn, payload)
    class _RuntimeScopeRevoked(RuntimeError):
        def __init__(self, reason: str, provider_calls_performed: bool) -> None:
            super().__init__(reason)
            self.reason = reason
            self.provider_calls_performed = provider_calls_performed

    def authorization_checkpoint(provider_calls_performed: bool) -> None:
        from app.workers.apify_jobs_worker_paid_scope import revalidate_paid_job_scope

        _action, reason, _actor = revalidate_paid_job_scope(
            payload,
            "kol_content_fit_analysis",
            connection_scope=db_connection_sync_scope,
        )
        if reason:
            raise _RuntimeScopeRevoked(reason, provider_calls_performed)

    try:
        with db_connection_sync_scope():
            result = kol_content_fit.analyze_content_fit(
                int(kol_pool_id),
                str(payload.get("product_sku") or "") or None,
                staff=staff if isinstance(staff, dict) else None,
                authorization_checkpoint=authorization_checkpoint,
            )
    except _RuntimeScopeRevoked as exc:
        from app.workers.apify_jobs_worker import _block_job

        _block_job(
            conn,
            int(job["id"]),
            exc.reason,
            {
                "provider_calls_performed": exc.provider_calls_performed,
                "paid_action": "content_fit_analysis",
            },
        )
        return
    state = str((result or {}).get("state") or (result or {}).get("status") or "")
    ok = state == "ready"
    # insufficient_evidence / llm_failed 不是错误态,但也无 cache 产出 → blocked(可见、不重试雪崩)。
    last_error = "" if ok else (str((result or {}).get("reason") or state or "content_fit_not_ready"))
    payload["content_fit_result"] = {
        "state": state,
        "kol_pool_id": kol_pool_id,
        "fit_verdict": ((result or {}).get("result") or {}).get("fit_verdict"),
        "confidence": ((result or {}).get("result") or {}).get("confidence"),
        "cached": (result or {}).get("cached"),
        "viltrox_fit_score_untouched": True,
    }
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=NULLIF(%s, ''), payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    last_error[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_contract_invoice_extract(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """发票回填提取(批E,2026-06-12):读本地存证文件 → Claude 提取收款字段 → cache(invoice)。
    失败域内不写 cache,这里标 blocked(模式同 _process_kol_outreach_draft)。"""
    from app.domains.projects import contract_assist

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = contract_assist.run_invoice_extract_for_job(
            payload, staff=staff, enforce_access_fence=True)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "invoice_extract_failed"))[:300],
                    int(job["id"]),
                ),
            )


def _process_contract_polish(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """合同条款 LLM 润色(批E,2026-06-12):llm_gateway(preferred openai)→ cache(contract_polish)。
    失败域内不写 cache,这里标 blocked(模式同 _process_kol_outreach_draft)。"""
    from app.domains.projects import contract_assist

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = contract_assist.run_contract_polish_for_job(
            payload, staff=staff, enforce_access_fence=True)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (str((result or {}).get("reason") or status or "contract_polish_failed"))[:300],
                    int(job["id"]),
                ),
            )


# 评论采集 all_posts_failed 的可重试判据(2026-07-11):此前逐帖全败(如 Apify 走代理
# 522)直接落 blocked 终态,永不自愈 —— 而其它任务类型同类瞬时错都经 _fail_job →
# provider_pressure → 退避 requeue 自愈。这里把「网络/5xx/proxy/timeout」词族的全败
# 改抛异常,汇入统一 failure 通道;不可重试类(URL 无效/帖子不存在等)保持 blocked。
_COMMENTS_RETRYABLE_ERROR_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "520",
    "521",
    "522",
    "523",
    "524",
    "5xx",
    "server error",
    "rate limit",
    "quota",
    "timeout",
    "timed out",
    "proxy",
    "connection",
    "reset",
    "refused",
    "unreachable",
    "temporarily",
    "unavailable",
    "network",
    "ssl",
)


def _comments_failed_errors_retryable(results: list[dict[str, Any]] | None) -> tuple[bool, str]:
    """全败批次的逐帖 error 是否全部属可重试类 → (retryable, 样本错误串)。

    只看 status 不在 (ok, skip, not_configured) 的条目;没有任何 error 文本时不可判 →
    保守 False(维持 blocked,不假装能自愈)。
    """
    errors: list[str] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") in ("ok", "skip", "not_configured"):
            continue
        text = str(item.get("error") or "").strip()
        if text:
            errors.append(text)
    if not errors:
        return False, ""
    retryable = all(
        any(marker in err.lower() for marker in _COMMENTS_RETRYABLE_ERROR_MARKERS) for err in errors
    )
    sample = "; ".join(sorted(set(errors))[:3])[:300]
    return retryable, sample


def _process_kol_pool_comments_collect(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
    *,
    paid_action_actor: dict[str, Any] | None = None,
) -> None:
    """KOL Pool 收藏行评论采集(2026-06-12 裁令):逐帖走 collect_post_comments,泳道可见。"""
    from app.domains.comments import collector as comments_collector

    # A fenced job receives the complete, freshly revalidated actor from the
    # dispatcher.  Keep it in memory for the audience follow-up; never widen
    # the durable payload beyond the existing actor identifiers.
    staff = paid_action_actor or _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = dict(comments_collector.run_kol_pool_comments_for_job(payload, staff=staff) or {})
    status = str(result.get("status") or "")
    ok = status == "ready"
    if not ok and "all_posts_failed" in status:
        retryable, sample = _comments_failed_errors_retryable(result.get("results"))
        if retryable:
            # 抛异常 → run_worker 捕获 → _fail_job 分类(522/timeout 等词族命中
            # provider_pressure/timeout)→ 退避 requeue,与其它任务类型自愈通道对齐。
            raise RuntimeError(f"comments_collect_all_posts_failed_retryable: {sample}")
    if ok and int(result.get("posts") or 0) > 0:
        try:
            with db_connection_sync_scope():
                audience_refresh_job = comments_collector.enqueue_kol_audience_stats_refresh_job(
                    int(result.get("kol_pool_id") or payload.get("kol_pool_id") or 0),
                    source_comments_job_id=int(job["id"]),
                    staff=staff,
                    lineage_payload=payload,
                    enforce_target_write=isinstance(
                        payload.get("my_kol_paid_action_fence"),
                        dict,
                    ),
                )
        except Exception:
            # 评论已成功;受众 follow-up 入队故障只做可见降级,
            # 不能把已完成的 Provider 采集反转为失败或触发重拓。
            logger.warning(
                "audience_stats follow-up enqueue failed | comments_job_id=%s kol_pool_id=%s",
                job.get("id"),
                result.get("kol_pool_id") or payload.get("kol_pool_id"),
                exc_info=True,
            )
            audience_refresh_job = {
                "status": "enqueue_failed",
                "job_id": None,
                "queue_lane": "batch",
            }
    else:
        audience_refresh_job = {
            "status": "not_queued",
            "job_id": None,
            "queue_lane": "batch",
            "reason": "comments_job_not_ready" if not ok else "no_posts",
        }
    result["audience_refresh_job"] = audience_refresh_job
    payload["comments_collect_result"] = {k: v for k, v in result.items() if k != "results"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "comments_collect_failed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


_AUDIENCE_REFRESH_DONE_STATUSES = frozenset(
    {"ok", "partial", "skipped", "no_posts", "no_commenters", "unsupported_platform"}
)


def _process_kol_video_metric_refresh(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Refresh one existing evidence row under the worker's provider fence."""

    from app.domains.kol import video_metric_refresh

    with db_connection_sync_scope():
        result = video_metric_refresh.run_video_metric_refresh_for_job(payload)
    result_status = str(result.get("status") or "")
    ok = result_status == "success"
    job_status = "done" if ok else ("blocked" if result_status == "blocked" else "failed")
    # Keep provider responses out of the durable queue payload.  The domain
    # returns only bounded observation identifiers and status fields.
    payload["video_metric_refresh_result"] = result
    last_error = "" if ok else str(
        result.get("error_code") or "video_metric_refresh_failed"
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (
                    job_status,
                    last_error[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_kol_audience_stats_refresh(
    conn: psycopg.Connection[Any],
    job: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Refresh audience intelligence as a durable, non-recursive batch job."""
    from app.domains.kol import audience_stats

    kol_pool_id = _int_or_none(payload.get("kol_pool_id") or payload.get("target_id"))
    if not kol_pool_id:
        raise ValueError("kol_audience_stats_refresh payload must include kol_pool_id")
    with db_connection_sync_scope():
        result = dict(
            audience_stats.refresh_audience_stats(
                int(kol_pool_id),
                enqueue_if_missing=False,
                allow_avatar_provider=False,
            )
            or {}
        )
    status = str(result.get("status") or "")
    job_status = "done" if status in _AUDIENCE_REFRESH_DONE_STATUSES else "blocked"
    # The full audience document already lives on vkpi_kol_pool; duplicating it
    # into every queue payload would make claims, heartbeat views, and history
    # reads heavier.  Keep only the execution summary in the durable Job.
    payload["audience_refresh_result"] = {
        key: value for key, value in result.items() if key != "audience"
    }
    last_error = "" if job_status == "done" else str(
        result.get("reason") or status or "audience_refresh_failed"
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s,
                    last_error=NULLIF(%s, ''),
                    payload=%s::jsonb,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )


def _process_official_channel_comments_collect(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    """官号评论批量采集(2026-07-11 治「worker 无 handler → 滑进 mock 假成功」):
    逐帖复用 collect_channel_post_comments,模式对齐 _process_kol_pool_comments_collect。
    X 官号缺 token 由域内标 not_configured 计 skipped,不算失败。全败且逐帖错误属
    可重试类(网络/5xx/proxy/timeout)→ 抛异常走统一 failure→requeue 通道。"""
    from app.domains.comments import channel as comments_channel

    staff = _resolve_job_staff(conn, payload)
    with db_connection_sync_scope():
        result = comments_channel.run_official_channel_comments_for_job(payload, staff=staff)
    status = str((result or {}).get("status") or "")
    ok = status == "ready"
    payload["comments_collect_result"] = {k: v for k, v in (result or {}).items() if k != "results"}
    if not ok and "all_posts_failed" in status:
        retryable, sample = _comments_failed_errors_retryable((result or {}).get("results"))
        if retryable:
            raise RuntimeError(f"comments_collect_all_posts_failed_retryable: {sample}")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE apify_jobs SET status=%s, last_error=%s, payload=%s::jsonb, updated_at=NOW() WHERE id=%s",
                (
                    "done" if ok else "blocked",
                    None if ok else (status or "comments_collect_failed")[:300],
                    _json(payload),
                    int(job["id"]),
                ),
            )


def _process_project_retrospective(conn: psycopg.Connection[Any], job: dict[str, Any], payload: dict[str, Any]) -> None:
    project_id = _int_or_none(payload.get("target_id") or payload.get("project_id"))
    if not project_id:
        raise ValueError("project_retrospective_aggregate payload must include target_id")
    staff = _resolve_job_staff(conn, payload)
    # retrospective_aggregate uses sqlite-compat '?' via its own get_conn(); run inside the sync scope.
    # R2 ordering: the domain writes cache(project) BEFORE we mark the job done; on no-ready/failure it
    # writes nothing to cache and we mark the job blocked (not done) so the UI/读端能区分。
    # Never touches vkpi_kol_pool / fit_score.
    with db_connection_sync_scope():
        result = project_retrospective.run_project_retrospective(
            int(project_id), staff=staff, access_payload=payload)
    status = str(result.get("status") or "")
    job_status = "done" if status == "ready" else "blocked"
    last_error = "" if job_status == "done" else str(result.get("reason") or status or "project_retrospective_not_ready")
    payload["project_retrospective_result"] = {k: v for k, v in result.items() if k != "result"}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status=%s, last_error=NULLIF(%s, ''), payload=%s::jsonb, updated_at=NOW()
                WHERE id=%s
                """,
                (job_status, last_error[:2000], _json(payload), int(job["id"])),
            )
