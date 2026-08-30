"""P4 deterministic new-launch match dry-run.

P4-1 reads raw Memory rows directly. It does not call P3 candidate helpers,
does not call providers, and does not write recommendation tables. P4-2 may
attach budget-gated recommendation reasons after ranking without changing the
deterministic score.
"""
from __future__ import annotations

import json
import math
import secrets
from pathlib import Path
from typing import Any

from app.core.model_registry import current_task_model_binding, split_binding
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import memory
from app.platform import llm_production
from app.domains.costs.budget_guard import check_budget, get_budget_status
from app.domains.costs.budget_readonly import get_budget_status_readonly
from app.domains.recommendations.new_launch_match_format import format_preview_summary, render_markdown
from app.domains.recommendations import new_launch_match_rerank


BUDGET_SCOPE = "cron:p4_recommendations_daily"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
SCENARIO = "new_launch_match"
FORBIDDEN_WRITE_FLAGS = {"--commit", "--write-db", "--provider-call"}
logger = get_logger(__name__)

from app.domains.recommendations.new_launch_match_helpers import *  # noqa: F403

# 召回门槛延迟进入 KOL package，避免 facade 反向导入暴露 partially initialized 公共导出。
def _reach_display_state(row: dict[str, Any]) -> str:
    from app.domains.kol.discovery_filters import _reach_display_state as implementation

    return implementation(row)


def _reach_floor_reason(row: dict[str, Any]) -> str:
    from app.domains.kol.discovery_filters import _reach_floor_reason as implementation

    return implementation(row)

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


