"""Read-only task queue projection for the V-KPI sidebar board.

This module intentionally does not enqueue, mutate, or mark anything. It only
projects active and recently-finished work from already-registered tables.
"""
from __future__ import annotations

import heapq
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.tasks.queue_runtime_state import (
    ACTIVE_STATUSES,
    QUEUED_STATUSES,
    RUNNING_STATUSES,
    STATUS_ALIASES,
    TERMINAL_STATUSES,
    _authoritative_llm_status,
    _llm_reason_code,
    _normal_status,
    _reason_projection,
    _runtime_reason_contract,
    _safe_runtime_attempt,
)
from app.domains.tasks.queue_llm_reservations import (
    query_llm_reservations,
    true_llm_reservation_counts,
)
from app.domains.tasks.queue_llm_calls import query_llm_calls
from app.services.cache import cache_get, cache_set
from app.domains.tasks.queue_lane_policy import queue_priority_sql_expression, queue_service_priority_sql_expression

from app.core.logging import get_logger

logger = get_logger(__name__)

# worker 真存活窗口:vkpi_worker_heartbeat 最近心跳在此秒数内 → 在线(与 system_health 同源 2 分钟)。
_WORKER_HEARTBEAT_WINDOW_SEC = 120
# worker 离线时 queued 任务的诚实 stage 标签(不谎报 ETA)。
_WORKER_OFFLINE_LABEL = "worker离线"
QUEUE_PRIORITY_SQL = queue_priority_sql_expression("payload")
QUEUE_SERVICE_PRIORITY_SQL = queue_service_priority_sql_expression("job_type", "created_at")


STAGE_LABELS = {
    "queued": "排队",
    "search": "搜索",
    "thinking": "思考",
    "summarizing": "总结",
}


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return default
    return parsed if parsed is not None else default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


from app.core.coerce import _text


def _timestamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# _infer_kind 有序分类规则表(顺序即优先级,逐字保持原 if/elif 链语义)。
# 每行 = (job_type 精确匹配值(None=不按 job_type 匹配), 是否仅限 source=="llm_calls",
#         haystack 子串元组(任一命中即成立), 分类结果)。
_KIND_RULES: tuple[tuple[str | None, bool, tuple[str, ...], str], ...] = (
    ("kol_lookup", False, ("kol_lookup",), "KOL查找"),
    ("smart_search_profile_advance", False, ("kol_smart_search_profile_advance",), "智能查找"),
    ("session_advance", False, (), "资料补全"),
    ("account_dossier_extract", False, ("kol_account_dossier_extract",), "账号沉淀"),
    ("project_contract_extract", False, ("project_contract_extract",), "合同提取"),
    ("contract_invoice_extract", False, ("contract_invoice_extract",), "发票提取"),
    ("contract_polish", False, ("contract_polish",), "合同润色"),
    ("project_retrospective_aggregate", False, ("project_retrospective",), "复盘聚合"),
    ("video_url_resolve", False, (), "视频解析"),
    ("kol_profile_deep_crawl", False, ("kol_profile_deep_crawl",), "账号分析"),
    ("kol_pool_comments_collect", False, ("kol_pool_comments_collect",), "评论采集"),
    ("kol_audience_stats_refresh", False, ("audience_stats", "audience_age"), "受众分析"),
    ("kol_content_fit_analysis", False, ("kol_content_fit_analysis", "content_fit_v1"), "内容契合"),
    ("kol_outreach_draft", False, ("kol_outreach_draft",), "联系草稿"),
    ("logistics_track_sync", False, ("logistics_track_sync",), "物流同步"),
    (None, False, ("keyframe_qa", "video_qa"), "视频QA"),
    (None, False, ("marketing_advisor", "advisor"), "营销顾问"),
    (None, True, ("sentiment", "comment_reply", "comment_intel"), "评论分析"),
    (None, True, ("recall_rerank", "query_plan", "discovery_localize"), "智能查找"),
    (None, False, ("final_v1", "video_analysis", "video"), "video深析"),
    (None, False, ("url", "profile", "crawl", "scan", "resolve", "download", "ingest", "sync"), "搜索/抓取"),
    (None, False, ("report", "brief", "summary"), "报告生成"),
    (None, False, ("cache_extract", "deep_result", "post_process", "backfill"), "总结沉淀"),
)

