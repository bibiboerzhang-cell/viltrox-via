"""Data Quality domain facade."""

from app.domains.data_quality.actions import act_on_issue
from app.domains.data_quality.checks import list_issues
from app.domains.data_quality.common import ensure_data_quality_schema
from app.domains.data_quality.service import list_quality_issues

__all__ = ["act_on_issue", "ensure_data_quality_schema", "list_issues", "list_quality_issues"]
