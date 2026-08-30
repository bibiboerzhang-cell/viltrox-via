"""Product launch analysis and KOL recommendation orchestration."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol import pool as kol_pool
from app.domains.recommendations import actions as product_analysis_actions
from app.domains.recommendations import evidence as product_analysis_evidence
from app.domains.recommendations import feature_store
from app.domains.recommendations import outcomes as outcome_collector
from app.domains.recommendations import rerank_shadow
from app.domains.recommendations.product_analysis_persist import (  # noqa: F401 — run_recommendations 落库层(2026-08-30 拆出)
    _build_run_filters,
    _insert_run_row,
    _persist_recommendations,
)
from app.domains import audit
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.scoring import ScoringRegistry
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)

COMPETITOR_SCORE_ADJUSTMENTS = {
    "avoid": -999.0,
    "caution": -8.0,
    "safe": 0.0,
    "opportunity": 5.0,
}

FEEDBACK_SCORE_ADJUSTMENTS = {
    "shortlist": 12.0,
    "claim": 10.0,
    "create_project": 14.0,
    "positive_signal": 8.0,
    "feedback": 2.0,
    "reject": -25.0,
    "snooze": -10.0,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _platform_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return {"ig": "instagram", "yt": "youtube", "tt": "tiktok", "twitter": "x", "小红书": "xiaohongshu"}.get(raw, raw)


def _target_platforms(launch: dict[str, Any]) -> list[str]:
    raw = launch.get("target_platforms")
    if raw is None:
        raw = _loads(launch.get("target_platforms_json"), [])
    if not isinstance(raw, list):
        return []
    platforms: list[str] = []
    seen: set[str] = set()
    for item in raw:
        platform = _platform_key(item)
        if platform and platform not in seen:
            seen.add(platform)
            platforms.append(platform)
    return platforms


def _pool_identity(item: dict[str, Any]) -> str:
    if item.get("id") is not None:
        return f"id:{item.get('id')}"
    return f"{_platform_key(item.get('platform'))}:{str(item.get('handle') or '').strip().lower()}"


def _pool_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    try:
        fit_score = float(item.get("viltrox_fit_score") or 0)
    except (TypeError, ValueError):
        fit_score = 0.0
    return fit_score, str(item.get("updated_at") or "")


def _candidate_pool(payload: dict[str, Any], launch: dict[str, Any], limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    query = str(payload.get("query") or "")
    target_platforms = _target_platforms(launch)
    if not target_platforms:
        pool = kol_pool.list_pool(limit=limit, platform=str(payload.get("platform") or ""), query=query).get("items") or []
        return pool, []

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for platform in target_platforms:
        items = kol_pool.list_pool(limit=limit, platform=platform, query=query).get("items") or []
        for item in items:
            key = _pool_identity(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    merged.sort(key=_pool_sort_key, reverse=True)
    return merged[:limit], target_platforms


def _last_by_uid(table: str, uid_col: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {uid_col}=?", (uid,)).fetchone()
    return dict(row) if row else {}


def _table_exists(table_name: str) -> bool:
    try:
        row = get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        row = get_conn().execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _strongest_competitor_relation(kol_pool_id: int) -> dict[str, Any]:
    if not kol_pool_id or not _table_exists("vkpi_competitor_relation"):
        return {}
    row = get_conn().execute(
        """
        SELECT competitor_brand, collaboration_depth, collaboration_count_90d,
               collaboration_count_total, sentiment, risk_score, risk_tier, computed_at
        FROM vkpi_competitor_relation
        WHERE kol_pool_id=?
        ORDER BY risk_score DESC, competitor_brand ASC
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else {}


def _competitor_context(kol_pool_id: int) -> dict[str, Any]:
    relation = _strongest_competitor_relation(kol_pool_id)
    tier = str(relation.get("risk_tier") or "opportunity").strip().lower() if relation else "opportunity"
    if tier not in COMPETITOR_SCORE_ADJUSTMENTS:
        tier = "opportunity"
    risk_score = float(relation.get("risk_score") or 0) if relation else 0.0
    brand = str(relation.get("competitor_brand") or "").strip().lower()
    adjustment = COMPETITOR_SCORE_ADJUSTMENTS[tier]
    return {
        "brand": brand,
        "risk_tier": tier,
        "risk_score": risk_score,
        "score_adjustment": adjustment,
        "relation": relation,
        "source": "vkpi_competitor_relation" if relation else "no_persisted_relation",
    }


