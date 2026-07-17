"""W1 Action Inbox 编排 — 生成(dry-run)+ 幂等落库 + run 级 ledger + scope 读取。

generate_daily_action_inbox():跑 8 个 producer(各自容错)→ 幂等 UPSERT 进 vkpi_action_inbox →
写一条 run 级 dry_run ledger(审计起点)→ commit。**绝不执行、绝不写业务表、绝不碰 viltrox_fit_score。**
list_inbox():按 scope 读(管理层看全局,成员只看自己 owner 的;owner=NULL 的公司级动作成员不可见)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.actions.producers import PRODUCERS

logger = get_logger(__name__)

_TABLE = "vkpi_action_inbox"
_LEDGER = "vkpi_action_execution_ledger"
_EXECUTION_RECONCILE_OVERDUE_SECONDS = 15 * 60


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _sql_now() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def _sql_json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _sql_is_today(column: str) -> str:
    """Return a backend-specific predicate for a trusted timestamp column."""
    return (
        f"{column}::date = CURRENT_DATE"
        if is_postgres_runtime()
        else f"date({column}) = date('now')"
    )


def persist_suggestions(suggestions: list[dict[str, Any]]) -> int:
    """把一组 make_suggestion 产物落 vkpi_action_inbox(status='suggested')。返回落库条数。

    H5:供编排器把计划步骤物化成可审批的 Inbox 项复用。决策四件套缺省由表 DEFAULT 兜底。
    ON CONFLICT(dedupe_key) 仅在 status='suggested' 时更新(不覆盖已审/已执行)。零触 viltrox_fit_score。
    """
    if not suggestions or not table_exists(_TABLE):
        return 0
    conn = get_conn()
    n = 0
    for s in suggestions:
        try:
            conn.execute(
                f"""
                INSERT INTO {_TABLE}
                  (dedupe_key, category, title, detail, priority, entity_type, entity_id,
                   suggested_endpoint, estimated_cost_cents, writes_business_data, uses_llm,
                   requires_approval, owner_staff_id, reason, payload_json, status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,'suggested',NOW(),NOW())
                ON CONFLICT (dedupe_key) DO UPDATE SET
                   title = EXCLUDED.title, detail = EXCLUDED.detail, updated_at = NOW()
                WHERE {_TABLE}.status = 'suggested'
                """,
                (
                    s["dedupe_key"], s["category"], s["title"], s["detail"], s["priority"],
                    s.get("entity_type", ""), s.get("entity_id", ""), s.get("suggested_endpoint", ""),
                    int(s.get("estimated_cost_cents", 0) or 0), s.get("writes_business_data", False),
                    s.get("uses_llm", False), s.get("requires_approval", True), s.get("owner_staff_id"),
                    s.get("reason", ""), _dumps(s.get("payload", {})),
                ),
            )
            n += 1
        except Exception:
            logger.warning("action_inbox.persist_suggestion_failed", extra={"dedupe_key": s.get("dedupe_key")}, exc_info=True)
    conn.commit()
    return n


# ── 生成(dry-run only) ───────────────────────────────────────────────
def generate_daily_action_inbox(
    staff: dict[str, Any] | None = None,
    *,
    dry_run: bool = True,
    persist: bool = True,
    limit_per_category: int = 25,
) -> dict[str, Any]:
    """聚合 8 类建议。W1 恒为 dry-run:只产建议、不执行、不写任何业务表。

    persist=True 时把建议幂等写入 vkpi_action_inbox(自身台账,非业务表);已被人工
    dismissed/snoozed/approved 的行不会被复活(UPSERT 仅刷新仍是 'suggested' 的)。
    """
    if not table_exists(_TABLE):
        return {"ok": False, "reason": "migration_141_not_applied", "dry_run": True}

    conn = get_conn()
    by_category: dict[str, int] = {}
    truncated: list[str] = []
    suggestions: list[dict[str, Any]] = []

    for category, producer in PRODUCERS.items():
        try:
            rows = producer(conn, limit=limit_per_category) or []
        except Exception:
            # 容错隔离:单类查询坏(列漂移等)→ 该类记 0,不拖垮其余 7 类。
            logger.warning("action_inbox.producer_failed", extra={"category": category}, exc_info=True)
            by_category[category] = 0
            continue
        by_category[category] = len(rows)
        if len(rows) >= limit_per_category:
            truncated.append(category)  # 无声截断 → 显式记录(诚实)
        suggestions.extend(rows)

    persisted = 0
    if persist and suggestions:
        for s in suggestions:
            try:
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}
                      (dedupe_key, category, title, detail, priority, entity_type, entity_id,
                       suggested_endpoint, estimated_cost_cents, writes_business_data, uses_llm,
                       requires_approval, owner_staff_id, reason, payload_json,
                       expected_gain, risk_level, evidence_refs_json,
                       verification_plan_json, affected_tables_json,
                       status, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?,?::jsonb,?::jsonb,?::jsonb,'suggested',NOW(),NOW())
                    ON CONFLICT (dedupe_key) DO UPDATE SET
                       title = EXCLUDED.title,
                       detail = EXCLUDED.detail,
                       priority = EXCLUDED.priority,
                       suggested_endpoint = EXCLUDED.suggested_endpoint,
                       estimated_cost_cents = EXCLUDED.estimated_cost_cents,
                       writes_business_data = EXCLUDED.writes_business_data,
                       uses_llm = EXCLUDED.uses_llm,
                       requires_approval = EXCLUDED.requires_approval,
                       owner_staff_id = EXCLUDED.owner_staff_id,
                       reason = EXCLUDED.reason,
                       payload_json = EXCLUDED.payload_json,
                       expected_gain = EXCLUDED.expected_gain,
                       risk_level = EXCLUDED.risk_level,
                       evidence_refs_json = EXCLUDED.evidence_refs_json,
                       verification_plan_json = EXCLUDED.verification_plan_json,
                       affected_tables_json = EXCLUDED.affected_tables_json,
                       updated_at = NOW()
                    WHERE {_TABLE}.status = 'suggested'
                    """,
                    (
                        s["dedupe_key"],
                        s["category"],
                        s["title"],
                        s["detail"],
                        s["priority"],
                        s["entity_type"],
                        s["entity_id"],
                        s["suggested_endpoint"],
                        s["estimated_cost_cents"],
                        s["writes_business_data"],
                        s["uses_llm"],
                        s["requires_approval"],
                        s["owner_staff_id"],
                        s["reason"],
                        _dumps(s["payload"]),
                        s.get("expected_gain", ""),
                        s.get("risk_level", "low"),
                        _dumps(s.get("evidence_refs", [])),
                        _dumps(s.get("verification_plan", [])),
                        _dumps(s.get("affected_tables", [])),
                    ),
                )
                persisted += 1
            except Exception:
                logger.warning("action_inbox.persist_failed", extra={"dedupe_key": s.get("dedupe_key")}, exc_info=True)

    total = sum(by_category.values())
    # run 级审计:每次生成落一条 dry_run ledger(W1 起点;W2 执行器再每动作落行)。
    if persist and table_exists(_LEDGER):
        try:
            conn.execute(
                f"""
                INSERT INTO {_LEDGER}
                  (action_id, category, dedupe_key, actor_staff_id, mode, outcome, endpoint, cost_cents, error, detail_json, created_at)
                VALUES (NULL, 'run', '', ?, 'dry_run', 'success', '', 0, '', ?::jsonb, NOW())
                """,
                (
                    int(scope.actor_staff_id(staff)) or None,
                    _dumps({"total": total, "persisted": persisted, "by_category": by_category, "truncated": truncated}),
                ),
            )
        except Exception:
            logger.warning("action_inbox.ledger_failed", exc_info=True)

    if persist:
        conn.commit()

    return {
        "ok": True,
        "dry_run": True,  # W1 恒 dry-run
        "persisted": persisted if persist else 0,
        "generated": total,
        "by_category": by_category,
        "truncated_categories": truncated,
    }


