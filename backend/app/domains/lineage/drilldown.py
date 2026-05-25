"""V-KPI drilldown service.

Given a metric_value_id (from a snapshot run), return the exact source rows
that contributed to it, hydrated with business entity context (project name,
KOL handle, staff name, etc.) so the frontend Evidence Drawer can render
"老板看到 GMV → 哪些订单 → 哪条短链 → 哪个项目 → 哪个 KOL → 谁负责"
without doing N joins per row.

This is the read side of the snapshot+source-map pattern in metric_lineage.py.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.lineage import ensure_vkpi_lineage_schema


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _safe_load(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except (TypeError, ValueError):
        return {}


def _project_is_live(conn: Any, project_id: Any) -> bool:
    pid = _int(project_id)
    if not pid:
        return True
    row = conn.execute("SELECT stage_status FROM vkpi_projects WHERE id=?", (pid,)).fetchone()
    if not row:
        return False
    return str(dict(row).get("stage_status") or "").lower() != "deleted"


def _source_is_live(conn: Any, data: dict[str, Any]) -> bool:
    """Do not show stale lineage evidence after the underlying source was deleted.

    Metric runs are historical snapshots, but the dashboard Evidence Drawer is a
    current operational view. If a project/source row has been removed or marked
    deleted, the latest drawer should fall back to live API evidence instead of
    showing frozen test orders from an old run.
    """
    source_type = str(data.get("source_type") or "").lower()
    source_id = _int(data.get("source_id"))
    if source_type == "sales_attribution":
        row = conn.execute("SELECT project_id FROM vkpi_sales_attributions WHERE id=?", (source_id,)).fetchone()
        return bool(row) and _project_is_live(conn, dict(row).get("project_id"))
    if source_type == "cost_ledger":
        row = conn.execute("SELECT project_id, status FROM vkpi_cost_ledger WHERE id=?", (source_id,)).fetchone()
        if not row:
            return False
        item = dict(row)
        return str(item.get("status") or "").lower() != "void" and _project_is_live(conn, item.get("project_id"))
    if source_type == "stage_event":
        row = conn.execute("SELECT project_id FROM vkpi_project_stage_events WHERE id=?", (source_id,)).fetchone()
        return bool(row) and _project_is_live(conn, dict(row).get("project_id"))
    if source_type == "claim":
        return bool(conn.execute("SELECT id FROM vkpi_kol_claims WHERE id=?", (source_id,)).fetchone())
    if source_type == "link_click":
        row = conn.execute(
            """
            SELECT l.project_id
            FROM vkpi_link_clicks c
            LEFT JOIN vkpi_links l ON l.id = c.link_id
            WHERE c.id=?
            """,
            (source_id,),
        ).fetchone()
        return bool(row) and _project_is_live(conn, dict(row).get("project_id"))
    if source_type == "content_post":
        row = conn.execute("SELECT project_id FROM vkpi_content_posts WHERE id=?", (source_id,)).fetchone()
        return bool(row) and _project_is_live(conn, dict(row).get("project_id"))
    if source_type == "project":
        return _project_is_live(conn, source_id)
    if source_type == "alert":
        row = conn.execute("SELECT status FROM vkpi_alerts WHERE id=?", (source_id,)).fetchone()
        return bool(row) and str(dict(row).get("status") or "").lower() == "open"
    if source_type == "kpi_ledger":
        return bool(conn.execute("SELECT id FROM vkpi_kpi_ledger WHERE id=?", (source_id,)).fetchone())
    return True


def _sales_order_snapshot(conn: Any, source_id: int) -> dict[str, Any] | None:
    if not source_id:
        return None
    row = conn.execute(
        """
        SELECT os.id, os.shopify_order_id, os.admin_graphql_api_id,
               os.order_name, os.order_number, os.processed_at, os.currency,
               os.subtotal_cents, os.total_cents, os.financial_status,
               os.fulfillment_status, os.refund_status, os.discount_codes_json,
               os.landing_site, os.note_attributes_json, os.line_items_json,
               os.raw_payload_hash, os.created_at, os.updated_at
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
        WHERE sa.id = ?
        LIMIT 1
        """,
        (int(source_id),),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    if not data.get("id"):
        return None
    for key in ("discount_codes_json", "note_attributes_json", "line_items_json"):
        data[key.replace("_json", "")] = _safe_load(data.get(key))
    return data


# ---------------------------------------------------------------------------
# main entrypoint: drilldown by metric_value_id
# ---------------------------------------------------------------------------

def drilldown_value(
    metric_value_id: int,
    *,
    limit: int = 200,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the full drilldown for one metric_value snapshot.

    Returns:
      {
        "value": {...header...},
        "run": {...run header...},
        "rows": [
          {
            "source_id", "source_type", "evidence_type", "evidence_ref",
            "contribution_amount", "contribution_percent",
            "occurred_at",
            "project": {"id","name"} | None,
            "kol":     {"id","name","platform"} | None,
            "staff":   {"id","name","email"} | None,
            "snapshot": {...},   # source row frozen at snapshot time
          },
          ...
        ],
        "filtered": {"project_id":..., "kol_id":..., "staff_id":...},
        "row_count": int,
      }
    """
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    effective_staff_id = scope.effective_staff_id(staff, staff_id)
    if project_id:
        scope.assert_project_access(int(project_id), staff)

    value_header = conn.execute(
        """
        SELECT v.*, r.run_uid, r.period_start, r.period_end, r.scope_type, r.scope_id,
               r.generated_at, r.definition_version, r.trigger_source, r.generated_by_staff_id
        FROM vkpi_metric_values v
        INNER JOIN vkpi_metric_runs r ON r.id = v.run_id
        WHERE v.id = ?
        """,
        (int(metric_value_id),),
    ).fetchone()
    if not value_header:
        raise LookupError("metric value not found")
    value_row = dict(value_header)
    if staff is not None and not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        run_scope_type = str(value_row.get("scope_type") or "")
        run_scope_id = _int(value_row.get("scope_id"))
        generated_by = _int(value_row.get("generated_by_staff_id"))
        if not actor or not ((run_scope_type == "staff" and run_scope_id == actor) or generated_by == actor):
            raise scope.ScopeDenied("metric value scope denied")

    where: list[str] = ["s.metric_value_id = ?"]
    params: list[Any] = [int(metric_value_id)]
    if project_id:
        where.append("s.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        where.append("s.kol_id = ?")
        params.append(int(kol_id))
    if staff_id:
        staff_id = effective_staff_id
    elif effective_staff_id:
        staff_id = effective_staff_id
    if staff_id:
        where.append("s.staff_id = ?")
        params.append(int(staff_id))

    rows = conn.execute(
        f"""
        SELECT s.*,
               p.project_name, p.project_uid,
               k.channel_name AS kol_name, k.platform AS kol_platform,
               u.name AS staff_name, u.email AS staff_email
        FROM vkpi_metric_sources s
        LEFT JOIN vkpi_projects p ON p.id = s.project_id
        LEFT JOIN kols k         ON k.id = s.kol_id
        LEFT JOIN staff st       ON st.id = s.staff_id
        LEFT JOIN users u        ON u.id = st.user_id
        WHERE {' AND '.join(where)}
        ORDER BY s.contribution_amount DESC, s.occurred_at DESC, s.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(1000, int(limit or 200)))),
    ).fetchall()

    hydrated_rows: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        if not _source_is_live(conn, data):
            continue
        order_snapshot = (
            _sales_order_snapshot(conn, _int(data.get("source_id")))
            if str(data.get("source_type") or "").lower() == "sales_attribution"
            else None
        )
        hydrated_rows.append({
            "id": _int(data.get("id")),
            "source_id": _int(data.get("source_id")),
            "source_type": data.get("source_type"),
            "evidence_type": data.get("evidence_type"),
            "evidence_ref": data.get("evidence_ref"),
            "contribution_amount": data.get("contribution_amount"),
            "contribution_percent": data.get("contribution_percent"),
            "occurred_at": data.get("occurred_at"),
            "project": (
                {"id": _int(data.get("project_id")), "name": data.get("project_name"), "uid": data.get("project_uid")}
                if data.get("project_id") else None
            ),
            "kol": (
                {"id": _int(data.get("kol_id")), "name": data.get("kol_name"), "platform": data.get("kol_platform")}
                if data.get("kol_id") else None
            ),
            "staff": (
                {"id": _int(data.get("staff_id")), "name": data.get("staff_name"), "email": data.get("staff_email")}
                if data.get("staff_id") else None
            ),
            "snapshot": _safe_load(data.get("snapshot_json")),
            "order_snapshot": order_snapshot,
        })

    result = {
        "value": {
            "id": _int(value_row.get("id")),
            "run_id": _int(value_row.get("run_id")),
            "metric_key": value_row.get("metric_key"),
            "value_numeric": value_row.get("value_numeric"),
            "currency": value_row.get("currency"),
            "unit": value_row.get("unit"),
            "calculation": _safe_load(value_row.get("calculation_json")),
            "source_count": _int(value_row.get("source_count")),
        },
        "run": {
            "uid": value_row.get("run_uid"),
            "period_start": value_row.get("period_start"),
            "period_end": value_row.get("period_end"),
            "scope_type": value_row.get("scope_type"),
            "scope_id": value_row.get("scope_id"),
            "generated_at": value_row.get("generated_at"),
            "definition_version": value_row.get("definition_version"),
            "trigger_source": value_row.get("trigger_source"),
            "generated_by_staff_id": value_row.get("generated_by_staff_id"),
        },
        "rows": hydrated_rows,
        "filtered": {"project_id": project_id, "kol_id": kol_id, "staff_id": staff_id},
        "row_count": len(hydrated_rows),
    }
    if _int(value_row.get("source_count")) > 0 and not hydrated_rows:
        result["empty_reason"] = "source_rows_deleted_or_void"
    return result


# ---------------------------------------------------------------------------
# convenience: drilldown by metric_key against the latest dashboard run
# ---------------------------------------------------------------------------

def drilldown_latest(
    metric_key: str,
    *,
    scope_type: str = "all",
    scope_id: int | None = None,
    limit: int = 200,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Find the latest dashboard run for this scope, then drilldown its value
    for the given metric_key. Returns same shape as drilldown_value.
    Returns empty rows if no run exists yet.
    """
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    effective_staff_id = scope.effective_staff_id(staff, staff_id)
    if project_id:
        scope.assert_project_access(int(project_id), staff)
    lookup_scope_type = scope_type or "all"
    lookup_scope_id = scope_id
    if staff is not None and not scope.can_view_all(staff):
        lookup_scope_type = "staff"
        lookup_scope_id = effective_staff_id
    # Build the lookup query with optional scope_id filter.
    # Kept SQLite/Postgres-portable by avoiding casts in the SQL string.
    scope_clause = ""
    params: list[Any] = [lookup_scope_type, metric_key]
    if lookup_scope_id is not None:
        scope_clause = " AND r.scope_id = ? "
        params.insert(1, int(lookup_scope_id))  # keep order: scope_type, scope_id, metric_key
    row = conn.execute(
        f"""
        SELECT v.id
        FROM vkpi_metric_values v
        INNER JOIN vkpi_metric_runs r ON r.id = v.run_id
        WHERE r.trigger_source='dashboard' AND r.status='ready'
          AND r.scope_type=?
          {scope_clause}
          AND v.metric_key=?
        ORDER BY r.generated_at DESC, v.id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    if not row:
        return {
            "value": None,
            "run": None,
            "rows": [],
            "filtered": {"project_id": project_id, "kol_id": kol_id, "staff_id": effective_staff_id},
            "row_count": 0,
            "empty_reason": "no_run_yet",
        }
    result = drilldown_value(
        int(row["id"]),
        limit=limit,
        project_id=project_id,
        kol_id=kol_id,
        staff_id=effective_staff_id,
        staff=staff,
    )
    if result.get("empty_reason") == "source_rows_deleted_or_void":
        return {
            "value": None,
            "run": None,
            "rows": [],
            "filtered": {"project_id": project_id, "kol_id": kol_id, "staff_id": effective_staff_id},
            "row_count": 0,
            "empty_reason": "source_rows_deleted_or_void",
        }
    return result
