"""V-KPI metric lineage public entrypoints.

This compatibility module keeps the existing import surface stable while the
heavy compute and persistence logic lives in focused modules.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import scope as scope_service
from app.services.vkpi.metric_definitions import DEFINITION_VERSION, METRICS, is_known_metric
from app.services.vkpi.metric_lineage_common import _generate_run_uid, _int, _json, _safe_json, _utcnow, _window_bounds
from app.services.vkpi.metric_lineage_compute import _compute_derived, _compute_metric
from app.services.vkpi.metric_lineage_store import (
    _persist_derived_sources,
    _persist_sources,
    _persist_value,
    get_run,
    latest_dashboard_run,
    list_runs,
)
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_lineage import ensure_vkpi_lineage_schema

def _existing_staff_id(conn: Any, value: Any) -> int | None:
    sid = _int(value)
    if sid <= 0:
        return None
    row = conn.execute("SELECT id FROM staff WHERE id=?", (sid,)).fetchone()
    return sid if row else None

def generate_run(
    *,
    period_days: int = 30,
    scope_type: str = "all",
    scope_id: int | None = None,
    trigger_source: str = "dashboard",
    generated_by_staff_id: int | None = None,
    metrics: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate one full metric run snapshot.

    Args:
      period_days: rolling window in days (e.g. 7 for weekly, 30 for monthly).
      scope_type: 'all' | 'staff' | 'project' | 'kol' | 'product'
      scope_id:   when scope_type != 'all', the bound entity id.
      trigger_source: 'dashboard' | 'weekly_report' | 'manual_export' | 'cron'
      generated_by_staff_id: who triggered this (None for cron).
      metrics: subset of metric_keys to compute. None = all known metrics.
      metadata: arbitrary filters/context to persist with the run.

    Returns:
      {"run_id": int, "run_uid": str, "metric_count": int, "source_count": int}
    """
    ensure_vkpi_schema()
    ensure_vkpi_lineage_schema()

    target_metrics = [k for k in (metrics or list(METRICS.keys())) if is_known_metric(k)]
    if not target_metrics:
        raise ValueError("no valid metrics requested")

    period_start, period_end = _window_bounds(period_days)
    run_uid = _generate_run_uid()
    now = _utcnow()
    conn = get_conn()
    generated_by_staff_id = _existing_staff_id(conn, generated_by_staff_id)

    # 1. write run header (status='pending' until all values written)
    conn.execute(
        """
        INSERT INTO vkpi_metric_runs (
            run_uid, period_start, period_end, scope_type, scope_id,
            trigger_source, generated_by_staff_id, generated_at,
            definition_version, status, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_uid,
            period_start,
            period_end,
            str(scope_type or "all"),
            scope_id,
            str(trigger_source or "dashboard"),
            generated_by_staff_id,
            now,
            DEFINITION_VERSION,
            "pending",
            _json(metadata),
        ),
    )
    row = conn.execute("SELECT id FROM vkpi_metric_runs WHERE run_uid=?", (run_uid,)).fetchone()
    run_id = int(row["id"]) if row else 0
    if not run_id:
        raise RuntimeError("failed to insert metric run header")

    # 2. compute + write each metric
    total_sources = 0
    metric_count = 0
    computed: dict[str, dict[str, Any]] = {}

    for metric_key in target_metrics:
        # derived metrics (net_contribution, roi) need other metrics computed first.
        # we run primaries first, then derived.
        if metric_key in ("net_contribution", "roi"):
            continue
        result = _compute_metric(
            conn,
            metric_key=metric_key,
            period_start=period_start,
            period_end=period_end,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        value_id = _persist_value(conn, run_id=run_id, metric_key=metric_key, result=result, now=now)
        _persist_sources(conn, value_id=value_id, sources=result.get("sources") or [], total=result.get("value_numeric") or 0, now=now)
        computed[metric_key] = result
        total_sources += len(result.get("sources") or [])
        metric_count += 1

    # 3. derived metrics
    for metric_key in target_metrics:
        if metric_key not in ("net_contribution", "roi"):
            continue
        result = _compute_derived(metric_key, computed)
        value_id = _persist_value(conn, run_id=run_id, metric_key=metric_key, result=result, now=now)
        # derived metrics reference the underlying value rows
        _persist_derived_sources(conn, value_id=value_id, computed=computed, metric_key=metric_key, now=now)
        metric_count += 1

    # 4. mark run ready
    conn.execute(
        "UPDATE vkpi_metric_runs SET status=? WHERE id=?",
        ("ready", run_id),
    )
    conn.commit()

    return {
        "run_id": run_id,
        "run_uid": run_uid,
        "definition_version": DEFINITION_VERSION,
        "period_start": period_start,
        "period_end": period_end,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "trigger_source": trigger_source,
        "metric_count": metric_count,
        "source_count": total_sources,
    }

def dashboard_metrics(
    *,
    period_days: int = 7,
    staff: dict[str, Any] | None = None,
    staff_id: int | None = None,
    generated_by_staff_id: int | None = None,
) -> dict[str, Any]:
    """Generate a current dashboard snapshot and return UI-ready metric values.

    Dashboard reads should be lineage-first: every visible metric carries the
    metric_value_id used by the Evidence Drawer. We intentionally generate a
    fresh run for the selected window so deleted projects / voided costs do not
    leak from old snapshots into the current operational view.
    """
    scoped_staff_id = scope_service.effective_staff_id(staff, staff_id)
    scope_type = "staff" if scoped_staff_id else "all"
    run_meta = generate_run(
        period_days=max(1, min(180, int(period_days or 7))),
        scope_type=scope_type,
        scope_id=scoped_staff_id,
        trigger_source="dashboard",
        generated_by_staff_id=generated_by_staff_id,
        metadata={"lineage_first": True},
    )
    run = get_run(int(run_meta["run_id"]), staff=staff)
    values = []
    for row in run.get("values") or []:
        item = dict(row)
        metric_key = str(item.get("metric_key") or "")
        values.append({
            "metric_key": metric_key,
            "metricValueId": _int(item.get("id")),
            "metric_value_id": _int(item.get("id")),
            "value_numeric": item.get("value_numeric"),
            "currency": item.get("currency") or "",
            "unit": item.get("unit") or "",
            "source_count": _int(item.get("source_count")),
            "calculation": _safe_json(item.get("calculation_json")),
            "drilldown_url": f"/api/admin/vkpi/lineage/values/{_int(item.get('id'))}/drilldown",
        })
    return {
        "run": run.get("run") or {},
        "metrics": values,
    }

__all__ = [
    "generate_run",
    "dashboard_metrics",
    "get_run",
    "latest_dashboard_run",
    "list_runs",
]
