"""Reports domain facade."""
from __future__ import annotations

from app.domains.reports.reports import generate_weekly_report, list_reports, report_file
from app.domains.reports.schema import ensure_vkpi_reports_schema

__all__ = [
    "ensure_vkpi_reports_schema",
    "generate_weekly_report",
    "list_reports",
    "report_file",
]
