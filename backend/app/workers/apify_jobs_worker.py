"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import socket
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from app.core.config import DB_RUNTIME_URL
from app.core.logging import get_logger
from app.db.connection import close_db_runtime_sync, db_connection_sync_scope
from app.domains.costs import budget_guard
from app.domains.kol.account_dossier_extract import upsert_account_dossier_extract
from app.domains.projects import contracts as project_contracts
from app.domains.projects import retrospective_aggregate as project_retrospective
from app.domains.kol.final_v1_extract import upsert_deep_analysis_from_final_v1_cache
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.kol import search_sessions as kol_search_sessions
from app.platform import llm_gateway
from app.services.media.video_download import download_direct_video_url
from app.domains.media.cache import cache_local_video_file
from app.domains.kol.url_deep_crawl_helpers import _video_id as _content_url_video_id
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from .apify_jobs_worker_helpers import (
    _float_or_none,
    _iso_or_none,
    _rate,
    _truthy,
    _as_dict,
    _compact_text,
    _derive_method,
    _error_category,
    _failure_disposition,
    _final_v1_payload,
    _int_or_none,
    _json,
    _loads,
    _parse_apify_resolver_stdout,
    _parse_last_json_stdout,
    _platform_from_content_url,
    _provider_retry_reason,
    _redact_sensitive_text,
    _score_confidence,
    _score_value,
    _target,
    _url_host,
)