def _adjust_score_for_competitor(base_score: float, context: dict[str, Any]) -> float:
    adjustment = float(context.get("score_adjustment") or 0)
    if adjustment <= -900:
        return 0.0
    return max(0.0, min(100.0, round(float(base_score or 0) + adjustment, 3)))


def _feedback_context(kol_pool_id: int, platform: str = "", handle: str = "") -> dict[str, Any]:
    if not _table_exists("vkpi_recommendation_feedback") or not _table_exists("vkpi_kol_recommendations"):
        return {
            "counts": {},
            "score_adjustment": 0.0,
            "sentiment": "none",
            "source": "feedback_table_unavailable",
        }
    where: list[str] = []
    params: list[Any] = []
    if kol_pool_id:
        where.append("rec.kol_pool_id=?")
        params.append(int(kol_pool_id))
    clean_platform = _platform_key(platform)
    clean_handle = str(handle or "").strip().lower()
    if clean_platform and clean_handle:
        where.append("(LOWER(rec.platform)=? AND LOWER(rec.handle)=?)")
        params.extend([clean_platform, clean_handle])
    if not where:
        return {
            "counts": {},
            "score_adjustment": 0.0,
            "sentiment": "none",
            "source": "no_candidate_identity",
        }
    rows = get_conn().execute(
        f"""
        SELECT fb.feedback_type, fb.note, fb.created_at, fb.metadata_json,
               rec.status AS recommendation_status, rec.id AS recommendation_id,
               rec.run_id
        FROM vkpi_recommendation_feedback fb
        INNER JOIN vkpi_kol_recommendations rec ON rec.id=fb.recommendation_id
        WHERE {" OR ".join(where)}
        ORDER BY fb.created_at DESC, fb.id DESC
        LIMIT 30
        """,
        tuple(params),
    ).fetchall()
    counts: dict[str, int] = {}
    latest: dict[str, Any] = {}
    for raw in rows:
        row = dict(raw)
        feedback_type = str(row.get("feedback_type") or "").strip().lower()
        if not feedback_type:
            continue
        counts[feedback_type] = counts.get(feedback_type, 0) + 1
        if not latest:
            latest = {
                "feedback_type": feedback_type,
                "note": row.get("note") or "",
                "created_at": row.get("created_at") or "",
                "recommendation_id": row.get("recommendation_id"),
                "run_id": row.get("run_id"),
            }
    adjustment = 0.0
    for feedback_type, count in counts.items():
        adjustment += FEEDBACK_SCORE_ADJUSTMENTS.get(feedback_type, 0.0) * min(3, int(count or 0))
    adjustment = max(-45.0, min(30.0, round(adjustment, 3)))
    if counts.get("reject"):
        sentiment = "negative_reject"
    elif counts.get("snooze"):
        sentiment = "negative_snooze"
    elif any(counts.get(key) for key in ("shortlist", "claim", "create_project", "positive_signal")):
        sentiment = "positive"
    elif counts:
        sentiment = "neutral"
    else:
        sentiment = "none"
    return {
        "counts": counts,
        "latest": latest,
        "score_adjustment": adjustment,
        "sentiment": sentiment,
        "source": "vkpi_recommendation_feedback" if rows else "no_feedback",
    }


def _adjust_score_for_feedback(base_score: float, context: dict[str, Any]) -> float:
    adjustment = float(context.get("score_adjustment") or 0)
    return max(0.0, min(100.0, round(float(base_score or 0) + adjustment, 3)))


def _competitor_reason(context: dict[str, Any]) -> str:
    tier = str(context.get("risk_tier") or "opportunity")
    brand = str(context.get("brand") or "").upper()
    score = float(context.get("risk_score") or 0)
    if tier == "avoid":
        return f"竞品强绑定 {brand or 'competitor'} risk {score:.1f}"
    if tier == "caution":
        return f"竞品谨慎 {brand or 'competitor'} risk {score:.1f}"
    if tier == "safe":
        return f"竞品弱关联 {brand or 'competitor'} risk {score:.1f}"
    return "未发现强竞品绑定"


def _feedback_reason(context: dict[str, Any]) -> str:
    counts = context.get("counts") or {}
    adjustment = float(context.get("score_adjustment") or 0)
    if not counts:
        return "暂无历史员工反馈"
    parts = [f"{key}:{value}" for key, value in sorted(counts.items())]
    return f"历史员工反馈 {'/'.join(parts)} 调分 {adjustment:+.1f}"


