"""Persistence and read helpers for V-KPI metric lineage snapshots."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope as scope_service
from app.domains.lineage.common import _float, _int, _json
from app.domains.lineage.schema import ensure_vkpi_lineage_schema

def _persist_value(conn: Any, *, run_id: int, metric_key: str, result: dict[str, Any], now: str) -> int:
    conn.execute(
        """
        INSERT INTO vkpi_metric_values (
            run_id, metric_key, value_numeric, value_text, currency, unit,
            calculation_json, source_count, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(run_id),
            metric_key,
            _float(result.get("value_numeric")),
            "",
            str(result.get("currency") or ""),
            str(result.get("unit") or ""),
            _json(result.get("calculation")),
            len(result.get("sources") or []),
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM vkpi_metric_values WHERE run_id=? AND metric_key=? ORDER BY id DESC LIMIT 1",
        (int(run_id), metric_key),
    ).fetchone()
    return int(row["id"]) if row else 0

def _persist_sources(conn: Any, *, value_id: int, sources: list[dict[str, Any]], total: float, now: str) -> None:
    if not value_id or not sources:
        return
    total_f = _float(total) or 0.0
    for src in sources:
        amount = _float(src.get("contribution_amount"))
        percent = round((amount / total_f) * 100, 4) if total_f else 0.0
        conn.execute(
            """
            INSERT INTO vkpi_metric_sources (
                metric_value_id, source_type, source_id, contribution_amount,
                contribution_percent, evidence_type, evidence_ref, project_id,
                kol_id, staff_id, occurred_at, snapshot_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(value_id),
                str(src.get("source_type") or ""),
                _int(src.get("source_id")),
                amount,
                percent,
                str(src.get("evidence_type") or ""),
                str(src.get("evidence_ref") or ""),
                src.get("project_id"),
                src.get("kol_id"),
                src.get("staff_id"),
                src.get("occurred_at"),
                _json(src.get("snapshot")),
                now,
            ),
        )

def _persist_derived_sources(
    conn: Any, *, value_id: int, computed: dict[str, dict[str, Any]], metric_key: str, now: str
) -> None:
    """For derived metrics, link back to the primary metric_value rows."""
    if not value_id:
        return
    primary_keys = ("gmv", "cost")
    inserted = 0
    for key in primary_keys:
        primary = computed.get(key)
        if not primary:
            continue
        # find the underlying value_id of the primary metric in same run
        row = conn.execute(
            """
            SELECT id FROM vkpi_metric_values
            WHERE run_id = (SELECT run_id FROM vkpi_metric_values WHERE id = ?)
              AND metric_key = ?
            ORDER BY id DESC LIMIT 1
            """,
            (int(value_id), key),
        ).fetchone()
        if not row:
            continue
        primary_value_id = int(row["id"])
        amount = _float(primary.get("value_numeric"))
        if metric_key == "net_contribution" and key == "cost":
            amount = -abs(amount)
        conn.execute(
            """
            INSERT INTO vkpi_metric_sources (
                metric_value_id, source_type, source_id, contribution_amount,
                contribution_percent, evidence_type, evidence_ref, project_id,
                kol_id, staff_id, occurred_at, snapshot_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(value_id),
                "metric_value",
                primary_value_id,
                amount,
                100.0,
                "derived_input",
                f"{metric_key}<-{key}",
                None,
                None,
                None,
                now,
                _json({"source_metric": key, "value": primary.get("value_numeric")}),
                now,
            ),
        )
        inserted += 1
    if inserted:
        conn.execute(
            "UPDATE vkpi_metric_values SET source_count=? WHERE id=?",
            (inserted, int(value_id)),
        )

def get_run(run_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return run header + all values + per-value source counts."""
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    header = conn.execute("SELECT * FROM vkpi_metric_runs WHERE id=?", (int(run_id),)).fetchone()
    if not header:
        raise LookupError("metric run not found")
    header_data = dict(header)
    if staff is not None and not scope_service.can_view_all(staff):
        actor = scope_service.actor_staff_id(staff)
        run_scope_type = str(header_data.get("scope_type") or "")
        run_scope_id = _int(header_data.get("scope_id"))
        generated_by = _int(header_data.get("generated_by_staff_id"))
        if not actor or not ((run_scope_type == "staff" and run_scope_id == actor) or generated_by == actor):
            raise scope_service.ScopeDenied("metric run scope denied")
    values = conn.execute(
        "SELECT * FROM vkpi_metric_values WHERE run_id=? ORDER BY metric_key",
        (int(run_id),),
    ).fetchall()
    return {
        "run": header_data,
        "values": [dict(row) for row in values],
    }

def latest_dashboard_run(scope_type: str = "all", scope_id: int | None = None) -> dict[str, Any]:
    """Return the most recent dashboard-trigger run for the given scope."""
    ensure_vkpi_lineage_schema()
    conn = get_conn()
    where = "WHERE trigger_source='dashboard' AND status='ready' AND scope_type=?"
    params: list[Any] = [scope_type or "all"]
    if scope_id is not None:
        where += " AND scope_id=?"
        params.append(int(scope_id))
    row = conn.execute(
        f"SELECT * FROM vkpi_metric_runs {where} ORDER BY generated_at DESC LIMIT 1",
        tuple(params),
    ).fetchone()
    if not row:
        return {}
    return get_run(int(row["id"]))

def list_runs(
    *,
    trigger_source: str | None = None,
    scope_type: str | None = None,
    limit: int = 20,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List recent runs (for an Audit / Reports history page)."""
    ensure_vkpi_lineage_schema()
    where: list[str] = []
    params: list[Any] = []
    if trigger_source:
        where.append("trigger_source=?")
        params.append(trigger_source)
    if scope_type:
        where.append("scope_type=?")
        params.append(scope_type)
    if staff is not None and not scope_service.can_view_all(staff):
        actor = scope_service.actor_staff_id(staff)
        if not actor:
            return {"runs": []}
        where.append("((scope_type='staff' AND scope_id=?) OR generated_by_staff_id=?)")
        params.extend([actor, actor])
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_metric_runs {clause} ORDER BY generated_at DESC LIMIT ?",
        (*params, max(1, min(200, int(limit or 20)))),
    ).fetchall()
    return {"runs": [dict(row) for row in rows]}
