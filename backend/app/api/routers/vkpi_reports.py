"""V-KPI reports and export routes."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains import reports
from app.domains import audit
from app.domains.access import scope
from app.domains.reports.contracts import ReportContractError
from app.domains.reports import export_jobs as exports
from app.domains.projects.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-reports"])
logger = get_logger(__name__)


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _contract_422(exc: ReportContractError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.as_detail())


class _StoredFileStreamingResponse(StreamingResponse):
    """Close the validated file even when ASGI streaming is cancelled."""

    def __init__(self, opened: reports.OpenedStoredFile, **kwargs):
        self._opened = opened
        super().__init__(opened.iter_bytes(), **kwargs)

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._opened.close()


def _stream_download(opened: reports.OpenedStoredFile, *, media_type: str) -> StreamingResponse:
    """Stream the exact descriptor that passed no-follow/fstat validation."""
    filename = opened.path.name
    encoded = quote(filename)
    if encoded == filename:
        disposition = f'attachment; filename="{filename}"'
    else:
        disposition = f"attachment; filename*=utf-8''{encoded}"
    return _StoredFileStreamingResponse(
        opened,
        media_type=media_type,
        headers={
            "content-disposition": disposition,
            "content-length": str(opened.size),
        },
    )


def _open_download(file_info: dict, *, missing_detail: str) -> reports.OpenedStoredFile:
    opened: reports.OpenedStoredFile | None = None
    try:
        opened = reports.open_stored_file(str(file_info.get("file_path") or ""))
        reports.verify_opened_file(
            opened,
            expected_size=file_info.get("file_size_bytes"),
            expected_sha256=str(file_info.get("sha256_hex") or ""),
        )
        return opened
    except (OSError, ValueError) as exc:
        if opened is not None:
            opened.close()
        # Do not reveal whether the DB path was absent, outside the root, a
        # symlink, a non-regular file, or failed its stored integrity metadata.
        raise HTTPException(status_code=404, detail=missing_detail) from exc


def _audit_download_best_effort(*, sensitive: dict, business: dict) -> None:
    """Do not turn a validated file into a failed download on audit outage."""
    try:
        audit.log_sensitive_access(**sensitive)
        audit.log_business_event(**business)
    except Exception as exc:
        # Audit helpers use the request DB connection.  A PostgreSQL error may
        # leave it aborted even though the download/count transaction committed.
        reports.rollback_current_report_transaction()
        logger.error("report download audit pending retry | error=%s", type(exc).__name__)


@router.post("/reports/weekly/generate")
def generate_weekly_report(body: dict | None = None, staff=Depends(require_manager_tab("vkpi", "write"))):
    try:
        result = reports.generate_weekly_report(
            staff=staff,
            filters=body,
            render_pdf=True,
        )
        context = result.get("context") or {}
        kpi_rows = ((context.get("kpi_appendix") or {}).get("source_rows") or [])
        source_appendix = context.get("source_appendix") or []
        return {
            "reportId": result["report_uid"],
            "report_id": result["report_uid"],
            "reportRunId": result["report_run_id"],
            "report_run_id": result["report_run_id"],
            "reportUid": result["report_uid"],
            "report_uid": result["report_uid"],
            "reportType": result.get("report_type", "weekly"),
            "report_type": result.get("report_type", "weekly"),
            "periodStart": result.get("period_start", ""),
            "period_start": result.get("period_start", ""),
            "periodEnd": result.get("period_end", ""),
            "period_end": result.get("period_end", ""),
            "status": result["status"],
            "downloadUrl": result.get("download_url", ""),
            "download_url": result.get("download_url", ""),
            "summary": result.get("summary_text", ""),
            "summary_text": result.get("summary_text", ""),
            "dataStatus": result.get("data_status", ""),
            "data_status": result.get("data_status", ""),
            "request": result.get("request") or {},
            "context": context,
            "source_appendix_metric_count": len(source_appendix),
            "kpi_appendix_source_count": len(kpi_rows),
            "kpi_appendix_formula_count": sum(1 for row in kpi_rows if row.get("formula")),
        }
    except ReportContractError as exc:
        raise _contract_422(exc) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except Exception as exc:
        logger.exception("weekly report generation failed")
        raise HTTPException(status_code=500, detail="report generation failed") from exc


@router.post("/exports/{export_format}")
def create_export(export_format: str, request: Request, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    clean_format = str(export_format or "").strip().lower()
    if clean_format not in {"pdf", "csv", "xlsx"}:
        raise HTTPException(status_code=400, detail="unsupported export format")
    try:
        return exports.create_export(
            export_format=clean_format,
            payload=body or {},
            staff=staff,
            request_meta={
                "ip": getattr(getattr(request, "client", None), "host", "") or "",
                "user_agent": str(request.headers.get("user-agent") or ""),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except Exception as exc:
        logger.exception("report export failed")
        raise HTTPException(status_code=500, detail="export failed") from exc


@router.get("/reports")
def list_reports(
    limit: int = Query(default=50, ge=1, le=200),
    report_type: str = "",
    archived: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return reports.list_reports(limit=limit, report_type=report_type, archived=archived, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.delete("/reports/{report_run_id}")
def archive_report(
    report_run_id: int,
    body: dict | None = None,
    staff=Depends(require_tab("vkpi", "write")),
):
    try:
        result = reports.archive_report(
            report_run_id,
            staff=staff,
            reason=str((body or {}).get("reason") or "user_archived"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="report_archive",
        target_type="report",
        target_id=report_run_id,
        detail="archive report",
        metadata={"archive_reason": result.get("reason") or "user_archived"},
    )
    return result


@router.post("/reports/{report_run_id}/restore")
def restore_report(report_run_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        result = reports.restore_report(report_run_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="report_restore",
        target_type="report",
        target_id=report_run_id,
        detail="restore report",
        metadata={"report_status": result.get("report_status")},
    )
    return result


@router.get("/reports/files/{report_run_id}/download")
def download_report_file(
    request: Request,
    report_run_id: int,
    file_format: str = Query(default="pdf", alias="format"),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        file_info = reports.report_file(report_run_id, file_format=file_format, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    opened = _open_download(file_info, missing_detail="report file missing")
    path = opened.path
    stored_format = str(file_info.get("file_format") or file_format).strip().lower()
    media_type = {
        "pdf": "application/pdf",
        "markdown": "text/markdown; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(stored_format, "application/octet-stream")
    try:
        response = _stream_download(opened, media_type=media_type)
        reports.record_report_download(int(file_info["id"]), staff=staff)
        _audit_download_best_effort(
            sensitive={
                "staff_id": resolve_staff_id(staff),
                "action_type": "download_report",
                "resource_type": "report",
                "resource_id": str(report_run_id),
                "page_path": str(request.url.path),
                "ip": getattr(getattr(request, "client", None), "host", "") or "",
                "user_agent": str(request.headers.get("user-agent") or ""),
                "metadata": {"format": stored_format, "filename": path.name},
            },
            business={
                "staff_id": resolve_staff_id(staff),
                "action_type": "report_download",
                "target_type": "report",
                "target_id": report_run_id,
                "detail": f"download {stored_format} report",
                "metadata": {"filename": path.name, "format": stored_format},
            },
        )
        return response
    except BaseException:
        opened.close()
        raise


@router.get("/exports")
def list_exports(limit: int = Query(default=50, ge=1, le=200), staff_id: int | None = None, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return exports.list_exports(limit=limit, staff_id=staff_id, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/exports/{export_id}/download")
def download_export(request: Request, export_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        file_info = exports.export_file(export_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except exports.ExportExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc) or "export link expired") from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    opened = _open_download(file_info, missing_detail="export file missing")
    path = opened.path
    file_format = str(file_info.get("file_format") or "").lower()
    media = "text/csv" if file_format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if file_format == "xlsx" else "application/pdf"
    try:
        response = _stream_download(opened, media_type=media)
        _audit_download_best_effort(
            sensitive={
                "staff_id": resolve_staff_id(staff),
                "action_type": "download_export",
                "resource_type": "export",
                "resource_id": str(export_id),
                "page_path": str(request.url.path),
                "ip": getattr(getattr(request, "client", None), "host", "") or "",
                "user_agent": str(request.headers.get("user-agent") or ""),
                "metadata": {"format": file_format, "filename": path.name, "export_type": file_info.get("export_type")},
            },
            business={
                "staff_id": resolve_staff_id(staff),
                "action_type": "export_download",
                "target_type": "export",
                "target_id": export_id,
                "detail": f"download {file_format} export",
                "metadata": {"filename": path.name, "format": file_format, "export_type": file_info.get("export_type")},
            },
        )
        return response
    except BaseException:
        opened.close()
        raise
