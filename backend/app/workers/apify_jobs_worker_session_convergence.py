"""Smart Search 会话停滞收敛:子任务排队超时 / 被拦时按「部分完成」收敛并写明原因。

问题(T 车道 2026-09-02,session 1124):智能搜索会话的档案阶段 40 秒就绪,
但受众补全子任务共用 ``gemini_video`` 并发槽(默认 1 路),8 条排队串行跑,
每次抢槽失败都被 ``_requeue_job`` 退回队列;视频子任务另被授权围栏 ``blocked``。
会话状态由 item 状态归约(``search_session_status_from_items``),只要还有 item
处于 running,会话就一直 running —— +5 分钟乃至 +20 分钟都不进终态。

本模块只做一件事:**在每次会话同步之后**检查「会话已跑多久」与「还在等的
子任务是什么状态」,超过上限且没有任何子任务真正在执行时,把仍在等待的 item
结算成 ``partial``(迁移 103 既有取值,不新增),并把原因原样记进
``payload_json.convergence`` 与会话 ``result_summary_json.convergence``。

红线:
* 零 provider 调用、零 LLM、零 Apify、不碰 viltrox_fit_score。
* 不动排队中的子任务:它们仍会在槽位空出来后跑完;跑完后的同步会把 item
  诚实升成 ready,收敛标记随之失效(``superseded``)。收敛只是不再让
  用户对着一个永远 running 的会话干等。
* 真正在执行(status=running)的子任务永不被收敛,worker 自有的作业超时负责它。
* 上限来自 env ``SMART_SEARCH_SESSION_MAX_RUNNING_SEC``,取值夹在
  [60, 86400],非法值回落默认并告警,不 fail-open 成「永不收敛」。
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core.logging import get_logger
from app.domains.kol.search_session_job_lineage import lineage_jobs_for_item
from app.domains.kol.search_session_job_support import json_dumps, loads
from app.domains.kol.search_session_job_sync import (
    rebuild_search_session_summary,
    search_session_status_from_items,
)
from app.domains.tasks.search_session_lineage import search_session_lineages


logger = get_logger(__name__)

SESSION_MAX_RUNNING_ENV = "SMART_SEARCH_SESSION_MAX_RUNNING_SEC"
DEFAULT_SESSION_MAX_RUNNING_SEC = 1800
MIN_SESSION_MAX_RUNNING_SEC = 60
MAX_SESSION_MAX_RUNNING_SEC = 86400

CONVERGENCE_SCHEMA = "search_session_convergence_v1"
REASON_CHILDREN_TIMED_OUT = "children_timed_out"

# ``_requeue_job`` 在并发槽满时写回的原因文本(apify_jobs_worker._process_claimed_job)。
SLOT_WAIT_MARKER = "concurrency limit reached"

# 仍在等结果的 item 取值(与 search_session_status_from_items 的 running 判定字面同步)。
ACTIVE_ITEM_STATUSES = frozenset({"queued", "running", "already_queued"})
# 子任务「真正在跑」:这类永不收敛,交给 worker 自己的作业超时。
EXECUTING_JOB_STATUSES = frozenset({"running", "processing", "retrying"})
BLOCKED_JOB_STATUSES = frozenset({"blocked"})


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def session_max_running_seconds(env: Mapping[str, str] | None = None) -> int:
    """读上限;非法值回落默认并告警,合法值夹在 [60, 86400]。"""
    source = os.environ if env is None else env
    raw = str(source.get(SESSION_MAX_RUNNING_ENV, "") or "").strip()
    if not raw:
        return DEFAULT_SESSION_MAX_RUNNING_SEC
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using default %s", SESSION_MAX_RUNNING_ENV, DEFAULT_SESSION_MAX_RUNNING_SEC)
        return DEFAULT_SESSION_MAX_RUNNING_SEC
    return max(MIN_SESSION_MAX_RUNNING_SEC, min(value, MAX_SESSION_MAX_RUNNING_SEC))


def classify_lineage_jobs(jobs: list[Mapping[str, Any]]) -> dict[str, list[int]]:
    """把一个 item 的血缘作业按「在跑 / 等槽 / 排队 / 被拦」分桶(纯函数)。"""
    buckets: dict[str, list[int]] = {"executing": [], "slot_waiting": [], "queued": [], "blocked": []}
    for job in jobs:
        status = str(job.get("status") or "").strip().lower()
        job_id = int(job.get("id") or 0)
        if status in EXECUTING_JOB_STATUSES:
            buckets["executing"].append(job_id)
        elif status == "queued":
            key = "slot_waiting" if SLOT_WAIT_MARKER in str(job.get("last_error") or "").lower() else "queued"
            buckets[key].append(job_id)
        elif status in BLOCKED_JOB_STATUSES:
            buckets["blocked"].append(job_id)
    return buckets


def _human_note(buckets: Mapping[str, list[int]], *, waited_sec: int, limit_sec: int) -> str:
    waiting = len(buckets.get("slot_waiting") or []) + len(buckets.get("queued") or [])
    blocked = len(buckets.get("blocked") or [])
    parts = [f"部分完成:已等待 {waited_sec // 60} 分钟,超过上限 {limit_sec // 60} 分钟。"]
    if waiting:
        parts.append(f"{waiting} 个后台补全任务仍在排队(受并发上限限制),跑完后结果会自动补上。")
    if blocked:
        parts.append(f"{blocked} 个任务被拦下,需要授权后才会继续。")
    return " ".join(parts)


def _prior_convergence(item_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    prior = item_payload.get("convergence")
    return prior if isinstance(prior, Mapping) else {}


def converge_item_verdict(
    item_status: str,
    item_payload: Mapping[str, Any],
    jobs: list[Mapping[str, Any]],
    *,
    waited_sec: float,
    max_running_sec: int,
) -> dict[str, Any] | None:
    """决定一个 item 要不要收敛(纯函数,零 I/O)。返回 None = 不动。"""
    if str(item_status or "").strip().lower() not in ACTIVE_ITEM_STATUSES:
        return None
    buckets = classify_lineage_jobs(jobs)
    if buckets["executing"]:
        return None
    if not (buckets["slot_waiting"] or buckets["queued"] or buckets["blocked"]):
        return None
    prior = _prior_convergence(item_payload)
    if waited_sec < max_running_sec and not prior.get("terminal"):
        return None
    waited = int(waited_sec)
    return {
        "schema": CONVERGENCE_SCHEMA,
        "terminal": True,
        "reason": REASON_CHILDREN_TIMED_OUT,
        "waiting_job_ids": sorted(buckets["slot_waiting"] + buckets["queued"]),
        "slot_waiting_job_ids": sorted(buckets["slot_waiting"]),
        "blocked_job_ids": sorted(buckets["blocked"]),
        "waited_sec": waited,
        "limit_sec": int(max_running_sec),
        "re_settled": bool(prior.get("terminal")),
        "note": _human_note(buckets, waited_sec=waited, limit_sec=int(max_running_sec)),
        "provider_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }


def _load_running_session(conn: psycopg.Connection[Any], session_id: int) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, status, created_at FROM vkpi_kol_search_sessions WHERE id=%s LIMIT 1",
            (int(session_id),),
        )
        row = cur.fetchone()
    if not row or str(row.get("status") or "").strip().lower() != "running":
        return None
    return dict(row)


def _load_active_items(conn: psycopg.Connection[Any], session_id: int) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, status, payload_json
            FROM vkpi_kol_search_session_items
            WHERE session_id=%s AND status IN ('queued', 'running', 'already_queued')
            ORDER BY id
            """,
            (int(session_id),),
        )
        rows = cur.fetchall() or []
    return [dict(row) for row in rows]


