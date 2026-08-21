"""Persistent apify_jobs worker with mock analysis and LLM brake controls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import secrets
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
from app.core.release_validation import release_validation_active
from app.db.connection import close_db_runtime_sync, db_connection_sync_scope
from app.domains.costs import budget_guard
from app.domains.kol.account_dossier_extract import upsert_account_dossier_extract
from app.domains.projects import contracts as project_contracts
from app.domains.projects import retrospective_aggregate as project_retrospective
from app.domains.kol.final_v1_extract import upsert_deep_analysis_from_final_v1_cache
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.kol import search_sessions as kol_search_sessions
from app.domains.local_workers.registry import SAFE_TASK_TYPES as LOCAL_EXCLUSIVE_JOB_TYPES
from app.platform import llm_gateway
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    acquire_provider_execution_claim,
    apify_execution_context,
    finalize_provider_execution_claim,
)
from app.platform.llm_local_evaluation import (
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
    LOCAL_EVALUATION_DERIVE_METHOD,
    LOCAL_EVALUATION_EXECUTION_CLASS,
    verify_job_local_evaluation_capability,
)
from app.services.media.video_download import download_direct_video_url
from app.domains.media.cache import cache_local_video_file
from app.domains.kol.url_deep_crawl_helpers import _video_id as _content_url_video_id
from app.services.ai.analyzers import gemini_video as gemini_video_analyzer
from .apify_job_lane import (
    claim_lane_sql,
    normalize_claim_lane,
    queue_lane_sql_expression,
    queue_priority_sql_expression,
    queue_service_priority_sql_expression,
)
from .apify_job_resource_slots import (
    RESOURCE_SLOT_SCOPE,
    acquire_resource_slot,
    resource_group_for_job,
    resource_slot_limits,
)
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
# T5 真实存活:每轮 poll UPSERT vkpi_worker_heartbeat;worker 名默认带主机名防多车道互覆,可 env 覆盖。
_DEFAULT_WORKER_NAME = f"apify_jobs_worker-{socket.gethostname()}-{os.getpid()}"
WORKER_HEARTBEAT_NAME = os.environ.get("APIFY_WORKER_HEARTBEAT_NAME", _DEFAULT_WORKER_NAME).strip() or _DEFAULT_WORKER_NAME
_WORKER_GIT_SHA_RAW = str(os.environ.get("APP_GIT_SHA") or "").strip().lower()
WORKER_GIT_SHA = _WORKER_GIT_SHA_RAW if re.fullmatch(r"[0-9a-f]{40}", _WORKER_GIT_SHA_RAW) else ""
_WORKER_BOOT_NONCE = str(os.environ.get("VKPI_WORKER_BOOT_NONCE") or "").strip() or secrets.token_urlsafe(32)
WORKER_BOOT_NONCE_SHA256 = hashlib.sha256(_WORKER_BOOT_NONCE.encode("utf-8")).hexdigest()
WORKER_STARTED_AT = str(os.environ.get("VKPI_WORKER_STARTED_AT") or "").strip() or (
    datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
)
# 车道过滤:interactive/batch 互斥,all 为单 worker 兼容默认;未知值 fail-fast。
CLAIM_LANE = normalize_claim_lane(os.environ.get("APIFY_WORKER_CLAIM_LANE", "all"))
CLAIM_LANE_SQL = claim_lane_sql(CLAIM_LANE)
QUEUE_LANE_SQL = queue_lane_sql_expression("payload")
QUEUE_PRIORITY_SQL = queue_priority_sql_expression("payload")
QUEUE_SERVICE_PRIORITY_SQL = queue_service_priority_sql_expression("job_type", "created_at")
RESOURCE_SLOT_LIMITS = resource_slot_limits(os.environ)
# 双认领毒化防护:本地算力 worker 只打 payload.local_lease_id 标记(registry.py),
# 主 worker 两道过滤:①带 local_lease_id 的行不抢;②本地专属 job_type 不抢
# (单一真源 registry.SAFE_TASK_TYPES,别名 import 防漂移);拼 SQL 全为常量白名单,无注入面。
_LOCAL_EXCLUSIVE_TYPES_SQL = ", ".join(f"'{t}'" for t in LOCAL_EXCLUSIVE_JOB_TYPES)
CLAIM_LOCAL_GUARD_SQL = (
    "AND (payload->>'local_lease_id') IS NULL "
    f"AND job_type NOT IN ({_LOCAL_EXCLUSIVE_TYPES_SQL})"
)
# 认领 SELECT 抽成模块常量供单测断言;CLAIM_LANE_SQL 由 env 在 import 时定死。
CLAIM_SELECT_SQL = f"""
    SELECT id, job_type, payload, attempts, next_retry_at, last_error_category
    FROM apify_jobs
    WHERE status = 'queued'
      AND (next_retry_at IS NULL OR next_retry_at <= NOW())
      AND NOT EXISTS (
        SELECT 1
        FROM vkpi_provider_execution_claims AS provider_claim
        WHERE provider_claim.task_id = CONCAT('apify-job:', apify_jobs.id::text)
          AND provider_claim.state = 'active'
          AND provider_claim.lease_expires_at > NOW()
      )
      {CLAIM_LANE_SQL}
      {CLAIM_LOCAL_GUARD_SQL}
    ORDER BY
      {QUEUE_PRIORITY_SQL},
      {QUEUE_SERVICE_PRIORITY_SQL},
      COALESCE(next_retry_at, created_at), created_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
