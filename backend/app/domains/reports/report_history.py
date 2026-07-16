"""History projection for generated V-KPI reports."""
from __future__ import annotations

from typing import Any, Mapping


def report_history_item_impl(
    report: dict[str, Any], namespace: Mapping[str, Any]
) -> dict[str, Any]:
    _archive_metadata = namespace['_archive_metadata']
    _load_json = namespace['_load_json']
    _truth_invalidation_metadata = namespace['_truth_invalidation_metadata']
    public_report_request = namespace['public_report_request']

    metadata = _load_json(report.get("metadata_json"))
    contract = metadata.get("_report_contract") if isinstance(metadata, dict) else {}
    if not isinstance(contract, dict):
        contract = {}
    archive = _archive_metadata(report)
    truth_invalidation = _truth_invalidation_metadata(report)
    request = public_report_request(metadata)
    if not request:
        request = public_report_request(contract.get("request"))
    return {
        key: report.get(key)
        for key in (
            "id",
            "report_uid",
            "report_type",
            "period_start",
            "period_end",
            "scope_type",
            "scope_id",
            "metric_run_id",
            "triggered_by_staff_id",
            "triggered_at",
            "status",
            "summary_text",
        )
    } | {
        "archived_at": archive.get("archived_at") or truth_invalidation.get("invalidated_at"),
        "archived_by_staff_id": archive.get("archived_by_staff_id"),
        "archive_reason": archive.get("reason") or truth_invalidation.get("reason") or "",
        "schema_version": contract.get("schema_version"),
        "data_status": "unavailable" if truth_invalidation else contract.get("data_status"),
        "truth_invalidated": bool(truth_invalidation),
        "truth_invalidation_reason": truth_invalidation.get("reason") or "",
        "truth_invalidated_at": truth_invalidation.get("invalidated_at"),
        "request": request,
        "period": request.get("period"),
        "period_days": request.get("period_days"),
        "language": request.get("language"),
        "sections": request.get("sections") or [],
        "format": request.get("format"),
        "request_scope": request.get("scope"),
    }
