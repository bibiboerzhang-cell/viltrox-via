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
from app.domains.recommendations.new_launch_match_format import format_preview_summary, render_markdown


BUDGET_SCOPE = "cron:p4_recommendations_daily"
REASON_BUDGET_SCOPE = "cron:p4_recommendation_reasons"
SCENARIO = "new_launch_match"
FORBIDDEN_WRITE_FLAGS = {"--commit", "--write-db", "--provider-call"}
logger = get_logger(__name__)

from app.domains.recommendations.new_launch_match_helpers import *  # noqa: F403

# 召回触达门槛(用户裁决 2026-07-11 + 2026-07-12 第二道闸):与 KOL 发现/召回侧同一实现。
# 这里必须延迟进入 app.domains.kol:Python 在加载 discovery_filters 之前会先执行 KOL
# package facade，而 facade 的 profile/product-fit 路径会反向导入本模块。模块顶层导入会让
# clean-process 的 recommendation 公共导出看到 partially initialized new_launch_match。
# 运行评分时本模块已完整初始化，延迟调用仍复用同一个 discovery_filters 真源。
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
        for item in items:
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
        # 学习闭环·结果段接线:每条持久化的推荐幂等落一行 outcome 底座(ensure_outcome 有行
        # 即跳过 → 同日重复刷新不刷屏落行)。此前这条每日 04:00 recommendation_refresh 真跑的
        # 持久化路径从不落 outcomes,是 5/22 起零新行的根因。必须放在整批 commit 之后:
        # ensure_outcome 内部会 commit,若在上面循环里调用会把半截 run 提前落库,破坏整批回滚语义。
        # 懒 import + 双层 try/except:失败只告警,绝不阻断推荐持久化主流程。零触 viltrox_fit_score。
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