def create_launch(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    name = str(payload.get("name") or payload.get("product_name") or "").strip()
    if not name:
        raise ValueError("launch name required")
    now = _utcnow()
    uid = f"launch-{secrets.token_hex(8)}"
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_product_launches
            (launch_uid, name, product_sku, product_name, category, target_market, target_platforms_json,
             target_audience_json, competitor_products_json, launch_window_start, launch_window_end,
             budget_range_json, goals_json, constraints_json, status, created_by_staff_id,
             metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            name,
            str(payload.get("product_sku") or payload.get("sku") or ""),
            str(payload.get("product_name") or name),
            str(payload.get("category") or ""),
            str(payload.get("target_market") or ""),
            _json(payload.get("target_platforms") or []),
            _json(payload.get("target_audience") or {}),
            _json(payload.get("competitor_products") or []),
            payload.get("launch_window_start") or None,
            payload.get("launch_window_end") or None,
            _json(payload.get("budget_range") or {}),
            _json(payload.get("goals") or {}),
            _json(payload.get("constraints") or {}),
            str(payload.get("status") or "draft"),
            resolve_staff_id(staff) or None,
            _json(payload.get("metadata") or {}),
            now,
            now,
        ),
    )
    conn.commit()
    launch = _last_by_uid("vkpi_product_launches", "launch_uid", uid)
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="product_launch_create", target_type="product_launch", target_id=launch.get("id") or uid, detail=name)
    return {"launch": launch}


def list_launches(limit: int = 100, status: str = "") -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where = "WHERE deleted_at IS NULL"
    params: list[Any] = []
    if status:
        where += " AND status=?"
        params.append(status)
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_product_launches {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
        (*params, max(1, min(300, int(limit or 100)))),
    ).fetchall()
    return {"launches": [dict(row) for row in rows]}


