"""Persistence helpers for KOL product-fit preview runs."""
from __future__ import annotations

import json
import secrets
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi.new_launch_match import _json_default, _row_to_dict


logger = get_logger(__name__)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _last_by_uid(table: str, column: str, uid: str) -> dict[str, Any]:
    row = get_conn().execute(f"SELECT * FROM {table} WHERE {column}=?", (uid,)).fetchone()
    return _row_to_dict(row) if row else {}


def persist_product_fit_preview_run(payload: dict[str, Any], *, scenario: str, generated_at: str) -> dict[str, Any]:
    summary = payload.get("summary") or {}
    items = payload.get("items") or []
    kol = payload.get("kol") or {}
    run_uid = f"p4kpf-{secrets.token_hex(8)}"
    conn = get_conn()
    filters = {
        "scenario": scenario,
        "source_mode": "kol_product_fit_preview",
        "dry_run": True,
        "kol": kol,
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
                "kol_product_fit_v1",
                "previewed",
                int(summary.get("total_families_evaluated") or 0),
                len(items),
                _json(filters),
                None,
                generated_at,
                generated_at,
            ),
        )
        run = _last_by_uid("vkpi_kol_recommendation_runs", "run_uid", run_uid)
        run_id = int(run.get("id") or 0)
        recommendation_ids: list[int] = []
        for item in items:
            rec_uid = f"p4kpf-rec-{secrets.token_hex(8)}"
            reason = item.get("recommendation_reason") or {}
            feature_snapshot = {
                "scenario": scenario,
                "kol": kol,
                "product_family_uid": item.get("product_family_uid"),
                "product_family_name": item.get("product_family_name"),
                "product_member_count": item.get("product_member_count"),
                "matched_catalog_product": item.get("matched_catalog_product"),
                "matched_catalog_products": item.get("matched_catalog_products") or [],
                "links": item.get("links") or {},
            }
            explanation = {
                "evidence_pro": item.get("evidence_pro") or [],
                "evidence_con": item.get("evidence_con") or [],
                "recommendation_reason": reason,
                "source": "p4_kol_product_fit",
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
                    kol.get("kol_pool_id") or None,
                    None,
                    kol.get("platform") or "",
                    kol.get("handle") or "",
                    item.get("product_family_name") or "",
                    item.get("score"),
                    item.get("rank"),
                    "previewed",
                    _json(feature_snapshot),
                    _json(item.get("score_breakdown") or {}),
                    _json(explanation),
                    generated_at,
                    generated_at,
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
                    "p4_kol_product_fit",
                    reason.get("short_reason") or "Explainable KOL product-fit preview.",
                    _json(item.get("evidence_pro") or []),
                    _json(item.get("evidence_con") or []),
                    reason.get("model") or "rule_v1",
                    generated_at,
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
            logger.warning("kol product fit rollback failed: %s", rollback_exc)
        raise
