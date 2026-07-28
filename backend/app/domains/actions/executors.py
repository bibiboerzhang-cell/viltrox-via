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

import asyncio
import json
import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.actions import inbox, validators
from app.domains.projects import automation_audit

logger = get_logger(__name__)

# skills-wire 开关(默认 OFF):execute_action 成功后是否顺带跑对应 Skill + record_skill_run。
# 默认关 → execute 返回契约/ledger 零变(测试与既有调用方不受 skill 的 DB 副作用污染);
# 线上经 env VKPI_SKILLS_AUTORUN=1 开启让 Skill 真自动跑(运行经 /skills/runs 端点 + detail.skill_run 回执可见)。
_SKILLS_AUTORUN = str(os.environ.get("VKPI_SKILLS_AUTORUN", "")).strip().lower() in ("1", "true", "yes", "on")

_LEDGER = "vkpi_action_execution_ledger"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str, ensure_ascii=False)


def _sql_now() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _sql_json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


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
            VALUES (?,?,?,?,'executed',?,?,?,?,{_sql_json_param()},{_sql_now()})
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


def _finalize_claimed_execution(
    *,
    action: dict[str, Any],
    action_id: int,
    staff: dict[str, Any] | None,
    outcome: str,
    error: str = "",
    detail: dict[str, Any] | None = None,
    checklist: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically persist ledger, checklist, and the claimed action's next state.

    External side effects cannot be rolled back, so a failed database finalize
    intentionally leaves the action in ``executing``.  That blocks automatic
    replay and makes the row require explicit reconciliation instead of risking
    a duplicate external action.
    """
    target_status = {
        "success": "executed",
        "failed": "failed",
        "skipped": "approved",
    }.get(str(outcome), "failed")
    try:
        est = int(action.get("estimated_cost_cents") or 0)
    except (TypeError, ValueError):
        est = 0

    conn = None
    ledger_id: int | None = None
    try:
        conn = get_conn()
        if not table_exists(_LEDGER):
            raise RuntimeError("action execution ledger is unavailable")
        row = conn.execute(
            f"""
            INSERT INTO {_LEDGER}
              (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
               endpoint, cost_cents, error, detail_json, created_at)
            VALUES (?,?,?,?,'executed',?,?,?,?,{_sql_json_param()},{_sql_now()})
            RETURNING id
            """,
            (
                int(action_id),
                str(action.get("category") or ""),
                str(action.get("dedupe_key") or ""),
                int(scope.actor_staff_id(staff)) or None,
                str(outcome),
                str(action.get("suggested_endpoint") or ""),
                est,
                str(error or ""),
                _dumps(detail),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("action execution ledger insert returned no id")
        ledger_id = int(dict(row)["id"])

        cursor = conn.execute(
            f"""
            UPDATE vkpi_action_inbox
            SET status = ?, result_checklist_json = {_sql_json_param()}, updated_at = {_sql_now()}
            WHERE id = ? AND status = 'executing'
            """,
            (target_status, _dumps(checklist), int(action_id)),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return {
                "ok": False,
                "reason": "execution_claim_lost",
                "status": "executing",
                "ledger_id": None,
            }
        conn.commit()
        return {
            "ok": True,
            "status": target_status,
            "ledger_id": ledger_id,
        }
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            logger.debug("action_executor.finalize_rollback_failed", exc_info=True)
        logger.warning(
            "action_executor.finalize_failed",
            extra={"action_id": action_id, "outcome": outcome},
            exc_info=True,
        )
        return {
            "ok": False,
            "reason": "execution_finalize_failed",
            "status": "executing",
            "ledger_id": None,
        }


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
        # record_feedback 的 INSERT 失败(如 created_by_staff_id FK 违例)会把请求/线程作用域的
        # 共享连接标记为 aborted;不 rollback 会让同作用域后续查询全报 "transaction is aborted"
        # (best-effort 写不该污染主链路)。先回滚清状态再静默吞掉。对齐 record_outcome 的兜底模式。
        try:
            get_conn().rollback()
        except Exception:
            logger.debug("action_executor.memory_feedback_rollback_skipped", exc_info=True)
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
            # 红线:action["id"] 是 vkpi_action_inbox 的行号,不是 vkpi_agent_actions.id。
            # agent_action_id FK 指向 vkpi_agent_actions(mig182),inbox 动作并不在那张表里,
            # 误传必触 ForeignKeyViolation(每次执行污染日志)。execute_action 链路本就不产
            # vkpi_agent_actions 留痕行,无真 id 可映射 → FK 列留空(列可空,ON DELETE SET NULL);
            # inbox 动作号已落在 evidence.action_id,学习闭环按 entity_type/entity_id 聚合不丢信息。
            agent_action_id=None,
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
    """用透 Apify · 把联邦发现补人排入 durable workflow。

    本执行器的成功只表示任务已可靠入队；真实 found/enrolled 结果由任务回执给出，
    不在审批请求内同步等待或提前冒充业务结果。
    """
    payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
    query = str(payload.get("query") or action.get("entity_id") or "").strip()
    if not query:
        return {"outcome": "skipped", "reason": "discovery_no_query", "detail": {}}
    from app.domains.kol import onboarding_workflow
    from app.services.jobs.queue import build_job_queue

    async def enqueue() -> dict[str, Any]:
        queue = build_job_queue()
        if queue is None:
            raise RuntimeError("durable job queue unavailable")
        try:
            return await onboarding_workflow.enqueue_kol_onboarding(
                queue,
                query,
                limit=int(payload.get("limit") or 20),
                staff=staff,
            )
        finally:
            await queue.close()

    res = asyncio.run(enqueue())
    return {
        "outcome": "success",
        "reason": "",
        "detail": {
            "query": query,
            "execution_status": "queued",
            "business_outcome": "pending",
            "enqueue": res,
            "note": "已排入联邦发现→落 Pool Workflow；进 MY KOL 仍需手动勾选。",
        },
    }


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


# ── skills-wire · 收口① 让 Skill 真跑 + 落账(best-effort,绝不阻断主执行) ──────
# 现状:marketing_brain/skills/*.py 的 run() 写好但无真实调用点,record_skill_run 从没被调。
# 这里把【至少 2 个 category】的成功执行接到对应 skill.run(record=True):
#   - kol_profile     → creator_match.run(...)  (用该 KOL 的 primary_topic / 推荐产品线当 product 口径)
#   - content_candidate → content_score.run(...) (用该内容贴的 evidence_id 当 final_v1 缓存 target_id)
# 跑一次真 execute 成功后 → vkpi_skill_runs 真多行。
# 铁律:model_fn 永远 None(走规则,绝不烧真 LLM);任何异常吞掉只 debug,不影响 execute_action 返回。

def _kol_product_query(kol_pool_id: int) -> tuple[str, str]:
    """从 vkpi_kol_pool 取一个「产品/选题」口径喂 creator_match。返回 (product_query, country)。

    优先 recommended_product_lines_json 第一项 → 其次 primary_topic → 兜底 handle/display_name。
    只读,绝不触 viltrox_fit_score。取不到任何信号则返回空串(skill 自会判 error,仍落一行账本)。
    """
    try:
        row = get_conn().execute(
            "SELECT primary_topic, recommended_product_lines_json, handle, display_name, country "
            "FROM vkpi_kol_pool WHERE id = ?",
            (int(kol_pool_id),),
        ).fetchone()
    except Exception:
        return "", ""
    if not row:
        return "", ""
    d = dict(row)
    product = ""
    raw_lines = d.get("recommended_product_lines_json")
    try:
        lines = raw_lines if isinstance(raw_lines, list) else (json.loads(raw_lines) if raw_lines else [])
    except Exception:
        lines = []
    if isinstance(lines, list) and lines:
        first = lines[0]
        product = str(first.get("name") if isinstance(first, dict) else first or "").strip()
    if not product:
        product = str(d.get("primary_topic") or "").strip()
    if not product:
        product = str(d.get("handle") or d.get("display_name") or "").strip()
    return product, str(d.get("country") or "").strip()


def _evidence_id_for_post(post_id: int) -> str:
    """从 vkpi_project_content_posts 取 evidence_id(content_score 用它定位 final_v1 缓存行)。"""
    try:
        row = get_conn().execute(
            "SELECT evidence_id FROM vkpi_project_content_posts WHERE id = ?",
            (int(post_id),),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    ev = dict(row).get("evidence_id")
    return str(ev).strip() if ev not in (None, "") else ""


def _maybe_run_skill(action: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """成功执行后顺带跑对应 skill 并 record_skill_run(best-effort)。返回 skill 摘要供回执,失败返 None。

    绝不烧真 LLM(model_fn=None);任何异常吞掉(只 debug),绝不影响 execute_action 主返回。
    """
    category = str(action.get("category") or "")
    try:
        if category == "kol_profile":
            kol_pool_id = _entity_id_int(action)
            if not kol_pool_id:
                return None
            product, country = _kol_product_query(kol_pool_id)
            if not product:
                return None
            from app.domains.marketing_brain.skills import creator_match

            out = creator_match.run(
                {"product": product, "market": country, "limit": 10},
                model_fn=None,
                record=True,
            )
            return {
                "skill": "creator_match",
                "recorded": True,
                "recommendations": len(out.get("recommendations") or []),
                "product_query": product,
            }
        if category == "content_candidate":
            payload = action.get("payload_json") if isinstance(action.get("payload_json"), dict) else {}
            try:
                post_id = int(payload.get("post_id"))
            except (TypeError, ValueError):
                post_id = 0
            if not post_id:
                return None
            evidence_id = _evidence_id_for_post(post_id)
            if not evidence_id:
                return None
            from app.domains.marketing_brain.skills import content_score

            out = content_score.run(
                {"analysis_cache_ref": {"target_id": evidence_id}},
                model_fn=None,
                record=True,
            )
            return {
                "skill": "content_score",
                "recorded": True,
                "skill_status": out.get("status"),
                "target_id": evidence_id,
            }
    except Exception:
        # best-effort:skill 跑挂 / 账本落不进绝不拖垮主执行。
        logger.debug("action_executor.skill_wire_failed", extra={"action_id": action.get("id"), "category": category}, exc_info=True)
        return None
    return None


def execute_action(action_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行一条已审批动作。原子抢占后才运行 handler;任何异常明确落 failed。"""
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

    # 3. 原子抢占:approved -> executing。只有一个请求能取得执行权。
    claim = inbox.claim_action_execution(action_id, staff)
    if not claim.get("ok"):
        return _result(
            ok=False,
            outcome="skipped",
            category=category,
            reason=str(claim.get("reason") or "execution_already_claimed"),
            detail={"status": claim.get("status")},
        )

    # S1:执行前对 affected_tables 取真 COUNT(执行后再取一次做 before/after delta)。
    affected_tables = action.get("affected_tables_json")
    before_counts = _snapshot_table_counts(affected_tables)

    try:
        outcome_obj = handler(action, staff)
    except Exception as exc:
        logger.warning("action_executor.handler_failed", extra={"action_id": action_id, "category": category}, exc_info=True)
        reason = str(exc)[:240]
        after_counts = _snapshot_table_counts(affected_tables)
        detail = {"error": reason}
        checklist = _build_result_checklist(
            action,
            outcome="failed",
            reason=reason,
            detail=detail,
            before_counts=before_counts,
            after_counts=after_counts,
        )
        detail["result_checklist"] = checklist
        finalized = _finalize_claimed_execution(
            action=action,
            action_id=action_id,
            staff=staff,
            outcome="failed",
            error=reason,
            detail=detail,
            checklist=checklist,
        )
        final_reason = "exception" if finalized.get("ok") else str(finalized.get("reason"))
        if not finalized.get("ok"):
            detail["manual_reconciliation_required"] = True
        return _result(
            ok=False,
            outcome="failed",
            category=category,
            reason=final_reason,
            ledger_id=finalized.get("ledger_id"),
            detail=detail,
        )

    if not isinstance(outcome_obj, dict):
        outcome = "failed"
        reason = "invalid_handler_result"
        detail = {"handler_result_type": type(outcome_obj).__name__}
    else:
        outcome = str(outcome_obj.get("outcome") or "failed")
        reason = str(outcome_obj.get("reason") or "")
        raw_detail = outcome_obj.get("detail")
        detail = raw_detail if isinstance(raw_detail, dict) else {}
    if outcome not in {"success", "failed", "skipped"}:
        detail = {**detail, "handler_outcome": outcome}
        outcome = "failed"
        reason = "invalid_handler_outcome"

    # S1:执行后再取一次 COUNT → before/after delta(真验收,确认这条 action 真写了)。
    after_counts = _snapshot_table_counts(affected_tables)
    # 路线0+S1:标准化验收回执(谁/几个 job / 写几行 / 是否花钱 / 真 before-after delta / 失败原因)。
    checklist = _build_result_checklist(
        action, outcome=outcome, reason=reason, detail=detail,
        before_counts=before_counts, after_counts=after_counts,
    )
    detail = {**detail, "result_checklist": checklist}

    # 4. ledger + checklist + 终态在同一事务内落地。提交失败时保留 executing，禁止自动重放。
    finalized = _finalize_claimed_execution(
        action=action,
        action_id=action_id,
        staff=staff,
        outcome=outcome,
        error=reason if outcome != "success" else "",
        detail=detail,
        checklist=checklist,
    )
    if not finalized.get("ok"):
        detail = {
            **detail,
            "side_effect_outcome": outcome,
            "manual_reconciliation_required": True,
        }
        return _result(
            ok=False,
            outcome="failed",
            category=category,
            reason=str(finalized.get("reason") or "execution_finalize_failed"),
            detail=detail,
        )
    lid = finalized.get("ledger_id")
    if outcome == "success":
        # R8:成功执行 → 写回 vkpi_memory_feedback 埋种(②学习闭环);best-effort,不阻断返回。
        _record_execution_feedback(action, staff, outcome="success", detail=detail)
        _record_outcome_eval(action, outcome="success")  # B5/H4 结果回写 → 推荐权重回流
        # skills-wire(opt-in,默认 OFF 见 _SKILLS_AUTORUN):成功后顺带跑对应 Skill + record_skill_run
        # (best-effort,不烧真 LLM)。默认关 → execute 返回/ledger 零变,不污染既有测试与调用方;
        # 开启后回执进 detail.skill_run,运行另经 /skills/runs 端点可见。
        if _SKILLS_AUTORUN:
            skill_summary = _maybe_run_skill(action, detail)
            if skill_summary:
                detail = {**detail, "skill_run": skill_summary}
        return _result(ok=True, outcome="success", category=category, ledger_id=lid, detail=detail)
    if outcome == "failed":
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