def _market_detail(signal: dict[str, Any]) -> str:
    payload = _fact_payload(signal)
    signal_type = payload.get("signal_type") or signal.get("fact_type")
    date_value = payload.get("signal_date") or payload.get("launch_date") or payload.get("publish_date") or ""
    product = payload.get("product_name") or payload.get("product") or payload.get("launch_name") or ""
    return f"{signal_type} {product} {date_value}".strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _last_by_uid(table: str, column: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {column}=?", (uid,)).fetchone()
    return _row_to_dict(row) if row else {}


def _persist_preview_run(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    now = _utcnow()
    run_uid = f"p4nlm-{secrets.token_hex(8)}"
    conn = get_conn()
    filters = {
        "scenario": SCENARIO,
        "source_mode": "new_launch_match_preview",
        "dry_run": True,
        "product_query": payload.get("product_query"),
        "target_family_uid": payload.get("target_family_uid"),
        "target_family_name": payload.get("target_family_name"),
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
                "new_launch_match_v1",
                "previewed",
                int(summary.get("total_candidates_evaluated") or 0),
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
        outcome_seeds: list[tuple[int, dict[str, Any], dict[str, Any]]] = []  # (rec_id, feature_snapshot, item) 供整批 commit 后落 outcome 底座
        rec_id_by_index: dict[int, int] = {}
        for item_index, item in enumerate(items):
            rec_uid = f"p4nlm-rec-{secrets.token_hex(8)}"
            reason = item.get("recommendation_reason") or {}
            feature_snapshot = {
                "scenario": SCENARIO,
                "product_query": payload.get("product_query"),
                "target_family_uid": payload.get("target_family_uid"),
                "target_family_name": payload.get("target_family_name"),
                "kol_entity_uid": item.get("kol_entity_uid"),
                "legacy_entity_uid": item.get("legacy_entity_uid"),
                "review_required": bool(item.get("review_required")),
                "links": item.get("links") or {},
            }
            explanation = {
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
                "recommendation_reason": reason,
                "source": "p4_new_launch_match",
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
                    item.get("kol_pool_id"),
                    None,
                    item.get("platform") or "",
                    item.get("handle") or "",
                    item.get("display_name") or item.get("handle") or "",
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
            rec_id_by_index[item_index] = rec_id
            outcome_seeds.append((rec_id, feature_snapshot, item))
            conn.execute(
                """
                INSERT INTO vkpi_recommendation_explanations
                    (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json,
                     model_version, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    rec_id,
                    "p4_new_launch_match",
                    reason.get("short_reason") or "Explainable new-launch match preview.",
                    _json(item.get("evidence_pro") or []),
                    _json(item.get("evidence_con") or []),
                    reason.get("model") or "rule_v1",
                    now,
                ),
            )
        conn.commit()
        # 整批 commit 后再落 outcome，避免 ensure_outcome 内部 commit 提前落半截 run。
        try:
            from app.domains.recommendations import outcomes as outcome_collector  # 懒 import:线上旧布局兼容

            for out_rec_id, out_snapshot, out_item in outcome_seeds:
                if int(out_rec_id or 0) <= 0:
                    continue
                try:
                    outcome_collector.ensure_outcome(
                        int(out_rec_id),
                        kol_pool_id=out_item.get("kol_pool_id"),
                        launch_id=None,
                        feature_snapshot=out_snapshot,
                        scoring_breakdown=out_item.get("score_breakdown") or {},
                        model_version="new_launch_match_v1",
                        display_position=out_item.get("rank"),
                        display_context={
                            "source": "new_launch_match_preview",
                            "run_id": run_id,
                            "run_uid": run_uid,
                            "rank": out_item.get("rank"),
                            "score": out_item.get("score"),
                            "product_query": payload.get("product_query"),
                            "target_family_name": payload.get("target_family_name"),
                        },
                    )
                except Exception:
                    logger.warning("new_launch_match outcome hook failed rec_id=%s", out_rec_id, exc_info=True)
        except Exception:
            logger.warning("new_launch_match outcome hook unavailable", exc_info=True)
        # W-L2 特征快照(推荐时刻特征 + arm + 影子量),整批 commit 之后落;失败只告警。
        try:
            snapshots = new_launch_match_rerank.persist_snapshots(
                items=items, rec_ids=rec_id_by_index, policy=payload.get("rerank_policy") or {}, run_id=run_id,
            )
        except Exception:
            logger.warning("new_launch_match feature snapshot hook failed", exc_info=True)
            snapshots = {"written": 0, "skipped": 0, "failed": len(items)}
        return {
            "enabled": True,
            "run_uid": run_uid,
            "run_id": run_id,
            "recommendation_count": len(recommendation_ids),
            "recommendation_ids": recommendation_ids,
            "feature_snapshots": snapshots,
        }
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.warning("new launch match rollback failed: %s", rollback_exc)
        raise


def _deterministic_reason(item: dict[str, Any]) -> dict[str, str]:
    pro = item.get("evidence_pro") or []
    con = item.get("evidence_con") or []
    strongest = pro[0].get("detail") if pro else "Has usable Memory evidence for this launch"
    concern = con[0].get("detail") if con else "No major concern in current Memory"
    handle = item.get("handle") or item.get("display_name") or item.get("kol_entity_uid")
    return {
        "short_reason": f"{handle} is a candidate because {strongest}.",
        "pitch_angle": "Use the historical product-family evidence as the first outreach angle.",
        "caution_note": concern,
    }


def _reason_prompt(payload: dict[str, Any], item: dict[str, Any]) -> str:
    pro = [
        {"type": row.get("type"), "detail": row.get("detail"), "source_ref": row.get("source_ref")}
        for row in (item.get("evidence_pro") or [])[:5]
    ]
    con = [
        {"type": row.get("type"), "detail": row.get("detail"), "severity": row.get("severity")}
        for row in (item.get("evidence_con") or [])[:5]
    ]
    compact = {
        "scenario": SCENARIO,
        "product": payload.get("product_query"),
        "target_family": payload.get("target_family_name"),
        "kol": {
            "platform": item.get("platform"),
            "handle": item.get("handle"),
            "display_name": item.get("display_name"),
            "country": item.get("country"),
            "score": item.get("score"),
            "review_required": item.get("review_required"),
        },
        "score_breakdown": item.get("score_breakdown"),
        "evidence_pro": pro,
        "evidence_con": con,
    }
    return (
        "Write a concise V-KPI recommendation reason as strict JSON with keys "
        "short_reason, pitch_angle, caution_note. Do not invent facts. "
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
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return None
    if not isinstance(parsed, dict):
        return None
    return {
        "short_reason": _text(parsed.get("short_reason")),
        "pitch_angle": _text(parsed.get("pitch_angle")),
        "caution_note": _text(parsed.get("caution_note")),
    }


def _reason_binding() -> tuple[str, str]:
    return split_binding(current_task_model_binding().get("kol_product_fit_reason") or "")


def _valid_reason_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("short_reason", "pitch_angle", "caution_note"):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 1600:
            return False
    return True


def _failure_code(value: Any) -> str:
    result = value if isinstance(value, dict) else {}
    failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    latest = errors[-1] if errors and isinstance(errors[-1], dict) else {}
    return str(
        failure.get("code")
        or result.get("failure_code")
        or result.get("reason")
        or latest.get("status")
        or result.get("status")
        or "llm_unavailable"
    )[:120]


def _preview_execution_policy(*, with_llm_reasons: bool, reason_limit: int, returned_count: int) -> dict[str, Any]:
    planned = max(0, min(int(reason_limit or 0), int(returned_count or 0))) if with_llm_reasons else 0
    provider_calls_allowed = planned > 0
    return {
        "mode": "ai_enriched_preview" if provider_calls_allowed else "dry_run",
        "provider_calls_allowed": provider_calls_allowed,
        "provider_calls_planned": planned,
        "provider_call_scope": "recommendation_reason_only" if provider_calls_allowed else "none",
        "deterministic_ranking": True,
        "business_actions_executed": False,
    }


def _attach_reason(
    payload: dict[str, Any],
    item: dict[str, Any],
    *,
    attempt_index: int = 1,
    total: int = 1,
) -> None:
    provider, model = _reason_binding()
    try:
        response = llm_production.generate_json(
            _reason_prompt(payload, item),
            provider=provider,
            model=model,
            purpose="p4_recommendation_reasons",
            max_output_tokens=220,
            cost_tag=REASON_BUDGET_SCOPE,
            triggered_by="new_launch_match",
            required_keys=("short_reason", "pitch_angle", "caution_note"),
            validator=_valid_reason_payload,
            metadata={
                "task_binding": "kol_product_fit_reason",
                "surface": "new_launch_match",
                "scenario": SCENARIO,
                "kol_entity_uid": item.get("kol_entity_uid"),
                "rank": item.get("rank"),
                "product_query": payload.get("product_query"),
                "phase": "recommendation",
                "subphase": "reason_generation",
                "attempt_index": max(1, int(attempt_index)),
                "total": max(1, int(total)),
                "target_label": item.get("handle") or item.get("display_name") or item.get("kol_entity_uid"),
            },
        )
    except Exception as exc:  # strict AI-off/readiness failure retains deterministic ranking and reason
        response = {
            "status": "failed",
            "reason": str(exc)[:120] or type(exc).__name__,
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
        }
    candidate = response.get("json") if isinstance(response, dict) else None
    parsed = (
        {
            "short_reason": _text(candidate.get("short_reason")),
            "pitch_angle": _text(candidate.get("pitch_angle")),
            "caution_note": _text(candidate.get("caution_note")),
        }
        if (
            str(response.get("status") or "") == "success"
            and str(response.get("provider") or "").strip().lower() == provider
            and str(response.get("model") or "").strip().startswith(model)
            and _valid_reason_payload(candidate)
        )
        else None
    )
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
        "fallback_reason": "" if mode == "llm" else _failure_code(response),
        **reason,
    }


def _append_evidence(
    rows: list[dict[str, Any]], evidence_type: str, polarity: str, severity: str,
    detail: str, score_component: str, row: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    rows.append(_evidence(
        evidence_type=evidence_type, polarity=polarity, severity=severity, detail=detail,
        score_component=score_component, row=row, payload=payload,
    ))

def _fact_evidence_payload(fact: dict[str, Any]) -> dict[str, Any]:
    return _fact_payload(fact) if fact else {}

def _candidate_evidence(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence_pro: list[dict[str, Any]] = []
    evidence_con: list[dict[str, Any]] = []
    product_link = state["product_match_link"]
    if product_link:
        product_type = state["product_match_type"]
        source_payload = _source_payload(product_link)
        product_name = _text(product_link.get("product_name"))
        _append_evidence(
            evidence_pro, product_type, "pro", "info",
            f"{product_type.replace('_', ' ')} via {product_name}", "product_match",
            product_link, source_payload)
    cooperation_count = state["cooperation_count"]
    if cooperation_count:
        row = state["links"][0]
        _append_evidence(
            evidence_pro, "cooperation_strength", "pro", "info",
            f"{cooperation_count} unique historical cooperation records", "cooperation_strength",
            row, _source_payload(row))
    for signal in state["market_evidence"][:2]:
        payload = _fact_payload(signal)
        _append_evidence(
            evidence_pro, _text(payload.get("signal_type") or signal.get("fact_type")),
            "pro", "info", _market_detail(signal), "market_signal", signal, payload)
    country = state["country"]
    country_fact = state["country_fact"]
    if state["region_score"] >= 6:
        _append_evidence(
            evidence_pro, "region_match", "pro", "info",
            f"{country or 'unknown'} matched as {state['region_reason']}", "region_match",
            country_fact, _fact_evidence_payload(country_fact))
    elif not country:
        _append_evidence(
            evidence_con, "missing_country", "con", "low",
            "KOL country is missing; neutral region score applied", "region_match",
            country_fact, _fact_evidence_payload(country_fact))
    contact_fact = state["contact_fact"]
    if state["contact_score"] > 0:
        _append_evidence(
            evidence_pro, "contact_available", "pro", "info",
            f"Contact availability: {state['contact_label']}", "contact_availability",
            contact_fact, _fact_evidence_payload(contact_fact))
    else:
        _append_evidence(
            evidence_con, "contact_missing", "con", "medium",
            "No usable contact status in Memory", "contact_availability",
            contact_fact, _fact_evidence_payload(contact_fact))
    evidence_count = state["evidence_count"]
    evidence_fact = state["evidence_fact"]
    if evidence_count < 5:
        _append_evidence(
            evidence_con, "low_evidence_count", "con", "low",
            f"Only {evidence_count} legacy evidence rows", "data_freshness",
            evidence_fact, _fact_evidence_payload(evidence_fact))
    else:
        _append_evidence(
            evidence_pro, "data_freshness", "pro", "info",
            f"{evidence_count} legacy evidence rows", "data_freshness",
            evidence_fact, _fact_evidence_payload(evidence_fact))
    _append_evidence(
        evidence_con, "no_recent_activity_signal", "con", "medium",
        "No KOL-linked activity fact found within 90 days", "recency_boost")
    if state["sync_status"] == "needs_human_review" or state["review_state"] == "needs_human_review":
        _append_evidence(
            evidence_con, "sync_needs_review", "con", "medium",
            f"sync_status={state['sync_status']}, review_state={state['review_state']}", "penalty")
    if state["decision"] == "escalate":
        _append_evidence(
            evidence_con, "resolution_escalate", "con", "low",
            "P2C resolution decision is escalate", "penalty")
    if state["risk_count"]:
        risk_fact = next(
            (fact for fact in state["facts"] if _text(fact.get("fact_type")) == "risk_flag"), {}
        )
        _append_evidence(
            evidence_con, "risk_flag", "con", "high",
            f"{state['risk_count']} risk flag(s) attached", "penalty",
            risk_fact, _fact_evidence_payload(risk_fact))
    return evidence_pro, evidence_con

def _candidate_state(kol: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    entity_id = int(kol["id"])
    identity = _entity_payload(kol, "identity_json")
    metadata = _entity_payload(kol, "metadata_json")
    source_ref = _text(identity.get("source_ref"))
    legacy_uid = _text(metadata.get("legacy_entity_uid"))
    pool = context["pool_map"].get(source_ref)
    legacy = context["legacy_map"].get(legacy_uid, {})
    facts = context["facts_by_entity"].get(entity_id, [])
    return {
        "kol": kol, "identity": identity, "legacy_uid": legacy_uid, "pool": pool,
        "facts": facts, "links": context["links_by_entity"].get(entity_id, []),
        "weak_label": _text(
            legacy.get("weak_label") or metadata.get("weak_label") or _latest_fact_value(facts, "weak_label")
        ),
        "decision": _text(legacy.get("resolution_decision") or ""),
        "sync_status": _text(
            (pool or {}).get("sync_status") or _latest_fact_value(facts, "sync_status") or kol.get("status")
        ),
        "review_state": _text(metadata.get("review_state") or _latest_fact_value(facts, "review_state")),
    }

def _candidate_exclusion(state: dict[str, Any]) -> str:
    if state["weak_label"] == "blocked_risk" or state["decision"] == "drop":
        return "hard"
    pool = state["pool"]
    reach_state = _reach_display_state(pool) if pool else "ok"
    if reach_state == "low_reach":
        logger.debug(
            "new_launch_match_reach_floor_filtered handle=%r kol_pool_id=%s reason=%s",
            (pool or {}).get("handle"), (pool or {}).get("id"),
            _reach_floor_reason(pool) or "low_reach_flag",
        )
        return "low_reach"
    if reach_state == "unknown":
        logger.debug(
            "new_launch_match_reach_unknown_hidden handle=%r kol_pool_id=%s",
            (pool or {}).get("handle"), (pool or {}).get("id"),
        )
        return "unknown_reach"
    return ""

def _candidate_country(state: dict[str, Any]) -> str:
    return _text(
        state["identity"].get("country") or (state["pool"] or {}).get("country")
        or _latest_fact_value(state["facts"], "country")
    )

def _candidate_display_fields(state: dict[str, Any]) -> tuple[str, str, str, int | None]:
    pool, identity, kol = state["pool"], state["identity"], state["kol"]
    platform = _text((pool or {}).get("platform") or identity.get("platform"))
    handle = _text((pool or {}).get("handle") or identity.get("handle"))
    display_name = _text((pool or {}).get("display_name") or kol.get("display_name") or handle)
    return platform, handle, display_name, _safe_int((pool or {}).get("id")) or None

def _candidate_preview(
    kol: dict[str, Any], context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    state = _candidate_state(kol, context)
    excluded = _candidate_exclusion(state)
    if excluded:
        return None, excluded
    pool, facts, links = state["pool"], state["facts"], state["links"]
    decision, sync_status = state["decision"], state["sync_status"]
    review_state, legacy_uid = state["review_state"], state["legacy_uid"]

    cooperation_count = len({link.get("source_ref") for link in links if _text(link.get("source_ref"))})
    evidence_count = _evidence_count(facts)
    risk_count = _risk_count(facts)
    country_fact = _first_fact(facts, "country")
    contact_fact = _first_fact(facts, "contact_status")
    evidence_fact = _first_fact(facts, "evidence_count")
    contact_status = _latest_fact_value(facts, "contact_status")
    country = _candidate_country(state)
    product_score, product_match_type, product_match_link = _product_match_score(
        target_family=context["target_family"], links=links,
        product_to_family=context["product_to_family"],
    )
    cooperation_score = _cooperation_score(cooperation_count)
    region_score, region_reason = _region_score(country, context["primary"], context["secondary"])
    contact_score, contact_label = _contact_score(contact_status, pool)
    recency_score = 0
    freshness_score = _freshness_score(evidence_count)
    base_score = (
        product_score + cooperation_score + context["market_score"] + region_score
        + contact_score + recency_score + freshness_score
    )
    penalty_factors = {
        "needs_human_review": 0.85
        if sync_status == "needs_human_review" or review_state == "needs_human_review" else 1.0,
        "risk_flag": 0.70 if risk_count > 0 else 1.0,
        "resolution_escalate": 0.90 if decision == "escalate" else 1.0,
    }
    penalty_factor = math.prod(penalty_factors.values())
    final_score = round(base_score * penalty_factor, 1)
    evidence_pro, evidence_con = _candidate_evidence({
        "product_match_link": product_match_link, "product_match_type": product_match_type,
        "cooperation_count": cooperation_count, "links": links,
        "market_evidence": context["market_evidence"], "country": country,
        "country_fact": country_fact, "region_score": region_score, "region_reason": region_reason,
        "contact_fact": contact_fact, "contact_score": contact_score, "contact_label": contact_label,
        "evidence_count": evidence_count, "evidence_fact": evidence_fact,
        "sync_status": sync_status, "review_state": review_state, "decision": decision,
        "risk_count": risk_count, "facts": facts,
    })
    if len(evidence_pro) + len(evidence_con) < 3:
        return None, "low_evidence"

    platform, handle, display_name, kol_pool_id = _candidate_display_fields(state)
    item = {
        "rank": 0,
        "percentile_rank": 0,
        "kol_entity_uid": kol["entity_uid"],
        "legacy_entity_uid": legacy_uid,
        "kol_pool_id": kol_pool_id,
        "platform": platform,
        "handle": handle,
        "display_name": display_name,
        "country": country,
        "score": final_score,
        "review_required": bool(sync_status == "needs_human_review" or decision == "escalate"),
        "hard_excluded": False,
        "score_breakdown": {
            "product_match": product_score,
            "cooperation_strength": cooperation_score,
            "market_signal": context["market_score"],
            "region_match": region_score,
            "contact_availability": contact_score,
            "recency_boost": recency_score,
            "data_freshness": freshness_score,
            "base": base_score,
            "penalty_factors": penalty_factors,
            "penalty_factor": round(penalty_factor, 4),
            "final": final_score,
        },
        "evidence_pro": evidence_pro,
        "evidence_con": evidence_con,
        "links": {"open_in_vkpi": _open_link(kol["entity_uid"])},
    }
    return item, ""

def _eligible_candidates(
    kol_rows: list[dict[str, Any]], context: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    eligible: list[dict[str, Any]] = []
    excluded = {"hard": 0, "low_evidence": 0, "low_reach": 0, "unknown_reach": 0}
    for kol in kol_rows:
        item, reason = _candidate_preview(kol, context)
        if item is None:
            excluded[reason] += 1
        else:
            eligible.append(item)
    return eligible, excluded

def _rank_candidates(
    eligible: list[dict[str, Any]], *, pool_map: dict[str, Any], staff: dict[str, Any] | None,
) -> dict[str, Any]:
    eligible.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["product_match"]),
            int(item["score_breakdown"]["cooperation_strength"]),
            int(item["score_breakdown"]["data_freshness"]),
        ),
        reverse=True,
    )
    rerank_policy = new_launch_match_rerank.apply_to_preview(eligible, pool_map=pool_map, staff=staff)
    for idx, item in enumerate(eligible):
        item["rank"] = idx + 1
        item["percentile_rank"] = _percentile(idx, len(eligible))
    return rerank_policy

def _preview_budget(with_llm_reasons: bool) -> tuple[Any, dict[str, Any]]:
    if with_llm_reasons:
        cost_ok = check_budget(BUDGET_SCOPE, 0.0)
        budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    else:
        budget_status = get_budget_status_readonly(BUDGET_SCOPE, estimated_cost=0.0)
        cost_ok = bool(budget_status.get("allowed"))
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")
    return cost_ok, budget_status

def build_new_launch_match_preview(
    *,
    product_query: str, limit: int = 100, primary_markets: str = "",
    secondary_markets: str = "", json_out: str = "", md_out: str = "",
    with_llm_reasons: bool = False, reason_limit: int = 20,
    persist_run: bool = False, staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic P4-1 dry-run preview from raw Memory rows."""

    safe_limit = _safe_limit(limit)
    readiness = memory.readiness()
    if readiness.get("status") != "ready_for_p4_dry_run":
        raise RuntimeError(f"memory readiness blocked P4 dry-run: {readiness.get('status')}")
    if bool(readiness.get("provider_calls_allowed")):
        raise RuntimeError("P4-1 requires provider_calls_allowed=false")
    cost_ok, budget_status = _preview_budget(with_llm_reasons)

    now = _utcnow()
    target_family = _select_target_family(product_query)
    product_to_family, _ = _product_family_maps()
    kol_rows = _kol_entities()
    pool_map = _pool_by_source_ref()
    context = {
        "target_family": target_family,
        "product_to_family": product_to_family,
        "pool_map": pool_map,
        "legacy_map": _legacy_entities_by_uid(),
        "facts_by_entity": _kol_facts(),
        "links_by_entity": _worked_links(),
    }
    signals = _target_market_signals(int(target_family["id"]))
    context["market_score"], context["market_evidence"] = _market_signal_score(signals, now=now)
    context["primary"] = _split_csv(primary_markets)
    context["secondary"] = _split_csv(secondary_markets)
    eligible, excluded = _eligible_candidates(kol_rows, context)
    rerank_policy = _rank_candidates(eligible, pool_map=pool_map, staff=staff)

    returned = eligible[:safe_limit]
    median = _median_score(returned)
    markdown_display = [item for item in returned if float(item["score"]) >= median]
    execution_policy = _preview_execution_policy(
        with_llm_reasons=with_llm_reasons,
        reason_limit=reason_limit,
        returned_count=len(returned),
    )
    reason_items = returned[: execution_policy["provider_calls_planned"]]
    summary = {
        "total_candidates_evaluated": len(kol_rows),
        "eligible_after_hard_filters": len(eligible),
        "excluded_blocked_or_dropped": excluded["hard"],
        "excluded_low_evidence": excluded["low_evidence"],
        "filtered_low_reach": excluded["low_reach"],
        "filtered_unknown_reach": excluded["unknown_reach"],
        "returned": len(returned),
        "markdown_display_count": len(markdown_display),
        "top_score": returned[0]["score"] if returned else 0,
        "median_score": median,
        "llm_reasons_requested": bool(with_llm_reasons),
        "reasons_attached": 0,
    }
    payload = {
        "scenario": SCENARIO,
        "mode": execution_policy["mode"],
        "generated_at": _iso(now),
        "product_query": product_query,
        "target_family_uid": target_family["entity_uid"],
        "target_family_name": target_family.get("display_name") or "",
        "provider_calls_allowed": execution_policy["provider_calls_allowed"],
        "execution_policy": execution_policy,
        "budget_guard": {
            "scope": BUDGET_SCOPE,
            "estimated_cost_usd": 0.0,
            "allowed": bool(cost_ok),
            "recorded_cost": False,
            "configured": bool(budget_status.get("configured")),
            "llm_reason_scope": REASON_BUDGET_SCOPE,
            "llm_reason_calls_planned": execution_policy["provider_calls_planned"],
            "llm_reason_atomic_reservation_per_call": True,
            "llm_reason_requires_configured_budget": True,
        },
        "summary": summary,
        "score_distribution": _distribution(returned),
        "rerank_policy": rerank_policy,
        "items": returned,
        "markdown_items": markdown_display,
    }
    if with_llm_reasons:
        for attempt_index, item in enumerate(reason_items, start=1):
            _attach_reason(payload, item, attempt_index=attempt_index, total=len(reason_items))
        summary["reasons_attached"] = len(reason_items)
    payload["persistence"] = _persist_preview_run(payload) if persist_run else {"enabled": False}
    new_launch_match_rerank.strip_internal_vectors(eligible)
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload
