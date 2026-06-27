"""W2 · Action 执行器(human-in-the-loop 之后的真执行,双闸守门)。

execute_action(action_id, staff) -> {"ok":bool,"outcome":"success|failed|skipped",...}

红线(双闸):
  闸1:仅 status=='approved' 才执行(未审批 → outcome='skipped', error='not_approved')。
  闸2:validators.validate_action 必须 ok(approved + touches_v6_fit=False + budget + entity 存在)。
任何会写业务表 / 烧 LLM 的动作绝不在未审批时执行;任何 exception → outcome='failed' 不抛。
每次执行落一行 vkpi_action_execution_ledger(mode='executed');成功置 action='executed',失败置 'failed'。

绝不写 vkpi_kol_pool.viltrox_fit_score、绝不改 rule_v0、绝不碰 KOL Pool 评分域。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.domains.actions import inbox, validators
from app.domains.projects import automation_audit

logger = get_logger(__name__)

_LEDGER = "vkpi_action_execution_ledger"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str, ensure_ascii=False)


def _write_ledger(
    *,
    action: dict[str, Any] | None,
    action_id: int,
    staff: dict[str, Any] | None,
    outcome: str,
    error: str = "",
    detail: dict[str, Any] | None = None,
) -> int | None:
    """落一行执行台账(record-only,容错)。返回 ledger id 或 None。

    列实证(141_vkpi_action_inbox.sql:47-65):action_id/category/dedupe_key/actor_staff_id/
    mode/outcome/endpoint/cost_cents/error/detail_json;detail_json 走 ?::jsonb。
    """
    if not table_exists(_LEDGER):
        return None
    act = action or {}
    try:
        est = int(act.get("estimated_cost_cents") or 0)
    except (TypeError, ValueError):
        est = 0
    try:
        conn = get_conn()
        cursor = conn.execute(
            f"""
            INSERT INTO {_LEDGER}
              (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
               endpoint, cost_cents, error, detail_json, created_at)
            VALUES (?,?,?,?,'executed',?,?,?,?,?::jsonb,NOW())
            RETURNING id
            """,
            (
                int(action_id),
                str(act.get("category") or ""),
                str(act.get("dedupe_key") or ""),
                int(scope.actor_staff_id(staff)) or None,
                str(outcome),
                str(act.get("suggested_endpoint") or ""),
                est,
                str(error or ""),
                _dumps(detail),
            ),
        )
        row = cursor.fetchone()
        conn.commit()
        return int(dict(row)["id"]) if row else None
    except Exception:
        logger.warning("action_executor.ledger_failed", extra={"action_id": action_id}, exc_info=True)
        return None


def _result(
    *,
    ok: bool,
    outcome: str,
    category: str,
    reason: str = "",
    ledger_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "outcome": outcome, "category": category, "detail": detail or {}}
    if reason:
        out["reason"] = reason
    if ledger_id is not None:
        out["ledger_id"] = ledger_id
    return out


def _entity_id_int(action: dict[str, Any]) -> int | None:
    try:
        return int(str(action.get("entity_id")).strip())
    except (TypeError, ValueError):
        return None


def _record_execution_feedback(
    action: dict[str, Any],
    staff: dict[str, Any] | None,
    *,
    outcome: str,
    detail: dict[str, Any] | None,
) -> None:
    """R8 · 执行成功 → 写一行 vkpi_memory_feedback 埋种(里程碑②学习闭环的历史素材)。

    best-effort:写库失败只 logger.warning,绝不阻断 execute_action 返回(执行已成功)。
    红线:feedback 仅为反馈载体,绝不读写 viltrox_fit_score / rule_v0。
    """
    try:
        from app.domains.memory import feedback as memory_feedback

        memory_feedback.record_feedback(
            {
                "feedback_type": "action_executed",
                "entity_uid": "",  # action 实体未必有 memory entity;留空,上下文进 feedback_json
                "rating": 1,
                "category": str(action.get("category") or ""),
                "action_id": int(action.get("id") or 0) or None,
                "entity_type": str(action.get("entity_type") or ""),
                "entity_id": str(action.get("entity_id") or ""),
                "outcome": str(outcome),
                "endpoint": str(action.get("suggested_endpoint") or ""),
                "detail": detail or {},
                "source": "action_executor",
            },
            staff=staff,
        )
    except Exception:
        logger.warning(
            "action_executor.memory_feedback_failed",
            extra={"action_id": action.get("id")},
            exc_info=True,
        )


def _record_outcome_eval(action: dict[str, Any], *, outcome: str) -> None:
    """B5/H4 学习闭环:把执行结果写进 vkpi_agent_outcome_evaluations → 回流推荐权重。

    仅对有实体的动作(kol/project…)记录;success→recommend_again,fail→不再推荐。
    best-effort:写失败只 warning,绝不阻断 execute_action 返回。零触 viltrox_fit_score。
    """
    entity_type = str(action.get("entity_type") or "").strip()
    entity_id = str(action.get("entity_id") or "").strip()
    # P1 事件总线:执行结果入统一事件流(best-effort,即使无实体也记)。
    try:
        from app.domains.platform import event_ledger

        event_ledger.emit(
            "action_executed",
            entity_type=entity_type or "action",
            entity_id=entity_id or str(action.get("id") or ""),
            actor_type="agent",
            source="action_executor",
            payload={"category": str(action.get("category") or ""), "outcome": outcome, "action_id": action.get("id")},
            trace_id=event_ledger.new_trace_id("action", action.get("id")),
        )
    except Exception:
        logger.debug("action_executor.event_emit_failed", exc_info=True)
    if not entity_type or not entity_id:
        return
    try:
        from app.domains.memory import agent_memory_writer

        agent_memory_writer.record_outcome(
            entity_type=entity_type,
            entity_id=entity_id,
            outcome=outcome,
            agent_action_id=int(action.get("id") or 0) or None,
            evidence={"category": str(action.get("category") or ""), "action_id": action.get("id")},
        )
    except Exception:
        logger.warning("action_executor.outcome_eval_failed", extra={"action_id": action.get("id")}, exc_info=True)


# ── per-category dispatch(全部复用既有 service,签名实证) ────────────────
def _exec_deep_missing(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """KOL 深析待跑 → 入 apify_jobs(账号深爬队列)。复用 enqueue_profile_deep_crawl_job(去重)。"""
    from app.domains.kol import url_deep_crawl

    kol_pool_id = _entity_id_int(action)
    if not kol_pool_id:
        return {"outcome": "skipped", "reason": "deep_missing_no_kol_id", "detail": {}}
    # 取 profile_url(深爬需 URL);无 URL → 诚实 skip(不臆造 URL)。
    row = get_conn().execute(
        "SELECT profile_url FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    url = str((dict(row).get("profile_url") if row else "") or "").strip()
    if not url:
        return {"outcome": "skipped", "reason": "deep_missing_no_profile_url", "detail": {"kol_pool_id": kol_pool_id}}
    res = url_deep_crawl.enqueue_profile_deep_crawl_job(url, kol_pool_id=kol_pool_id, staff=staff)
    return {"outcome": "success", "reason": "", "detail": {"enqueue": res}}


def _exec_failed_retry(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """失败/受阻 apify_job 重排 → requeue(status failed|blocked → queued)。

    只 requeue 真 failed/blocked 行;清 last_error/last_error_category/next_retry_at
    (与 worker _claim_job 重领时清空语义一致,让重排行能被 worker 立即重领)。
    attempts 不在此处篡改(保留试错史,worker 重跑时按既有逻辑递增)。
    此为 apify_jobs provider 队列(账号/视频抓取),与前端任务看板的 vkpi_tasks
    营销队列两套互不相干(后者由 tasks.enqueue.retry_task 走 job_execution_ledger 重排)。
    """
    job_id = _entity_id_int(action)
    if not job_id:
        return {"outcome": "skipped", "reason": "failed_retry_no_job_id", "detail": {}}
    conn = get_conn()
    cursor = conn.execute(
        """
        UPDATE apify_jobs
        SET status = 'queued', last_error = NULL, last_error_category = NULL,
            next_retry_at = NULL, updated_at = NOW()
        WHERE id = ? AND status IN ('failed', 'blocked')
        """,
        (int(job_id),),
    )
    conn.commit()
    requeued = int(getattr(cursor, "rowcount", 0) or 0) > 0
    if not requeued:
        return {"outcome": "skipped", "reason": "failed_retry_not_in_failed_state", "detail": {"job_id": job_id}}
    return {
        "outcome": "success",
        "reason": "requeued_apify_jobs",
        "detail": {
            "job_id": job_id,
            "requeued": True,
            "queue": "apify_jobs",
            "note": (
                "重试 apify_jobs 队列(账号/视频抓取的 provider 队列),"
                "≠ 前端任务看板的 vkpi_tasks 营销队列"
                "(后者由 tasks.enqueue.retry_task 走 job_execution_ledger 重排)"
            ),
        },
    }


def _exec_kol_profile(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """KOL profile 补字段 → 入 apify_jobs profile 抓取队列(worker 异步跑,绝不同步烧 LLM)。

    复用 enqueue_profile_deep_crawl_job(同 _exec_deep_missing 的 sync apify_jobs 入队器):
    取 profile_url → 无 URL 诚实 skip(不臆造)→ 入队(max_posts=1 取轻量,profile 补字段非视频深析)。
    LLM/视频在 worker run_profile_deep_crawl_for_job 异步跑并过预算闸。绝不写 viltrox_fit_score。
    """
    from app.domains.kol import url_deep_crawl

    kol_pool_id = _entity_id_int(action)
    if not kol_pool_id:
        return {"outcome": "skipped", "reason": "kol_profile_no_kol_id", "detail": {}}
    # 取 profile_url(profile 抓取需 URL);无 URL → 诚实 skip(不臆造 URL)。
    row = get_conn().execute(
        "SELECT profile_url FROM vkpi_kol_pool WHERE id = ?",
        (int(kol_pool_id),),
    ).fetchone()
    url = str((dict(row).get("profile_url") if row else "") or "").strip()
    if not url:
        return {"outcome": "skipped", "reason": "kol_profile_no_profile_url", "detail": {"kol_pool_id": kol_pool_id}}
    res = url_deep_crawl.enqueue_profile_deep_crawl_job(url, kol_pool_id=kol_pool_id, max_posts=1, staff=staff)
    return {
        "outcome": "success",
        "reason": "",
        "detail": {
            "enqueue": res,
            "note": "入 apify_jobs profile 抓取队列(worker 异步跑,绝不同步烧 LLM)",
        },
    }


def _exec_project_observation(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """项目签收待观察 → scan_delivered_into_windows;每个新开窗口落 window_open 审计。"""
    from app.domains.projects import observation_windows

    res = observation_windows.scan_delivered_into_windows(staff=staff, days_overdue=7)
    created = res.get("created") or []
    project_id = _entity_id_int(action)
    if project_id:
        for wid in created:
            automation_audit.record_audit(
                project_id=project_id,
                action="window_open",
                window_id=int(wid) if wid is not None else None,
                reason="action_execute:project_observation",
            )
    return {"outcome": "success", "reason": "", "detail": {"created_windows": created, "scan": res}}


def _exec_content_candidate(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """内容候选 → review_content_post(action='matched');落 content_match 审计。"""
    from app.domains.projects import observation_windows

    payload = action.get("payload_json") or {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        post_id = int(payload.get("post_id"))
    except (TypeError, ValueError):
        return {"outcome": "skipped", "reason": "content_candidate_no_post_id", "detail": {}}
    res = observation_windows.review_content_post(
        post_id=post_id, action="matched", staff=staff, note="action_execute"
    )
    if str(res.get("status") or "") != "ok":
        return {"outcome": "failed", "reason": res.get("error") or "review_failed", "detail": {"review": res}}
    project_id = _entity_id_int(action)
    if project_id:
        automation_audit.record_audit(
            project_id=project_id,
            action="content_match",
            matched_count=1,
            reason="action_execute:content_candidate",
            detail={"post_id": post_id},
        )
    return {"outcome": "success", "reason": "", "detail": {"review": res}}


def _exec_retrospective(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """复盘 → enqueue_project_retrospective(只排队不跑 LLM,内部去重);落 retrospective_enqueue 审计。"""
    from app.domains.projects import retrospective_aggregate

    project_id = _entity_id_int(action)
    if not project_id:
        return {"outcome": "skipped", "reason": "retrospective_no_project_id", "detail": {}}
    res = retrospective_aggregate.enqueue_project_retrospective(project_id, staff=staff)
    enqueue_status = str(res.get("status") or "")
    automation_audit.record_audit(
        project_id=project_id,
        action="retrospective_enqueue",
        reason="action_execute:retrospective",
        detail={"enqueue_status": enqueue_status},
    )
    # queued / already_queued / already_running 都算 success(幂等去重命中亦视为达成)。
    return {"outcome": "success", "reason": "", "detail": {"enqueue": res}}


def _exec_event_followup(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """R6 · 活动收尾提醒 → 受理并留痕(ledger 自动落 who/when)。

    红线诚实:ROI/线索/复盘是真业务数值,绝不由 agent 臆造填 0;执行=「已受理此提醒」,
    真数据仍需人工在 PATCH /events/{id} 回填。无 event_id → 诚实 skip。
    """
    event_id = str(action.get("entity_id") or "").strip()
    if not event_id:
        return {"outcome": "skipped", "reason": "event_followup_no_event_id", "detail": {}}
    payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
    return {
        "outcome": "success",
        "reason": "",
        "detail": {
            "acknowledged": True,
            "event_id": event_id,
            "missing": payload.get("missing"),
            "next_endpoint": action.get("suggested_endpoint"),
            "note": "已受理活动收尾提醒(进 ledger 留痕);ROI/线索/复盘真值仍需人工在 PATCH /events/{id} 回填,绝不由 agent 臆造。",
        },
    }


def _exec_inventory_low(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """R6 · 库存偏低提醒 → 受理并留痕。

    红线诚实:补货是人工采购决策,绝不自动改 qty / 自动下单 / 自动花钱;执行=「已受理预警」。
    无 inventory id → 诚实 skip。
    """
    inv_id = str(action.get("entity_id") or "").strip()
    if not inv_id:
        return {"outcome": "skipped", "reason": "inventory_low_no_id", "detail": {}}
    payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
    return {
        "outcome": "success",
        "reason": "",
        "detail": {
            "acknowledged": True,
            "inventory_id": inv_id,
            "sku": payload.get("sku"),
            "qty": payload.get("qty"),
            "next_endpoint": action.get("suggested_endpoint"),
            "note": "已受理库存预警(进 ledger 留痕);补货是人工采购决策,绝不自动改 qty / 自动下单。",
        },
    }


def _exec_skip_reminder(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """纯提醒类(project_shared_to_you):requires_approval=False 且不写业务数据,

    本不该进 approved 执行路径 → 防御性 skip。
    (event_followup / inventory_low 已各有受理执行器;kol_profile 有真入队执行器。)
    """
    category = str(action.get("category") or "")
    reason_map = {
        "project_shared_to_you": "reminder_only_no_executor",
    }
    return {"outcome": "skipped", "reason": reason_map.get(category, "no_executor"), "detail": {}}


def _exec_discovery_enroll(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """用透 Apify · 联邦发现补人 → 候选自动落 Pool(智能闭环"该补谁"的执行端)。

    query 取自 payload.query / entity_id。落库走 enroll(去重 + source=discovered)。
    红线:只落新档(数据薄诚实),零触 viltrox_fit_score;商业源未配置则只自有源(诚实)。
    """
    payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
    query = str(payload.get("query") or action.get("entity_id") or "").strip()
    if not query:
        return {"outcome": "skipped", "reason": "discovery_no_query", "detail": {}}
    from app.domains.discovery import enroll

    res = enroll.federated_discover_and_enroll(query, limit=int(payload.get("limit") or 20), staff=staff)
    return {"outcome": "success", "reason": "", "detail": {
        "query": query, "found": res.get("found"), "enrolled": res.get("enrolled"),
        "skipped": res.get("skipped"), "sources": res.get("sources"),
        "note": "联邦发现→落 Pool;进 MY KOL 仍需手动勾选。",
    }}


_DISPATCH = {
    "deep_missing": _exec_deep_missing,
    "discovery_enroll": _exec_discovery_enroll,
    "failed_retry": _exec_failed_retry,
    "project_observation": _exec_project_observation,
    "content_candidate": _exec_content_candidate,
    "retrospective": _exec_retrospective,
    "kol_profile": _exec_kol_profile,
    "event_followup": _exec_event_followup,
    "inventory_low": _exec_inventory_low,
    "project_shared_to_you": _exec_skip_reminder,
}


def execute_action(action_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行一条已审批动作。双闸守门;任何异常 → outcome='failed' 不抛。"""
    # 1. scope 收口取 action(取不到/越权 → not_found)。
    action = inbox.get_action(action_id, staff)
    if action is None:
        return {"ok": False, "outcome": "skipped", "category": "", "reason": "not_found_or_out_of_scope", "detail": {}}
    category = str(action.get("category") or "")

    # 闸1:仅 approved 才执行。
    if str(action.get("status") or "") != "approved":
        lid = _write_ledger(
            action=action, action_id=action_id, staff=staff, outcome="skipped", error="not_approved"
        )
        return _result(ok=False, outcome="skipped", category=category, reason="not_approved", ledger_id=lid)

    # 闸2:validators 双闸(approved + touches_v6_fit=False + budget + entity 存在)。
    v = validators.validate_action(action)
    if not v.get("ok"):
        reason = str(v.get("reason") or "validation_failed")
        lid = _write_ledger(
            action=action, action_id=action_id, staff=staff, outcome="skipped",
            error=reason, detail={"checks": v.get("checks")},
        )
        return _result(ok=False, outcome="skipped", category=category, reason=reason, ledger_id=lid)

    # 3. 派发执行(任何 exception → failed,不抛)。
    handler = _DISPATCH.get(category)
    if handler is None:
        lid = _write_ledger(
            action=action, action_id=action_id, staff=staff, outcome="skipped", error="unknown_category"
        )
        return _result(ok=False, outcome="skipped", category=category, reason="unknown_category", ledger_id=lid)

    # S1:执行前对 affected_tables 取真 COUNT(执行后再取一次做 before/after delta)。
    affected_tables = action.get("affected_tables_json")
    before_counts = _snapshot_table_counts(affected_tables)

    try:
        outcome_obj = handler(action, staff)
    except Exception as exc:
        logger.warning("action_executor.handler_failed", extra={"action_id": action_id, "category": category}, exc_info=True)
        lid = _write_ledger(
            action=action, action_id=action_id, staff=staff, outcome="failed", error=str(exc)[:240]
        )
        inbox.set_status(action_id, "failed")
        return _result(ok=False, outcome="failed", category=category, reason="exception", ledger_id=lid, detail={"error": str(exc)[:240]})

    outcome = str(outcome_obj.get("outcome") or "failed")
    reason = str(outcome_obj.get("reason") or "")
    detail = outcome_obj.get("detail") or {}

    # S1:执行后再取一次 COUNT → before/after delta(真验收,确认这条 action 真写了)。
    after_counts = _snapshot_table_counts(affected_tables)
    # 路线0+S1:标准化验收回执(谁/几个 job / 写几行 / 是否花钱 / 真 before-after delta / 失败原因)。
    checklist = _build_result_checklist(
        action, outcome=outcome, reason=reason, detail=detail,
        before_counts=before_counts, after_counts=after_counts,
    )
    detail = {**detail, "result_checklist": checklist}

    # 4. 落 ledger(含回执)+ 写回执到 action 行 + 置 action 终态。
    lid = _write_ledger(
        action=action, action_id=action_id, staff=staff, outcome=outcome, error=reason if outcome != "success" else "", detail=detail
    )
    inbox.set_result_checklist(action_id, checklist)
    if outcome == "success":
        inbox.set_status(action_id, "executed")
        # R8:成功执行 → 写回 vkpi_memory_feedback 埋种(②学习闭环);best-effort,不阻断返回。
        _record_execution_feedback(action, staff, outcome="success", detail=detail)
        _record_outcome_eval(action, outcome="success")  # B5/H4 结果回写 → 推荐权重回流
        return _result(ok=True, outcome="success", category=category, ledger_id=lid, detail=detail)
    if outcome == "failed":
        inbox.set_status(action_id, "failed")
        _record_outcome_eval(action, outcome="fail")  # B5/H4 失败也回写(下次降权)
        return _result(ok=False, outcome="failed", category=category, reason=reason, ledger_id=lid, detail=detail)
    # skipped:不改 action 状态(仍 approved,人可再处理或 dismiss)。
    return _result(ok=False, outcome="skipped", category=category, reason=reason, ledger_id=lid, detail=detail)


