"""Read-only task queue projection for the V-KPI sidebar board.

This module intentionally does not enqueue, mutate, or mark anything. It only
projects active and recently-finished work from already-registered tables.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn
from app.services.cache import cache_get, cache_set


ACTIVE_STATUSES = {"queued", "retrying", "processing", "running", "in_progress", "started"}
TERMINAL_STATUSES = {
    "done",
    "success",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "partial_done",
    "prefilter_rejected",
    "all_providers_failed",
    "ai_budget_hard_stop",
    "budget_disabled",
    "not_configured",
}

STATUS_ALIASES = {
    "success": "done",
    "all_providers_failed": "failed",
    "ai_budget_hard_stop": "failed",
    "budget_disabled": "failed",
    "not_configured": "failed",
    "in_progress": "running",
    "started": "running",
}

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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normal_status(value: Any) -> str:
    raw = _text(value).lower() or "queued"
    return STATUS_ALIASES.get(raw, raw)


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


def _infer_kind(source: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    data = payload if isinstance(payload, dict) else {}
    haystack = " ".join(
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
    if _text(job_type).lower() == "smart_search_profile_advance" or "kol_smart_search_profile_advance" in haystack:
        return "智能查找"
    if _text(job_type).lower() == "session_advance":
        return "资料补全"
    if _text(job_type).lower() == "account_dossier_extract" or "kol_account_dossier_extract" in haystack:
        return "账号沉淀"
    if _text(job_type).lower() == "project_contract_extract" or "project_contract_extract" in haystack:
        return "合同提取"
    if _text(job_type).lower() == "contract_invoice_extract" or "contract_invoice_extract" in haystack:
        return "发票提取"
    if _text(job_type).lower() == "contract_polish" or "contract_polish" in haystack:
        return "合同润色"
    if _text(job_type).lower() == "project_retrospective_aggregate" or "project_retrospective" in haystack:
        return "复盘聚合"
    if _text(job_type).lower() == "kol_profile_deep_crawl" or "kol_profile_deep_crawl" in haystack:
        return "账号分析"
    if _text(job_type).lower() == "kol_pool_comments_collect" or "kol_pool_comments_collect" in haystack:
        return "评论采集"
    if _text(job_type).lower() == "kol_outreach_draft" or "kol_outreach_draft" in haystack:
        return "联系草稿"
    if _text(job_type).lower() == "logistics_track_sync" or "logistics_track_sync" in haystack:
        return "物流同步"
    if "final_v1" in haystack or "video_analysis" in haystack or "video" in haystack:
        return "video深析"
    if any(word in haystack for word in ("url", "profile", "crawl", "scan", "resolve", "download", "ingest", "sync")):
        return "搜索/抓取"
    if any(word in haystack for word in ("report", "brief", "summary")):
        return "报告生成"
    if any(word in haystack for word in ("cache_extract", "deep_result", "post_process", "backfill")):
        return "总结沉淀"
    if source == "llm_calls" or any(word in haystack for word in ("gemini", "claude", "openai", "llm", "score")):
        return "LLM分析"
    return "任务"


def _infer_stage(status: str, kind: str, job_type: str = "", purpose: str = "", payload: Any = None) -> str:
    if status == "queued":
        return "queued"
    data = payload if isinstance(payload, dict) else {}
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
        or data.get("prompt")
        or data.get("summary")
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
    target = {
        "target_type": target_type or None,
        "target_id": target_id or None,
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
    """Read-only:近 7 天 done 任务的均时(秒),供排队 ETA 估算;无样本回退默认值。"""
    try:
        rows = conn.execute(
            """
            SELECT job_type, AVG(EXTRACT(EPOCH FROM (updated_at - created_at))) AS avg_sec
            FROM apify_jobs
            WHERE status='done' AND updated_at >= NOW() - INTERVAL '7 days'
            GROUP BY job_type
            LIMIT 50
            """,
        ).fetchall()
        return {str(r["job_type"]): max(30.0, float(r["avg_sec"] or 0)) for r in rows}
    except Exception:
        return {}


def _query_apify_jobs(cutoff: datetime, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    active_rows = conn.execute(
        """
        SELECT id, job_type, payload, status, last_error, last_error_category,
               next_retry_at, created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('queued', 'retrying', 'processing', 'running')
        ORDER BY COALESCE(next_retry_at, created_at) ASC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    recent_rows = conn.execute(
        """
        SELECT id, job_type, payload, status, last_error, last_error_category,
               next_retry_at, created_at, updated_at
        FROM apify_jobs
        WHERE status IN ('done', 'failed', 'blocked')
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    # 排队位次/ETA(worker 串行消费 apify_jobs):按 claim 排序(COALESCE(next_retry_at,created_at))
    # 给每个 queued 任务算 前方任务数 与 预计等待秒数(基于近 7 天同类型均时,无样本回退 300s)。
    avg_by_type = _avg_duration_by_job_type(conn)

    def _dur(job_type: str) -> float:
        return avg_by_type.get(job_type, DEFAULT_JOB_DURATION_SEC)

    eta_info: dict[Any, dict[str, Any]] = {}
    running_remaining = 0.0
    ahead = 0
    for row in active_rows:  # 已按 claim 顺序排序
        data = dict(row)
        jt = _text(data.get("job_type"))
        status = _text(data.get("status"))
        if status in ("running", "processing"):
            running_remaining += _dur(jt) / 2  # 在跑任务按半程估
            ahead += 1
        elif status in ("queued", "retrying"):
            eta_info[data.get("id")] = {
                "queue_position": sum(1 for v in eta_info.values()) + 1,
                "ahead_count": ahead,
                "eta_seconds": int(running_remaining + sum(x["_q"] for x in eta_info.values())),
                "_q": _dur(jt),
            }
            ahead += 1
    for v in eta_info.values():
        v.pop("_q", None)

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
        if data.get("id") in eta_info:
            extra.update(eta_info[data.get("id")])
        return _make_item(
            source="apify_jobs",
            row_id=data.get("id"),
            raw_status=data.get("status"),
            job_type=_text(data.get("job_type")),
            payload=payload,
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            extra=extra,
        )

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
        WHERE status IN ('queued', 'retrying', 'processing', 'running')
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
        WHERE status IN ('done', 'failed', 'cancelled', 'timeout', 'partial_done', 'prefilter_rejected')
          AND updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    def convert(row: Any) -> dict[str, Any]:
        data = dict(row)
        payload = _loads(data.get("payload_json"), {})
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
            },
        )
        if data.get("stage") and item["stage"] == "thinking" and str(data.get("stage")).lower() in {"ingest", "crawl"}:
            item["stage"] = "search"
            item["stage_label"] = STAGE_LABELS["search"]
        return item

    return [convert(row) for row in active_rows], [convert(row) for row in recent_rows]


def _query_llm_calls(cutoff: datetime, limit: int, scan_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = get_conn().execute(
        """
        SELECT id, call_uid, provider, model, purpose, status, created_at, metadata_json, latency_ms, cost_cents
        FROM vkpi_llm_calls
        ORDER BY id DESC
        LIMIT ?
        """,
        (scan_limit,),
    ).fetchall()

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        raw_status = _text(data.get("status")).lower()
        created_at = data.get("created_at")
        created_dt = _as_datetime(created_at)
        is_active = raw_status in ACTIVE_STATUSES
        is_recent = raw_status in TERMINAL_STATUSES and created_dt is not None and created_dt >= cutoff
        if not is_active and not is_recent:
            continue
        metadata = _loads(data.get("metadata_json"), {})
        item = _make_item(
            source="llm_calls",
            row_id=data.get("call_uid") or data.get("id"),
            raw_status=raw_status,
            purpose=_text(data.get("purpose")),
            payload={**(metadata if isinstance(metadata, dict) else {}), "provider": data.get("provider"), "model": data.get("model")},
            created_at=created_at,
            updated_at=created_at,
            target=_target_from_payload(metadata if isinstance(metadata, dict) else {}, fallback={"label": data.get("purpose")}),
            extra={
                "llm_call_id": data.get("id"),
                "provider": data.get("provider"),
                "model": data.get("model"),
                "latency_ms": data.get("latency_ms"),
                "cost_cents": data.get("cost_cents"),
            },
        )
        if is_active:
            active.append(item)
        else:
            recent.append(item)
    return active[:limit], recent[:limit]


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


def get_task_queue(*, limit: int = 50, recent_minutes: int = 10, include_llm_calls: bool = True) -> dict[str, Any]:
    """Return a compact read-only projection for 2-3s polling."""

    safe_limit = max(1, min(int(limit or 50), 100))
    safe_recent_minutes = max(1, min(int(recent_minutes or 10), 120))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=safe_recent_minutes)

    active: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    sources = ["apify_jobs", "job_execution_ledger"]

    apify_active, apify_recent = _query_apify_jobs(cutoff, safe_limit)
    ledger_active, ledger_recent = _query_job_execution_ledger(cutoff, safe_limit)
    active.extend(apify_active)
    active.extend(ledger_active)
    recent.extend(apify_recent)
    recent.extend(ledger_recent)

    llm_scan_limit = max(200, safe_limit * 5)
    if include_llm_calls:
        llm_active, llm_recent = _query_llm_calls(cutoff, safe_limit, llm_scan_limit)
        active.extend(llm_active)
        recent.extend(llm_recent)
        sources.append("vkpi_llm_calls")

    active = sorted(active, key=_recent_sort_key, reverse=True)
    active = sorted(active, key=_active_status_rank)[:safe_limit]
    recent = sorted(recent, key=_recent_sort_key, reverse=True)[:safe_limit]
    active_status_counts = Counter(str(item.get("status") or "") for item in active)
    recent_status_counts = Counter(str(item.get("status") or "") for item in recent)
    active_stage_counts = Counter(str(item.get("stage") or "") for item in active)

    return {
        "status": "ready",
        "source": "apify_jobs+job_execution_ledger+vkpi_llm_calls" if include_llm_calls else "apify_jobs+job_execution_ledger",
        "query": {
            "limit": safe_limit,
            "recent_minutes": safe_recent_minutes,
            "include_llm_calls": bool(include_llm_calls),
            "llm_scan_strategy": "latest_id_window",
            "llm_scan_limit": llm_scan_limit if include_llm_calls else 0,
        },
        "counts": {
            "active_total": len(active),
            "recent_total": len(recent),
            "queued": active_status_counts.get("queued", 0),
            "running": active_status_counts.get("running", 0) + active_status_counts.get("processing", 0) + active_status_counts.get("retrying", 0),
            "active_by_status": dict(active_status_counts),
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
            "llm_calls_coverage": "registered LLM calls only; Gateway-bypassing naked calls are not visible in phase 1",
            "write_db": False,
            "llm_calls": False,
            "worker_touched": False,
        },
    }


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