"""
# 泳道帮工:batch 车道批量捞空时用无泳道过滤的同款 SELECT 帮抢交互任务
# (优先序 interactive 先行,插队语义不变);APIFY_WORKER_LANE_STEAL=0 可关。
CLAIM_LANE_STEAL_ENABLED = (
    CLAIM_LANE == "batch"
    and str(os.environ.get("APIFY_WORKER_LANE_STEAL", "1")).strip().lower() not in {"0", "false", "off"}
)
CLAIM_SELECT_SQL_STEAL = CLAIM_SELECT_SQL.replace(CLAIM_LANE_SQL, "") if CLAIM_LANE_SQL else CLAIM_SELECT_SQL

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
# 1200 会截断六层 final_v1(分镜只剩前 ~35s);4096 容纳整段分镜时间线,可 env 覆盖。
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
# Worker processes are always production by default.  A persisted, server-
# signed job capability is the only mechanism that can authorize the narrow
# local evaluation branch for one job; an environment flag cannot reinterpret
# an old queue.
WORKER_LLM_EXECUTION_CLASS = llm_gateway.PRODUCTION_EXECUTION_CLASS
# One exact worker model is both preflighted and executed.  The former default
# fallback list (3-flash-preview -> 2.5-flash) let a preflight for one binding
# authorize a different provider request.
FINAL_V1_GEMINI_MODELS = gemini_video_analyzer.final_v1_gemini_models(
    [WORKER_GEMINI_MODEL]
)
FINAL_V1_KEYFRAME_QA_MODEL = os.environ.get("GEMINI_FINAL_V1_QA_MODEL", "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
_stop_event = threading.Event()
_gemini_qps_lock = threading.Lock()
_last_gemini_call_started_at = 0.0
_GEMINI_QPS_SCOPE = "vkpi_apify_worker_provider_rate"
_GEMINI_QPS_KEY = "google_gemini"
_GEMINI_QPS_CACHE_KEY = "vkpi:worker-rate:google-gemini"


def _request_stop(_signum: int, _frame: Any) -> None:
    _stop_event.set()


def _provider_retry_delay_seconds(next_attempt: int) -> int:
    attempt = max(1, int(next_attempt or 1))
    base_delay = min(PROVIDER_RETRY_MAX_DELAY_SECONDS, PROVIDER_RETRY_BASE_SECONDS * (4 ** max(0, attempt - 1)))
    if PROVIDER_RETRY_JITTER_RATIO <= 0:
        return int(base_delay)
    jitter = random.uniform(0, base_delay * PROVIDER_RETRY_JITTER_RATIO)
    return int(min(PROVIDER_RETRY_MAX_DELAY_SECONDS, round(base_delay + jitter)))


def _respect_gemini_qps(conn: psycopg.Connection[Any]) -> None:
    """Pace Gemini job starts across the whole PostgreSQL-backed fleet.

    A process-local monotonic clock is insufficient once multiple workers are
    enabled.  The advisory lock serializes the short decision window and
    ``persistent_cache`` carries the last start time between processes.  The
    first call after an idle period starts immediately; subsequent calls wait
    only the remaining interval.  If the shared state is unavailable, retain
    the older process-local limiter as a fail-soft fallback instead of
    removing throttling entirely.
    """

    global _last_gemini_call_started_at
    if GEMINI_MIN_INTERVAL_SECONDS <= 0:
        return
    shared_locked = False
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_advisory_lock(hashtext(%s), hashtext(%s)) AS locked",
                (_GEMINI_QPS_SCOPE, _GEMINI_QPS_KEY),
            )
            cur.fetchone()
            shared_locked = True
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now_epoch, value_json "
                "FROM persistent_cache WHERE cache_key=%s",
                (_GEMINI_QPS_CACHE_KEY,),
            )
            row = cur.fetchone() or {}
            now_epoch = float(row.get("now_epoch") or time.time())
            state = _loads(row.get("value_json"), {})
            last_epoch = float(state.get("last_started_at_epoch") or 0.0)
            wait_seconds = max(0.0, last_epoch + GEMINI_MIN_INTERVAL_SECONDS - now_epoch)
            if wait_seconds > 0:
                logger.info("gemini fleet qps throttle sleep | seconds=%.2f", wait_seconds)
                time.sleep(wait_seconds)
            cur.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS now_epoch")
            started_row = cur.fetchone() or {}
            started_epoch = float(started_row.get("now_epoch") or time.time())
            cur.execute(
                """
                INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at)
                VALUES (%s, %s, NOW() + INTERVAL '1 day', NOW())
                ON CONFLICT (cache_key) DO UPDATE
                SET value_json=EXCLUDED.value_json,
                    expires_at=EXCLUDED.expires_at,
                    created_at=EXCLUDED.created_at
                """,
                (_GEMINI_QPS_CACHE_KEY, _json({"last_started_at_epoch": started_epoch})),
            )
        return
    except Exception:
        logger.warning("gemini fleet qps state unavailable; using process-local throttle", exc_info=True)
    finally:
        if shared_locked:
            try:
                _advisory_unlock(conn, _GEMINI_QPS_SCOPE, _GEMINI_QPS_KEY)
            except Exception:
                logger.warning("gemini fleet qps advisory unlock failed", exc_info=True)

    with _gemini_qps_lock:
        now = time.monotonic()
        wait_seconds = (_last_gemini_call_started_at + GEMINI_MIN_INTERVAL_SECONDS) - now
        if wait_seconds > 0:
            logger.info("gemini process qps throttle sleep | seconds=%.2f", wait_seconds)
            time.sleep(wait_seconds)
            now = time.monotonic()
        _last_gemini_call_started_at = now


# 媒体解析 / 子进程分析器 / R2 回灌簇整簇已抽到 apify_jobs_worker_media.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。本 import 必须在
# 上面超时常量(GEMINI_CALL_*/MEDIA_RESOLVE_*)定义之后(它们在 media 模块底部被 import)。
from app.workers.apify_jobs_worker_media import (  # noqa: E402
    _gemini_analyzer_child_code,
    _mock_result,
    _resolve_cached_or_provider_video,
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


def _finish_skipped(
    conn: psycopg.Connection[Any],
    job_id: int,
    reason: str,
    *,
    evaluation_only: bool = False,
) -> None:
    analysis_summary: dict[str, Any] | None = (
        {
            "evaluation_only": True,
            "production_authorized": False,
            "claim_status": "descriptive_only",
            "model_readiness_status": "evaluation_only_not_production_ready",
            "cache_derive_method": LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
        }
        if evaluation_only
        else None
    )
    if not evaluation_only and "skipped_existing_analysis_cache" in str(reason or ""):
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
    _process_kol_audience_stats_refresh,
    _process_kol_auto_poll,
    _process_kol_content_fit_analysis,
    _process_kol_outreach_draft,
    _process_kol_pool_comments_collect,
    _process_kol_video_metric_refresh,
    _process_kol_profile_deep_crawl,
    _process_logistics_track_sync,
    _process_official_channel_comments_collect,
    _process_project_contract_extract,
    _process_project_retrospective,
    _process_session_advance,
    _process_smart_search_profile_advance,
    _resolve_job_staff,
)
from app.workers.apify_jobs_worker_video_url import _process_video_url_resolve  # noqa: E402
# 2026-07-11 未知 job_type 防线:_process_job 显式分支簇之外,只有 'video' 一种 job_type
# 合法落 _target 兜底分支(video_analysis_enqueue.py 唯一以裸 target/derive_method 入队)。
# 此前任何不认识的 job_type(如 official_channel_comments_collect 落地前)会带着
# derive_method 缺省='mock' 一路滑进 mock 假成功路径:写 mock cache + 标 done,
# 队列面板绿灯但什么都没干。现在:不在集合内 → _block_job('unknown_job_type'),诚实可见。
TARGET_FALLBACK_JOB_TYPES = frozenset({"video"})


# LLM 预算 preflight 簇 + keyframe 抽帧/Gemini override 簇整簇已抽到 apify_jobs_worker_prep.py
# (函数体逐字不变,re-export 兜住所有调用点;含下划线私有名)。本 import 必须放在
# 上面常量(LLM_MAX_OUTPUT_TOKENS/LLM_BUDGET_SCOPE/WORKER_GEMINI_MODEL)定义之后
# (它们在 prep 模块底部被 import)。
from app.workers.apify_jobs_worker_prep import (  # noqa: E402
    _download_youtube_for_keyframes,
    _extract_keyframes_for_qa,
    _gemini_worker_overrides,
    _google_allowed,
    _google_execution_authorization,
    _llm_budget_preflight,
    _load_video_evidence,
    _log_budget_preflight_record_only,
    _provider_allowed,
    _provider_budget_preflight,
)


# 成本/定价核算已抽到 apify_jobs_cost.py(行为不变,re-export 兜住调用点)。
from app.workers.apify_jobs_cost import (  # noqa: E402
    _anthropic_cost,
    _authoritative_gemini_cost,
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
from app.workers.apify_jobs_worker_execution import execute_claimed_job_impl  # noqa: E402


def _claim_job(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
    # The release fence is checked at the last boundary before SELECT ... FOR
    # UPDATE so a validation-started worker can prove liveness without taking
    # ownership of queued or externally billed work.
    if release_validation_active():
        return None
    lease_owner = f"{WORKER_HEARTBEAT_NAME}:{os.getpid()}"
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(CLAIM_SELECT_SQL)
            job = cur.fetchone()
            if not job and CLAIM_LANE_STEAL_ENABLED:
                cur.execute(CLAIM_SELECT_SQL_STEAL)
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
                (lease_owner, STALE_RECLAIM_SECONDS, job["id"]),
            )
            claimed = dict(job)
            claimed["lease_owner"] = lease_owner
    _sync_search_session_job(conn, int(claimed["id"]), raw_status="running")
    return claimed


def _process_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    job_type = str(job.get("job_type") or "").strip().lower()
    from app.workers.apify_jobs_worker_paid_scope import revalidate_paid_job_scope

    paid_action, block_reason, paid_action_actor = revalidate_paid_job_scope(
        payload,
        job_type,
        connection_scope=db_connection_sync_scope,
    )
    if block_reason:
        _block_job(
            conn,
            int(job["id"]),
            block_reason,
            {"provider_calls_performed": False, "paid_action": paid_action},
        )
        return
    if job_type == "session_advance":
        _process_session_advance(conn, job, payload)
        return
    if job_type == "smart_search_profile_advance":
        _process_smart_search_profile_advance(conn, job, payload)
        return
    if job_type == "kol_content_fit_analysis":
        _process_kol_content_fit_analysis(conn, job, payload)
        return
    if job_type == "account_dossier_extract":
        _process_account_dossier_extract(conn, job, payload)
        return
    if job_type == "project_contract_extract":
        _process_project_contract_extract(conn, job, payload)
        return
    if job_type == "project_retrospective_aggregate":
        _process_project_retrospective(conn, job, payload)
        return
    if job_type == "video_url_resolve":
        _process_video_url_resolve(conn, job, payload)
        return
    if job_type == "kol_video_metric_refresh":
        return _process_kol_video_metric_refresh(conn, job, payload)
    if job_type == "kol_profile_deep_crawl":
        _process_kol_profile_deep_crawl(conn, job, payload)
        return
    if job_type == "kol_pool_comments_collect":
        _process_kol_pool_comments_collect(
            conn,
            job,
            payload,
            paid_action_actor=paid_action_actor,
        )
        return
    if job_type == "kol_audience_stats_refresh":
        _process_kol_audience_stats_refresh(conn, job, payload)
        return
    if job_type == "official_channel_comments_collect":
        _process_official_channel_comments_collect(conn, job, payload)
        return
    if job_type == "kol_outreach_draft":
        _process_kol_outreach_draft(conn, job, payload)
        return
    if job_type == "contract_invoice_extract":
        _process_contract_invoice_extract(conn, job, payload)
        return
    if job_type == "contract_polish":
        _process_contract_polish(conn, job, payload)
        return
    if job_type == "logistics_track_sync":
        _process_logistics_track_sync(conn, job, payload)
        return
    if job_type == "kol_auto_poll":
        _process_kol_auto_poll(conn, job, payload)
        return
    if job_type not in TARGET_FALLBACK_JOB_TYPES:
        _block_job(conn, int(job["id"]), "unknown_job_type", {"job_type": job_type})
        return
    target_type, target_id = _target(payload)
    if not target_type or not target_id:
        raise ValueError("payload must include target_type and target_id")

    from app.workers.apify_jobs_worker_runtime import process_job_impl

    process_job_impl(conn, job, globals())


def _process_claimed_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> None:
    """Run one claimed row behind its reviewed cross-process resource cap.

    Row claiming already prevents duplicate ownership.  This additional guard
    bounds provider-heavy families when more than one worker process is
    intentionally enabled.  Slot exhaustion requeues with jitter and performs
    no provider call.  The existing per-target and LLM locks remain in force.
    """

    job_lock = str(int(job["id"]))
    if not _advisory_lock(conn, "vkpi_apify_job_execution", job_lock):
        _requeue_job(
            conn,
            int(job["id"]),
            "job execution lease is still held by another worker",
            retry_delay_seconds=random.uniform(5.0, 10.0),
        )
        return
    try:
        resource_group = resource_group_for_job(job)
        if resource_group is None:
            _process_job(conn, job)
            return
        limit = RESOURCE_SLOT_LIMITS[resource_group]
        slot_key = acquire_resource_slot(
            resource_group,
            limit,
            try_lock=lambda scope, key: _advisory_lock(conn, scope, key),
        )
        if slot_key is None:
            _requeue_job(
                conn,
                int(job["id"]),
                f"{resource_group} concurrency limit reached",
                retry_delay_seconds=random.uniform(5.0, 10.0),
            )
            return
        try:
            _process_job(conn, job)
        finally:
            _advisory_unlock(conn, RESOURCE_SLOT_SCOPE, slot_key)
    finally:
        _advisory_unlock(conn, "vkpi_apify_job_execution", job_lock)


def _fail_job(conn: psycopg.Connection[Any], job_id: int, exc: Exception) -> None:
    from app.workers.apify_jobs_worker_runtime import fail_job_impl

    fail_job_impl(conn, job_id, exc, globals())


def _upsert_worker_heartbeat(conn: psycopg.Connection[Any]) -> None:
    """T5:每轮 poll(含空闲)UPSERT 一行 worker 心跳。失败仅告警,绝不打断 poll 循环。

    若 vkpi_worker_heartbeat 表尚未迁移(140 未跑),静默跳过 —— system_health 会回退到旧的
    apify_jobs 启发式,不影响 worker 处理作业。
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO vkpi_worker_heartbeat (
                    worker_name, last_heartbeat_at, pid, updated_at,
                    worker_git_sha, boot_nonce_sha256, started_at
                )
                VALUES (%s, NOW(), %s, NOW(), %s, %s, %s)
                ON CONFLICT (worker_name) DO UPDATE
                SET last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                    pid = EXCLUDED.pid,
                    updated_at = EXCLUDED.updated_at,
                    worker_git_sha = EXCLUDED.worker_git_sha,
                    boot_nonce_sha256 = EXCLUDED.boot_nonce_sha256,
                    started_at = EXCLUDED.started_at
                """,
                (
                    WORKER_HEARTBEAT_NAME,
                    os.getpid(),
                    WORKER_GIT_SHA or None,
                    WORKER_BOOT_NONCE_SHA256,
                    WORKER_STARTED_AT,
                ),
            )
    except Exception as exc:
        logger.warning("apify_jobs worker heartbeat upsert failed | name=%s error=%s", WORKER_HEARTBEAT_NAME, exc)


def _heartbeat_running_job(
    job_id: int,
    lease_owner: str,
    provider_task_id: str,
    provider_fence_token: int,
    stop_signal: threading.Event,
) -> None:
    while not stop_signal.wait(RUNNING_HEARTBEAT_SECONDS):
        try:
            with psycopg.connect(DB_RUNTIME_URL, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        # Fabric 增量1:心跳同时续租约(updated_at 仍写 → 不动今天的 reclaim 判据)。
                        "UPDATE apify_jobs SET updated_at=NOW(), "
                        "lease_expires_at=NOW() + make_interval(secs => %s) "
                        "WHERE id=%s AND status='running' AND lease_owner=%s",
                        (STALE_RECLAIM_SECONDS, job_id, lease_owner),
                    )
                    cur.execute(
                        """
                        UPDATE vkpi_provider_execution_claims
                        SET lease_expires_at=NOW() + make_interval(secs => %s),
                            updated_at=NOW()
                        WHERE task_id=%s AND fence_token=%s AND lease_owner=%s
                          AND state='active' AND lease_expires_at>NOW()
                        """,
                        (
                            STALE_RECLAIM_SECONDS,
                            provider_task_id,
                            provider_fence_token,
                            lease_owner,
                        ),
                    )
                    if cur.rowcount != 1:
                        logger.error(
                            "apify_jobs provider fence renewal lost | id=%s task_id=%s fence=%s",
                            job_id,
                            provider_task_id,
                            provider_fence_token,
                        )
                # Long provider jobs can legitimately run well beyond the
                # two-minute liveness window.  Keep the process heartbeat on
                # the same independent timer as the job lease so the UI and
                # release gate do not report a working worker as offline while
                # the main poll loop is blocked inside _process_job().
                _upsert_worker_heartbeat(conn)
        except Exception as exc:
            logger.warning("apify_jobs running heartbeat failed | id=%s error=%s", job_id, exc)


@contextmanager
def _running_job_heartbeat(
    job_id: int,
    lease_owner: str,
    provider_task_id: str,
    provider_fence_token: int,
):
    stop_signal = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_running_job,
        args=(job_id, lease_owner, provider_task_id, provider_fence_token, stop_signal),
        name=f"apify-job-heartbeat-{job_id}",
        daemon=True,
    )
    thread.start()
    try:
        yield
    finally:
        stop_signal.set()
        thread.join(timeout=2)


def _execute_claimed_job(conn: psycopg.Connection[Any], job: dict[str, Any]) -> str:
    return execute_claimed_job_impl(conn, job, globals())


# 失败领养 / 陈旧 running 回收运维簇整簇已抽到 apify_jobs_worker_maintenance.py
# (函数体逐字不变,re-export 兜住所有调用点)。本 import 在上面重试常量定义之后。
from app.workers.apify_jobs_worker_maintenance import (  # noqa: E402
    _adopt_recent_provider_pressure_failures,
    _reconcile_terminal_search_session_jobs,
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
        "apify_jobs worker started | name=%s lane=%s llm_execution_class=%s gemini_model=%s poll_seconds=%s stale_minutes=%s resolve_timeout_sec=%s gemini_timeout_sec=%s llm_concurrency=%s gemini_qps=%s gemini_min_interval_sec=%s resource_slots=%s provider_retry_max_attempts=%s provider_retry_base_sec=%s provider_retry_max_delay_sec=%s provider_retry_adopt_window_min=%s",
        WORKER_HEARTBEAT_NAME,
        CLAIM_LANE,
        WORKER_LLM_EXECUTION_CLASS,
        WORKER_GEMINI_MODEL,
        POLL_SECONDS,
        STALE_RUNNING_MINUTES,
        MEDIA_RESOLVE_TIMEOUT_SECONDS,
        GEMINI_CALL_TIMEOUT_SECONDS,
        LLM_CONCURRENCY_LIMIT,
        GEMINI_QPS_LIMIT,
        GEMINI_MIN_INTERVAL_SECONDS,
        RESOURCE_SLOT_LIMITS,
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
                    was_release_fenced = release_validation_active()
                    if not was_release_fenced:
                        _reclaim_stale_running_jobs(conn)
                        _adopt_recent_provider_pressure_failures(conn)
                        # Repair sessions left running by older worker versions
                        # whose downstream jobs are already terminal.  The query
                        # is bounded and only targets currently-running sessions;
                        # the reducer itself is idempotent.
                        _reconcile_terminal_search_session_jobs(conn)
                    last_reclaim = time.monotonic()
                    _upsert_worker_heartbeat(conn)
                    while not _stop_event.is_set():
                        # T5:每轮 poll(含空闲)写心跳 → 空闲 worker 不再被判离线。
                        _upsert_worker_heartbeat(conn)
                        release_fenced = release_validation_active()
                        if release_fenced:
                            was_release_fenced = True
                            _stop_event.wait(POLL_SECONDS)
                            continue
                        if was_release_fenced:
                            # Activation is one-way for a successful release.
                            # Run deferred repair only after the controller has
                            # removed the root-owned fence.
                            _reclaim_stale_running_jobs(conn)
                            _adopt_recent_provider_pressure_failures(conn)
                            _reconcile_terminal_search_session_jobs(conn)
                            last_reclaim = time.monotonic()
                            was_release_fenced = False
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
                            final_status = _execute_claimed_job(conn, job)
                            # 执行体内部限流/租约冲突 requeue 后行仍 queued;按终态
                            # 区分日志(旧无条件 "job done" 曾把审计骗成同 job 跑 21 次)。
                            _verb = "requeued by executor" if final_status == "queued" else "done"
                            logger.info("apify_jobs job %s | id=%s status=%s", _verb, job["id"], final_status or "unknown")
                        except ApifyExecutionClaimBlocked as exc:
                            logger.warning(
                                "apify_jobs job left unexecuted behind live provider fence | id=%s error=%s",
                                job.get("id"),
                                _redact_sensitive_text(str(exc)),
                            )
                            _requeue_job(
                                conn,
                                int(job["id"]),
                                "provider execution lease remains active",
                                retry_delay_seconds=random.uniform(5.0, 10.0),
                            )
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
