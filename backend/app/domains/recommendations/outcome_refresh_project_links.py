"""Exact recommendation attribution after a bounded legacy SQL prefilter."""
from __future__ import annotations

import json
from typing import Any, Callable


def _matches_project(row: Any, context: dict[str, Any]) -> bool:
    raw = row["metadata_json"]
    try:
        metadata = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(metadata, dict):
        return False
    if "recommendation_id" in metadata:
        value = metadata["recommendation_id"]
        return type(value) is int and value == context["rec_id"]
    return bool(
        context["kol_id"] > 0 and row["kol_id"] == context["kol_id"]
        and row["source_type"] == "product_recommendation"
        and (not context["launch_sku"] or row["product_sku"] == context["launch_sku"])
    )


def load_refresh_projects(
    conn: Any, context: dict[str, Any], *, ids_clause: Callable, first_timestamp: Callable,
) -> dict[str, Any]:
    rec_id = context["rec_id"]
    rows = conn.execute(
        """
        SELECT id, stage, created_at, updated_at, metadata_json, kol_id, source_type, product_sku
        FROM vkpi_projects
        WHERE COALESCE(stage_status, '') != 'deleted' AND created_at >= ?
          AND (metadata_json LIKE ? OR metadata_json LIKE ? OR
            (? > 0 AND kol_id=? AND source_type='product_recommendation'
             AND (? = '' OR product_sku = ?)))
        """,
        (context["recommended_at"], f'%"recommendation_id": {rec_id}%',
         f'%"recommendation_id":{rec_id}%', context["kol_id"], context["kol_id"],
         context["launch_sku"], context["launch_sku"]),
    ).fetchall()
    projects = [row for row in rows if _matches_project(row, context)]
    project_ids = [int(row["id"]) for row in projects]
    clause, params = ids_clause("project_id", project_ids)
    return {"rows": projects, "ids": project_ids, "clause": clause, "params": params,
            "stage_map": {int(row["id"]): str(row["stage"] or "") for row in projects},
            "first_project": first_timestamp([row["created_at"] for row in projects])}