_SNAPSHOT_ALLOWED_TABLES = {
    "apify_jobs", "vkpi_kol_pool", "vkpi_project_content_observation_windows",
    "vkpi_project_content_posts", "vkpi_action_execution_ledger",
}


def _snapshot_table_counts(tables: Any) -> dict[str, int]:
    """S1:对 affected_tables 取真 COUNT(*)(只白名单表,只读,容错)。

    用于执行前后对比 → before/after delta = 这条 action 真写了几行。只 COUNT 不改任何数据。
    """
    out: dict[str, int] = {}
    if not isinstance(tables, list):
        return out
    for t in tables:
        name = str(t or "").strip()
        if name not in _SNAPSHOT_ALLOWED_TABLES or not table_exists(name):
            continue
        try:
            row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()
            out[name] = int(dict(row).get("n") or 0) if row else 0
        except Exception:
            logger.debug("action_executor.snapshot_failed", extra={"table": name}, exc_info=True)
    return out


def _build_result_checklist(
    action: dict[str, Any], *, outcome: str, reason: str, detail: dict[str, Any] | None,
    before_counts: dict[str, int] | None = None, after_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """路线0+S1:从 executor 的 detail 提炼标准化验收回执(谁都能一眼看懂执行后发生了什么)。

    jobs_created:enqueue/requeue 命中算 1;rows_written:created_windows 等列表长度;
    cost_spent_cents:仅成功且 uses_llm 才计预估成本(真实消耗仍以 cost ledger 为准);
    wrote_business_data:仅成功且建议标记写业务表才为真。
    S1 before_after:对 affected_tables 取执行前后真 COUNT delta(真验收,确认真写了)。绝不臆造数字。
    """
    d = detail if isinstance(detail, dict) else {}
    jobs = 0
    enq = d.get("enqueue")
    if isinstance(enq, dict) and (enq.get("job") or enq.get("job_id") or enq.get("id")):
        jobs += 1
    if isinstance(d.get("enqueue"), dict) and str(d["enqueue"].get("status") or "") in ("queued", "already_queued", "already_running"):
        jobs = max(jobs, 1)
    if d.get("requeued"):
        jobs += 1
    rows = 0
    for key in ("created_windows", "created", "created_posts"):
        val = d.get(key)
        if isinstance(val, list):
            rows += len(val)
    success = outcome == "success"
    uses_llm = bool(action.get("uses_llm"))
    cost = int(action.get("estimated_cost_cents") or 0) if (success and uses_llm) else 0
    return {
        "outcome": outcome,
        "wrote_business_data": bool(action.get("writes_business_data")) and success,
        "jobs_created": jobs,
        "rows_written": rows,
        "cost_spent_cents": cost,
        "failed_reason": reason if not success else "",
        "acknowledged": bool(d.get("acknowledged")),
        # S1:真 before/after delta(每个 affected_table 执行前后行数变化)。
        "before_after": _diff_counts(before_counts or {}, after_counts or {}),
    }


def _diff_counts(before: dict[str, int], after: dict[str, int]) -> list[dict[str, Any]]:
    """组装 before/after delta 列表(只对两端都取到的表)。"""
    rows: list[dict[str, Any]] = []
    for tbl in sorted(set(before) | set(after)):
        b = int(before.get(tbl, 0))
        a = int(after.get(tbl, 0))
        rows.append({"table": tbl, "before": b, "after": a, "delta": a - b})
    return rows
