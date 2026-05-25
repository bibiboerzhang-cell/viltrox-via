"""P4-11 deterministic project next-action dry-run."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import llm_gateway
from app.services.vkpi.budget_guard import check_budget, get_budget_status
from app.services.vkpi.project_next_action_format import format_preview_summary, render_markdown
from app.services.vkpi.new_launch_match import (
    BUDGET_SCOPE,
    FORBIDDEN_WRITE_FLAGS,
    _distribution,
    _json_default,
    _median_score,
    _percentile,
    _safe_int,
    _safe_limit,
    _text,
)


SCENARIO = "project_next_action"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_date(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(value: Any, *, now: datetime) -> int:
    parsed = _parse_date(value)
    if not parsed:
        return 999
    return max(0, (now - parsed).days)


def _json_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _markdown_write(path: str, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(payload), encoding="utf-8")


def _score_stage_staleness(days: int) -> int:
    if days > 14:
        return 30
    if days >= 7:
        return 20
    if days >= 3:
        return 10
    return 0


def _priority(score: float) -> str:
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _evidence(
    *,
    evidence_type: str,
    polarity: str,
    severity: str,
    detail: str,
    score_component: str,
    source_table: str = "vkpi_projects",
    source_id: Any = "",
) -> dict[str, Any]:
    return {
        "type": evidence_type,
        "polarity": polarity,
        "severity": severity,
        "detail": detail,
        "score_component": score_component,
        "source_table": source_table,
        "source_id": str(source_id or ""),
    }


def _project_counts(project_id: int) -> dict[str, int]:
    conn = get_conn()
    def count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE project_id=?", (int(project_id),)).fetchone()
        return _safe_int(row["count"] if row else 0)

    return {
        "messages": count("vkpi_messages"),
        "content_posts": count("vkpi_content_posts"),
        "terms": count("vkpi_project_terms"),
        "shipments": count("vkpi_shipments"),
        "costs": count("vkpi_cost_ledger"),
        "stage_events": count("vkpi_project_stage_events"),
    }


def _list_projects(*, limit: int, stage: str = "", staff_id: int = 0) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "WHERE p.stage_status <> 'deleted'"
    if stage:
        where += " AND p.stage=?"
        params.append(stage)
    if staff_id:
        where += " AND (p.assigned_staff_id=? OR p.created_by_staff_id=?)"
        params.extend([int(staff_id), int(staff_id)])
    rows = get_conn().execute(
        f"""
        SELECT p.*,
               k.channel_name AS kol_name,
               k.platform AS kol_platform,
               s.name AS staff_name,
               (
                   SELECT MAX(e.effective_at)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id AND e.to_stage = p.stage
               ) AS current_stage_started_at
        FROM vkpi_projects p
        LEFT JOIN kols k ON k.id = p.kol_id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        {where}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(200, int(limit or 50)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _missing_artifact(stage: str, counts: dict[str, int], project: dict[str, Any]) -> tuple[int, str, str]:
    sample_status = _text(project.get("sample_status"))
    if stage in {"discovery", "contacted"} and counts["messages"] == 0:
        return 20, "follow_up_message", "No message evidence is attached to this early-stage project"
    if stage in {"negotiating", "agreed"} and counts["terms"] == 0:
        return 20, "confirm_terms", "No project terms are recorded"
    if stage in {"sample_pending", "shipped"} and counts["shipments"] == 0 and sample_status not in {"not_required", "delivered"}:
        return 20, "ship_sample", "Sample stage has no shipment evidence"
    if stage in {"content_pending", "drafting"} and counts["content_posts"] == 0:
        return 20, "request_content", "No content post evidence is attached"
    if stage in {"published", "completed"} and counts["costs"] == 0:
        return 20, "record_cost", "Published/completed project has no cost evidence"
    return 0, "manual_review", "No mandatory artifact gap detected"


def _recent_signal(counts: dict[str, int], project: dict[str, Any]) -> tuple[int, str]:
    if counts["content_posts"]:
        return 15, "content evidence exists"
    if counts["shipments"]:
        return 12, "shipment evidence exists"
    if counts["messages"]:
        return 10, "message evidence exists"
    if _text(project.get("target_post_date")):
        return 8, "target post date exists"
    return 0, "no recent workflow signal"


def _business_value(project: dict[str, Any], counts: dict[str, int]) -> tuple[int, str]:
    value = 0
    reasons: list[str] = []
    if _text(project.get("product_name") or project.get("product_sku")):
        value += 5
        reasons.append("product attached")
    if counts["stage_events"] >= 2:
        value += 5
        reasons.append("multi-stage project")
    if _text(project.get("priority")) in {"high", "urgent"}:
        value += 5
        reasons.append("high priority")
    return min(15, value), ", ".join(reasons) or "no business value signal"


def _risk_or_blocker(project: dict[str, Any], counts: dict[str, int]) -> tuple[int, list[str]]:
    score = 0
    blockers: list[str] = []
    if not _safe_int(project.get("assigned_staff_id")):
        score += 4
        blockers.append("unassigned project")
    if _text(project.get("sample_status")) in {"delayed", "lost", "blocked"}:
        score += 4
        blockers.append(f"sample_status={project.get('sample_status')}")
    if counts["messages"] == 0:
        score += 2
        blockers.append("no message evidence")
    return min(10, score), blockers


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _last_by_uid(table: str, column: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {column}=?", (uid,)).fetchone()
    return dict(row) if row else {}


def _persist_preview_run(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    now = _iso(_utcnow())
    run_uid = f"p4pna-{secrets.token_hex(8)}"
    conn = get_conn()
    filters = {
        "scenario": SCENARIO,
        "source_mode": "project_next_action_preview",
        "dry_run": True,
        "filters": payload.get("filters") or {},
        "llm_reasons_requested": bool(summary.get("llm_reasons_requested")),
        "reason_count": int(summary.get("reasons_attached") or 0),
    }
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_recommendation_runs
                (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count,
                 filters_json, created_by_staff_id, created_at, completed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_uid,
                None,
                "project_next_action_v1",
                "previewed",
                int(summary.get("projects_evaluated") or 0),
                len(items),
                _json(filters),
                None,
                now,
                now,
            ),
        )
        run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
        run_id = int(run.get("id") or 0)
        recommendation_ids: list[int] = []
        for item in items:
            rec_uid = f"p4pna-rec-{secrets.token_hex(8)}"
            reason = item.get("recommendation_reason") or {}
            feature_snapshot = {
                "scenario": SCENARIO,
                "project_id": item.get("project_id"),
                "project_uid": item.get("project_uid"),
                "current_stage": item.get("current_stage"),
                "suggested_action": item.get("suggested_action"),
                "priority": item.get("priority"),
                "allowed_next_operations": item.get("allowed_next_operations") or [],
                "workflow_counts": item.get("workflow_counts") or {},
                "links": item.get("links") or {},
            }
            explanation = {
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
                "recommendation_reason": reason,
                "source": "p4_project_next_action",
            }
            conn.execute(
                """
                INSERT INTO vkpi_kol_recommendations
                    (recommendation_uid, run_id, launch_id, kol_pool_id, linked_main_kol_id, platform, handle,
                     display_name, score, rank, status, feature_snapshot_json, scoring_breakdown_json,
                     explanation_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    rec_uid,
                    run_id,
                    None,
                    None,
                    item.get("kol_id") or None,
                    "project",
                    f"project:{item.get('project_id')}",
                    item.get("project_name") or f"Project {item.get('project_id')}",
                    item.get("score"),
                    item.get("rank"),
                    "previewed",
                    _json(feature_snapshot),
                    _json(item.get("score_breakdown") or {}),
                    _json(explanation),
                    now,
                    now,
                ),
            )
            rec = _last_by_uid("vkpi_kol_recommendations", "recommendation_uid", rec_uid)
            rec_id = int(rec.get("id") or 0)
            recommendation_ids.append(rec_id)
            conn.execute(
                """
                INSERT INTO vkpi_recommendation_explanations
                    (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json,
                     model_version, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    rec_id,
                    "p4_project_next_action",
                    reason.get("short_reason") or item.get("reason") or "Explainable project next-action preview.",
                    _json(item.get("evidence_pro") or []),
                    _json(item.get("evidence_con") or []),
                    reason.get("model") or "rule_v1",
                    now,
                ),
            )
        conn.commit()
        return {
            "enabled": True,
            "run_uid": run_uid,
            "run_id": run_id,
            "recommendation_count": len(recommendation_ids),
            "recommendation_ids": recommendation_ids,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.warning("project next action rollback failed: %s", rollback_exc)
        raise


def _deterministic_reason(item: dict[str, Any]) -> dict[str, str]:
    pro = item.get("evidence_pro") or []
    con = item.get("evidence_con") or []
    strongest = pro[0].get("detail") if pro else item.get("reason") or "Project has reviewable workflow evidence"
    concern = con[0].get("detail") if con else "No major blocker in current workflow evidence"
    return {
        "short_reason": f"Next action is {item.get('suggested_action')} because {strongest}.",
        "execution_note": "Open the project and confirm the evidence before taking action.",
        "caution_note": concern,
    }


def _reason_prompt(item: dict[str, Any]) -> str:
    compact = {
        "scenario": SCENARIO,
        "project": {
            "project_id": item.get("project_id"),
            "project_name": item.get("project_name"),
            "current_stage": item.get("current_stage"),
            "assigned_staff_id": item.get("assigned_staff_id"),
            "suggested_action": item.get("suggested_action"),
            "priority": item.get("priority"),
            "score": item.get("score"),
        },
        "score_breakdown": item.get("score_breakdown"),
        "workflow_counts": item.get("workflow_counts"),
        "evidence_pro": [
            {"type": row.get("type"), "detail": row.get("detail"), "source_id": row.get("source_id")}
            for row in (item.get("evidence_pro") or [])[:5]
        ],
        "evidence_con": [
            {"type": row.get("type"), "detail": row.get("detail"), "severity": row.get("severity")}
            for row in (item.get("evidence_con") or [])[:5]
        ],
    }
    return (
        "Write a concise V-KPI project next-action reason as strict JSON with keys "
        "short_reason, execution_note, caution_note. Do not invent facts or imply an action was executed. "
        "Use only the evidence below. No markdown.\n\n"
        + json.dumps(compact, ensure_ascii=False, default=_json_default)
    )


def _parse_reason_text(text: str) -> dict[str, str] | None:
    raw = _text(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "short_reason": _text(parsed.get("short_reason")),
        "execution_note": _text(parsed.get("execution_note")),
        "caution_note": _text(parsed.get("caution_note")),
    }


def _attach_reason(item: dict[str, Any]) -> None:
    response = llm_gateway.invoke(
        _reason_prompt(item),
        purpose="p4_recommendation_reasons",
        max_output_tokens=220,
        cost_tag=REASON_BUDGET_SCOPE,
        metadata={
            "scenario": SCENARIO,
            "project_id": item.get("project_id"),
            "suggested_action": item.get("suggested_action"),
            "rank": item.get("rank"),
        },
    )
    parsed = _parse_reason_text(str(response.get("text") or "")) if response.get("status") == "success" else None
    if parsed and all(parsed.values()):
        reason = parsed
        mode = "llm"
    else:
        reason = _deterministic_reason(item)
        mode = "deterministic_fallback"
    item["recommendation_reason"] = {
        "mode": mode,
        "provider": response.get("provider") or "rule_v0",
        "model": response.get("model") or "rule_v0",
        "status": response.get("status") or "",
        "fallback_reason": response.get("reason") or "",
        **reason,
    }


def build_project_next_action_preview(
    *,
    project_id: int = 0,
    stage: str = "",
    staff_id: int = 0,
    priority: str = "",
    include_unassigned: bool = False,
    include_low_evidence: bool = False,
    limit: int = 50,
    json_out: str = "",
    md_out: str = "",
    with_llm_reasons: bool = False,
    reason_limit: int = 10,
    persist_run: bool = False,
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    cost_ok = check_budget(BUDGET_SCOPE, 0.0)
    budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")

    now = _utcnow()
    raw_projects = _list_projects(limit=200, stage=stage, staff_id=staff_id)
    items: list[dict[str, Any]] = []
    low_evidence = 0
    hard_excluded = 0
    for project in raw_projects:
        if project_id and _safe_int(project.get("id")) != int(project_id):
            continue
        if not include_unassigned and not _safe_int(project.get("assigned_staff_id")):
            hard_excluded += 1
            continue
        stage_value = _text(project.get("stage") or "discovery")
        counts = _project_counts(_safe_int(project.get("id")))
        stage_started = project.get("current_stage_started_at") or project.get("last_activity_at") or project.get("updated_at")
        stale_days = _age_days(stage_started, now=now)
        staleness_score = _score_stage_staleness(stale_days)
        artifact_score, action, artifact_reason = _missing_artifact(stage_value, counts, project)
        recent_score, recent_reason = _recent_signal(counts, project)
        value_score, value_reason = _business_value(project, counts)
        blocker_score, blockers = _risk_or_blocker(project, counts)
        staff_score = 10 if _safe_int(project.get("assigned_staff_id")) else 0
        base_score = staleness_score + artifact_score + recent_score + value_score + blocker_score + staff_score
        penalty_factor = 0.90 if not _safe_int(project.get("assigned_staff_id")) else 1.0
        final_score = round(base_score * penalty_factor, 1)
        item_priority = _priority(final_score)
        if priority and item_priority != _text(priority):
            continue

        evidence_pro = [
            _evidence(
                evidence_type="stage_staleness",
                polarity="pro",
                severity="info",
                detail=f"Current stage has been unchanged for {stale_days} day(s)",
                score_component="stage_staleness",
                source_id=project.get("id"),
            ),
            _evidence(
                evidence_type="suggested_action",
                polarity="pro",
                severity="info",
                detail=artifact_reason,
                score_component="missing_required_artifact",
                source_id=project.get("id"),
            ),
            _evidence(
                evidence_type="business_value",
                polarity="pro",
                severity="info",
                detail=value_reason,
                score_component="business_value",
                source_id=project.get("id"),
            ),
        ]
        evidence_con: list[dict[str, Any]] = []
        if recent_score:
            evidence_pro.append(
                _evidence(
                    evidence_type="recent_signal",
                    polarity="pro",
                    severity="info",
                    detail=recent_reason,
                    score_component="recent_signal",
                    source_id=project.get("id"),
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="no_recent_signal",
                    polarity="con",
                    severity="medium",
                    detail="No message, shipment, content, or target-post signal is present",
                    score_component="recent_signal",
                    source_id=project.get("id"),
                )
            )
        for blocker in blockers:
            evidence_con.append(
                _evidence(
                    evidence_type="workflow_blocker",
                    polarity="con",
                    severity="medium",
                    detail=blocker,
                    score_component="risk_or_blocker",
                    source_id=project.get("id"),
                )
            )
        if len(evidence_pro) + len(evidence_con) < 3 and not include_low_evidence:
            low_evidence += 1
            continue
        items.append(
            {
                "rank": 0,
                "percentile_rank": 0,
                "score": final_score,
                "priority": item_priority,
                "project_id": _safe_int(project.get("id")),
                "project_uid": project.get("project_uid"),
                "project_name": project.get("project_name"),
                "kol_id": _safe_int(project.get("kol_id")),
                "kol_name": project.get("kol_name"),
                "assigned_staff_id": _safe_int(project.get("assigned_staff_id")),
                "assigned_staff_name": project.get("staff_name"),
                "current_stage": stage_value,
                "suggested_action": action,
                "reason": artifact_reason,
                "score_breakdown": {
                    "stage_staleness": staleness_score,
                    "missing_required_artifact": artifact_score,
                    "recent_signal": recent_score,
                    "business_value": value_score,
                    "risk_or_blocker": blocker_score,
                    "staff_scope_fit": staff_score,
                    "base": base_score,
                    "penalty_factor": penalty_factor,
                    "final": final_score,
                },
                "workflow_counts": counts,
                "evidence_pro": evidence_pro,
                "evidence_con": evidence_con,
                "allowed_next_operations": ["open_project", "dismiss_suggestion"],
                "links": {"open_in_vkpi": f"/projects/{project.get('id')}"},
            }
        )

    items.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["stage_staleness"]),
            int(item["score_breakdown"]["missing_required_artifact"]),
        ),
        reverse=True,
    )
    for idx, item in enumerate(items):
        item["rank"] = idx + 1
        item["percentile_rank"] = _percentile(idx, len(items))
    returned = items[:safe_limit]
    median = _median_score(returned)
    markdown_display = [item for item in returned if float(item["score"]) >= median]
    summary = {
        "projects_evaluated": len(raw_projects),
        "eligible_after_hard_filters": len(items),
        "excluded_unassigned": hard_excluded,
        "excluded_low_evidence": low_evidence,
        "returned": len(returned),
        "markdown_display_count": len(markdown_display),
        "top_score": returned[0]["score"] if returned else 0,
        "median_score": median,
        "llm_reasons_requested": bool(with_llm_reasons),
        "reasons_attached": 0,
    }
    payload = {
        "scenario": SCENARIO,
        "mode": "dry_run",
        "generated_at": _iso(now),
        "provider_calls_allowed": False,
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "estimated_cost_usd": 0.0,
            "allowed": bool(cost_ok),
            "recorded_cost": False,
            "configured": bool(budget_status.get("configured")),
        },
        "filters": {
            "project_id": int(project_id or 0),
            "stage": stage,
            "staff_id": int(staff_id or 0),
            "priority": priority,
            "include_unassigned": bool(include_unassigned),
        },
        "summary": summary,
        "score_distribution": _distribution(returned),
        "items": returned,
        "markdown_items": markdown_display,
    }
    if with_llm_reasons:
        reasons_attached = 0
        for item in returned[: max(0, min(int(reason_limit or 0), len(returned)))]:
            _attach_reason(item)
            reasons_attached += 1
        summary["reasons_attached"] = reasons_attached
    if persist_run:
        payload["persistence"] = _persist_preview_run(payload)
    else:
        payload["persistence"] = {"enabled": False}
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload

