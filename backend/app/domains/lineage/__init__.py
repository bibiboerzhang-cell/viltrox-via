"""Metric lineage domain facade."""
from __future__ import annotations

from app.domains.lineage.definitions import DEFINITION_VERSION, METRICS, is_known_metric
from app.domains.lineage.schema import ensure_vkpi_lineage_schema
from app.domains.lineage.service import dashboard_metrics, generate_run, get_run, latest_dashboard_run, list_runs

__all__ = [
    "DEFINITION_VERSION",
    "METRICS",
    "dashboard_metrics",
    "ensure_vkpi_lineage_schema",
    "generate_run",
    "get_run",
    "is_known_metric",
    "latest_dashboard_run",
    "list_runs",
]
