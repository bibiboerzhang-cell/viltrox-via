"""卡住的会话项续补:幂等、有上限、可关停,零 provider 调用、零花钱。

用户诉求(2026-08-25):
    「不齐全的数据也要去补齐,而不是中途二分」——卡住的活儿要有机制继续推,
    直到完成或明确判定不可完成,不能永远停在半路。

线上只读探针(prod a05e48dd3,2026-08-25)对 242 条 ``status='partial'`` 的
会话项逐条判档,结论是**绝大多数根本不需要再花钱**:

    档位                                        条数   stage 分布
    T1 档案其实已就绪(纯状态陈旧)                210   profile 114 / summary 96
    T3 需人工裁决(身份候选多)                     29   profile 29
    T4 从未落库 + job 已 blocked 且不可重试          3   summary 3

    kol_pool_id 指向不存在的行:0
    T1 的 210 条,池行 handle / followers / avatar 三项俱全:210/210
    242 条里还挂着在跑的 job:0(3 条 job=blocked,其余 239 条根本没有 job)

T1 之所以是「纯状态陈旧」:``search_sessions_items.update_item_profile_execution``
已经改成「档案就绪就算就绪,联系方式 / 受众这类可选补全另算」(见该文件里
``profile_execute`` 上方那段注释),但**历史行是旧口径写下的**,没人回头改。
探针实测这 210 条的 ``profile_execute.status`` 全是 ready,缺的只是可选补全:

    audience: partial 144 / pending 64 / waiting_for_profile 18 / waiting_for_evidence 2
    contact:  no_contacts 102 / ok 62 / pending_l0 46 / waiting_for_profile 18

所以本任务做三件事,一件都不掏钱:

  1. **续推**:证据显示档案已就绪的,把陈旧的 partial 结算成 ready,并把
     还没补齐的可选项原样记进 ``followup.optional_gaps``(不抹掉、不假装齐了)。
  2. **判终态**:确实推不动的(需人工认人、job 已 blocked 且不可重试、从未落库),
     记下终态原因就**不再重试**,该花钱才能补的打上 ``needs_paid_recovery``
     交人裁决 —— 自己绝不下单。
  3. **记账**:每轮报「扫了几条 / 推进几条 / 判终态几条 / 退避几条」,
     写进 ``scheduler_tasks.last_run_summary``(迁移 302)。

红线:零 provider 调用、零 LLM、零 Apify、不碰 viltrox_fit_score、
不新增 item/session 的 status 取值(迁移 103 的 CHECK 一个字不动)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from app.core.coerce import _truthy
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.search_sessions_completion import (
    COMPLETION_SHAPE_ALL_COMPLETE,
    session_completion_breakdown,
)
from app.domains.kol.search_sessions_items import _update_session
from app.domains.kol.search_sessions_serde import (
    _dict,
    _int_or_none,
    _json_dumps,
    _loads,
    _sanitize_session_payload,
    _text,
)

logger = get_logger(__name__)

FOLLOWUP_TASK_KEY = "vkpi_session_stuck_item_followup"
FOLLOWUP_SCHEMA = "session_stuck_item_followup_v1"

# 每轮上限:线上全部卡住项也才 242 条,100 一轮足够,且天然挡住「一次扫全库」。
DEFAULT_BATCH_LIMIT = 100
MAX_BATCH_LIMIT = 500

# 重试上限 + 退避阶梯(小时)。本任务不打 provider,重试的唯一意义是
# 「等别的 worker 把在飞的活儿跑完」,所以阶梯拉长、次数给死。
FOLLOWUP_MAX_ATTEMPTS = 5
FOLLOWUP_BACKOFF_HOURS: tuple[int, ...] = (1, 6, 24, 72, 168)

# ── 处置(disposition)──────────────────────────────────────────────────────
DISPOSITION_ADVANCED = "advanced"        # 证据够了,结算成 ready
DISPOSITION_TERMINAL = "terminal"        # 推不动,记原因,不再重试
DISPOSITION_RETRY = "retry"              # 可能有别的 worker 在跑,退避后再看
DISPOSITION_SKIPPED = "skipped_backoff"  # 退避窗口没到,本轮不动

# ── 终态原因(全部要么需人工、要么需花钱,一律交人裁决)──────────────────
REASON_NEEDS_HUMAN_CHOICE = "needs_human_choice"
REASON_BLOCKED_NOT_RETRYABLE = "blocked_not_retryable"
REASON_NEVER_MATERIALIZED = "never_materialized"
REASON_PROFILE_CRAWL_FAILED = "profile_crawl_failed"
REASON_RETRY_EXHAUSTED = "retry_exhausted"
REASON_PROFILE_COMPLETE = "profile_complete_optional_enrichment_pending"
REASON_PROVIDER_WORK_IN_FLIGHT = "provider_work_in_flight"

# 需要人点头才能继续的终态。
_NEEDS_HUMAN_REASONS = frozenset({REASON_NEEDS_HUMAN_CHOICE, REASON_BLOCKED_NOT_RETRYABLE})
# 只有再掏钱重抓才可能补上的终态 —— 打标交人,任务自己绝不下单。
_NEEDS_PAID_REASONS = frozenset(
    {REASON_NEVER_MATERIALIZED, REASON_PROFILE_CRAWL_FAILED, REASON_RETRY_EXHAUSTED}
)

# 档案就绪的两种写法(与 update_item_profile_execution 的判定字面同步)。
_PROFILE_READY_STATUSES = frozenset({"ready", "already_analyzed"})
# 抓取失败:再重试也是同样的结果,只能重抓(要钱)。
_PROFILE_FAILED_STATUSES = frozenset({"error", "crawl_failed", "unsupported"})
# 还在别人手里的中间态:退避等它自己跑完。
_PROFILE_IN_FLIGHT_STATUSES = frozenset({"pending", "queued", "running", "in_progress"})

# 补全项的「已了结」取值:no_contacts 是**结论**(找过了,没有),不是缺口。
_CONTACT_SETTLED = frozenset({"ok", "ready", "no_contacts", "not_requested", "skipped"})
_AUDIENCE_SETTLED = frozenset({"ok", "ready", "not_requested", "skipped"})


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _backoff_until(now: datetime, attempts: int) -> str:
    index = min(max(int(attempts) - 1, 0), len(FOLLOWUP_BACKOFF_HOURS) - 1)
    return _iso(now + timedelta(hours=FOLLOWUP_BACKOFF_HOURS[index]))


def _blocked_not_retryable(payload: Mapping[str, Any]) -> bool:
    """job 已被判 blocked 且明确 ``retry_allowed=false``。

    线上这 3 条的 ``job_last_error`` 是 JSON 文本
    (``{"reason":"search_session_target_drifted","retry_allowed":false,...}``),
    所以先按 JSON 读;读不出来再退回子串判定,两条路都判不出就**不**当终态
    (失败方向安全:宁可多退避一轮,不可误判成永不再补)。
    """
    if _text(payload.get("job_status")).lower() != "blocked":
        return False
    raw_error = payload.get("job_last_error")
    parsed = _loads(raw_error, {}) if isinstance(raw_error, (str, bytes, bytearray)) else _dict(raw_error)
    if isinstance(parsed, dict) and "retry_allowed" in parsed:
        # 读回可能是 True/1/'true' 各种写法,``is False`` 会漏判;用统一容错。
        return not _truthy(parsed.get("retry_allowed"))
    return '"retry_allowed":false' in _text(raw_error).lower().replace(" ", "")


def optional_enrichment_gaps(profile_execute: Mapping[str, Any]) -> list[str]:
    """列出还没补齐的可选项。已了结的不算缺口,缺字段的诚实记 ``missing``。"""
    gaps: list[str] = []
    for role, settled in (("contact", _CONTACT_SETTLED), ("audience", _AUDIENCE_SETTLED)):
        block = _dict(profile_execute.get(f"{role}_enrichment"))
        if not block:
            gaps.append(f"{role}:missing")
            continue
        status = _text(block.get("status")).lower()
        if not status:
            gaps.append(f"{role}:missing")
        elif status not in settled:
            gaps.append(f"{role}:{status}")
    return gaps


def classify_stuck_item(
    payload: Mapping[str, Any],
    *,
    kol_pool_id: Any,
    pool_present: bool,
    attempts: int = 0,
) -> dict[str, Any]:
    """只看 item 自己已记下的证据判档 —— 纯函数,零 I/O、零 provider 调用。

    判定顺序即优先级:先判「需要人」的终态(最具体),再判「可以结算」,
    最后才是退避。判不出来的一律退避而不是硬判终态(失败方向安全)。
    """
    profile_execute = _dict(payload.get("profile_execute"))
    profile_status = _text(profile_execute.get("status")).lower()

    def verdict(disposition: str, reason: str, **extra: Any) -> dict[str, Any]:
        terminal = disposition == DISPOSITION_TERMINAL
        return {
            "disposition": disposition,
            "reason": reason,
            "terminal": terminal,
            "needs_human": terminal and reason in _NEEDS_HUMAN_REASONS,
            "needs_paid_recovery": terminal and reason in _NEEDS_PAID_REASONS,
            "optional_gaps": [],
            **extra,
        }

    # 1. 身份有多个候选,机器不许替人选(线上 29 条)。
    if profile_status == "needs_human_choice":
        return verdict(DISPOSITION_TERMINAL, REASON_NEEDS_HUMAN_CHOICE)

    # 2. job 已被判 blocked 且不可重试(线上 3 条)。
    if _blocked_not_retryable(payload):
        return verdict(DISPOSITION_TERMINAL, REASON_BLOCKED_NOT_RETRYABLE)

    # 3. 档案已就绪:剩下的只是可选补全,结算成 ready,缺口原样记账不抹掉。
    if profile_status in _PROFILE_READY_STATUSES and _int_or_none(kol_pool_id) and pool_present:
        return verdict(
            DISPOSITION_ADVANCED,
            REASON_PROFILE_COMPLETE,
            optional_gaps=optional_enrichment_gaps(profile_execute),
        )

    # 4. 抓取失败 / 平台不支持:重试不会有新结果,只能重抓 —— 要钱,交人。
    if "failed" in profile_status or profile_status in _PROFILE_FAILED_STATUSES:
        return verdict(DISPOSITION_TERMINAL, REASON_PROFILE_CRAWL_FAILED)

    # 5. 从来没落过库、也没跑过档案:补它必然要重抓 —— 要钱,交人。
    if not _int_or_none(kol_pool_id) and not profile_execute:
        return verdict(DISPOSITION_TERMINAL, REASON_NEVER_MATERIALIZED)

    # 6. 池行被删成孤儿:重建同样要重抓 —— 要钱,交人。
    if _int_or_none(kol_pool_id) and not pool_present:
        return verdict(DISPOSITION_TERMINAL, REASON_NEVER_MATERIALIZED)

    # 7. 还在别人手里(或判不出):退避等一轮;次数用尽再判终态并交人。
    if int(attempts) >= FOLLOWUP_MAX_ATTEMPTS:
        return verdict(DISPOSITION_TERMINAL, REASON_RETRY_EXHAUSTED)
    reason = (
        REASON_PROVIDER_WORK_IN_FLIGHT
        if profile_status in _PROFILE_IN_FLIGHT_STATUSES
        else f"unclassified:{profile_status or 'no_profile_execute'}"
    )
    return verdict(DISPOSITION_RETRY, reason)


def _select_due_items(conn: Any, *, now_iso: str, limit: int) -> list[dict[str, Any]]:
    """挑本轮该看的卡住项:已判终态的不再捞,退避窗口没到的不捞。

    ``followup`` 状态存在 item 的 payload 里(不加列、不加迁移)。
    ``#>>`` 取出来是文本,ISO-8601 带 Z 的时间串按字典序比较即时间序;
    从没记过账的行取到 ``''``,恒小于任何时间串,所以一定会被捞到。
    """
    rows = conn.execute(
        """
        SELECT i.id AS item_id,
               i.session_id AS session_id,
               i.stage AS item_stage,
               i.kol_pool_id AS kol_pool_id,
               i.payload_json AS payload_json,
               p.id AS pool_row_id
        FROM vkpi_kol_search_session_items i
        LEFT JOIN vkpi_kol_pool p ON p.id = i.kol_pool_id
        WHERE i.status='partial'
          AND COALESCE(i.payload_json #>> '{followup,terminal}', 'false') <> 'true'
          AND COALESCE(i.payload_json #>> '{followup,next_attempt_after}', '') <= ?
        ORDER BY i.updated_at ASC, i.id ASC
        LIMIT ?
        """,
        (now_iso, int(limit)),
    ).fetchall()
    return [dict(row) for row in rows or []]


def _settle_item(
    conn: Any,
    row: Mapping[str, Any],
    verdict: Mapping[str, Any],
    *,
    now: datetime,
    attempts: int,
) -> None:
    """把判档结果写回 item。``advanced`` 才动 status,其余只记账不动 status。"""
    payload = _dict(_loads(row.get("payload_json"), {})).copy()
    disposition = _text(verdict.get("disposition"))
    followup = {
        "schema": FOLLOWUP_SCHEMA,
        "attempts": int(attempts),
        "last_attempt_at": _iso(now),
        "disposition": disposition,
        "reason": _text(verdict.get("reason")),
        "terminal": bool(verdict.get("terminal")),
        "needs_human": bool(verdict.get("needs_human")),
        "needs_paid_recovery": bool(verdict.get("needs_paid_recovery")),
        "optional_gaps": list(verdict.get("optional_gaps") or []),
        # 红线自证:本任务从不打 provider、从不改 fit。
        "provider_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }
    if disposition == DISPOSITION_RETRY:
        followup["next_attempt_after"] = _backoff_until(now, attempts)
    payload["followup"] = followup
    payload = _sanitize_session_payload(payload)

    if disposition == DISPOSITION_ADVANCED:
        # status 取值来自迁移 103 既有的 CHECK 集合,stage 原样不动 ——
        # 只把陈旧的标签结算掉,不改流水线阶段,也不新增任何取值。
        conn.execute(
            """
            UPDATE vkpi_kol_search_session_items
            SET status='ready',
                payload_json=?::jsonb,
                updated_at=NOW()
            WHERE id=? AND status='partial'
            """,
            (_json_dumps(payload), int(row.get("item_id"))),
        )
        return
    # 判终态 / 退避都不动 status:诚实保留 partial,只把「为什么」和
    # 「下次什么时候再看」记下来。updated_at 也不动,免得把队列排序搅乱。
    conn.execute(
        """
        UPDATE vkpi_kol_search_session_items
        SET payload_json=?::jsonb
        WHERE id=? AND status='partial'
        """,
        (_json_dumps(payload), int(row.get("item_id"))),
    )


def _refresh_session(conn: Any, session_id: int) -> dict[str, Any]:
    """重算一个会话的完成度;只在「所有行都已完成」时把陈旧的 partial 升成 ready。

    绝不降级(ready 不会被改回 partial),绝不碰 running/failed/cancelled/planned ——
    那些状态有别的写端在负责,这里插手会打架。
    """
    row = conn.execute(
        "SELECT status, result_summary_json FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not row:
        return {"session_id": int(session_id), "promoted": False, "found": False}
    current = dict(row)
    summary = _loads(current.get("result_summary_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    completion = session_completion_breakdown(conn, int(session_id))
    status = _text(current.get("status")).lower()
    promoted = (
        status == "partial"
        and _text(completion.get("shape")) == COMPLETION_SHAPE_ALL_COMPLETE
        and int(completion.get("total") or 0) > 0
    )
    next_status = "ready" if promoted else status
    # 走 _update_session 这个唯一写入口:origin_breakdown / completion 一并重算,
    # 不在这里另写一份可能漂移的 UPDATE。
    _update_session(conn, int(session_id), status=next_status, summary=summary)
    return {
        "session_id": int(session_id),
        "promoted": bool(promoted),
        "found": True,
        "shape": completion.get("shape"),
    }


def _safe_batch_limit(limit: Any) -> int:
    """上限硬夹在 [1, MAX_BATCH_LIMIT]。``None`` 才是「没给」走默认;
    显式传 0 / 负数是调用方写错了,夹成 1 而不是悄悄放大成默认值。"""
    if limit is None:
        return DEFAULT_BATCH_LIMIT
    try:
        # 这里不能用 _int_or_none:它把 <=0 也判成「没给」,0 就会被悄悄放大成 100。
        parsed = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_LIMIT
    return max(1, min(parsed, MAX_BATCH_LIMIT))


def run_session_stuck_followup(
    *,
    limit: int | None = DEFAULT_BATCH_LIMIT,
    dry_run: bool = True,
    get_conn_fn: Callable[[], Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """扫一批卡住的会话项,逐条判档并续推。零 provider 调用、零花钱。

    幂等:结算过的行 status 已不是 partial,下轮捞不到;判终态的行被 SQL 过滤掉;
    退避中的行到点才再捞。同一轮重复跑不会产生新的副作用。

    ``dry_run=True``(默认)只判档不落库 —— 任何裸调用都是安全的,
    真跑由调度注册表的开关 + 注册时显式传 ``dry_run=False`` 两道门放行。
    """
    safe_limit = _safe_batch_limit(limit)
    moment = now or _utc_now()
    conn = (get_conn_fn or get_conn)()
    rows = _select_due_items(conn, now_iso=_iso(moment), limit=safe_limit)

    tally: dict[str, int] = {
        DISPOSITION_ADVANCED: 0,
        DISPOSITION_TERMINAL: 0,
        DISPOSITION_RETRY: 0,
        DISPOSITION_SKIPPED: 0,
    }
    reasons: dict[str, int] = {}
    needs_human = 0
    needs_paid = 0
    gaps_recorded = 0
    touched_sessions: list[int] = []

    for row in rows:
        payload = _dict(_loads(row.get("payload_json"), {}))
        prior = _dict(payload.get("followup"))
        attempts = (_int_or_none(prior.get("attempts")) or 0) + 1
        verdict = classify_stuck_item(
            payload,
            kol_pool_id=row.get("kol_pool_id"),
            pool_present=bool(_int_or_none(row.get("pool_row_id"))),
            attempts=attempts,
        )
        disposition = _text(verdict.get("disposition"))
        tally[disposition] = tally.get(disposition, 0) + 1
        reason = _text(verdict.get("reason"))
        reasons[reason] = reasons.get(reason, 0) + 1
        needs_human += 1 if verdict.get("needs_human") else 0
        needs_paid += 1 if verdict.get("needs_paid_recovery") else 0
        gaps_recorded += len(verdict.get("optional_gaps") or [])
        session_id = _int_or_none(row.get("session_id"))
        if session_id and session_id not in touched_sessions:
            touched_sessions.append(session_id)
        if not dry_run:
            _settle_item(conn, row, verdict, now=moment, attempts=attempts)

    sessions_refreshed: list[dict[str, Any]] = []
    if not dry_run and rows:
        # 会话摘要跟着受影响的行一起重算,和逐条结算同一个事务里提交 ——
        # 免得 item 已经结算、会话摘要还停在旧数字。
        for session_id in touched_sessions:
            sessions_refreshed.append(_refresh_session(conn, int(session_id)))
        conn.commit()

    promoted = sum(1 for entry in sessions_refreshed if entry.get("promoted"))
    result = {
        "status": "ready",
        "schema": FOLLOWUP_SCHEMA,
        "task_key": FOLLOWUP_TASK_KEY,
        "dry_run": bool(dry_run),
        "limit": safe_limit,
        "scanned": len(rows),
        "advanced": tally[DISPOSITION_ADVANCED],
        "terminal": tally[DISPOSITION_TERMINAL],
        "retry": tally[DISPOSITION_RETRY],
        "needs_human": needs_human,
        "needs_paid_recovery": needs_paid,
        "optional_gaps_recorded": gaps_recorded,
        "sessions_touched": len(touched_sessions),
        "sessions_promoted": promoted,
        "reasons": dict(sorted(reasons.items())),
        # 红线自证,写进返回体好让调度台账一眼看见。
        "provider_calls_performed": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }
    result["run_summary"] = run_summary_line(result)
    logger.info("kol.session_stuck_followup", extra={"followup": result["run_summary"]})
    return result


def run_summary_line(result: Mapping[str, Any]) -> str:
    """一行记账文本,进 ``scheduler_tasks.last_run_summary``(迁移 302)。"""
    parts = [
        f"scanned={int(result.get('scanned') or 0)}",
        f"advanced={int(result.get('advanced') or 0)}",
        f"terminal={int(result.get('terminal') or 0)}",
        f"retry={int(result.get('retry') or 0)}",
        f"needs_human={int(result.get('needs_human') or 0)}",
        f"needs_paid={int(result.get('needs_paid_recovery') or 0)}",
        f"sessions={int(result.get('sessions_touched') or 0)}",
        f"promoted={int(result.get('sessions_promoted') or 0)}",
    ]
    if result.get("dry_run"):
        parts.insert(0, "dry_run")
    return " ".join(parts)[:500]


def run_session_stuck_followup_job(
    *, limit: int | None = DEFAULT_BATCH_LIMIT, dry_run: bool = False
) -> dict[str, Any]:
    """调度入口:跑一轮并把记账写进注册表。config-gate 由注册方把守(默认 OFF)。"""
    result = run_session_stuck_followup(limit=limit, dry_run=dry_run)
    try:
        from app.domains.ops import scheduler_registry

        scheduler_registry.record_run(
            FOLLOWUP_TASK_KEY, ok=True, note=_text(result.get("run_summary"))
        )
    except Exception:
        # 记账失败不许拖垮续补本体;但要留痕,不做静默吞异常。
        logger.warning("kol.session_stuck_followup_record_failed", exc_info=True)
    return result


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "DISPOSITION_ADVANCED",
    "DISPOSITION_RETRY",
    "DISPOSITION_SKIPPED",
    "DISPOSITION_TERMINAL",
    "FOLLOWUP_BACKOFF_HOURS",
    "FOLLOWUP_MAX_ATTEMPTS",
    "FOLLOWUP_SCHEMA",
    "FOLLOWUP_TASK_KEY",
    "MAX_BATCH_LIMIT",
    "REASON_BLOCKED_NOT_RETRYABLE",
    "REASON_NEEDS_HUMAN_CHOICE",
    "REASON_NEVER_MATERIALIZED",
    "REASON_PROFILE_COMPLETE",
    "REASON_PROFILE_CRAWL_FAILED",
    "REASON_RETRY_EXHAUSTED",
    "classify_stuck_item",
    "optional_enrichment_gaps",
    "run_session_stuck_followup",
    "run_session_stuck_followup_job",
    "run_summary_line",
]
