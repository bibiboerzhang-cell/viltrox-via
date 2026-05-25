"""Read-only Data Quality domain service."""
from __future__ import annotations

from typing import Any

from app.domains.data_quality import checks


def list_quality_issues(*, limit: int = 100, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the current data-quality issue summary without triggering remediation."""
    return checks.list_issues(limit=limit, staff=staff)
