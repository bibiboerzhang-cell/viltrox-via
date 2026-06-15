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
    """失败 apify_job 重排 → requeue(status=failed → queued)。仅 failed 行可重排。

    apify_jobs.status CHECK 仅含 queued/running/done/failed(无 blocked);只 requeue failed。
    注:前端 retryTask 走的是另一套 vkpi_tasks 队列;这里针对 apify_jobs 直接 requeue。
    """
    job_id = _entity_id_int(action)
    if not job_id:
        return {"outcome": "skipped", "reason": "failed_retry_no_job_id", "detail": {}}
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE apify_jobs SET status = 'queued', updated_at = NOW() WHERE id = ? AND status = 'failed'",
        (int(job_id),),
    )
    conn.commit()
    requeued = int(getattr(cursor, "rowcount", 0) or 0) > 0
    if not requeued:
        return {"outcome": "skipped", "reason": "failed_retry_not_in_failed_state", "detail": {"job_id": job_id}}
    return {"outcome": "success", "reason": "", "detail": {"job_id": job_id, "requeued": True}}


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


def _exec_skip_reminder(action: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """纯提醒类(kol_profile / event_followup / inventory_low / project_shared_to_you):

    这些 requires_approval=False 且不写业务数据,本不该进 approved 执行路径 → 防御性 skip。
    kol_profile 当前无干净同步刷新 service(grep 0 命中 refresh_profile)→ 先 skip 待补。
    """
    category = str(action.get("category") or "")
    reason_map = {
        "kol_profile": "profile_refresh_no_executor",
        "event_followup": "reminder_only_no_executor",
        "inventory_low": "reminder_only_no_executor",
        "project_shared_to_you": "reminder_only_no_executor",
    }
    return {"outcome": "skipped", "reason": reason_map.get(category, "no_executor"), "detail": {}}


_DISPATCH = {
    "deep_missing": _exec_deep_missing,
    "failed_retry": _exec_failed_retry,
    "project_observation": _exec_project_observation,
    "content_candidate": _exec_content_candidate,
    "retrospective": _exec_retrospective,
    "kol_profile": _exec_skip_reminder,
    "event_followup": _exec_skip_reminder,
    "inventory_low": _exec_skip_reminder,
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

    # 4. 落 ledger + 置 action 终态。
    lid = _write_ledger(
        action=action, action_id=action_id, staff=staff, outcome=outcome, error=reason if outcome != "success" else "", detail=detail
    )
    if outcome == "success":
        inbox.set_status(action_id, "executed")
        return _result(ok=True, outcome="success", category=category, ledger_id=lid, detail=detail)
    if outcome == "failed":
        inbox.set_status(action_id, "failed")
        return _result(ok=False, outcome="failed", category=category, reason=reason, ledger_id=lid, detail=detail)
    # skipped:不改 action 状态(仍 approved,人可再处理或 dismiss)。
    return _result(ok=False, outcome="skipped", category=category, reason=reason, ledger_id=lid, detail=detail)