# ── 今日闭环计数(只读统计;灌水可见化) ──────────────────────────────
def today_summary(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """当天已执行 / 已批准计数(scope 同 list_inbox;成员只算自己 owner 的)。

    today_executed_count:今天真跑成功的动作数 —— 读 vkpi_action_execution_ledger
      (mode='executed' + outcome='success' + created_at::date=CURRENT_DATE)。这是
      闭环"在转"的硬证据(execute 后立增)。成员按 JOIN inbox.owner_staff_id 收口。
    today_approved_count:今天通过人审的动作数 —— 读 vkpi_action_inbox 当天 updated_at
      且 status 已越过 'approved' 关口(approved/executing/executed/failed)。

    红线:全程只读两张自身台账表,绝不写;不碰 viltrox_fit_score / rule_v0。
    缺表 / 任意异常 → 返回 0(诚实降级,不拖垮 inbox 列表)。
    """
    out = {"today_executed_count": 0, "today_approved_count": 0}
    if not table_exists(_TABLE):
        return out
    conn = get_conn()
    can_all = scope.can_view_all(staff)
    actor = int(scope.actor_staff_id(staff))

    # 今日已执行:执行台账里今天成功落的 executed 行。
    if table_exists(_LEDGER):
        try:
            if can_all:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM {_LEDGER}
                    WHERE mode = 'executed' AND outcome = 'success'
                      AND {_sql_is_today('created_at')}
                    """,
                ).fetchone()
            else:
                # 成员:只算归属自己 owner 的 action 的执行行(run 级 action_id NULL 不计)。
                row = conn.execute(
                    f"""
                    SELECT COUNT(*) AS n FROM {_LEDGER} l
                    JOIN {_TABLE} a ON a.id = l.action_id
                    WHERE l.mode = 'executed' AND l.outcome = 'success'
                      AND {_sql_is_today('l.created_at')}
                      AND a.owner_staff_id = ?
                    """,
                    (actor,),
                ).fetchone()
            out["today_executed_count"] = int(dict(row).get("n") or 0) if row else 0
        except Exception:
            logger.warning("action_inbox.today_executed_count_failed", exc_info=True)

    # 今日已批准:今天 updated_at 且已越过人审关口(approved 及其后续 executed/failed)。
    try:
        if can_all:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM {_TABLE}
                WHERE status IN ('approved', 'executing', 'executed', 'failed')
                  AND {_sql_is_today('updated_at')}
                """,
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n FROM {_TABLE}
                WHERE status IN ('approved', 'executing', 'executed', 'failed')
                  AND {_sql_is_today('updated_at')}
                  AND owner_staff_id = ?
                """,
                (actor,),
            ).fetchone()
        out["today_approved_count"] = int(dict(row).get("n") or 0) if row else 0
    except Exception:
        logger.warning("action_inbox.today_approved_count_failed", exc_info=True)

    return out


# ── 读取(scope-gated) ────────────────────────────────────────────────
def list_inbox(
    staff: dict[str, Any] | None = None,
    *,
    status: str = "suggested",
    category: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """读建议。管理层看全局;成员只看 owner_staff_id=自己 的(owner=NULL 的公司级动作成员不可见)。"""
    if not table_exists(_TABLE):
        return {"items": [], "available": False, "reason": "migration_141_not_applied"}

    conn = get_conn()
    safe_limit = max(1, min(int(limit or 50), 200))
    where = ["status = ?"]
    params: list[Any] = [status]

    if not scope.can_view_all(staff):
        # 红线4:成员看不到全局 action;只放行归属自己的。
        where.append("owner_staff_id = ?")
        params.append(int(scope.actor_staff_id(staff)))

    if category:
        where.append("category = ?")
        params.append(category)

    params.append(safe_limit)
    rows = conn.execute(
        f"""
        SELECT id, dedupe_key, category, title, detail, priority, entity_type, entity_id,
               suggested_endpoint, estimated_cost_cents, writes_business_data, uses_llm,
               requires_approval, owner_staff_id, reason, payload_json,
               expected_gain, risk_level, evidence_refs_json, result_checklist_json, approval_reason,
               verification_plan_json, affected_tables_json,
               status, created_at, updated_at
        FROM {_TABLE}
        WHERE {' AND '.join(where)}
        ORDER BY
          CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
          updated_at DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()

    # failed_retry 活状态复核(2026-07-16):建议行由每日生成器落库,底下 apify_jobs 状态随时在变
    # (失败池回收后任务已回 queued/done);读端按当前真实状态过滤,不让「失败待重试」chip 指向
    # 已不在失败/受阻态的任务。纯读一把 IN 查询,不写建议行(执行器本就有 not_in_failed_state 闸)。
    live_retry_ok: set[str] | None = None
    retry_job_ids = sorted({
        str(dict(r).get("entity_id") or "").strip()
        for r in rows
        if str(dict(r).get("category") or "") == "failed_retry"
        and str(dict(r).get("entity_id") or "").strip().isdigit()
    })
    if retry_job_ids and table_exists("apify_jobs"):
        try:
            placeholders = ",".join(["?"] * len(retry_job_ids))
            job_rows = conn.execute(
                f"SELECT id, status FROM apify_jobs WHERE id IN ({placeholders})",
                tuple(int(job_id) for job_id in retry_job_ids),
            ).fetchall()
            live_retry_ok = {
                str(dict(job).get("id"))
                for job in job_rows
                if str(dict(job).get("status") or "") in ("failed", "blocked")
            }
        except Exception:
            # 复核失败 → 不过滤(诚实降级为旧行为),绝不因只读校验拖垮 inbox。
            logger.warning("action_inbox.failed_retry_live_check_failed", exc_info=True)
            live_retry_ok = None

    items = []
    counts: dict[str, int] = {}
    for r in rows:
        row = dict(r)
        if (
            live_retry_ok is not None
            and str(row.get("category") or "") == "failed_retry"
            and str(row.get("entity_id") or "").strip().isdigit()
            and str(row.get("entity_id") or "").strip() not in live_retry_ok
        ):
            continue  # 任务已离开 failed/blocked(如已回收入队),chip 不再展示
        row["payload_json"] = _loads(row.get("payload_json"))
        row["evidence_refs_json"] = _loads(row.get("evidence_refs_json"))
        row["result_checklist_json"] = _loads(row.get("result_checklist_json"))
        row["verification_plan_json"] = _loads(row.get("verification_plan_json"))
        row["affected_tables_json"] = _loads(row.get("affected_tables_json"))
        if str(row.get("status") or "") == "executing":
            age_seconds = _execution_age_seconds(row.get("updated_at"))
            row["execution_age_seconds"] = age_seconds
            row["manual_reconciliation_required"] = True
            row["reconciliation_overdue"] = (
                age_seconds is not None
                and age_seconds >= _EXECUTION_RECONCILE_OVERDUE_SECONDS
            )
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        items.append(row)

    return {
        "items": items,
        "available": True,
        "count": len(items),
        "by_category": counts,
        "scope": "all" if scope.can_view_all(staff) else "own",
        # 灌水可见化:当天闭环计数(已执行 / 已批准),纯加字段、scope 同上、只读统计。
        "today_summary": today_summary(staff),
    }


def _execution_age_seconds(value: Any) -> int | None:
    """Return a stable age for an executing claim without trusting the client."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))


def reconcile_executing_action(
    action_id: int,
    staff: dict[str, Any] | None,
    *,
    decision: str,
    reason: str,
    evidence: list[dict[str, Any]],
    correlation_id: str,
) -> dict[str, Any]:
    """Manually reconcile an execution whose external result is uncertain.

    The action row is locked before checking the state and correlation id.  The
    audit ledger insert and state transition commit together, so concurrent or
    repeated requests cannot create conflicting terminal decisions.
    """
    normalized_decision = str(decision or "").strip().lower()
    normalized_reason = str(reason or "").strip()
    normalized_correlation = str(correlation_id or "").strip()
    normalized_evidence = [item for item in evidence if isinstance(item, dict) and item]
    actor_id = int(scope.actor_staff_id(staff))

    if normalized_decision not in {"succeeded", "failed", "unknown"}:
        return {"ok": False, "reason": "invalid_reconciliation_decision"}
    if not normalized_reason:
        return {"ok": False, "reason": "reconciliation_reason_required"}
    if not normalized_evidence:
        return {"ok": False, "reason": "reconciliation_evidence_required"}
    if not normalized_correlation:
        return {"ok": False, "reason": "reconciliation_correlation_required"}
    if actor_id <= 0:
        return {"ok": False, "reason": "reconciliation_actor_required"}
    if not table_exists(_TABLE) or not table_exists(_LEDGER):
        return {"ok": False, "reason": "reconciliation_ledger_unavailable"}

    target_status = {
        "succeeded": "executed",
        "failed": "failed",
        "unknown": "executing",
    }[normalized_decision]
    ledger_outcome = {
        "succeeded": "success",
        "failed": "failed",
        "unknown": "pending",
    }[normalized_decision]
    conn = get_conn()
    owner_clause = ""
    params: list[Any] = [int(action_id)]
    if not scope.can_view_all(staff):
        owner_clause = " AND owner_staff_id = ?"
        params.append(actor_id)

    try:
        postgres_runtime = is_postgres_runtime()
        if not postgres_runtime and not bool(getattr(conn, "in_transaction", False)):
            # SQLite has no SELECT ... FOR UPDATE.  BEGIN IMMEDIATE serializes
            # the read/check/write sequence across the per-thread test conns.
            conn.execute("BEGIN IMMEDIATE")
        lock_clause = " FOR UPDATE" if postgres_runtime else ""
        row = conn.execute(
            f"""
            SELECT id, dedupe_key, category, suggested_endpoint, status,
                   result_checklist_json
            FROM {_TABLE}
            WHERE id = ?{owner_clause}
            {lock_clause}
            """,
            tuple(params),
        ).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "reason": "not_found_or_out_of_scope"}
        action = dict(row)

        existing_rows = conn.execute(
            f"""
            SELECT id, outcome, detail_json
            FROM {_LEDGER}
            WHERE action_id = ?
              AND endpoint = 'manual:reconcile'
            ORDER BY id DESC
            """,
            (int(action_id),),
        ).fetchall()
        existing = None
        for candidate in existing_rows:
            candidate_row = dict(candidate)
            candidate_detail = _loads(candidate_row.get("detail_json"))
            if (
                isinstance(candidate_detail, dict)
                and str(candidate_detail.get("correlation_id") or "") == normalized_correlation
            ):
                existing = candidate_row
                break
        if existing is not None:
            existing_row = dict(existing)
            existing_detail = _loads(existing_row.get("detail_json"))
            existing_decision = str(
                existing_detail.get("decision") if isinstance(existing_detail, dict) else ""
            )
            conn.rollback()
            if existing_decision != normalized_decision:
                return {"ok": False, "reason": "reconciliation_correlation_conflict"}
            return {
                "ok": True,
                "action_id": int(action_id),
                "decision": existing_decision,
                "status": str(existing_detail.get("target_status") or action.get("status") or ""),
                "ledger_id": int(existing_row["id"]),
                "correlation_id": normalized_correlation,
                "idempotent": True,
            }

        current_status = str(action.get("status") or "")
        if current_status != "executing":
            conn.rollback()
            return {
                "ok": False,
                "reason": "action_not_awaiting_reconciliation",
                "status": current_status,
            }

        audit_detail = {
            "kind": "manual_reconciliation",
            "audit_version": 1,
            "decision": normalized_decision,
            "reason": normalized_reason,
            "evidence": normalized_evidence,
            "correlation_id": normalized_correlation,
            "actor": {
                "staff_id": actor_id,
                "role": str((staff or {}).get("role") or ""),
            },
            "previous_status": current_status,
            "target_status": target_status,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        checklist_patch = {
            "manual_reconciliation": {
                "decision": normalized_decision,
                "reason": normalized_reason,
                "correlation_id": normalized_correlation,
                "evidence_count": len(normalized_evidence),
                "actor_staff_id": actor_id,
            }
        }
        current_checklist = _loads(action.get("result_checklist_json"))
        merged_checklist = dict(current_checklist) if isinstance(current_checklist, dict) else {}
        merged_checklist.update(checklist_patch)
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET status = ?,
                result_checklist_json = {_sql_json_param()},
                updated_at = {_sql_now()}
            WHERE id = ? AND status = 'executing'
            """,
            (target_status, _dumps(merged_checklist), int(action_id)),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return {"ok": False, "reason": "reconciliation_state_changed"}

        ledger_row = conn.execute(
            f"""
            INSERT INTO {_LEDGER}
              (action_id, category, dedupe_key, actor_staff_id, mode, outcome,
               endpoint, cost_cents, error, detail_json, created_at)
            VALUES (?,?,?,?,'executed',?,'manual:reconcile',0,?,{_sql_json_param()},{_sql_now()})
            RETURNING id
            """,
            (
                int(action_id),
                str(action.get("category") or ""),
                str(action.get("dedupe_key") or ""),
                actor_id,
                ledger_outcome,
                "" if normalized_decision == "succeeded" else normalized_reason,
                _dumps(audit_detail),
            ),
        ).fetchone()
        if ledger_row is None:
            raise RuntimeError("reconciliation ledger insert returned no id")
        ledger_id = int(dict(ledger_row)["id"])
        conn.commit()
        return {
            "ok": True,
            "action_id": int(action_id),
            "decision": normalized_decision,
            "status": target_status,
            "ledger_id": ledger_id,
            "correlation_id": normalized_correlation,
            "idempotent": False,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_inbox.reconcile_rollback_failed", exc_info=True)
        logger.warning(
            "action_inbox.reconcile_failed",
            extra={"action_id": action_id, "actor_staff_id": actor_id},
            exc_info=True,
        )
        return {"ok": False, "reason": "reconciliation_persist_failed"}


# ── R7 执行台账回读(scope-gated 只读) ────────────────────────────────
_LEDGER_COLUMNS = (
    "id, action_id, category, dedupe_key, actor_staff_id, mode, outcome, "
    "endpoint, cost_cents, error, detail_json, created_at"
)


def read_execution_ledger(
    staff: dict[str, Any] | None = None,
    *,
    action_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """读 vkpi_action_execution_ledger(只读)。action_id 给定→该条 action 的所有执行行;否则最近 N 条。

    scope:成员只见自己 owner 的 action 的台账;管理层全局(含 run 级 action_id=NULL 行)。
    红线:全程只读 ledger,绝不写;不碰 viltrox_fit_score / rule_v0。
    """
    if not table_exists(_LEDGER):
        return {"items": [], "available": False, "reason": "ledger_not_available"}
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 50), 200))
    can_all = scope.can_view_all(staff)

    if action_id is not None:
        # 单条 action:先 scope 校验可见(越权/不存在 → 空,不泄漏)。
        if get_action(int(action_id), staff) is None:
            return {"items": [], "available": True, "count": 0, "by_outcome": {}, "scope": "own"}
        rows = conn.execute(
            f"SELECT {_LEDGER_COLUMNS} FROM {_LEDGER} WHERE action_id = ? "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            (int(action_id), safe_limit),
        ).fetchall()
    elif can_all:
        rows = conn.execute(
            f"SELECT {_LEDGER_COLUMNS} FROM {_LEDGER} ORDER BY created_at DESC, id DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    else:
        # 成员:只放行自己 owner 的 action 的台账(JOIN inbox.owner_staff_id)。run 级(action_id NULL)不可见。
        rows = conn.execute(
            f"""
            SELECT l.id, l.action_id, l.category, l.dedupe_key, l.actor_staff_id, l.mode,
                   l.outcome, l.endpoint, l.cost_cents, l.error, l.detail_json, l.created_at
            FROM {_LEDGER} l
            JOIN {_TABLE} a ON a.id = l.action_id
            WHERE a.owner_staff_id = ?
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ?
            """,
            (int(scope.actor_staff_id(staff)), safe_limit),
        ).fetchall()

    items = []
    by_outcome: dict[str, int] = {}
    for r in rows:
        row = dict(r)
        row["detail_json"] = _loads(row.get("detail_json"))
        oc = str(row.get("outcome") or "")
        by_outcome[oc] = by_outcome.get(oc, 0) + 1
        items.append(row)
    return {
        "items": items,
        "available": True,
        "count": len(items),
        "by_outcome": by_outcome,
        "scope": "all" if can_all else "own",
    }


# ── W2 状态流转 + 单条读取(scope-gated) ────────────────────────────────
_INBOX_COLUMNS = (
    "id, dedupe_key, category, title, detail, priority, entity_type, entity_id, "
    "suggested_endpoint, estimated_cost_cents, writes_business_data, uses_llm, "
    "requires_approval, owner_staff_id, reason, payload_json, "
    "expected_gain, risk_level, evidence_refs_json, result_checklist_json, approval_reason, "
    "verification_plan_json, affected_tables_json, "
    "status, touches_v6_fit, created_at, updated_at"
)

# 人可发起的流转目标(executed/failed 仅由 executor 内部 set_status 用,不开放给 _transition)。
_TRANSITION_TARGETS = {"approved", "dismissed", "snoozed"}
# 允许「进入」某目标态的源态白名单(终态 executed/dismissed/failed 拒)。
_TRANSITION_FROM: dict[str, tuple[str, ...]] = {
    "approved": ("suggested", "snoozed"),  # snoozed 可被重新 approve
    "dismissed": ("suggested", "snoozed"),
    "snoozed": ("suggested",),
}


def get_action(action_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """读单条建议(scope 收口)。越权 / 不存在 / 缺表 → None。

    scope 与 list_inbox 同款:成员只放行 owner_staff_id==自己(owner=NULL 公司级成员不可见)。
    payload_json _loads 回 dict。
    """
    if not table_exists(_TABLE):
        return None
    row = get_conn().execute(
        f"SELECT {_INBOX_COLUMNS} FROM {_TABLE} WHERE id = ?",
        (int(action_id),),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    if not scope.can_view_all(staff):
        # 成员越权收口:owner 必须等于 actor;公司级(owner=NULL)成员不可见。
        owner = item.get("owner_staff_id")
        if owner is None or int(owner) != int(scope.actor_staff_id(staff)):
            return None
    item["payload_json"] = _loads(item.get("payload_json"))
    item["evidence_refs_json"] = _loads(item.get("evidence_refs_json"))
    item["result_checklist_json"] = _loads(item.get("result_checklist_json"))
    item["verification_plan_json"] = _loads(item.get("verification_plan_json"))
    item["affected_tables_json"] = _loads(item.get("affected_tables_json"))
    return item


def claim_action_execution(
    action_id: int, staff: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Atomically claim one approved action for execution.

    The scope predicate is part of the compare-and-set UPDATE, so a stale read
    cannot widen access.  Only the request that changes approved -> executing
    may run the handler; every concurrent or repeated request fails closed.
    """
    if not table_exists(_TABLE):
        return {"ok": False, "reason": "migration_141_not_applied", "action_id": int(action_id)}

    actor = int(scope.actor_staff_id(staff))
    can_all = scope.can_view_all(staff)
    if not can_all and actor <= 0:
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}

    conn = get_conn()
    params: list[Any] = [int(action_id)]
    owner_clause = ""
    if not can_all:
        owner_clause = " AND owner_staff_id = ?"
        params.append(actor)

    try:
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET status = 'executing', updated_at = {_sql_now()}
            WHERE id = ? AND status = 'approved'{owner_clause}
            RETURNING id, status
            """,
            tuple(params),
        )
        row = cursor.fetchone()
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("action_inbox.claim_rollback_failed", exc_info=True)
        logger.warning("action_inbox.claim_failed", extra={"action_id": action_id}, exc_info=True)
        return {
            "ok": False,
            "reason": "execution_claim_failed",
            "action_id": int(action_id),
        }

    if row is not None:
        return {"ok": True, "status": "executing", "action_id": int(action_id)}

    current = get_action(action_id, staff)
    if current is None:
        reason = "not_found_or_out_of_scope"
        current_status = ""
    else:
        reason = "execution_already_claimed"
        current_status = str(current.get("status") or "")
    return {
        "ok": False,
        "reason": reason,
        "action_id": int(action_id),
        "status": current_status,
    }


def _transition(
    action_id: int,
    to_status: str,
    staff: dict[str, Any] | None = None,
    *,
    snooze_until: str | None = None,
    approval_reason: str | None = None,
) -> dict[str, Any]:
    """通用流转:approve/dismiss/snooze。scope 收口 + 源态白名单 + 自身行 UPDATE。

    红线:只动 vkpi_action_inbox 自己这一行,绝不碰 viltrox_fit_score / rule_v0。
    """
    target = str(to_status or "").strip().lower()
    if target not in _TRANSITION_TARGETS:
        return {"ok": False, "reason": "invalid_transition", "action_id": int(action_id)}

    current = get_action(action_id, staff)
    if current is None:
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}

    allowed_from = _TRANSITION_FROM[target]
    if str(current.get("status") or "") not in allowed_from:
        return {
            "ok": False,
            "reason": "illegal_state_transition",
            "action_id": int(action_id),
            "from_status": current.get("status"),
            "to_status": target,
        }

    conn = get_conn()
    from_placeholders = ",".join(["?"] * len(allowed_from))
    if target == "snoozed" and snooze_until:
        # snooze_until 写进 payload_json(表无专列)。Python read-modify-write 既有 payload,
        # 走与生成器同款 _dumps + ?::jsonb 路径(不引入新 jsonb_set/to_jsonb 方言面)。
        payload = current.get("payload_json")
        merged = dict(payload) if isinstance(payload, dict) else {}
        merged["snooze_until"] = str(snooze_until)
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET status = ?, payload_json = {_sql_json_param()}, updated_at = {_sql_now()}
            WHERE id = ? AND status IN ({from_placeholders})
            """,
            (target, _dumps(merged), int(action_id), *allowed_from),
        )
    elif target == "approved" and approval_reason:
        # cut2:批准理由落 approval_reason 死列(此前无写路径)。只动本行,不碰其它。
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET status = ?, approval_reason = ?, updated_at = {_sql_now()}
            WHERE id = ? AND status IN ({from_placeholders})
            """,
            (target, str(approval_reason)[:2000], int(action_id), *allowed_from),
        )
    else:
        cursor = conn.execute(
            f"""
            UPDATE {_TABLE}
            SET status = ?, updated_at = {_sql_now()}
            WHERE id = ? AND status IN ({from_placeholders})
            """,
            (target, int(action_id), *allowed_from),
        )
    conn.commit()
    if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
        # 并发竞态:行已被别处改走源态 → 诚实回 illegal。
        return {"ok": False, "reason": "illegal_state_transition", "action_id": int(action_id)}

    result: dict[str, Any] = {"ok": True, "status": target, "action_id": int(action_id)}
    if target == "snoozed" and snooze_until:
        result["snooze_until"] = str(snooze_until)
    return result


def approve_action(action_id: int, staff: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    """人审通过 → status=approved(execute 仍需后端 validators 双闸)。reason 落 approval_reason。"""
    res = _transition(action_id, "approved", staff, approval_reason=(str(reason).strip() or None))
    if isinstance(res, dict) and res.get("ok"):  # P1 事件总线:行动获批入流(best-effort)
        try:
            from app.domains.platform import event_ledger

            event_ledger.emit(
                "action_approved", entity_type="action", entity_id=int(action_id),
                actor_type="staff", actor_id=str((staff or {}).get("id") or ""), source="action_inbox",
                trace_id=event_ledger.new_trace_id("action", action_id),
            )
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
    return res


def dismiss_action(action_id: int, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """忽略 → status=dismissed(终态)。"""
    return _transition(action_id, "dismissed", staff)


def snooze_action(
    action_id: int, staff: dict[str, Any] | None = None, minutes: int = 1440
) -> dict[str, Any]:
    """延后 → status=snoozed;snooze_until = now + minutes(Python 算 ISO 串传参,不裸 %)。"""
    from datetime import datetime, timedelta, timezone

    mins = max(1, min(int(minutes or 1440), 60 * 24 * 30))  # 1 分钟 ~ 30 天
    snooze_until = (datetime.now(timezone.utc) + timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _transition(action_id, "snoozed", staff, snooze_until=snooze_until)


def mark_done_action(
    action_id: int, staff: dict[str, Any] | None = None, note: str | None = None
) -> dict[str, Any]:
    """人工已执行 → status=executed(仅 approved 可转)。

    场景:suggested_endpoint 为空的动作(如 gtm_bet)走 /execute 只会 skipped,
    行永远停在 approved —— 本函数给「人在系统外做完了」一个诚实终态。
    落一条 manual_execution 台账:ledger CHECK 限 mode IN ('dry_run','executed'),
    人工语义放 detail_json.kind='manual_execution' + endpoint='manual:mark-done'。
    红线:只动 vkpi_action_inbox 自己这一行 + 追加自身台账,绝不碰 viltrox_fit_score / rule_v0。
    """
    current = get_action(action_id, staff)
    if current is None:
        return {"ok": False, "reason": "not_found_or_out_of_scope", "action_id": int(action_id)}
    if str(current.get("status") or "") != "approved":
        return {
            "ok": False,
            "reason": "illegal_state_transition",
            "action_id": int(action_id),
            "from_status": current.get("status"),
            "to_status": "executed",
        }

    conn = get_conn()
    cursor = conn.execute(
        f"UPDATE {_TABLE} SET status = 'executed', updated_at = {_sql_now()} "
        f"WHERE id = ? AND status = 'approved'",
        (int(action_id),),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) <= 0:
        # 并发竞态:行已被别处改走 approved → 诚实回 illegal(不落台账)。
        conn.commit()
        return {"ok": False, "reason": "illegal_state_transition", "action_id": int(action_id)}

    ledger_id: int | None = None
    if table_exists(_LEDGER):
        try:
            row = conn.execute(
                f"""
                INSERT INTO {_LEDGER}
                  (action_id, category, dedupe_key, actor_staff_id, mode, outcome, endpoint, cost_cents, error, detail_json, created_at)
                VALUES (?,?,?,?,'executed','success','manual:mark-done',0,'',{_sql_json_param()},{_sql_now()})
                RETURNING id
                """,
                (
                    int(action_id),
                    str(current.get("category") or ""),
                    str(current.get("dedupe_key") or ""),
                    int(scope.actor_staff_id(staff)) or None,
                    _dumps({"kind": "manual_execution", "note": str(note or "")[:2000]}),
                ),
            ).fetchone()
            ledger_id = int(dict(row)["id"]) if row else None
        except Exception:
            logger.warning("action_inbox.mark_done_ledger_failed", extra={"action_id": action_id}, exc_info=True)
    conn.commit()

    result: dict[str, Any] = {"ok": True, "status": "executed", "action_id": int(action_id)}
    if ledger_id is not None:
        result["ledger_id"] = ledger_id
    return result


def set_status(action_id: int, status: str) -> None:
    """内部可信状态置位(executor 用,不做 scope)。只动本行 status + updated_at。

    executor 执行成功置 'executed'、失败置 'failed';CHECK 约束限定合法值。
    """
    try:
        conn = get_conn()
        conn.execute(
            f"UPDATE {_TABLE} SET status = ?, updated_at = {_sql_now()} WHERE id = ?",
            (str(status), int(action_id)),
        )
        conn.commit()
    except Exception:
        logger.warning("action_inbox.set_status_failed", extra={"action_id": action_id, "status": status}, exc_info=True)


def set_result_checklist(action_id: int, checklist: dict[str, Any]) -> None:
    """路线0:executor 执行后把标准化验收回执写回本 action 行(只动 result_checklist_json + updated_at)。

    回执 = {outcome, wrote_business_data, jobs_created, rows_written, cost_spent_cents, failed_reason, ...}。
    best-effort:写失败只 warning,不影响 execute_action 返回(回执也已落 ledger.detail_json)。
    """
    try:
        conn = get_conn()
        conn.execute(
            f"UPDATE {_TABLE} SET result_checklist_json = {_sql_json_param()}, "
            f"updated_at = {_sql_now()} WHERE id = ?",
            (_dumps(checklist or {}), int(action_id)),
        )
        conn.commit()
    except Exception:
        logger.warning("action_inbox.set_result_checklist_failed", extra={"action_id": action_id}, exc_info=True)
