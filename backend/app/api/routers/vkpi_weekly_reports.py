"""
backend/app/api/routers/vkpi_weekly_reports_router.py

P1.6 API endpoints.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_permission
from app.domains.reports import weekly_generator as weekly_report_generator
from app.services.vkpi.p1_compat import admin_router_prefix


router = APIRouter(prefix=admin_router_prefix("weekly-reports"), tags=["vkpi-weekly-reports"])


@router.post("/generate-for-staff/{staff_id}")
def api_generate_for_staff(
    staff_id: int,
    period_end: date | None = Query(None),
    staff: dict = Depends(require_permission("vkpi.weekly_reports.generate")),
) -> dict[str, Any]:
    """Generate weekly reports for one staff member."""
    if period_end is None:
        period_end = date.today()
    from datetime import timedelta
    return weekly_report_generator.generate_for_staff(
        staff_id=staff_id,
        period_start=period_end - timedelta(days=7),
        period_end=period_end,
    )


@router.post("/generate-all")
def api_generate_all(
    period_end: date | None = Query(None),
    staff: dict = Depends(require_permission("vkpi.weekly_reports.generate_all")),
) -> dict[str, Any]:
    """Generate weekly reports for all active staff (1 leader + 7 employees = 23 reports)."""
    return weekly_report_generator.generate_for_all_staff(period_end=period_end)


@router.get("/list")
def api_list(
    staff_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.weekly_reports.read")),
) -> dict[str, Any]:
    """List recent weekly reports."""
    return weekly_report_generator.list_reports(
        staff_id=staff_id,
        limit=limit,
    )


@router.get("/{report_id}")
def api_get_report(
    report_id: int,
    staff: dict = Depends(require_permission("vkpi.weekly_reports.read")),
) -> dict[str, Any]:
    """Get full report by ID."""
    from app.db.connection import get_conn
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, staff_id, layer, template_key, period_start, period_end,
               title, body_md, llm_provider, status, cost_cents, generated_at
        FROM vkpi_weekly_reports WHERE id = ?
        """,
        (report_id,),
    ).fetchone()
    if not row:
        return {"status": "fail", "error": "report not found"}
    return dict(row)
