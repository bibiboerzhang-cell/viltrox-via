"""V-KPI on-demand CSV/XLSX/PDF exports."""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from app.db.connection import get_conn
from app.domains import reports
from app.domains import audit
from app.domains.access import scope
from app.domains.projects import workflow
from app.domains.reports.pdf_renderer import store_bytes
from app.domains.reports import ensure_vkpi_reports_schema


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(prefix: str) -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _staff_id(staff: dict[str, Any] | None) -> int:
    return workflow.staff_id(staff) or 0


def _date_filter(filters: dict[str, Any], column: str) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if filters.get("date_from"):
        where.append(f"{column}>=?")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        where.append(f"{column}<=?")
        params.append(filters["date_to"])
    return (" AND " + " AND ".join(where)) if where else "", params


def _rows(export_type: str, filters: dict[str, Any], *, staff: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    if export_type in {"weekly", "projects"}:
        where_parts: list[str] = []
        params: list[Any] = []
        scope_clause, scope_params = scope.project_filter("p", staff, filters.get("staff_id"))
        if scope_clause:
            where_parts.append(scope_clause)
            params.extend(scope_params)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(
            f"""
            SELECT p.id, p.project_uid, p.project_name, p.stage, p.product_sku, p.product_name,
                   p.platform, p.marketplace, p.sample_status, p.tracking_number,
                   p.created_at, p.updated_at, k.channel_name AS kol_name,
                   u.name AS staff_name, u.email AS staff_email
            FROM vkpi_projects p
            LEFT JOIN kols k ON k.id = p.kol_id
            LEFT JOIN staff s ON s.id = p.assigned_staff_id
            LEFT JOIN users u ON u.id = s.user_id
            {where}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT 50000
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"attribution", "finance"}:
        extra, params = _date_filter(filters, "sa.occurred_at")
        scope_clause, scope_params = scope.row_staff_filter("sa", staff, filters.get("staff_id"), domain="finance" if export_type == "finance" else "general")
        if scope_clause:
            extra += f" AND {scope_clause}"
            params.extend(scope_params)
        rows = conn.execute(
            f"""
            SELECT sa.*, p.project_name, k.channel_name AS kol_name, u.name AS staff_name, u.email AS staff_email
            FROM vkpi_sales_attributions sa
            LEFT JOIN vkpi_projects p ON p.id = sa.project_id
            LEFT JOIN kols k ON k.id = sa.kol_id
            LEFT JOIN staff s ON s.id = sa.staff_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE 1=1 {extra}
            ORDER BY sa.occurred_at DESC, sa.id DESC
            LIMIT 50000
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"cost", "costs"}:
        extra, params = _date_filter(filters, "cl.incurred_at")
        scope_clause, scope_params = scope.row_staff_filter("cl", staff, filters.get("staff_id"), domain="cost")
        if scope_clause:
            extra += f" AND {scope_clause}"
            params.extend(scope_params)
        rows = conn.execute(
            f"""
            SELECT cl.*, p.project_name, k.channel_name AS kol_name, u.name AS staff_name, u.email AS staff_email
            FROM vkpi_cost_ledger cl
            LEFT JOIN vkpi_projects p ON p.id = cl.project_id
            LEFT JOIN kols k ON k.id = cl.kol_id
            LEFT JOIN staff s ON s.id = cl.staff_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE 1=1 {extra}
            ORDER BY cl.incurred_at DESC, cl.id DESC
            LIMIT 50000
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"kpi_ledger", "staff_kpi"}:
        where_parts: list[str] = []
        params: list[Any] = []
        scope_clause, scope_params = scope.row_staff_filter("", staff, filters.get("staff_id"))
        if scope_clause:
            where_parts.append(scope_clause)
            params.extend(scope_params)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(f"SELECT * FROM vkpi_kpi_ledger {where} ORDER BY ledger_date DESC, id DESC LIMIT 50000", tuple(params)).fetchall()
        return [dict(row) for row in rows]
    if export_type == "kols":
        where_parts: list[str] = []
        params: list[Any] = []
        scoped_staff_id = scope.effective_staff_id(staff, filters.get("staff_id"))
        if scoped_staff_id:
            where_parts.append("(k.assigned_staff_id=? OR k.created_by_staff_id=? OR c.staff_id=?)")
            params.extend([scoped_staff_id, scoped_staff_id, scoped_staff_id])
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(
            f"""
            SELECT k.*, c.status AS claim_status, c.staff_id AS claim_staff_id, c.claimed_at, c.released_at
            FROM kols k
            LEFT JOIN vkpi_kol_claims c ON c.kol_id = k.id AND c.status='active'
            {where}
            ORDER BY k.id DESC
            LIMIT 50000
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    raise ValueError("unsupported export_type")


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    fields = sorted({key for row in rows for key in row.keys()}) or ["empty"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fields})
    return output.getvalue().encode("utf-8-sig")


def _xlsx_bytes(rows: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "V-KPI Export"
    fields = sorted({key for row in rows for key in row.keys()}) or ["empty"]
    ws.append(fields)
    for row in rows:
        ws.append([row.get(key, "") for key in fields])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def create_export(*, export_format: str, payload: dict[str, Any], staff: dict[str, Any] | None = None, request_meta: dict[str, str] | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    fmt = str(export_format or "").lower().strip()
    if fmt not in {"csv", "xlsx", "pdf"}:
        raise ValueError("unsupported export format")
    export_type = str(payload.get("report_type") or payload.get("reportType") or payload.get("export_type") or "weekly").lower().strip()
    actor_id = _staff_id(staff)
    export_uid = _uid(f"export-{fmt}")
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_export_jobs
            (export_uid, requested_by_staff_id, export_type, file_format, filters_json, status, triggered_at, expires_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (export_uid, actor_id or None, export_type, fmt, _json(payload), "running", _utcnow(), (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    row = conn.execute("SELECT id FROM vkpi_export_jobs WHERE export_uid=?", (export_uid,)).fetchone()
    export_id = int(row["id"]) if row else 0
    try:
        if fmt == "pdf":
            report = reports.generate_weekly_report(period_days=int(payload.get("period_days") or 7), staff=staff, filters=payload, render_pdf=True)
            file_path = str(report.get("file", {}).get("file_path") or "")
            file_size = int(report.get("file", {}).get("file_size_bytes") or 0)
            sha = str(report.get("file", {}).get("sha256_hex") or "")
            download_url = str(report.get("download_url") or report.get("downloadUrl") or "")
            row_count = len(report.get("context", {}).get("projects") or [])
        else:
            rows = _rows(export_type, payload, staff=staff)
            content = _csv_bytes(rows) if fmt == "csv" else _xlsx_bytes(rows)
            stored = store_bytes(content, filename=f"{export_uid}.{fmt}")
            file_path = stored["file_path"]
            file_size = int(stored["file_size_bytes"])
            sha = stored["sha256_hex"]
            download_url = f"/api/admin/vkpi/exports/{export_id}/download"
            row_count = len(rows)
        conn.execute(
            """
            UPDATE vkpi_export_jobs
            SET status='ready', file_path=?, download_url=?, row_count=?, completed_at=?
            WHERE id=?
            """,
            (file_path, download_url, row_count, _utcnow(), export_id),
        )
        conn.commit()
        audit.log_export(
            staff_id=actor_id,
            export_kind="pdf_report" if fmt == "pdf" else fmt,
            export_target=export_type,
            filters=payload,
            row_count=row_count,
            file_size_bytes=file_size,
            file_sha256=sha,
            download_url=download_url,
            contains_pii=export_type in {"kols", "attribution", "finance"},
            contains_financial=export_type in {"weekly", "attribution", "finance", "cost", "costs"},
            ip=(request_meta or {}).get("ip", ""),
            user_agent=(request_meta or {}).get("user_agent", ""),
            metric_keys=["views", "sales", "cost", "kpi"],
        )
        return {"export_id": export_id, "exportId": export_id, "export_uid": export_uid, "status": "ready", "download_url": download_url, "downloadUrl": download_url, "row_count": row_count}
    except Exception as exc:
        conn.execute("UPDATE vkpi_export_jobs SET status='failed', error_message=? WHERE id=?", (str(exc)[:500], export_id))
        conn.commit()
        raise


def export_file(export_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    row = get_conn().execute("SELECT * FROM vkpi_export_jobs WHERE id=?", (int(export_id),)).fetchone()
    if not row or not str(row["file_path"] or ""):
        raise LookupError("export file not found")
    item = dict(row)
    requested_by = int(item.get("requested_by_staff_id") or 0)
    if requested_by:
        scope.assert_staff_access(requested_by, staff, domain="export")
    elif not scope.can_view_all(staff, domain="export"):
        raise scope.ScopeDenied("export scope denied")
    return item


def list_exports(limit: int = 50, staff_id: int | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reports_schema()
    scoped_staff_id = scope.effective_staff_id(staff, staff_id, domain="export")
    where = "WHERE requested_by_staff_id=?" if scoped_staff_id else ""
    params: tuple[Any, ...] = (int(scoped_staff_id), max(1, min(200, int(limit or 50)))) if scoped_staff_id else (max(1, min(200, int(limit or 50))),)
    rows = get_conn().execute(f"SELECT * FROM vkpi_export_jobs {where} ORDER BY triggered_at DESC, id DESC LIMIT ?", params).fetchall()
    return {"exports": [dict(row) for row in rows]}
