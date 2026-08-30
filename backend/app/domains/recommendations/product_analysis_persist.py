"""run_recommendations 的落库层(2026-08-30 从 product_analysis 提出,行为不变)。

逐条 INSERT / commit 时机、字段顺序与解释文案与旧实现逐字一致;协作符号
(_json/_last_by_uid/resolve_staff_id/rerank_shadow/outcome_collector/_competitor_reason/
_feedback_reason)一律经门面 product_analysis 在调用时解析,tests 对门面的
monkeypatch 原样生效。红线:绝不触 viltrox_fit_score / rule_v0 评分内核,只搬编排。
"""
from __future__ import annotations

import secrets
from typing import Any


def _pa() -> Any:
    """调用时解析门面模块:门面上的 monkeypatch / 运行时替换一律生效。"""
    from app.domains.recommendations import product_analysis

    return product_analysis


def _build_run_filters(
    payload: dict[str, Any], include_avoid_competitors: bool, effective_platforms: list[str]
) -> dict[str, Any]:
    run_filters = dict(payload)
    run_filters["competitor_filter"] = "include_avoid" if include_avoid_competitors else "exclude_avoid"
    run_filters["feedback_policy"] = "score_adjust_v1"
    if effective_platforms:
        run_filters["effective_platforms"] = effective_platforms
        run_filters["platform_filter_source"] = "launch.target_platforms"
    return run_filters


def _insert_run_row(
    conn: Any,
    run_uid: str,
    launch_id: int,
    strategy_version: str,
    candidate_count: int,
    run_filters: dict[str, Any],
    staff: dict[str, Any] | None,
    now: str,
) -> None:
    pa = _pa()
    conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs
            (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count, filters_json,
             created_by_staff_id, created_at, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (run_uid, launch_id or None, strategy_version, "completed", candidate_count, 0, pa._json(run_filters), pa.resolve_staff_id(staff) or None, now, now),
    )
    conn.commit()


def _candidate_breakdown(
    cand: dict[str, Any],
    result: Any,
    competitor: dict[str, Any],
    feedback: dict[str, Any],
    rerank_arm: Any,
    rerank_policy: dict[str, Any],
) -> dict[str, Any]:
    breakdown = dict(result.breakdown or {})
    breakdown["rerank_shadow"] = _pa().rerank_shadow.breakdown_entry(
        cand, rerank_arm, rerank_policy["applied"], rerank_policy["model_version"]
    )
    breakdown["competitor"] = {
        "brand": competitor.get("brand"),
        "risk_tier": competitor.get("risk_tier"),
        "risk_score": competitor.get("risk_score"),
        "score_adjustment": competitor.get("score_adjustment"),
        "source": competitor.get("source"),
    }
    breakdown["operator_feedback"] = {
        "counts": feedback.get("counts") or {},
        "sentiment": feedback.get("sentiment"),
        "score_adjustment": feedback.get("score_adjustment"),
        "latest": feedback.get("latest") or {},
        "source": feedback.get("source"),
    }
    return breakdown


def _reason_notes(result: Any, competitor: dict[str, Any], feedback: dict[str, Any]) -> tuple[list[str], list[str]]:
    pa = _pa()
    strengths = list(result.strengths)
    concerns = list(result.concerns)
    competitor_note = pa._competitor_reason(competitor)
    if competitor.get("risk_tier") in {"avoid", "caution"}:
        concerns.append(competitor_note)
    else:
        strengths.append(competitor_note)
    feedback_note = pa._feedback_reason(feedback)
    if float(feedback.get("score_adjustment") or 0) < 0:
        concerns.append(feedback_note)
    elif float(feedback.get("score_adjustment") or 0) > 0:
        strengths.append(feedback_note)
    return strengths, concerns