# 表全 miss 后的 LLM 兜底提示词(source=="llm_calls" 或任一命中 → "LLM分析",否则 "任务")。
_LLM_HINT_WORDS = ("gemini", "claude", "openai", "llm", "score")


def _kind_haystack(source: str, job_type: str, purpose: str, payload: Any) -> str:
    data = payload if isinstance(payload, dict) else {}
    return " ".join(
        [
            source,
            job_type,
            purpose,
            _text(data.get("derive_method")),
            _text(data.get("target_type")),
            _text(data.get("prompt")),
            _text(data.get("script")),
        ]
    ).lower()


def _infer_kind(source: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    haystack = _kind_haystack(source, job_type, purpose, payload)
    jt = _text(job_type).lower()
    llm_source = source == "llm_calls"
    for exact_job_type, llm_source_only, tokens, kind in _KIND_RULES:
        if llm_source_only and not llm_source:
            continue
        if jt == exact_job_type or any(token in haystack for token in tokens):
            return kind
    if llm_source or any(word in haystack for word in _LLM_HINT_WORDS):
        return "LLM分析"
    return "任务"


def _infer_stage(status: str, kind: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    if status in QUEUED_STATUSES:
        return "queued"
    data = payload if isinstance(payload, dict) else {}
    # KOL 查找(同步路径)自带真实板内 stage(search/thinking/summarizing),直接采信,
    # 不靠关键词反推——查找的 search 阶段不能被默认 thinking 吞掉。
    if _text(job_type).lower() == "kol_lookup" or "kol_lookup" in _text(data.get("search_session_stage")).lower():
        explicit = _text(data.get("stage") or data.get("search_session_stage")).lower()
        if explicit in {"search", "thinking", "summarizing", "queued"}:
            return explicit
        return "search"
    haystack = " ".join(
        [
            kind,
            job_type,
            purpose,
            _text(data.get("derive_method")),
            _text(data.get("target_type")),
            _text(data.get("prompt")),
        ]
    ).lower()
    if _text(job_type).lower() == "video_url_resolve":
        return "search"
    # 收口路①-3:内容契合深析(逐候选)= 思考中。job_type/derive_method/kind 任一命中即归桶。
    if "kol_content_fit_analysis" in haystack or "content_fit_v1" in haystack or "内容契合" in haystack:
        return "thinking"
    if "project_contract_extract" in haystack or "outreach_draft" in haystack:
        return "thinking"
    if "contract_invoice_extract" in haystack or "contract_polish" in haystack:
        return "thinking"
    if "project_retrospective" in haystack:
        return "summarizing"
    if any(word in haystack for word in ("report", "summary", "cache_extract", "deep_result", "post_process", "backfill", "account_dossier_extract")):
        return "summarizing"
    if any(word in haystack for word in ("gemini", "claude", "openai", "llm", "final_v1", "video_analysis", "score")):
        return "thinking"
    if any(word in haystack for word in ("url", "profile", "crawl", "scan", "resolve", "download", "ingest", "sync")):
        return "search"
    return "thinking"


def _target_from_payload(payload: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    fallback = fallback or {}
    target_type = _text(data.get("target_type") or fallback.get("target_type"))
    target_id = _text(data.get("target_id") or fallback.get("target_id"))
    source_url = _text(data.get("source_url") or data.get("url") or fallback.get("source_url"))
    # label 链补业务键兜底(未命名案,2026-06-12):query_text 系缺失时退 handle/标题/文件名,
    # 再退 platform/handle 组合——泳道里"看得出是谁"优先于留空。
    label = _text(
        data.get("query_text")
        or data.get("summary")
        # Strict LLM reservations deliberately persist only a bounded
        # ``target_label`` instead of prompts/request bodies.  Keep that safe
        # correlation label visible in the progress center rather than
        # collapsing every call to its generic purpose.
        or data.get("target_label")
        or data.get("prompt")
        or data.get("handle")
        or data.get("display_name")
        or data.get("title")
        or data.get("file_name")
        or fallback.get("label")
    )
    if not label:
        platform = _text(data.get("platform"))
        handle = _text(data.get("kol_handle") or data.get("creator_handle"))
        if platform and handle:
            label = f"{platform}/{handle}"
    # item1(2026-06-16):video/account 等任务的 KOL 主体在 payload.kol_pool_id(target_id 是
    # video/evidence id,非 KOL)。透出供前端「打开」直达 KOL Pool 抽屉。
    kol_pool_id = _text(data.get("kol_pool_id") or fallback.get("kol_pool_id"))
    target = {
        "target_type": target_type or None,
        "target_id": target_id or None,
        "kol_pool_id": kol_pool_id or None,
        "source_url": source_url or None,
        "label": label[:180] if label else None,
        "platform": _text(data.get("platform") or fallback.get("platform")) or None,
    }
    return {key: value for key, value in target.items() if value not in (None, "")}


def _search_session_from_payload(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    session_id = _text(data.get("search_session_id"))
    if not session_id and _text(data.get("target_type")) == "search_session":
        session_id = _text(data.get("target_id"))
    item_id = _text(data.get("search_session_item_id"))
    if not session_id and not item_id:
        return {}
    session = {
        "session_id": session_id or None,
        "item_id": item_id or None,
        "item_status": _text(data.get("search_session_item_status")) or None,
        "stage": _text(data.get("search_session_stage")) or None,
    }
    return {key: value for key, value in session.items() if value not in (None, "")}


def _make_item(
    *,
    source: str,
    row_id: Any,
    raw_status: Any,
    job_type: str = "",
    purpose: str = "",
    payload: Any = None,
    created_at: Any = None,
    updated_at: Any = None,
    target: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = _normal_status(raw_status)
    kind = _infer_kind(source, job_type=job_type, purpose=purpose, payload=payload)
    stage = _infer_stage(status, kind, job_type=job_type, purpose=purpose, payload=payload)
    item = {
        "id": str(row_id),
        "source": source,
        "kind": kind,
        "job_type": job_type or purpose or "",
        "target": target or _target_from_payload(payload),
        "status": status,
        "raw_status": _text(raw_status),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "created_at": _timestamp(created_at),
        "updated_at": _timestamp(updated_at or created_at),
    }
    if extra:
        item.update(extra)
    search_session = _search_session_from_payload(payload)
    if search_session:
        item["search_session"] = search_session
    return _jsonable(item)


DEFAULT_JOB_DURATION_SEC = 300


def _avg_duration_by_job_type(conn: Any) -> dict[str, float]:
    """Read-only:近 7 天 done 任务的真处理均时(秒),供排队 ETA 估算;无样本回退默认值。

    诊断 P1-1 a根治:用 started_at(worker claim 时写,迁移112)算"真处理时长"
    (claim→done),而非墙钟(created_at→done,含排队等待)。墙钟被数天前队列积压
    污染成天文数字(video 均时≈7.8天)致 ETA「约X分」爆表。历史 done 行 started_at
    为 NULL 时排除;新行累积后 ETA 自愈,无样本则上层回退 DEFAULT_JOB_DURATION_SEC。
    """
    try:
        rows = conn.execute(
            """
            SELECT job_type, AVG(EXTRACT(EPOCH FROM (updated_at - started_at))) AS avg_sec
            FROM apify_jobs
            WHERE status='done' AND started_at IS NOT NULL
              AND updated_at >= NOW() - INTERVAL '7 days'
            GROUP BY job_type
            LIMIT 50
            """,
        ).fetchall()
        return {str(r["job_type"]): max(30.0, float(r["avg_sec"] or 0)) for r in rows}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _worker_online(conn: Any) -> bool:
    """worker 真存活闸:vkpi_worker_heartbeat 最近心跳在窗内 → 在线。

    worker 每轮 poll(含空闲)都写心跳,故空闲但活着的 worker 也判在线;
    表缺失/无心跳/读失败/心跳超窗 → 离线。与 system_health._worker_online 同源。
    离线时上层不再据串行 ETA 谎报 eta_seconds:300(死队列没人处理)。
    """
    if not table_exists("vkpi_worker_heartbeat"):
        return False
    try:
        row = conn.execute("SELECT MAX(last_heartbeat_at) AS latest FROM vkpi_worker_heartbeat").fetchone()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return False
    latest = row["latest"] if row is not None else None
    latest_dt = _as_datetime(latest)
    if latest_dt is None:
        return False
    age = (datetime.now(timezone.utc) - latest_dt).total_seconds()
    return age <= _WORKER_HEARTBEAT_WINDOW_SEC


def _online_worker_count(conn: Any) -> int:
    """Return currently heartbeating apify worker identities.

    One process owns one unique ``worker_name`` row.  A count is required for
    truthful ETA once the queue is split across bounded worker lanes; a simple
    online boolean would keep reporting the old single-worker serial wait.
    """
    if not table_exists("vkpi_worker_heartbeat"):
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=_WORKER_HEARTBEAT_WINDOW_SEC)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM vkpi_worker_heartbeat WHERE last_heartbeat_at >= ?",
            (cutoff,),
        ).fetchone()
        return max(0, int((row or {}).get("n") or 0))
    except Exception:
        logger.warning("worker heartbeat count failed", exc_info=True)
        return 0


def _multi_lane_eta_info(
    rows: list[dict[str, Any]],
    *,
    duration_for_job_type: Any,
    worker_count: int,
) -> dict[Any, dict[str, Any]]:
    """FCFS queue-start estimates for the observed worker lanes.

    Running jobs occupy one lane for half of their historical mean (the same
    conservative midpoint used by the prior serial estimator). Queued jobs are
    then assigned in the worker's lane-priority + aged-SPT claim order to the
    earliest available lane. Provider caps are intentionally not inferred
    here, so every result remains a rough ETA.
    """
    running = [
        row
        for row in rows
        if _normal_status(row.get("status")) in RUNNING_STATUSES
    ]
    lanes = max(1, int(worker_count or 0), len(running))
    lane_heap: list[tuple[float, int]] = [(0.0, index) for index in range(lanes)]
    heapq.heapify(lane_heap)
    for row in running:
        available, lane_id = heapq.heappop(lane_heap)
        duration = float(duration_for_job_type(str(row.get("job_type") or ""))) / 2.0
        heapq.heappush(lane_heap, (available + max(0.0, duration), lane_id))

    eta_info: dict[Any, dict[str, Any]] = {}
    queued_ahead = 0
    for row in rows:
        status = _normal_status(row.get("status"))
        if status in RUNNING_STATUSES:
            continue
        if status not in QUEUED_STATUSES:
            continue
        available, lane_id = heapq.heappop(lane_heap)
        duration = max(0.0, float(duration_for_job_type(str(row.get("job_type") or ""))))
        eta_info[row.get("id")] = {
            "queue_position": queued_ahead + 1,
            "ahead_count": len(running) + queued_ahead,
            "eta_seconds": int(max(0.0, available)),
            "eta_worker_lanes": lanes,
            "eta_model": "lane_priority_aged_spt_observed_worker_lanes_avg7d",
        }
        heapq.heappush(lane_heap, (available + duration, lane_id))
        queued_ahead += 1
    return eta_info


def _query_apify_jobs(
    cutoff: datetime,
    limit: int,
    worker_online: bool = True,
    worker_count: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    active_rows = conn.execute(
        f"""
        SELECT id, job_type, payload, status, last_error, last_error_category,
               next_retry_at, created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('queued', 'retrying', 'processing', 'running', 'in_progress', 'started')
        ORDER BY
          {QUEUE_PRIORITY_SQL},
          {QUEUE_SERVICE_PRIORITY_SQL},
          COALESCE(next_retry_at, created_at), created_at, id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT id, job_type, payload, status, last_error, last_error_category,
               next_retry_at, created_at, updated_at
        FROM apify_jobs
        WHERE status IN (
          'done', 'success', 'failed', 'blocked', 'triage', 'cancelled',
          'timeout', 'partial_done', 'prefilter_rejected'
        )
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    # 排队位次/ETA:按 worker 真 claim 顺序(车道优先 + 15min aging SPT + 时间稳定键)
    # 给每个 queued 任务算 前方任务数 与 预计等待秒数(基于近 7 天同类型均时,无样本回退 300s)。
    avg_by_type = _avg_duration_by_job_type(conn)

    def _dur(job_type: str) -> float:
        return avg_by_type.get(job_type, DEFAULT_JOB_DURATION_SEC)

    eta_info: dict[Any, dict[str, Any]] = {}
    # worker 离线时不算串行 ETA:死队列没人处理,谎报 eta_seconds:300 是欺骗。
    # 离线分支下 eta_info 保持空,convert() 会把 queued 任务标为 worker离线 + eta_seconds=None。
    if worker_online:
        eta_info = _multi_lane_eta_info(
            [dict(row) for row in active_rows],
            duration_for_job_type=_dur,
            worker_count=worker_count,
        )

    def convert(row: Any) -> dict[str, Any]:
        data = dict(row)
        payload = _loads(data.get("payload"), {})
        extra = {
            "error": _text(data.get("last_error")) or None,
            "error_category": _text(data.get("last_error_category")) or None,
            "next_retry_at": _timestamp(data.get("next_retry_at")),
            # 发起人透传(2026-06-12 主管裁令:排队显示用户,内容仅本人/管理员可见)
            "initiator_user_id": _text(payload.get("triggered_by_user_id") or payload.get("user_id")) or None,
            "initiator_staff_id": _text(payload.get("staff_id")) or None,
        }
        extra.update(
            _reason_projection(
                data.get("status"),
                data.get("last_error"),
                data.get("last_error_category"),
            )
        )
        if data.get("id") in eta_info:
            extra.update(eta_info[data.get("id")])
        item = _make_item(
            source="apify_jobs",
            row_id=data.get("id"),
            raw_status=data.get("status"),
            job_type=_text(data.get("job_type")),
            payload=payload,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            extra=extra,
        )
        # worker 离线:排队/重试任务不谎报 ETA。eta_seconds=None + stage_label 标注 worker离线。
        if not worker_online and item.get("status") in ("queued", "retrying"):
            item["eta_seconds"] = None
            item["stage_label"] = _WORKER_OFFLINE_LABEL
            item["worker_online"] = False
        return item

    return [convert(row) for row in active_rows], [convert(row) for row in recent_rows]


LEDGER_JOB_TYPE_CN = {
    "vkpi_official_channel_sync": "官号同步",
}


def _ledger_fallback_label(job_type: str, payload: Any) -> str:
    """ledger 行无 summary/query_text 时按 job_type 组可读名(未命名案,2026-06-12)。"""
    data = payload if isinstance(payload, dict) else {}
    name = LEDGER_JOB_TYPE_CN.get(_text(job_type), _text(job_type))
    ref = _text(
        data.get("handle")
        or data.get("channel_id")
        or data.get("kol_pool_id")
        or data.get("verification_id")
        or data.get("content_id")
        or data.get("target_id")
        or data.get("url")
    )
    return f"{name} · {ref}" if name and ref else name


def _query_job_execution_ledger(cutoff: datetime, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    # 新鲜闸:in-process 队列随 admin 进程生灭,排队行超 24h 未动即孤儿
    # (实案:05-20 两条 official_channel_sync 在「排队等待」滞留三周)——不再算 active。
    active_rows = conn.execute(
        """
        SELECT id, task_id, job_type, status, stage, summary, payload_json, result_json, error_message,
               submission_id, user_id, created_at, updated_at, started_at, finished_at
        FROM job_execution_ledger
        WHERE status IN ('queued', 'retrying', 'processing', 'running', 'in_progress', 'started')
          AND updated_at >= NOW() - INTERVAL '24 hours'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT id, task_id, job_type, status, stage, summary, payload_json, result_json, error_message,
               submission_id, user_id, created_at, updated_at, started_at, finished_at
        FROM job_execution_ledger
        WHERE status IN (
          'done', 'success', 'failed', 'blocked', 'triage', 'cancelled',
          'timeout', 'partial_done', 'prefilter_rejected'
        )
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    def convert(row: Any) -> dict[str, Any]:
        data = dict(row)
        payload = _loads(data.get("payload_json"), {})
        reason_projection = _reason_projection(
            data.get("status"),
            data.get("error_message"),
        )
        target = _target_from_payload(
            payload,
            fallback={
                "target_id": data.get("submission_id"),
                "target_type": "submission" if data.get("submission_id") else "",
                "label": _text(data.get("summary")) or _ledger_fallback_label(_text(data.get("job_type")), payload),
            },
        )
        item = _make_item(
            source="ledger",
            row_id=data.get("task_id") or data.get("id"),
            raw_status=data.get("status"),
            job_type=_text(data.get("job_type")),
            payload={**(payload if isinstance(payload, dict) else {}), "stage": data.get("stage"), "summary": data.get("summary")},
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            target=target,
            extra={
                "ledger_id": data.get("id"),
                "error": _text(data.get("error_message")) or None,
                "started_at": _timestamp(data.get("started_at")),
                "finished_at": _timestamp(data.get("finished_at")),
                "initiator_user_id": _text(data.get("user_id") or payload.get("user_id") or payload.get("created_by_user_id")) or None,
                "initiator_staff_id": _text(payload.get("staff_id")) or None,
                **reason_projection,
            },
        )
        if data.get("stage") and item["stage"] == "thinking" and str(data.get("stage")).lower() in {"ingest", "crawl"}:
            item["stage"] = "search"
            item["stage_label"] = STAGE_LABELS["search"]
        return item

    return [convert(row) for row in active_rows], [convert(row) for row in recent_rows]


def _query_llm_calls(cutoff: datetime, limit: int, scan_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return query_llm_calls(
        cutoff,
        limit,
        scan_limit,
        get_conn=get_conn,
        make_item=_make_item,
        target_from_payload=_target_from_payload,
        loads=_loads,
        text=_text,
        as_datetime=_as_datetime,
        active_statuses=ACTIVE_STATUSES,
        terminal_statuses=TERMINAL_STATUSES,
        runtime_reason_contract=_runtime_reason_contract,
        authoritative_llm_status=_authoritative_llm_status,
    )


def _query_llm_reservations(
    cutoff: datetime,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return query_llm_reservations(
        cutoff,
        limit,
        table_exists=table_exists,
        get_conn=get_conn,
        make_item=_make_item,
        target_from_payload=_target_from_payload,
        loads=_loads,
        text=_text,
        as_datetime=_as_datetime,
        timestamp=_timestamp,
        logger=logger,
    )


def _true_llm_reservation_counts(conn: Any) -> Counter:
    return true_llm_reservation_counts(
        conn,
        table_exists=table_exists,
        text=_text,
        logger=logger,
    )


def _active_status_rank(item: dict[str, Any]) -> int:
    status = str(item.get("status") or "")
    if status in {"running", "processing"}:
        return 0
    if status == "retrying":
        return 1
    if status == "queued":
        return 2
    return 3


def _recent_sort_key(item: dict[str, Any]) -> str:
    return str(item.get("updated_at") or item.get("created_at") or "")


def _true_active_counts(conn: Any) -> Counter:
    """C1:不带 LIMIT 的真实在队计数。active 列表仅取前 safe_limit 条渲染采样,
    但 counts.queued/running/active_total 必须反映全量(实案:234 条排队只显示~49)。
    覆盖两个 SQL 源 —— apify_jobs 全量 + job_execution_ledger 24h 新鲜闸
    (与各自 active 查询同条件,见 _query_apify_jobs/_query_job_execution_ledger);
    LLM 在飞调用走窗口扫描、无独立计数源,由调用方就 active 列表内补计。"""
    counts: Counter = Counter()
    try:
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM apify_jobs
            WHERE status IN ('queued', 'retrying', 'processing', 'running', 'in_progress', 'started')
            GROUP BY status
            """,
        ).fetchall():
            counts[str(r["status"] or "")] += int(r["n"] or 0)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    try:
        for r in conn.execute(
            """
            SELECT status, COUNT(*) AS n FROM job_execution_ledger
            WHERE status IN ('queued', 'retrying', 'processing', 'running', 'in_progress', 'started')
              AND updated_at >= NOW() - INTERVAL '24 hours'
            GROUP BY status
            """,
        ).fetchall():
            counts[str(r["status"] or "")] += int(r["n"] or 0)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    return counts


def _rollup_active_counts(counts: Counter) -> tuple[int, int, int]:
    """Return ``(queued, running, total)`` from authoritative raw statuses.

    ``retrying`` is queued work.  It must never inflate the running headline;
    only statuses that a worker has actually claimed belong in ``running``.
    """
    queued = 0
    running = 0
    total = 0
    for raw_status, raw_count in counts.items():
        count = max(0, int(raw_count or 0))
        status = _normal_status(raw_status)
        if status in QUEUED_STATUSES:
            queued += count
            total += count
        elif status in RUNNING_STATUSES:
            running += count
            total += count
    return queued, running, total


def get_task_queue(*, limit: int = 50, recent_minutes: int = 10, include_llm_calls: bool = True, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a compact read-only projection for 2-3s polling."""

    safe_limit = max(1, min(int(limit or 50), 100))
    safe_recent_minutes = max(1, min(int(recent_minutes or 10), 120))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=safe_recent_minutes)

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    sources = ["apify_jobs", "job_execution_ledger"]

    conn = get_conn()
    worker_count = _online_worker_count(conn)
    # Compatibility fallback: old/mocked heartbeat readers may only expose the
    # boolean probe. Never invent more than one lane from that fallback.
    worker_online = worker_count > 0 or _worker_online(conn)
    effective_worker_count = worker_count if worker_count > 0 else (1 if worker_online else 0)
    apify_active, apify_recent = _query_apify_jobs(
        cutoff,
        safe_limit,
        worker_online=worker_online,
        worker_count=effective_worker_count,
    )
    ledger_active, ledger_recent = _query_job_execution_ledger(cutoff, safe_limit)
    active.extend(apify_active)
    active.extend(ledger_active)
    recent.extend(apify_recent)
    recent.extend(ledger_recent)

    llm_scan_limit = max(200, safe_limit * 5)
    reservation_schema_available = bool(
        include_llm_calls and table_exists("vkpi_llm_budget_reservations")
    )
    if include_llm_calls:
        llm_active, llm_recent = _query_llm_calls(cutoff, safe_limit, llm_scan_limit)
        active.extend(llm_active)
        recent.extend(llm_recent)
        sources.append("vkpi_llm_calls")
        reservation_active, reservation_recent = _query_llm_reservations(
            cutoff, safe_limit
        )
        active.extend(reservation_active)
        recent.extend(reservation_recent)
        if reservation_schema_available:
            sources.append("vkpi_llm_budget_reservations")

    active = sorted(active, key=_recent_sort_key, reverse=True)
    active = sorted(active, key=_active_status_rank)[:safe_limit]
    recent = sorted(recent, key=_recent_sort_key, reverse=True)[:safe_limit]
    active_status_counts = Counter(str(item.get("status") or "") for item in active)
    recent_status_counts = Counter(str(item.get("status") or "") for item in recent)
    active_stage_counts = Counter(str(item.get("stage") or "") for item in active)

    # C1 真实计数:active 列表已被 [:safe_limit] 截断为渲染采样,headline 计数(queued/running/
    # active_total)须取全量,否则 234 条排队只显示 ~49。LLM 在飞调用无独立 COUNT 源,就采样列表补计。
    true_active_counts = _true_active_counts(get_conn())
    true_active_counts.update(_true_llm_reservation_counts(get_conn()))
    for item in active:
        if str(item.get("source") or "") == "llm_calls":
            st = str(item.get("status") or "")
            if st in ("queued", "retrying", "processing", "running"):
                true_active_counts[st] += 1
    true_queued, true_running, true_active_total = _rollup_active_counts(true_active_counts)

    payload = {
        "status": "ready",
        "source": "+".join(sources),
        "query": {
            "limit": safe_limit,
            "recent_minutes": safe_recent_minutes,
            "include_llm_calls": bool(include_llm_calls),
            "llm_scan_strategy": "latest_id_window",
            "llm_scan_limit": llm_scan_limit if include_llm_calls else 0,
        },
        "counts": {
            # C1:headline 取全量真实计数(不受列表 LIMIT 截断);列表本身仍是 ≤limit 的采样。
            "active_total": true_active_total,
            "active_total_rendered": len(active),
            "recent_total": len(recent),
            "queued": true_queued,
            "running": true_running,
            "active_by_status": dict(true_active_counts),
            "active_by_status_rendered": dict(active_status_counts),
            "recent_by_status": dict(recent_status_counts),
            "active_by_stage": dict(active_stage_counts),
        },
        "active": active,
        "recent": recent,
        "diagnostics": {
            "sources": sources,
            "indexes_used": {
                "apify_jobs": "idx_apify_jobs_status_next_retry(status, next_retry_at, created_at)",
                "job_execution_ledger": "idx_job_execution_ledger_status_updated(status, updated_at)",
                "vkpi_llm_calls": "pkey id desc window; no status/created_at index in phase 1",
            },
            "llm_calls_coverage": (
                "registered Gateway outcomes plus strict atomic reservations; "
                "Gateway-bypassing naked calls remain invisible"
            ),
            "llm_reservation_schema_available": reservation_schema_available,
            "write_db": False,
            "llm_calls": False,
            "worker_touched": False,
            # worker 心跳存活:false 时 apify queued 任务的 eta_seconds=None(不谎报死队列 ETA)。
            "worker_online": worker_online,
            "worker_count": effective_worker_count,
            "queue_eta_model": "fcfs_observed_worker_lanes_avg7d; provider caps not inferred",
        },
    }
    # 波2 R1(2026-06-12 体检):重型端点同样按观看者遮蔽,杜绝绕过 compact 隐私
    return _apply_viewer_visibility(payload, viewer)


def _speed_light(counts: dict[str, Any]) -> dict[str, Any]:
    active_total = int(counts.get("active_total") or 0)
    queued = int(counts.get("queued") or 0)
    running = int(counts.get("running") or 0)
    if queued <= 10 and active_total <= 20:
        level = "L1"
        tone = "green"
        label = "有序"
    elif queued <= 50 and active_total <= 80:
        level = "L2"
        tone = "amber"
        label = "拥挤"
    else:
        level = "L3"
        tone = "red"
        label = "积压"
    return {
        "level": level,
        "tone": tone,
        "label": label,
        "queued": queued,
        "running": running,
        "active_total": active_total,
        "policy": "L1<=10 queued and <=20 active; L2<=50 queued and <=80 active; otherwise L3",
    }


def _apply_viewer_visibility(payload: dict[str, Any], viewer: dict[str, Any] | None) -> dict[str, Any]:
    """队列隐私(2026-06-12 主管裁令):非管理员只见他人任务的"队列存在"(位次/状态/发起人),
    任务内容(label/会话入口)仅本人与管理员可见。缓存为全员共享,故在缓存读取后按
    观看者后处理,且只产副本、绝不变异缓存内对象。"""
    if not viewer:
        return payload
    if str(viewer.get("role") or "").strip().lower() == "admin":
        return payload
    viewer_user = _text(viewer.get("user_id"))
    viewer_staff = _text(viewer.get("id") or viewer.get("staff_id"))

    def _mask(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            owner_user = _text(item.get("initiator_user_id"))
            owner_staff = _text(item.get("initiator_staff_id"))
            mine = (owner_user and viewer_user and owner_user == viewer_user) or (
                owner_staff and viewer_staff and owner_staff == viewer_staff
            )
            if mine:
                out.append(item)
                continue
            masked = dict(item)
            who = owner_user or owner_staff
            masked["target"] = {"label": f"用户 {who} 的任务" if who else "其他成员的任务"}
            masked.pop("search_session", None)
            masked["masked"] = True
            out.append(masked)
        return out

    result = dict(payload)
    result["active"] = _mask(payload.get("active"))
    result["recent"] = _mask(payload.get("recent"))
    return result


def get_task_queue_compact(*, limit: int = 30, recent_minutes: int = 5, viewer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a cached sidebar projection for high-frequency polling."""

    safe_limit = max(1, min(int(limit or 30), 50))
    safe_recent_minutes = max(1, min(int(recent_minutes or 5), 30))
    cache_key = f"vkpi:task_queue:compact:limit:{safe_limit}:recent:{safe_recent_minutes}"
    cached = cache_get(cache_key)
    if isinstance(cached, dict):
        result = dict(cached)
        result["cache"] = {"hit": True, "ttl_sec": 2}
        return _apply_viewer_visibility(result, viewer)

    payload = get_task_queue(
        limit=safe_limit,
        recent_minutes=safe_recent_minutes,
        include_llm_calls=False,
    )
    payload["method"] = "task_queue_compact_v1"
    payload["speed_light"] = _speed_light(payload.get("counts") or {})
    payload["polling"] = {
        "recommended_interval_ms": 2500,
        "cache_ttl_sec": 2,
        "burst_profile": "100 visible clients share the same short Redis/memory cache window",
        "include_llm_calls": False,
    }
    diagnostics = dict(payload.get("diagnostics") or {})
    diagnostics["compact"] = True
    diagnostics["cache_ttl_sec"] = 2
    diagnostics["burst_safe_for_100_clients"] = True
    payload["diagnostics"] = diagnostics
    payload["cache"] = {"hit": False, "ttl_sec": 2}
    cache_set(cache_key, payload, ttl=2)
    return _apply_viewer_visibility(payload, viewer)