logger = get_logger(__name__)
POLL_SECONDS = float(os.environ.get("APIFY_WORKER_POLL_SECONDS", "2"))
MEDIA_RESOLVE_TIMEOUT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_MEDIA_RESOLVE_TIMEOUT_SEC", "90")))
GEMINI_CALL_TIMEOUT_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TIMEOUT_SEC", "1200")))
GEMINI_CALL_TERMINATE_GRACE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_GEMINI_CALL_TERMINATE_GRACE_SEC", "5")))
STALE_RUNNING_MINUTES = max(1, int(os.environ.get("APIFY_WORKER_STALE_RUNNING_MINUTES", "10")))
STALE_RECLAIM_SECONDS = STALE_RUNNING_MINUTES * 60
STALE_RECLAIM_POLL_SECONDS = max(30, int(os.environ.get("APIFY_WORKER_STALE_RECLAIM_POLL_SECONDS", "60")))
RUNNING_HEARTBEAT_SECONDS = max(10, int(os.environ.get("APIFY_WORKER_RUNNING_HEARTBEAT_SECONDS", "30")))
# DB 连接瞬断后重连前等待秒数。治 6/23 那次:连接丢失 → 未捕获 → worker 永久死 3.5 天。
WORKER_DB_RECONNECT_SECONDS = max(2, int(os.environ.get("APIFY_WORKER_DB_RECONNECT_SECONDS", "5")))
# T5 真实存活:每轮 poll(含空闲)向 vkpi_worker_heartbeat UPSERT 一行,
# system_health._worker_online 据此判在线(MAX 全表聚合,与名字无关);逻辑 worker 名可经 env 覆盖。
# 默认带主机名:多机/多车道认领时心跳行与 lease_owner 可辨,不再互相覆盖。
_DEFAULT_WORKER_NAME = f"apify_jobs_worker-{socket.gethostname()}"
WORKER_HEARTBEAT_NAME = os.environ.get("APIFY_WORKER_HEARTBEAT_NAME", _DEFAULT_WORKER_NAME).strip() or _DEFAULT_WORKER_NAME
# 车道过滤:interactive=只认领交互档(无 batch 标记);all=全量(默认,行为不变)。
# 值域是代码内白名单,拼接进 SQL 的片段为常量,无注入面。
CLAIM_LANE = os.environ.get("APIFY_WORKER_CLAIM_LANE", "all").strip().lower()
CLAIM_LANE_SQL = (
    "AND COALESCE(payload->>'batch', '') NOT IN ('on_demand_batch', 'recent', 'remaining')"
    if CLAIM_LANE == "interactive"
    else ""
)
MAX_JOB_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_MAX_ATTEMPTS", "2")))
PROVIDER_RETRY_MAX_ATTEMPTS = max(1, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_MAX_ATTEMPTS", "5")))
PROVIDER_RETRY_BASE_SECONDS = max(1, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_BASE_SECONDS", "60")))
PROVIDER_RETRY_MAX_DELAY_SECONDS = max(
    PROVIDER_RETRY_BASE_SECONDS,
    int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_MAX_DELAY_SECONDS", "960")),
)
PROVIDER_RETRY_JITTER_RATIO = max(0.0, min(0.5, float(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_JITTER_RATIO", "0.20"))))
PROVIDER_RETRY_ADOPT_WINDOW_MINUTES = max(0, int(os.environ.get("APIFY_WORKER_PROVIDER_RETRY_ADOPT_WINDOW_MINUTES", "1440")))
LLM_BUDGET_SCOPE = os.environ.get("APIFY_WORKER_LLM_BUDGET_SCOPE", "cron:vkpi_analysis_worker")
# 旧 min(6) 硬钳是预算安全时代产物;预算闸/台账已成熟,上限交给 env(全 fleet 同值,advisory lock 库级全局)。
LLM_CONCURRENCY_LIMIT = max(1, int(os.environ.get("APIFY_WORKER_LLM_CONCURRENCY", "2")))
# 1200 太小:6 层 final_v1 JSON(含整条 scene_timeline)会被截断,分镜只剩前 ~35s。
# 抬到 4096 容纳整段视频的分镜时间线(完整不截断);可经 env 覆盖。
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get("APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "4096"))
# 0.05 是免费层口径(每条 job 前最多干等 20s,250 条批量净耗 ~83 分钟);付费层 RPM 数百,0.5 仍留 60 倍余量。
GEMINI_QPS_LIMIT = max(0.0, float(os.environ.get("APIFY_WORKER_GEMINI_QPS", "0.5")))
GEMINI_MIN_INTERVAL_SECONDS = max(
    0.0,
    float(os.environ.get("APIFY_WORKER_GEMINI_MIN_INTERVAL_SEC", str((1.0 / GEMINI_QPS_LIMIT) if GEMINI_QPS_LIMIT > 0 else 0.0))),
)
LLM_TARGET_TYPES = {"video", "contract"}
GEMINI_VIDEO_V2_DERIVE_METHODS = {
    "gemini_video_v2",
    "gemini_video_v2_pro_single",
    "gemini_video_v2_flash_pro_judge",
    "gemini_video_v2_flash_gpt55_judge",
    "gemini_video_v2_flash_claude_judge",
}
FINAL_V1_KEYFRAME_QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
GEMINI_VIDEO_FINAL_DERIVE_METHODS = {"video_analysis_final_v1", FINAL_V1_KEYFRAME_QA_DERIVE_METHOD}
GEMINI_VIDEO_DERIVE_METHODS = {"gemini", *GEMINI_VIDEO_V2_DERIVE_METHODS, *GEMINI_VIDEO_FINAL_DERIVE_METHODS}
WORKER_GEMINI_MODEL = os.environ.get("APIFY_WORKER_GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
FINAL_V1_GEMINI_MODELS = gemini_video_analyzer.final_v1_gemini_models()
FINAL_V1_KEYFRAME_QA_MODEL = os.environ.get("GEMINI_FINAL_V1_QA_MODEL", "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
_stop_event = threading.Event()
_gemini_qps_lock = threading.Lock()
_last_gemini_call_started_at = 0.0


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _provider_retry_delay_seconds(next_attempt: int) -> int:
    attempt = max(1, int(next_attempt or 1))
    base_delay = min(PROVIDER_RETRY_MAX_DELAY_SECONDS, PROVIDER_RETRY_BASE_SECONDS * (4 ** max(0, attempt - 1)))
    if PROVIDER_RETRY_JITTER_RATIO <= 0:
        return int(base_delay)
    jitter = random.uniform(0, base_delay * PROVIDER_RETRY_JITTER_RATIO)
    return int(min(PROVIDER_RETRY_MAX_DELAY_SECONDS, round(base_delay + jitter)))


def _respect_gemini_qps() -> None:
    global _last_gemini_call_started_at
    if GEMINI_MIN_INTERVAL_SECONDS <= 0:
        return
    with _gemini_qps_lock:
        now = time.monotonic()
        wait_seconds = (_last_gemini_call_started_at + GEMINI_MIN_INTERVAL_SECONDS) - now
        if wait_seconds > 0:
            logger.info("gemini qps throttle sleep | seconds=%.2f", wait_seconds)
            time.sleep(wait_seconds)
            now = time.monotonic()
        _last_gemini_call_started_at = now


# 媒体解析 / 子进程分析器 / R2 回灌簇整簇已抽到 apify_jobs_worker_media.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。本 import 必须在
# 上面超时常量(GEMINI_CALL_*/MEDIA_RESOLVE_*)定义之后(它们在 media 模块底部被 import)。
from app.workers.apify_jobs_worker_media import (  # noqa: E402
    _gemini_analyzer_child_code,
    _mock_result,
    _resolve_video_media,
    _run_gemini_analyzer_with_timeout,
    _scrape_with_apify_timeout,
    _warm_video_to_r2_from_local,
)


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


# 搜索会话同步 + final_v1 后续链式入队整簇已抽到 apify_jobs_worker_session.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。
from app.workers.apify_jobs_worker_session import (  # noqa: E402
    _enqueue_account_dossier_extract_after_final_v1,
    _enqueue_comments_collect_after_final_v1,
    _enqueue_content_fit_after_final_v1,
    _kol_pool_id_from_evidence,
    _rebuild_search_session_summary,
    _score_entry,
    _search_session_analysis_summary_from_ready_cache,
    _search_session_analysis_summary_from_result,
    _search_session_item_counts,
    _search_session_job_state,
    _search_session_status_from_items,
    _session_url_enrichment_error,
    _sync_deep_analysis_result_from_cache,
    _sync_search_session_job,
    _sync_search_session_job_impl,
)


def _finish_skipped(conn: psycopg.Connection[Any], job_id: int, reason: str) -> None:
    analysis_summary: dict[str, Any] | None = None
    if "skipped_existing_analysis_cache" in str(reason or ""):
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT payload FROM apify_jobs WHERE id=%s LIMIT 1", (int(job_id),))
                row = cur.fetchone() or {}
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else _loads(row.get("payload"), {})
            analysis_summary = _search_session_analysis_summary_from_ready_cache(conn, payload if isinstance(payload, dict) else {})
            cache_id = _int_or_none((analysis_summary or {}).get("cache_id"))
            deep_result = _sync_deep_analysis_result_from_cache(
                conn,
                cache_id=cache_id,
                derive_method=_derive_method(payload if isinstance(payload, dict) else {}),
                job_id=int(job_id),
            )
            account_extract_job = _enqueue_account_dossier_extract_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            # L2:final_v1 就绪 → 链式入队内容契合深析(每 KOL 一次,cache 复用/去重/预算闸控量)。
            content_fit_job = _enqueue_content_fit_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            if analysis_summary and content_fit_job:
                analysis_summary["content_fit_job"] = content_fit_job
            # 2026-06-16:视频深析就绪 → 链式入队评论采集(用户要求:评论也要抓)。
            comments_collect_job = _enqueue_comments_collect_after_final_v1(
                conn,
                job_id=int(job_id),
                deep_result=deep_result,
            )
            if analysis_summary and comments_collect_job:
                analysis_summary["comments_collect_job"] = comments_collect_job
            if analysis_summary and deep_result:
                analysis_summary["deep_result"] = deep_result
            if analysis_summary and account_extract_job:
                analysis_summary["account_dossier_extract_job"] = account_extract_job
        except Exception as exc:
            logger.warning("skipped cache deep/account sync failed | job_id=%s error=%s", job_id, exc)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='done',
                    last_error=%s,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (reason[:2000], job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="done", reason=reason, analysis_summary=analysis_summary)


def _block_job(conn: psycopg.Connection[Any], job_id: int, reason: str, detail: dict[str, Any] | None = None) -> None:
    payload = {"reason": reason, **(detail or {})}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='blocked',
                    last_error=%s,
                    last_error_category='blocked',
                    next_retry_at=NULL,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (_json(payload)[:2000], job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="blocked", reason=_json(payload))


def _requeue_job(conn: psycopg.Connection[Any], job_id: int, reason: str, *, retry_delay_seconds: float = 0.0) -> None:
    # 原因同步落 journal(此前只存在于会被下次 claim 清掉的 last_error,排障只能靠猜);
    # retry_delay_seconds>0 时写 next_retry_at 退避——根治槽满时空闲车道每 2s 集体抢-退(实测 435 次/5min)。
    logger.info("job requeued | id=%s delay=%.1fs reason=%s", job_id, float(retry_delay_seconds or 0.0), reason[:120])
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='queued',
                    last_error=%s,
                    last_error_category=NULL,
                    next_retry_at=CASE WHEN %s::float8 > 0 THEN NOW() + make_interval(secs => %s) ELSE NULL END,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (reason[:2000], float(retry_delay_seconds or 0.0), float(retry_delay_seconds or 0.0), job_id),
            )
    _sync_search_session_job(conn, job_id, raw_status="queued", reason=reason)


# 单一 job_type 处理簇整簇已抽到 apify_jobs_worker_handlers.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。
from app.workers.apify_jobs_worker_handlers import (  # noqa: E402
    _process_account_dossier_extract,
    _process_contract_invoice_extract,
    _process_contract_polish,
    _process_kol_auto_poll,
    _process_kol_content_fit_analysis,
    _process_kol_outreach_draft,
    _process_kol_pool_comments_collect,
    _process_kol_profile_deep_crawl,
    _process_logistics_track_sync,
    _process_project_contract_extract,
    _process_project_retrospective,
    _process_session_advance,
    _process_smart_search_profile_advance,
    _resolve_job_staff,
)


# LLM 预算 preflight 簇 + keyframe 抽帧/Gemini override 簇整簇已抽到 apify_jobs_worker_prep.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。本 import 必须放在
# 上面常量(LLM_MAX_OUTPUT_TOKENS/LLM_BUDGET_SCOPE/WORKER_GEMINI_MODEL)定义之后
# (它们在 prep 模块底部被 import)。
from app.workers.apify_jobs_worker_prep import (  # noqa: E402
    _download_youtube_for_keyframes,
    _extract_keyframes_for_qa,
    _gemini_worker_overrides,
    _google_allowed,
    _llm_budget_preflight,
    _load_video_evidence,
    _log_budget_preflight_record_only,
    _provider_allowed,
    _provider_budget_preflight,
)


# 成本/定价核算已抽到 apify_jobs_cost.py(行为不变,re-export 兜住调用点)。
from app.workers.apify_jobs_cost import (  # noqa: E402
    _anthropic_cost,
    _gemini_cost,
    _gemini_input_cost_usd,
    _gemini_output_rate_usd_per_mtok,
    _openai_cost,
    _usage_count,
)


# 视频上下文塑形已抽到 apify_jobs_video_context.py(行为不变,re-export 兜调用点)。
from app.workers.apify_jobs_video_context import (  # noqa: F401,E402
    _low_scores,
    _select_keyframe_requests,
    _video_final_context,
    _video_performance_context,
)


# Gemini 视频分析处理簇 + 成本入账 + 结果塑形/落库整簇已抽到 apify_jobs_worker_gemini.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。本 import 必须放在
# 上面所有被 gemini 模块依赖的常量/小工具定义之后(它们在 gemini 模块底部被 import)。
from app.workers.apify_jobs_worker_gemini import (  # noqa: E402
    _process_gemini_video,
    _process_gemini_video_final_v1_keyframe_qa,
    _process_gemini_video_flash_claude_judge,
    _process_gemini_video_flash_gpt55_judge,
    _process_gemini_video_flash_pro_judge,
    _record_anthropic_cost,
    _record_gemini_cost,
    _record_openai_cost,
    _shape_gemini_result,
    _write_gemini_cache,
)



def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT id, job_type, payload, attempts, next_retry_at, last_error_category
                FROM apify_jobs
                WHERE status = 'queued'
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                  {CLAIM_LANE_SQL}
                ORDER BY
                  CASE
                    WHEN payload->>'batch' = 'on_demand_batch' THEN 1
                    WHEN payload->>'batch' IN ('recent', 'remaining') THEN 2
                    ELSE 0
                  END,
                  COALESCE(next_retry_at, created_at), created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            job = cur.fetchone()
            if not job:
                return None
            cur.execute(
                """
                UPDATE apify_jobs
                SET status='running',
                    last_error=NULL,
                    last_error_category=NULL,
                    next_retry_at=NULL,
                    started_at=NOW(),
                    lease_owner=%s,
                    lease_expires_at=NOW() + make_interval(secs => %s),
                    updated_at=NOW()
                WHERE id=%s
                """,
                # Fabric 增量1:claim 即写显式租约(owner=worker:pid,TTL=STALE_RECLAIM_SECONDS,
                # 与今天 reclaim 时序一致)。本增量只「写」租约,reclaim 仍判 updated_at,零行为变更。
                (f"{WORKER_HEARTBEAT_NAME}:{os.getpid()}", STALE_RECLAIM_SECONDS, job["id"]),
            )
            claimed = dict(job)
    _sync_search_session_job(conn, int(claimed["id"]), raw_status="running")
    return claimed


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
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
    if str(job.get("job_type") or "").strip().lower() == "kol_profile_deep_crawl":
        _process_kol_profile_deep_crawl(conn, job, payload)
        return
    if str(job.get("job_type") or "").strip().lower() == "kol_pool_comments_collect":
        _process_kol_pool_comments_collect(conn, job, payload)
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
            _requeue_job(conn, int(job["id"]), "analysis target already in progress", retry_delay_seconds=random.uniform(2.0, 5.0))
            return
        slot = _acquire_llm_slot(conn)
        try:
            if slot is None:
                _requeue_job(conn, int(job["id"]), "llm concurrency limit reached", retry_delay_seconds=random.uniform(5.0, 10.0))
                return
            if _analysis_cache_exists(conn, target_type, target_id, derive_method):
                _finish_skipped(conn, int(job["id"]), "skipped_existing_analysis_cache")
                return
            preflight = _llm_budget_preflight(job, payload)
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
            if derive_method in GEMINI_VIDEO_DERIVE_METHODS:
                _respect_gemini_qps()
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


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
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


def _upsert_worker_heartbeat(conn: psycopg.Connection[Any]) -> None:
    """T5:每轮 poll(含空闲)UPSERT 一行 worker 心跳。失败仅告警,绝不打断 poll 循环。

    若 vkpi_worker_heartbeat 表尚未迁移(140 未跑),静默跳过 —— system_health 会回退到旧的
    apify_jobs 启发式,不影响 worker 处理作业。
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_worker_heartbeat (worker_name, last_heartbeat_at, pid, updated_at)
                VALUES (%s, NOW(), %s, NOW())
                ON CONFLICT (worker_name) DO UPDATE
                SET last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                    pid = EXCLUDED.pid,
                    updated_at = EXCLUDED.updated_at
                """,
                (WORKER_HEARTBEAT_NAME, os.getpid()),
            )
    except Exception as exc:
        logger.warning("apify_jobs worker heartbeat upsert failed | name=%s error=%s", WORKER_HEARTBEAT_NAME, exc)


def _heartbeat_running_job(job_id: int, stop_signal: threading.Event) -> None:
    while not stop_signal.wait(RUNNING_HEARTBEAT_SECONDS):
        try:
            with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        # Fabric 增量1:心跳同时续租约(updated_at 仍写 → 不动今天的 reclaim 判据)。
                        "UPDATE apify_jobs SET updated_at=NOW(), "
                        "lease_expires_at=NOW() + make_interval(secs => %s) "
                        "WHERE id=%s AND status='running'",
                        (STALE_RECLAIM_SECONDS, job_id),
                    )
        except Exception as exc:
            logger.warning("apify_jobs running heartbeat failed | id=%s error=%s", job_id, exc)


@contextmanager
def _running_job_heartbeat(job_id: int):
    stop_signal = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_running_job,
        args=(job_id, stop_signal),
        name=f"apify-job-heartbeat-{job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_signal.set()
        thread.join(timeout=2)


# 失败领养 / 陈旧 running 回收运维簇整簇已抽到 apify_jobs_worker_maintenance.py
# (函数体逐字不变,re-export 兜住所有调用点)。本 import 在上面重试常量定义之后。
from app.workers.apify_jobs_worker_maintenance import (  # noqa: E402
    _adopt_recent_provider_pressure_failures,
    _reclaim_stale_running_jobs,
)


def run_worker() -> None:
    if not DB_RUNTIME_URL:
        raise RuntimeError("DATABASE_URL is required for apify_jobs worker")
    # ytdlp_startup_check(2026-07-02):yt-dlp 二进制部署后曾三次丢失(download 失败桶 15%),
    # 启动时自检并大声报错 —— 不中止(其它 job 类型不依赖它),但日志可查。
    import shutil as _sh

    if not (_sh.which("yt-dlp") or os.path.exists(os.path.join(os.path.dirname(sys.executable), "yt-dlp"))):
        logger.error("yt-dlp binary MISSING - video downloads will fail; run: .venv/bin/pip install yt-dlp")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logger.info(
        "apify_jobs worker started | poll_seconds=%s stale_minutes=%s resolve_timeout_sec=%s gemini_timeout_sec=%s llm_concurrency=%s gemini_qps=%s gemini_min_interval_sec=%s provider_retry_max_attempts=%s provider_retry_base_sec=%s provider_retry_max_delay_sec=%s provider_retry_adopt_window_min=%s",
        POLL_SECONDS,
        STALE_RUNNING_MINUTES,
        MEDIA_RESOLVE_TIMEOUT_SECONDS,
        GEMINI_CALL_TIMEOUT_SECONDS,
        LLM_CONCURRENCY_LIMIT,
        GEMINI_QPS_LIMIT,
        GEMINI_MIN_INTERVAL_SECONDS,
        PROVIDER_RETRY_MAX_ATTEMPTS,
        PROVIDER_RETRY_BASE_SECONDS,
        PROVIDER_RETRY_MAX_DELAY_SECONDS,
        PROVIDER_RETRY_ADOPT_WINDOW_MINUTES,
    )
    try:
        # 外层重连循环:DB 连接断/丢(6/23 'the connection is lost')→ 不让 worker 永久死,
        # 睡几秒拿新连接重来。内层为原有的单连接 poll 循环。
        while not _stop_event.is_set():
            try:
                with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
                    _reclaim_stale_running_jobs(conn)
                    _adopt_recent_provider_pressure_failures(conn)
                    last_reclaim = time.monotonic()
                    _upsert_worker_heartbeat(conn)
                    while not _stop_event.is_set():
                        # T5:每轮 poll(含空闲)写心跳 → 空闲 worker 不再被判离线。
                        _upsert_worker_heartbeat(conn)
                        if time.monotonic() - last_reclaim >= STALE_RECLAIM_POLL_SECONDS:
                            _reclaim_stale_running_jobs(conn)
                            _adopt_recent_provider_pressure_failures(conn)
                            last_reclaim = time.monotonic()
                        job = _claim_job(conn)
                        if not job:
                            _stop_event.wait(POLL_SECONDS)
                            continue
                        if _stop_event.is_set():
                            _requeue_job(conn, int(job["id"]), "worker stop requested before processing")
                            break
                        try:
                            with _running_job_heartbeat(int(job["id"])):
                                _process_job(conn, job)
                            logger.info("apify_jobs job done | id=%s", job["id"])
                        except Exception as exc:
                            logger.error(
                                "apify_jobs job failed | id=%s category=%s error=%s",
                                job.get("id"),
                                _error_category(str(exc)),
                                _redact_sensitive_text(f"{type(exc).__name__}: {exc}"),
                            )
                            _fail_job(conn, int(job["id"]), exc)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                logger.error(
                    "apify_jobs worker db connection lost — reconnecting in %ss | %s",
                    WORKER_DB_RECONNECT_SECONDS,
                    _redact_sensitive_text(f"{type(exc).__name__}: {exc}"),
                )
                _stop_event.wait(WORKER_DB_RECONNECT_SECONDS)
    finally:
        close_db_runtime_sync()
        logger.info("apify_jobs worker stopped")


if __name__ == "__main__":
    run_worker()
