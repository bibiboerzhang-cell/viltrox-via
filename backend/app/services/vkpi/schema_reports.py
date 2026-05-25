"""Compatibility shim for the reports domain schema guard."""
from __future__ import annotations

from app.domains.reports.schema import ensure_vkpi_reports_schema

__all__ = ["ensure_vkpi_reports_schema"]
