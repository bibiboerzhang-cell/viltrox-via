"""J2 · Report Builder 导出 —— report_generator 结构化 report → PDF / XLSX 文件(只读)。

复用 reports.pdf_renderer.render_pdf_bytes(WeasyPrint)+ openpyxl。红线:只读组装导出;零触 viltrox_fit_score。
"""
from __future__ import annotations

import io
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def _report_html(report: dict[str, Any]) -> str:
    blocks = []
    for s in report.get("sections", []):
        body = json.dumps(s.get("data", {}), ensure_ascii=False, indent=2)
        blocks.append(f"<h2>{s.get('name', '')}</h2><pre>{body}</pre>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:-apple-system,Segoe UI,sans-serif;padding:24px;color:#1a1a1a}"
        "h1{font-size:20px} h2{font-size:14px;color:#444;margin-top:18px}"
        "pre{background:#f6f7f9;padding:12px;border-radius:8px;white-space:pre-wrap;word-break:break-all;font-size:11px}"
        "</style></head><body>"
        f"<h1>{report.get('title', 'V-KPI 报告')}</h1>"
        f"<div style='color:#888;font-size:12px'>窗口 {report.get('window_days', '')} 天 · 本地数据</div>"
        f"{''.join(blocks)}"
        "<div style='margin-top:20px;color:#aaa;font-size:10px'>V-KPI · 本地数据生成 · 零触 viltrox_fit_score</div>"
        "</body></html>"
    )


def _report_xlsx(report: dict[str, Any]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for i, s in enumerate(report.get("sections", []) or [{"name": "report", "data": {}}]):
        title = (str(s.get("name") or f"sheet{i}")[:31]) or f"sheet{i}"
        ws = wb.create_sheet(title=title)
        ws.append(["key", "value"])
        data = s.get("data", {})
        if isinstance(data, dict):
            for k, v in data.items():
                ws.append([str(k), json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v])
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                ws.append([str(idx), json.dumps(item, ensure_ascii=False, default=str)])
        else:
            ws.append(["value", str(data)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_report_file(report_type: str, *, fmt: str = "pdf", window_days: int = 30, staff: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """生成报告并导出。fmt: pdf | xlsx。未知 report_type / 渲染失败 → None。"""
    from app.domains.dashboard import report_generator

    rep = report_generator.generate_report(report_type, staff=staff, window_days=window_days)
    if rep.get("status") != "ok":
        return None
    fmt = str(fmt or "pdf").strip().lower()
    base = f"vkpi-report-{report_type}"
    try:
        if fmt == "xlsx":
            return {"bytes": _report_xlsx(rep), "filename": f"{base}.xlsx",
                    "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        from app.domains.reports import pdf_renderer

        return {"bytes": pdf_renderer.render_pdf_bytes(_report_html(rep)), "filename": f"{base}.pdf",
                "content_type": "application/pdf"}
    except Exception:
        logger.warning("report_export.render_failed", extra={"report_type": report_type, "fmt": fmt}, exc_info=True)
        return None