def get_launch(launch_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_product_launches WHERE id=? AND deleted_at IS NULL", (int(launch_id),)).fetchone()
    if not row:
        raise LookupError("launch not found")
    launch = dict(row)
    launch["target_platforms"] = _loads(launch.get("target_platforms_json"), [])
    launch["target_audience"] = _loads(launch.get("target_audience_json"), {})
    launch["competitor_products"] = _loads(launch.get("competitor_products_json"), [])
    return {"launch": launch}


def update_launch(launch_id: int, payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    get_launch(launch_id)
    allowed = {
        "name": "name",
        "product_sku": "product_sku",
        "product_name": "product_name",
        "category": "category",
        "target_market": "target_market",
        "status": "status",
    }
    sets: list[str] = []
    params: list[Any] = []
    for key, col in allowed.items():
        if key in payload:
            sets.append(f"{col}=?")
            params.append(str(payload.get(key) or ""))
    json_fields = {
        "target_platforms": "target_platforms_json",
        "target_audience": "target_audience_json",
        "competitor_products": "competitor_products_json",
        "budget_range": "budget_range_json",
        "goals": "goals_json",
        "constraints": "constraints_json",
        "metadata": "metadata_json",
    }
    for key, col in json_fields.items():
        if key in payload:
            sets.append(f"{col}=?")
            params.append(_json(payload.get(key)))
    if not sets:
        return get_launch(launch_id)
    sets.append("updated_at=?")
    params.extend([_utcnow(), int(launch_id)])
    get_conn().execute(f"UPDATE vkpi_product_launches SET {', '.join(sets)} WHERE id=?", params)
    get_conn().commit()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="product_launch_update", target_type="product_launch", target_id=launch_id)
    return get_launch(launch_id)


def delete_launch(launch_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    get_launch(launch_id)
    get_conn().execute("UPDATE vkpi_product_launches SET deleted_at=?, updated_at=? WHERE id=?", (_utcnow(), _utcnow(), int(launch_id)))
    get_conn().commit()
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="product_launch_delete", target_type="product_launch", target_id=launch_id)
    return {"deleted": True, "launch_id": int(launch_id)}


def _load_rerank_model() -> Any:
    try:
        return rerank_shadow.load_active_model()
    except Exception:
        logger.warning("run_recommendations.rerank_model_unavailable", exc_info=True)
        return None


def _evaluate_candidate(
    item: dict[str, Any],
    strategy: Any,
    brief: dict[str, Any],
    launch_id: int,
    include_avoid_competitors: bool,
) -> tuple[float, dict[str, Any], dict[str, Any], Any, dict[str, Any], dict[str, Any]] | None:
    """单候选打分 + 竞品/反馈上下文注入;avoid 档且未显式包含 → None(被过滤)。"""
    kol_pool_id = int(item.get("id") or 0)
    features = feature_store.snapshot_features(kol_pool_id=kol_pool_id, launch_id=launch_id or None)
    result = strategy.score(features, brief)
    competitor = _competitor_context(kol_pool_id)
    feedback = _feedback_context(kol_pool_id, str(item.get("platform") or ""), str(item.get("handle") or ""))
    features["competitor_risk_tier"] = competitor.get("risk_tier")
    features["competitor_risk_score"] = competitor.get("risk_score")
    features["competitor_brand"] = competitor.get("brand")
    features["operator_feedback_counts"] = feedback.get("counts") or {}
    features["operator_feedback_sentiment"] = feedback.get("sentiment")
    features["operator_feedback_adjustment"] = feedback.get("score_adjustment")
    if competitor.get("risk_tier") == "avoid" and not include_avoid_competitors:
        return None
    competitor_adjusted_score = _adjust_score_for_competitor(float(result.score), competitor)
    adjusted_score = _adjust_score_for_feedback(competitor_adjusted_score, feedback)
    return (adjusted_score, item, features, result, competitor, feedback)


def _score_pool(
    pool: list[dict[str, Any]],
    strategy: Any,
    brief: dict[str, Any],
    launch_id: int,
    include_avoid_competitors: bool,
) -> tuple[list[tuple[float, dict[str, Any], dict[str, Any], Any, dict[str, Any], dict[str, Any]]], dict[str, int]]:
    scored: list[tuple[float, dict[str, Any], dict[str, Any], Any, dict[str, Any], dict[str, Any]]] = []
    counters = {"filtered_competitor_avoid": 0, "feedback_candidates": 0, "feedback_positive": 0, "feedback_negative": 0}
    for item in pool:
        entry = _evaluate_candidate(item, strategy, brief, launch_id, include_avoid_competitors)
        if entry is None:
            counters["filtered_competitor_avoid"] += 1
            continue
        feedback = entry[5]
        if feedback.get("counts"):
            counters["feedback_candidates"] += 1
        if float(feedback.get("score_adjustment") or 0) > 0:
            counters["feedback_positive"] += 1
        if float(feedback.get("score_adjustment") or 0) < 0:
            counters["feedback_negative"] += 1
        scored.append(entry)
    return scored, counters


def _write_shadow_snapshots(
    rerank_policy: dict[str, Any],
    rerank_rows: list[dict[str, Any]],
    rerank_arm: Any,
    staff: dict[str, Any] | None,
    run: dict[str, Any],
    launch_id: int,
) -> None:
    # 特征快照(学习闭环·冻结推荐时刻特征 + arm + 影子量):整批 commit 之后落,失败只告警。
    try:
        rerank_policy["snapshots"] = rerank_shadow.write_snapshots_for_items(
            rerank_rows,
            engine="product_analysis",
            arm=rerank_arm,
            applied=rerank_policy["applied"],
            model_version=rerank_policy["model_version"],
            staff_id=resolve_staff_id(staff) or None,
            run_id=int(run.get("id") or 0) or None,
            launch_id=launch_id or None,
            rec_id_of=lambda row: row.get("recommendation_id"),
        )
    except Exception:
        logger.warning("run_recommendations.feature_snapshot_failed", exc_info=True)
        rerank_policy["snapshots"] = {"written": 0, "skipped": 0, "failed": len(rerank_rows)}


def run_recommendations(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    launch_id = int(payload.get("launch_id") or 0)
    launch = get_launch(launch_id).get("launch") if launch_id else {}
    strategy_version = str(payload.get("strategy_version") or "rule_v0")
    strategy = ScoringRegistry.get(strategy_version)
    limit = max(1, min(200, int(payload.get("limit") or 50)))
    pool, effective_platforms = _candidate_pool(payload, launch, min(500, limit * 3))
    include_avoid_competitors = str(payload.get("include_avoid_competitors") or "").lower() in {"1", "true", "yes", "on"}
    run_filters = _build_run_filters(payload, include_avoid_competitors, effective_platforms)
    run_uid = f"recrun-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    _insert_run_row(conn, run_uid, launch_id, strategy_version, len(pool), run_filters, staff, now)
    run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
    brief = {
        "product_sku": launch.get("product_sku"),
        "product_name": launch.get("product_name"),
        "category": launch.get("category"),
        "target_platforms": _loads(launch.get("target_platforms_json"), []),
    }
    # W-L2 影子重排序:按 staff 哈希分流 arm;有激活模型才产出调整量。score 列永不改,只在 treatment 动次序。
    rerank_arm = rerank_shadow.arm_for_staff(staff)
    rerank_model = _load_rerank_model()
    scored, counters = _score_pool(pool, strategy, brief, launch_id, include_avoid_competitors)
    candidates = [
        {"score": score, "item": item, "features": features, "result": result, "competitor": competitor, "feedback": feedback}
        for score, item, features, result, competitor, feedback in scored
    ]
    rerank_policy = rerank_shadow.apply_shadow_rerank(
        candidates,
        arm=rerank_arm,
        model=rerank_model,
        engine="product_analysis",
        profile_of=lambda cand: cand["features"],
        breakdown_of=lambda cand: dict((cand["result"].breakdown or {}).items()),
    )
    if not rerank_policy["applied"]:
        candidates.sort(key=lambda cand: float(cand["score"]), reverse=True)
    rows, rerank_rows = _persist_recommendations(
        conn, candidates[:limit], run, launch_id, rerank_arm, rerank_policy, now
    )
    conn.execute("UPDATE vkpi_kol_recommendation_runs SET recommendation_count=? WHERE id=?", (len(rows), int(run.get("id") or 0)))
    conn.commit()
    _write_shadow_snapshots(rerank_policy, rerank_rows, rerank_arm, staff, run, launch_id)
    run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="recommendation_run", target_type="product_launch", target_id=launch_id, detail=f"{len(rows)} recommendations")
    return {
        "run": run,
        "recommendations": rows,
        "provider_status": "local_rule_only",
        "competitor_filter": {
            "mode": run_filters["competitor_filter"],
            "filtered_avoid": counters["filtered_competitor_avoid"],
            "provider_calls": False,
        },
        "feedback_policy": {
            "mode": "score_adjust_v1",
            "candidates_with_feedback": counters["feedback_candidates"],
            "positive_adjusted": counters["feedback_positive"],
            "negative_adjusted": counters["feedback_negative"],
            "provider_calls": False,
        },
        "rerank_policy": rerank_policy,
    }


def list_recommendations(launch_id: int | None = None, run_id: int | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where: list[str] = []
    params: list[Any] = []
    if launch_id:
        where.append("launch_id=?")
        params.append(int(launch_id))
    if run_id:
        where.append("run_id=?")
        params.append(int(run_id))
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_kol_recommendations {clause} ORDER BY run_id DESC, rank ASC LIMIT ?",
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    recs = [dict(row) for row in rows]
    # 学习闭环·结果段接线:每条被展示的推荐幂等落一行 outcome 底座(按 recommendation_id
    # 一行,先批量查缺只补缺失行 → 反复展示/刷新绝不刷屏落行)。helper 内部全量吞错并告警;
    # 这里再包一层双保险,挂钩任何失败都不影响推荐展示主流程。零触 viltrox_fit_score。
    try:
        outcome_collector.ensure_outcomes_for_display(
            recs,
            display_context={"source": "product_analysis.list_recommendations"},
        )
    except Exception:
        logger.debug("outcome 底座落行失败(双保险:helper 已自吞并告警,展示可用性优先)", exc_info=True)
    return {"recommendations": recs}


def list_recommendation_runs(
    *,
    strategy_version: str = "",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    where: list[str] = []
    params: list[Any] = []
    if strategy_version:
        where.append("strategy_version=?")
        params.append(str(strategy_version))
    if status:
        where.append("status=?")
        params.append(str(status))
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_kol_recommendation_runs {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, max(1, min(300, int(limit or 100)))),
    ).fetchall()
    runs: list[dict[str, Any]] = []
    for raw in rows:
        run = dict(raw)
        run_id = int(run.get("id") or 0)
        counts = get_conn().execute(
            """
            SELECT status, COUNT(*) AS count
            FROM vkpi_kol_recommendations
            WHERE run_id=?
            GROUP BY status
            """,
            (run_id,),
        ).fetchall()
        run["filters"] = _loads(run.get("filters_json"), {})
        run["recommendation_status_counts"] = {str(row["status"]): int(row["count"] or 0) for row in counts}
        runs.append(run)
    return {"runs": runs}


def get_recommendation_evidence(recommendation_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_analysis_evidence.get_recommendation_evidence(recommendation_id, staff=staff)


def recommendation_outcome_summary(launch_id: int | None = None, run_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    return product_analysis_evidence.recommendation_outcome_summary(launch_id=launch_id, run_id=run_id, limit=limit)


def action_recommendation(recommendation_id: int, action: str, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_analysis_actions.action_recommendation(recommendation_id, action, payload or {}, staff=staff)
