"""V-KPI reports and export routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.api.dependencies.perms import require_tab
from app.domains import reports
from app.domains import audit
from app.domains.access import scope
from app.domains.reports import export_jobs as exports
from app.domains.projects.workflow import staff_id as resolve_staff_id

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-reports"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.post("/reports/weekly/generate")
def generate_weekly_report(body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        result = reports.generate_weekly_report(
            period_days=int(payload.get("period_days") or 7),
            staff=staff,
            filters=payload,
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
            "status": result["status"],
            "downloadUrl": result.get("download_url", ""),
            "download_url": result.get("download_url", ""),
            "summary": result.get("summary_text", ""),
            "source_appendix_metric_count": len(source_appendix),
            "kpi_appendix_source_count": len(kpi_rows),
            "kpi_appendix_formula_count": sum(1 for row in kpi_rows if row.get("formula")),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/exports/{export_format}")
def create_export(export_format: str, request: Request, body: dict | None = None, staff=Depends(require_tab("vkpi", "read"))):
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/reports")
def list_reports(limit: int = Query(default=50, ge=1, le=200), report_type: str = "", staff=Depends(require_tab("vkpi", "read"))):
    try:
        return reports.list_reports(limit=limit, report_type=report_type, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


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
    path = Path(str(file_info.get("file_path") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="report file missing")
    audit.log_sensitive_access(
        staff_id=resolve_staff_id(staff),
        action_type="download_report",
        resource_type="report",
        resource_id=str(report_run_id),
        page_path=str(request.url.path),
        ip=getattr(getattr(request, "client", None), "host", "") or "",
        user_agent=str(request.headers.get("user-agent") or ""),
        metadata={"format": file_format, "file_path": str(path)},
    )
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="report_download",
        target_type="report",
        target_id=report_run_id,
        detail=f"download {file_format} report",
        metadata={"file_path": str(path), "format": file_format},
    )
    return FileResponse(path, filename=path.name, media_type="application/pdf" if file_format == "pdf" else "application/octet-stream")


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
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    path = Path(str(file_info.get("file_path") or ""))
    if not path.exists():
        raise HTTPException(status_code=404, detail="export file missing")
    file_format = str(file_info.get("file_format") or "").lower()
    media = "text/csv" if file_format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if file_format == "xlsx" else "application/pdf"
    audit.log_sensitive_access(
        staff_id=resolve_staff_id(staff),
        action_type="download_export",
        resource_type="export",
        resource_id=str(export_id),
        page_path=str(request.url.path),
        ip=getattr(getattr(request, "client", None), "host", "") or "",
        user_agent=str(request.headers.get("user-agent") or ""),
        metadata={"format": file_format, "file_path": str(path), "export_type": file_info.get("export_type")},
    )
    audit.log_business_event(
        staff_id=resolve_staff_id(staff),
        action_type="export_download",
        target_type="export",
        target_id=export_id,
        detail=f"download {file_format} export",
        metadata={"file_path": str(path), "format": file_format, "export_type": file_info.get("export_type")},
    )
    return FileResponse(path, filename=path.name, media_type=media)