def _insert_recommendation_row(
    conn: Any,
    rec_uid: str,
    run: dict[str, Any],
    launch_id: int,
    item: dict[str, Any],
    score: float,
    idx: int,
    features: dict[str, Any],
    breakdown: dict[str, Any],
    strengths: list[str],
    concerns: list[str],
    result: Any,
    now: str,
) -> dict[str, Any]:
    pa = _pa()
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
            int(run.get("id") or 0),
            launch_id or None,
            item.get("id"),
            item.get("linked_main_kol_id"),
            item.get("platform") or "",
            item.get("handle") or "",
            item.get("display_name") or item.get("handle") or "",
            score,
            idx,
            "recommended",
            pa._json(features),
            pa._json(breakdown),
            pa._json({"strengths": strengths, "concerns": concerns, "version": result.version, "competitor": breakdown["competitor"], "operator_feedback": breakdown["operator_feedback"]}),
            now,
            now,
        ),
    )
    conn.commit()
    return pa._last_by_uid("vkpi_kol_recommendations", "recommendation_uid", rec_uid)


def _insert_explanation_and_outcome(
    conn: Any,
    rec: dict[str, Any],
    item: dict[str, Any],
    launch_id: int,
    idx: int,
    score: float,
    run: dict[str, Any],
    features: dict[str, Any],
    breakdown: dict[str, Any],
    strengths: list[str],
    concerns: list[str],
    result: Any,
    now: str,
) -> None:
    pa = _pa()
    conn.execute(
        """
        INSERT INTO vkpi_recommendation_explanations
            (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json, model_version, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            int(rec.get("id") or 0),
            "rule",
            "规则评分，未启用大模型或机器学习；已接入竞品风险过滤和员工反馈调分。",
            pa._json(strengths),
            pa._json(concerns),
            result.version,
            now,
        ),
    )
    conn.commit()
    pa.outcome_collector.ensure_outcome(
        int(rec.get("id") or 0),
        kol_pool_id=item.get("id"),
        launch_id=launch_id or None,
        feature_snapshot=features,
        scoring_breakdown=breakdown,
        model_version=result.version,
        display_position=idx,
        display_context={"rank": idx, "score": score, "run_id": run.get("id"), "competitor": breakdown["competitor"], "operator_feedback": breakdown["operator_feedback"]},
    )


def _persist_recommendations(
    conn: Any,
    top_candidates: list[dict[str, Any]],
    run: dict[str, Any],
    launch_id: int,
    rerank_arm: Any,
    rerank_policy: dict[str, Any],
    now: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按名次逐条落 recommendation + explanation + outcome(commit 时机与旧实现逐条一致)。"""
    rows: list[dict[str, Any]] = []
    rerank_rows: list[dict[str, Any]] = []
    for idx, cand in enumerate(top_candidates, start=1):
        score, item, features, result, competitor, feedback = (
            cand["score"], cand["item"], cand["features"], cand["result"], cand["competitor"], cand["feedback"]
        )
        rec_uid = f"rec-{secrets.token_hex(8)}"
        breakdown = _candidate_breakdown(cand, result, competitor, feedback, rerank_arm, rerank_policy)
        strengths, concerns = _reason_notes(result, competitor, feedback)
        rec = _insert_recommendation_row(
            conn, rec_uid, run, launch_id, item, score, idx, features, breakdown, strengths, concerns, result, now
        )
        _insert_explanation_and_outcome(
            conn, rec, item, launch_id, idx, score, run, features, breakdown, strengths, concerns, result, now
        )
        # 后端内部字段(前端若展示只给 rerank_policy.display_note 一句,不给数字)。
        rec["rerank_adjustment"] = float(cand.get("rerank_adjustment") or 0.0)
        rec["rerank_reason_codes"] = list(cand.get("rerank_reason_codes") or [])
        rerank_rows.append({
            "recommendation_id": rec.get("id"), "kol_pool_id": item.get("id"), "score": score,
            "rerank_vector": cand.get("rerank_vector") or {},
            "rerank_adjustment": cand.get("rerank_adjustment"), "rerank_reason_codes": cand.get("rerank_reason_codes") or [],
        })
        rows.append(rec)
    return rows, rerank_rows