def _settle_item(conn: psycopg.Connection[Any], session_id: int, item_id: int, verdict: Mapping[str, Any]) -> None:
    patch = {"convergence": {**verdict, "settled_at": _iso(_utc_now())}}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE vkpi_kol_search_session_items
                SET status='partial',
                    stage='summary',
                    payload_json = payload_json || %s::jsonb,
                    updated_at=NOW()
                WHERE id=%s AND session_id=%s
                """,
                (json_dumps(patch), int(item_id), int(session_id)),
            )


def _refresh_session(conn: psycopg.Connection[Any], session_id: int, convergence: Mapping[str, Any]) -> str:
    """会话级摘要先并入 convergence,再走既有 rebuild(它保留 current_summary 的键)。"""
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE vkpi_kol_search_sessions
                SET result_summary_json = result_summary_json || %s::jsonb
                WHERE id=%s
                """,
                (json_dumps({"convergence": convergence}), int(session_id)),
            )
            cur.execute(
                "SELECT status, stage FROM vkpi_kol_search_session_items WHERE session_id=%s",
                (int(session_id),),
            )
            session_status = search_session_status_from_items([dict(item) for item in (cur.fetchall() or [])])
            rebuild_search_session_summary(cur, session_id=int(session_id), session_status=session_status)
    return session_status


