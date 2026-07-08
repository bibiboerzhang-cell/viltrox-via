"""V-KPI on-demand CSV/XLSX/PDF exports."""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from app.db.connection import get_conn
from app.domains import reports
from app.domains import audit
from app.domains.access import scope
from app.domains.attribution import revenue as attribution_revenue
from app.domains.projects import workflow
from app.domains.reports.pdf_renderer import store_bytes
from app.domains.reports import ensure_vkpi_reports_schema

# 单次导出硬上限。查询按 +1 取,溢出即截断并回报 truncated,避免拉爆内存/文件。
_EXPORT_ROW_LIMIT = 50000
# 电子表格公式注入前缀:以此开头的字符串单元格前置单引号,阻止 =CMD()/DDE 等被求值。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_usd(value: Any) -> float | None:
    """cents(整数)→ USD(两位小数)。None/非数原样为 None(导出留空)。与 PDF _money_cents 同 ÷100 口径。"""
    try:
        return round(int(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def _normalize_money(row: dict[str, Any]) -> dict[str, Any]:
    """把行内所有 *_cents 列换算为 *_usd(÷100),保持列的原顺序不排序。

    kpi_ledger 的 metric_value 当 metric_key 以 _cents 结尾时同样按货币换算,
    并把 metric_key 标注为 *_usd,避免调用方误把 cents 当美元。
    """
    metric_key = row.get("metric_key")
    metric_is_money = isinstance(metric_key, str) and metric_key.endswith("_cents")
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key.endswith("_cents"):
            out[key[:-6] + "_usd"] = _to_usd(value)
        elif key == "metric_value" and metric_is_money:
            out[key] = _to_usd(value)
        else:
            out[key] = value
    if metric_is_money:
        out["metric_key"] = metric_key[:-6] + "_usd"
    return out


def _guard_formula(text: str) -> str:
    return "'" + text if text[:1] in _FORMULA_PREFIXES else text


def _csv_cell(value: Any) -> Any:
    if isinstance(value, str):
        return _guard_formula(value)
    return value


def _xlsx_cell(value: Any) -> Any:
    if isinstance(value, str):
        return _guard_formula(ILLEGAL_CHARACTERS_RE.sub("", value))
    return value


def _fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    """列序=首见顺序(即 SELECT 显式列序,id 等主键在前),不做字母排序。"""
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)
    return fields or ["empty"]


def _uid(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


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
            LIMIT {_EXPORT_ROW_LIMIT + 1}
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"attribution", "finance"}:
        extra, params = _date_filter(filters, "sa.occurred_at")
        # 与页面 list_attributions 同源:剔除已删除项目的归因,导出行数/金额对齐页面。
        extra += f" AND {attribution_revenue._active_project_filter('sa')}"
        scope_clause, scope_params = scope.row_staff_filter("sa", staff, filters.get("staff_id"), domain="finance" if export_type == "finance" else "general")
        if scope_clause:
            extra += f" AND {scope_clause}"
            params.extend(scope_params)
        rows = conn.execute(
            f"""
            SELECT sa.id, sa.source_platform, sa.source_ref, sa.project_id, sa.link_id,
                   sa.kol_id, sa.staff_id, sa.product_sku, sa.order_id, sa.amazon_campaign_id,
                   sa.revenue_cents, sa.commission_cents, sa.currency, sa.attribution_model,
                   sa.confidence, sa.occurred_at, sa.imported_at, sa.created_at,
                   sa.shopify_order_snapshot_id,
                   p.project_name, k.channel_name AS kol_name, u.name AS staff_name, u.email AS staff_email
            FROM vkpi_sales_attributions sa
            LEFT JOIN vkpi_projects p ON p.id = sa.project_id
            LEFT JOIN kols k ON k.id = sa.kol_id
            LEFT JOIN staff s ON s.id = sa.staff_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE 1=1 {extra}
            ORDER BY sa.occurred_at DESC, sa.id DESC
            LIMIT {_EXPORT_ROW_LIMIT + 1}
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"cost", "costs"}:
        extra, params = _date_filter(filters, "cl.incurred_at")
        # 与看板/报表同口径:作废(void)成本不计入导出。
        extra += " AND COALESCE(cl.status, '') <> 'void'"
        scope_clause, scope_params = scope.row_staff_filter("cl", staff, filters.get("staff_id"), domain="cost")
        if scope_clause:
            extra += f" AND {scope_clause}"
            params.extend(scope_params)
        rows = conn.execute(
            f"""
            SELECT cl.id, cl.project_id, cl.kol_id, cl.staff_id, cl.cost_type, cl.amount_cents,
                   cl.currency, cl.status, cl.incurred_at, cl.source_ref, cl.note,
                   cl.created_by_staff_id, cl.created_at, cl.approved_by_staff_id, cl.approved_at,
                   cl.voided_by_staff_id, cl.voided_at, cl.updated_at,
                   p.project_name, k.channel_name AS kol_name, u.name AS staff_name, u.email AS staff_email
            FROM vkpi_cost_ledger cl
            LEFT JOIN vkpi_projects p ON p.id = cl.project_id
            LEFT JOIN kols k ON k.id = cl.kol_id
            LEFT JOIN staff s ON s.id = cl.staff_id
            LEFT JOIN users u ON u.id = s.user_id
            WHERE 1=1 {extra}
            ORDER BY cl.incurred_at DESC, cl.id DESC
            LIMIT {_EXPORT_ROW_LIMIT + 1}
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
        rows = conn.execute(f"SELECT * FROM vkpi_kpi_ledger {where} ORDER BY ledger_date DESC, id DESC LIMIT {_EXPORT_ROW_LIMIT + 1}", tuple(params)).fetchall()
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
            LIMIT {_EXPORT_ROW_LIMIT + 1}
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    if export_type in {"vkpi_kol_pool", "favorites", "project_kols"}:
        return _kol_pool_rows(export_type, filters, staff=staff)
    raise ValueError("unsupported export_type")


# ---- KOL 名单导出列(P0-5)。纯只读 SELECT,零触 viltrox_fit_score 写点。----
# 注:CSV/XLSX 引擎按 SELECT 显式列序出列(_fieldnames 首见顺序,不排序)。
# email 列受 _pool_rows_gated 权限闸控制:非管理层导出时整列剔除。
# 友商关系列(competitor_brands/risk_tier)留待 P1-1 vkpi_competitor_relation 落库后再加
# (当前 0 行;且 TEXT 列 MAX 非严重度序、string_agg 跨库不可移植,P1-1 一并正确实现)。
_KOL_POOL_BASE_SELECT = """
    SELECT kp.handle AS handle,
           kp.platform AS platform,
           kp.viltrox_fit_score AS fit_score,
           kp.primary_topic AS kol_type,
           kp.country AS country,
           kp.followers AS followers,
           kp.engagement_rate AS engagement_rate,
           kp.email AS email
    FROM vkpi_kol_pool kp
"""


def _region_dedup_hooks(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    """红线 hook:P0-6 地区过滤 +(若 P0-4 落地)duplicate_of_id IS NULL。

    本 concern 仅留参数位,实际过滤接其他 concern。默认空串=不过滤,行为与现状一致。
    其他 concern 落地时:读 filters['region']/filters['country'] 追加 'kp.country = ?',
    并视开关追加 'kp.duplicate_of_id IS NULL'(列 mig 109 已存在,实跑确认)。
    """
    extra: list[str] = []
    params: list[Any] = []
    # --- P0-6 region filter hook (no-op until wired by region concern) ---
    # --- P0-4 duplicate_of_id IS NULL hook (no-op until wired) ---
    clause = (" AND " + " AND ".join(extra)) if extra else ""
    return clause, params


def _pool_rows_gated(rows: list[Any], staff: dict[str, Any] | None) -> list[dict[str, Any]]:
    """KOL 池导出邮箱权限闸(P0-4)。对齐读端裁决:批量邮箱不外泄给非管理层。

    读端 mask_pool_item 对所有人默认掩码,真值仅 contact_reveal 逐条二次确认。
    导出无逐条确认路径,故此处整列剔除 email(非管理层),管理层按 export 域授权保留。
    """
    items = [dict(row) for row in rows]
    if not scope.can_view_all(staff, domain="export"):
        for item in items:
            item.pop("email", None)
    return items


def _kol_pool_rows(export_type: str, filters: dict[str, Any], *, staff: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    conn = get_conn()
    hook_clause, hook_params = _region_dedup_hooks(filters)
    if export_type == "vkpi_kol_pool":
        rows = conn.execute(
            f"""
            {_KOL_POOL_BASE_SELECT}
            WHERE 1=1 {hook_clause}
            ORDER BY COALESCE(kp.viltrox_fit_score, 0) DESC, kp.id DESC
            LIMIT {_EXPORT_ROW_LIMIT + 1}
            """,
            tuple(hook_params),
        ).fetchall()
        return _pool_rows_gated(rows, staff)
    if export_type == "favorites":
        # 当前员工收藏:始终强制 actor staff,view-all 也不放大到他人收藏。
        scoped_staff_id = scope.actor_staff_id(staff) or scope.effective_staff_id(staff, filters.get("staff_id"))
        if not scoped_staff_id:
            return []
        rows = conn.execute(
            f"""
            {_KOL_POOL_BASE_SELECT}
            JOIN vkpi_kol_pool_favorites f ON f.kol_pool_id = kp.id
            WHERE f.staff_id = ? {hook_clause}
            ORDER BY f.created_at DESC, f.id DESC
            LIMIT {_EXPORT_ROW_LIMIT + 1}
            """,
            (scoped_staff_id, *hook_params),
        ).fetchall()
        return _pool_rows_gated(rows, staff)
    # project_kols:某项目的 KOL 名单。
    try:
        project_id = int(filters.get("project_id") or filters.get("projectId") or 0)
    except (TypeError, ValueError):
        project_id = 0
    if not project_id:
        raise ValueError("project_kols export requires project_id")
    scope.assert_project_access(project_id, staff)
    rows = conn.execute(
        f"""
        {_KOL_POOL_BASE_SELECT}
        JOIN vkpi_project_kol_assignments a ON a.kol_pool_id = kp.id
        WHERE a.project_id = ? {hook_clause}
        ORDER BY a.id DESC
        LIMIT {_EXPORT_ROW_LIMIT + 1}
        """,
        (project_id, *hook_params),
    ).fetchall()
    return _pool_rows_gated(rows, staff)


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    fields = _fieldnames(rows)
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_cell(row.get(key, "")) for key in fields})
    return output.getvalue().encode("utf-8-sig")


def _xlsx_bytes(rows: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "V-KPI Export"
    fields = _fieldnames(rows)
    ws.append([_xlsx_cell(field) for field in fields])
    for row in rows:
        ws.append([_xlsx_cell(row.get(key, "")) for key in fields])
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
        (export_uid, actor_id or None, export_type, fmt, _json(payload), "running", _utcnow(), (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    row = conn.execute("SELECT id FROM vkpi_export_jobs WHERE export_uid=?", (export_uid,)).fetchone()
    export_id = int(row["id"]) if row else 0
    truncated = False
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
            # 查询按 _EXPORT_ROW_LIMIT+1 取:超出即截断,并回报 truncated 让调用方知晓不完整。
            truncated = len(rows) > _EXPORT_ROW_LIMIT
            if truncated:
                rows = rows[:_EXPORT_ROW_LIMIT]
            rows = [_normalize_money(row) for row in rows]
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
            contains_pii=export_type in {"kols", "attribution", "finance", "vkpi_kol_pool", "favorites", "project_kols"},
            contains_financial=export_type in {"weekly", "attribution", "finance", "cost", "costs"},
            ip=(request_meta or {}).get("ip", ""),
            user_agent=(request_meta or {}).get("user_agent", ""),
            metric_keys=["views", "sales", "cost", "kpi"],
        )
        return {"export_id": export_id, "exportId": export_id, "export_uid": export_uid, "status": "ready", "download_url": download_url, "downloadUrl": download_url, "row_count": row_count, "truncated": truncated}
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
