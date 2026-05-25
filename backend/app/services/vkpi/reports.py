"""Compatibility shim for the reports domain."""
from __future__ import annotations

from app.domains.reports.reports import generate_weekly_report, list_reports, report_file

__all__ = ["generate_weekly_report", "list_reports", "report_file"]
