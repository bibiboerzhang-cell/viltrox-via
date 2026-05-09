"""Product launch analysis and KOL recommendation orchestration."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import audit, feature_store, kol_pool, outcome_collector, product_analysis_actions, product_analysis_evidence
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.scoring import ScoringRegistry
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _last_by_uid(table: str, uid_col: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {uid_col}=?", (uid,)).fetchone()
    return dict(row) if row else {}


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


def run_recommendations(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    launch_id = int(payload.get("launch_id") or 0)
    launch = get_launch(launch_id).get("launch") if launch_id else {}
    strategy_version = str(payload.get("strategy_version") or "rule_v0")
    strategy = ScoringRegistry.get(strategy_version)
    limit = max(1, min(200, int(payload.get("limit") or 50)))
    pool = kol_pool.list_pool(limit=limit, platform=str(payload.get("platform") or ""), query=str(payload.get("query") or "")).get("items") or []
    run_uid = f"recrun-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_kol_recommendation_runs
            (run_uid, launch_id, strategy_version, status, candidate_count, recommendation_count, filters_json,
             created_by_staff_id, created_at, completed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (run_uid, launch_id or None, strategy_version, "completed", len(pool), 0, _json(payload), resolve_staff_id(staff) or None, now, now),
    )
    conn.commit()
    run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
    rows: list[dict[str, Any]] = []
    brief = {
        "product_sku": launch.get("product_sku"),
        "product_name": launch.get("product_name"),
        "category": launch.get("category"),
        "target_platforms": _loads(launch.get("target_platforms_json"), []),
    }
    scored: list[tuple[float, dict[str, Any], dict[str, Any], Any]] = []
    for item in pool:
        features = feature_store.snapshot_features(kol_pool_id=int(item.get("id") or 0), launch_id=launch_id or None)
        result = strategy.score(features, brief)
        scored.append((float(result.score), item, features, result))
    scored.sort(key=lambda row: row[0], reverse=True)
    for idx, (_score, item, features, result) in enumerate(scored, start=1):
        rec_uid = f"rec-{secrets.token_hex(8)}"
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
                result.score,
                idx,
                "recommended",
                _json(features),
                _json(result.breakdown),
                _json({"strengths": result.strengths, "concerns": result.concerns, "version": result.version}),
                now,
                now,
            ),
        )
        conn.commit()
        rec = _last_by_uid("vkpi_kol_recommendations", "recommendation_uid", rec_uid)
        conn.execute(
            """
            INSERT INTO vkpi_recommendation_explanations
                (recommendation_id, explanation_type, explanation_text, strengths_json, concerns_json, model_version, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                int(rec.get("id") or 0),
                "rule",
                "规则评分，未启用大模型或机器学习。",
                _json(result.strengths),
                _json(result.concerns),
                result.version,
                now,
            ),
        )
        conn.commit()
        outcome_collector.ensure_outcome(
            int(rec.get("id") or 0),
            kol_pool_id=item.get("id"),
            launch_id=launch_id or None,
            feature_snapshot=features,
            scoring_breakdown=result.breakdown,
            model_version=result.version,
            display_position=idx,
            display_context={"rank": idx, "score": result.score, "run_id": run.get("id")},
        )
        rows.append(rec)
    conn.execute("UPDATE vkpi_kol_recommendation_runs SET recommendation_count=? WHERE id=?", (len(rows), int(run.get("id") or 0)))
    conn.commit()
    run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
    audit.log_business_event(staff_id=resolve_staff_id(staff), action_type="recommendation_run", target_type="product_launch", target_id=launch_id, detail=f"{len(rows)} recommendations")
    return {"run": run, "recommendations": rows, "provider_status": "local_rule_only"}


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
    return {"recommendations": [dict(row) for row in rows]}


def get_recommendation_evidence(recommendation_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_analysis_evidence.get_recommendation_evidence(recommendation_id, staff=staff)


def recommendation_outcome_summary(launch_id: int | None = None, run_id: int | None = None, limit: int = 50) -> dict[str, Any]:
    return product_analysis_evidence.recommendation_outcome_summary(launch_id=launch_id, run_id=run_id, limit=limit)


def action_recommendation(recommendation_id: int, action: str, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return product_analysis_actions.action_recommendation(recommendation_id, action, payload or {}, staff=staff)