def build_new_launch_match_preview(
    *,
    product_query: str,
    limit: int = 100,
    primary_markets: str = "",
    secondary_markets: str = "",
    json_out: str = "",
    md_out: str = "",
    with_llm_reasons: bool = False,
    reason_limit: int = 20,
    persist_run: bool = False,
) -> dict[str, Any]:
    """Build a deterministic P4-1 dry-run preview from raw Memory rows."""

    safe_limit = _safe_limit(limit)
    readiness = memory.readiness()
    if readiness.get("status") != "ready_for_p4_dry_run":
        raise RuntimeError(f"memory readiness blocked P4 dry-run: {readiness.get('status')}")
    if bool(readiness.get("provider_calls_allowed")):
        raise RuntimeError("P4-1 requires provider_calls_allowed=false")

    cost_ok = check_budget(BUDGET_SCOPE, 0.0)
    budget_status = get_budget_status(BUDGET_SCOPE, estimated_cost=0.0)
    if not cost_ok:
        raise RuntimeError("budget_guard_blocked")

    now = _utcnow()
    target_family = _select_target_family(product_query)
    product_to_family, _ = _product_family_maps()
    kol_rows = _kol_entities()
    pool_map = _pool_by_source_ref()
    legacy_map = _legacy_entities_by_uid()
    facts_by_entity = _kol_facts()
    links_by_entity = _worked_links()
    signals = _target_market_signals(int(target_family["id"]))
    market_score, market_evidence = _market_signal_score(signals, now=now)
    primary = _split_csv(primary_markets)
    secondary = _split_csv(secondary_markets)
    eligible: list[dict[str, Any]] = []
    hard_excluded = 0
    low_evidence = 0
    filtered_low_reach = 0
    filtered_unknown_reach = 0

    for kol in kol_rows:
        entity_id = int(kol["id"])
        identity = _entity_payload(kol, "identity_json")
        metadata = _entity_payload(kol, "metadata_json")
        source_ref = _text(identity.get("source_ref"))
        legacy_uid = _text(metadata.get("legacy_entity_uid"))
        pool = pool_map.get(source_ref)
        legacy = legacy_map.get(legacy_uid, {})
        facts = facts_by_entity.get(entity_id, [])
        links = links_by_entity.get(entity_id, [])
        weak_label = _text(legacy.get("weak_label") or metadata.get("weak_label") or _latest_fact_value(facts, "weak_label"))
        decision = _text(legacy.get("resolution_decision") or "")
        sync_status = _text((pool or {}).get("sync_status") or _latest_fact_value(facts, "sync_status") or kol.get("status"))
        review_state = _text(metadata.get("review_state") or _latest_fact_value(facts, "review_state"))
        if weak_label == "blocked_risk" or decision == "drop":
            hard_excluded += 1
            continue
        # 召回触达门槛(用户裁决 2026-07-11 + 2026-07-12 第二道闸):pool 行三态走
        # _reach_display_state 单一真源——low_reach(followers 明确 < 门槛/互动实测全零/补全后
        # low_reach 标)或 unknown(followers 未知,「分析后再 po」)→ 不进推荐刷新产出
        # (vkpi_kol_recommendations 不再落这类行;pool 数据保留不删)。与 blocked_risk/drop
        # 同层的候选硬排除;pool 缺行(legacy 无池身)放行(不误杀)。零触评分公式/viltrox_fit_score。
        _reach_state = _reach_display_state(pool) if pool else "ok"
        if _reach_state == "low_reach":
            filtered_low_reach += 1
            logger.debug(
                "new_launch_match_reach_floor_filtered handle=%r kol_pool_id=%s reason=%s",
                (pool or {}).get("handle"), (pool or {}).get("id"),
                _reach_floor_reason(pool) or "low_reach_flag",
            )
            continue
        if _reach_state == "unknown":
            filtered_unknown_reach += 1
            logger.debug(
                "new_launch_match_reach_unknown_hidden handle=%r kol_pool_id=%s",
                (pool or {}).get("handle"), (pool or {}).get("id"),
            )
            continue

        source_refs = {link.get("source_ref") for link in links if _text(link.get("source_ref"))}
        cooperation_count = len(source_refs)
        evidence_count = _evidence_count(facts)
        risk_count = _risk_count(facts)
        country_fact = _first_fact(facts, "country")
        contact_fact = _first_fact(facts, "contact_status")
        evidence_fact = _first_fact(facts, "evidence_count")
        contact_status = _latest_fact_value(facts, "contact_status")
        country = _text(identity.get("country") or (pool or {}).get("country") or _latest_fact_value(facts, "country"))

        product_score, product_match_type, product_match_link = _product_match_score(
            target_family=target_family,
            links=links,
            product_to_family=product_to_family,
        )
        cooperation_score = _cooperation_score(cooperation_count)
        region_score, region_reason = _region_score(country, primary, secondary)
        contact_score, contact_label = _contact_score(contact_status, pool)
        recency_score = 0
        freshness_score = _freshness_score(evidence_count)
        base_score = (
            product_score
            + cooperation_score
            + market_score
            + region_score
            + contact_score
            + recency_score
            + freshness_score
        )
        penalty_factors = {
            "needs_human_review": 0.85 if sync_status == "needs_human_review" or review_state == "needs_human_review" else 1.0,
            "risk_flag": 0.70 if risk_count > 0 else 1.0,
            "resolution_escalate": 0.90 if decision == "escalate" else 1.0,
        }
        penalty_factor = math.prod(penalty_factors.values())
        final_score = round(base_score * penalty_factor, 1)

        evidence_pro: list[dict[str, Any]] = []
        evidence_con: list[dict[str, Any]] = []
        if product_match_link:
            source_payload = _source_payload(product_match_link)
            product_name = _text(product_match_link.get("product_name"))
            evidence_pro.append(
                _evidence(
                    evidence_type=product_match_type,
                    polarity="pro",
                    severity="info",
                    detail=f"{product_match_type.replace('_', ' ')} via {product_name}",
                    score_component="product_match",
                    row=product_match_link,
                    payload=source_payload,
                )
            )
        if cooperation_count:
            row = links[0]
            source_payload = _source_payload(row)
            evidence_pro.append(
                _evidence(
                    evidence_type="cooperation_strength",
                    polarity="pro",
                    severity="info",
                    detail=f"{cooperation_count} unique historical cooperation records",
                    score_component="cooperation_strength",
                    row=row,
                    payload=source_payload,
                )
            )
        for signal in market_evidence[:2]:
            payload = _fact_payload(signal)
            evidence_pro.append(
                _evidence(
                    evidence_type=_text(payload.get("signal_type") or signal.get("fact_type")),
                    polarity="pro",
                    severity="info",
                    detail=_market_detail(signal),
                    score_component="market_signal",
                    row=signal,
                    payload=payload,
                )
            )
        if region_score >= 6:
            evidence_pro.append(
                _evidence(
                    evidence_type="region_match",
                    polarity="pro",
                    severity="info",
                    detail=f"{country or 'unknown'} matched as {region_reason}",
                    score_component="region_match",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        elif not country:
            evidence_con.append(
                _evidence(
                    evidence_type="missing_country",
                    polarity="con",
                    severity="low",
                    detail="KOL country is missing; neutral region score applied",
                    score_component="region_match",
                    row=country_fact,
                    payload=_fact_payload(country_fact) if country_fact else {},
                )
            )
        if contact_score > 0:
            evidence_pro.append(
                _evidence(
                    evidence_type="contact_available",
                    polarity="pro",
                    severity="info",
                    detail=f"Contact availability: {contact_label}",
                    score_component="contact_availability",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        else:
            evidence_con.append(
                _evidence(
                    evidence_type="contact_missing",
                    polarity="con",
                    severity="medium",
                    detail="No usable contact status in Memory",
                    score_component="contact_availability",
                    row=contact_fact,
                    payload=_fact_payload(contact_fact) if contact_fact else {},
                )
            )
        if evidence_count < 5:
            evidence_con.append(
                _evidence(
                    evidence_type="low_evidence_count",
                    polarity="con",
                    severity="low",
                    detail=f"Only {evidence_count} legacy evidence rows",
                    score_component="data_freshness",
                    row=evidence_fact,
                    payload=_fact_payload(evidence_fact) if evidence_fact else {},
                )
            )
        else:
            evidence_pro.append(
                _evidence(
                    evidence_type="data_freshness",
                    polarity="pro",
                    severity="info",
                    detail=f"{evidence_count} legacy evidence rows",
                    score_component="data_freshness",
                    row=evidence_fact,
                    payload=_fact_payload(evidence_fact) if evidence_fact else {},
                )
            )
        evidence_con.append(
            _evidence(
                evidence_type="no_recent_activity_signal",
                polarity="con",
                severity="medium",
                detail="No KOL-linked activity fact found within 90 days",
                score_component="recency_boost",
            )
        )
        if sync_status == "needs_human_review" or review_state == "needs_human_review":
            evidence_con.append(
                _evidence(
                    evidence_type="sync_needs_review",
                    polarity="con",
                    severity="medium",
                    detail=f"sync_status={sync_status}, review_state={review_state}",
                    score_component="penalty",
                )
            )
        if decision == "escalate":
            evidence_con.append(
                _evidence(
                    evidence_type="resolution_escalate",
                    polarity="con",
                    severity="low",
                    detail="P2C resolution decision is escalate",
                    score_component="penalty",
                )
            )
        if risk_count:
            risk_fact = next((fact for fact in facts if _text(fact.get("fact_type")) == "risk_flag"), {})
            evidence_con.append(
                _evidence(
                    evidence_type="risk_flag",
                    polarity="con",
                    severity="high",
                    detail=f"{risk_count} risk flag(s) attached",
                    score_component="penalty",
                    row=risk_fact,
                    payload=_fact_payload(risk_fact) if risk_fact else {},
                )
            )

        if len(evidence_pro) + len(evidence_con) < 3:
            low_evidence += 1
            continue

        platform = _text((pool or {}).get("platform") or identity.get("platform"))
        handle = _text((pool or {}).get("handle") or identity.get("handle"))
        display_name = _text((pool or {}).get("display_name") or kol.get("display_name") or handle)
        kol_pool_id = _safe_int((pool or {}).get("id")) or None
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
                "market_signal": market_score,
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
        eligible.append(item)

    eligible.sort(
        key=lambda item: (
            float(item["score"]),
            int(item["score_breakdown"]["product_match"]),
            int(item["score_breakdown"]["cooperation_strength"]),
            int(item["score_breakdown"]["data_freshness"]),
        ),
        reverse=True,
    )
    for idx, item in enumerate(eligible):
        item["rank"] = idx + 1
        item["percentile_rank"] = _percentile(idx, len(eligible))

    returned = eligible[:safe_limit]
    median = _median_score(returned)
    markdown_display = [item for item in returned if float(item["score"]) >= median]
    execution_policy = _preview_execution_policy(
        with_llm_reasons=with_llm_reasons,
        reason_limit=reason_limit,
        returned_count=len(returned),
    )
    reason_items = returned[: execution_policy["provider_calls_planned"]]
    reasons_attached = 0
    summary = {
        "total_candidates_evaluated": len(kol_rows),
        "eligible_after_hard_filters": len(eligible),
        "excluded_blocked_or_dropped": hard_excluded,
        "excluded_low_evidence": low_evidence,
        # 召回触达门槛命中数(诚实可见:被挡=不落 vkpi_kol_recommendations,非降分)。
        "filtered_low_reach": filtered_low_reach,
        # followers 未知不落推荐(「分析后再 po」,2026-07-12 裁决);补全回填达标后自动回归。
        "filtered_unknown_reach": filtered_unknown_reach,
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
        "items": returned,
        "markdown_items": markdown_display,
    }
    if with_llm_reasons:
        for attempt_index, item in enumerate(reason_items, start=1):
            _attach_reason(payload, item, attempt_index=attempt_index, total=len(reason_items))
            reasons_attached += 1
        summary["reasons_attached"] = reasons_attached
    if persist_run:
        payload["persistence"] = _persist_preview_run(payload)
    else:
        payload["persistence"] = {"enabled": False}
    _json_write(json_out, {key: value for key, value in payload.items() if key != "markdown_items"})
    _markdown_write(md_out, payload)
    return payload