def _settle_waiting_items(
    conn: psycopg.Connection[Any],
    session_id: int,
    *,
    waited_sec: float,
    limit_sec: int,
    lineage_jobs: Callable[..., list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    settled: list[dict[str, Any]] = []
    for item in _load_active_items(conn, int(session_id)):
        raw_payload = item.get("payload_json")
        payload = raw_payload if isinstance(raw_payload, dict) else loads(raw_payload, {})
        jobs = lineage_jobs(conn, session_id=int(session_id), item_id=int(item["id"]))
        verdict = converge_item_verdict(
            str(item.get("status") or ""),
            payload if isinstance(payload, dict) else {},
            jobs,
            waited_sec=waited_sec,
            max_running_sec=limit_sec,
        )
        if verdict is None:
            continue
        _settle_item(conn, int(session_id), int(item["id"]), verdict)
        settled.append({"item_id": int(item["id"]), **{k: verdict[k] for k in ("waiting_job_ids", "blocked_job_ids", "re_settled")}})
    return settled


def converge_search_session(
    conn: psycopg.Connection[Any],
    session_id: int,
    *,
    now: datetime | None = None,
    max_running_sec: int | None = None,
    lineage_jobs: Callable[..., list[dict[str, Any]]] = lineage_jobs_for_item,
) -> dict[str, Any]:
    """检查一个会话;超时且无子任务在跑时把等待中的 item 结算成 partial。"""
    limit_sec = int(max_running_sec or session_max_running_seconds())
    session = _load_running_session(conn, int(session_id))
    if session is None:
        return {"session_id": int(session_id), "converged": False, "reason": "session_not_running"}
    moment = now or _utc_now()
    started = _as_utc(session.get("created_at")) or moment
    waited_sec = max(0.0, (moment - started).total_seconds())
    settled = _settle_waiting_items(conn, int(session_id), waited_sec=waited_sec, limit_sec=limit_sec, lineage_jobs=lineage_jobs)
    if not settled:
        return {"session_id": int(session_id), "converged": False, "reason": "within_budget_or_executing", "waited_sec": int(waited_sec), "limit_sec": limit_sec}
    convergence = {
        "schema": CONVERGENCE_SCHEMA,
        "reason": REASON_CHILDREN_TIMED_OUT,
        "waited_sec": int(waited_sec),
        "limit_sec": limit_sec,
        "items_settled": settled,
        "settled_at": _iso(moment),
        "note": "部分完成:后台补全任务排队超过上限,已有结果先交付;补全跑完会自动升级。",
        "provider_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }
    session_status = _refresh_session(conn, int(session_id), convergence)
    logger.info(
        "search session converged | session_id=%s status=%s waited_sec=%s limit_sec=%s items=%s",
        session_id,
        session_status,
        int(waited_sec),
        limit_sec,
        len(settled),
    )
    return {"session_id": int(session_id), "converged": True, "session_status": session_status, "items_settled": settled, "waited_sec": int(waited_sec), "limit_sec": limit_sec}


def converge_sessions_for_job(
    conn: psycopg.Connection[Any],
    job_id: int,
    *,
    raw_status: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """同步入口:一条作业的每次状态回写后,检查它血缘上的每个会话。running 事件跳过。"""
    if str(raw_status or "").strip().lower() in EXECUTING_JOB_STATUSES:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT payload FROM apify_jobs WHERE id=%s LIMIT 1", (int(job_id),))
        row = cur.fetchone() or {}
    return [converge_search_session(conn, session_id, now=now) for session_id in _lineage_session_ids(row.get("payload"))]


def _lineage_session_ids(raw_payload: Any) -> list[int]:
    payload = raw_payload if isinstance(raw_payload, dict) else loads(raw_payload, {})
    session_ids: list[int] = []
    for entry in search_session_lineages(payload if isinstance(payload, dict) else {}):
        try:
            session_id = int(entry.get("search_session_id") or 0)
        except (TypeError, ValueError):
            continue
        if session_id > 0 and session_id not in session_ids:
            session_ids.append(session_id)
    return session_ids


__all__ = [
    "ACTIVE_ITEM_STATUSES",
    "CONVERGENCE_SCHEMA",
    "DEFAULT_SESSION_MAX_RUNNING_SEC",
    "MAX_SESSION_MAX_RUNNING_SEC",
    "MIN_SESSION_MAX_RUNNING_SEC",
    "REASON_CHILDREN_TIMED_OUT",
    "SESSION_MAX_RUNNING_ENV",
    "SLOT_WAIT_MARKER",
    "classify_lineage_jobs",
    "converge_item_verdict",
    "converge_search_session",
    "converge_sessions_for_job",
    "session_max_running_seconds",
]
