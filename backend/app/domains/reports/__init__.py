"""Reports domain facade."""
from __future__ import annotations

from app.domains.reports.contracts import (
    DataStatus,
    ReportMetricSpec,
    ReportMetricValue,
    ReportSpec,
    WEEKLY_REPORT_SPEC,
)
from app.domains.reports.model_policy import (
    REPORT_CHALLENGER_MODEL,
    REPORT_JUDGE_CANDIDATES,
    REPORT_PRIMARY_MODEL,
    ReportModelDecision,
    ReportSourceSample,
    evaluate_report_model_policy,
)
from app.domains.reports.reports import (
    archive_report,
    generate_weekly_report,
    list_reports,
    record_report_download,
    report_file,
    rollback_current_report_transaction,
    restore_report,
)
from app.domains.reports.pdf_renderer import (
    OpenedStoredFile,
    open_stored_file,
    remove_stored_file,
    render_and_store_pdf,
    render_report_html,
    resolve_stored_path,
    store_bytes,
    verify_opened_file,
)
from app.domains.reports.schema import ensure_vkpi_reports_schema
from app.domains.reports.weekly_generator import ensure_vkpi_weekly_reports_schema

__all__ = [
    "DataStatus",
    "ReportMetricSpec",
    "ReportMetricValue",
    "ReportModelDecision",
    "ReportSpec",
    "ReportSourceSample",
    "REPORT_CHALLENGER_MODEL",
    "REPORT_JUDGE_CANDIDATES",
    "REPORT_PRIMARY_MODEL",
    "WEEKLY_REPORT_SPEC",
    "OpenedStoredFile",
    "archive_report",
    "ensure_vkpi_reports_schema",
    "ensure_vkpi_weekly_reports_schema",
    "evaluate_report_model_policy",
    "generate_weekly_report",
    "list_reports",
    "open_stored_file",
    "record_report_download",
    "remove_stored_file",
    "render_and_store_pdf",
    "render_report_html",
    "resolve_stored_path",
    "report_file",
    "rollback_current_report_transaction",
    "restore_report",
    "store_bytes",
    "verify_opened_file",
]
