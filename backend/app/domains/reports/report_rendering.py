"""Markdown rendering for structured reports."""
from __future__ import annotations

import html
from typing import Any

from app.domains.reports.contracts import REPORT_SECTION_KEYS
from app.domains.reports.report_helpers import _localized


def _markdown_cell(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "-"), quote=False).replace(
        "|", "\\|"
    ).replace("\n", " ")


def _render_markdown_report(context: dict[str, Any]) -> str:
    language = str(context.get("language") or "zh")
    selected = set(context.get("sections") or REPORT_SECTION_KEYS)
    lines = [
        f"# {_markdown_cell(context.get('title'))}",
        "",
        f"**{_localized(language, '周期', 'Period')}:** {_markdown_cell(context.get('period_label'))}",
        f"**Report UID:** {_markdown_cell(context.get('report_uid'))}",
        f"**Data status:** {_markdown_cell(context.get('data_status'))}",
        "",
    ]
    if "summary" in selected:
        lines.extend(
            [
                f"## {_localized(language, '管理层总结', 'Executive summary')}",
                "",
                _markdown_cell(context.get("summary_text")),
                "",
            ]
        )
    if "kpiOverview" in selected:
        lines.extend(
            [
                f"## {_localized(language, '核心指标', 'Core metrics')}",
                "",
                f"| {_localized(language, '指标', 'Metric')} | {_localized(language, '值', 'Value')} | Data status |",
                "| --- | ---: | --- |",
            ]
        )
        lines.extend(
            f"| {_markdown_cell(row.get('label'))} | {_markdown_cell(row.get('value'))} | {_markdown_cell(row.get('data_status'))} |"
            for row in context.get("kpis") or []
        )
        lines.append("")
    if "projects" in selected:
        lines.extend(
            [
                f"## {_localized(language, '项目', 'Projects')}",
                "",
                f"| {_localized(language, '项目', 'Project')} | KOL | {_localized(language, '阶段', 'Stage')} | {_localized(language, '销售额', 'Sales')} | {_localized(language, '成本', 'Cost')} |",
                "| --- | --- | --- | ---: | ---: |",
            ]
        )
        lines.extend(
            f"| {_markdown_cell(row.get('project_name'))} | {_markdown_cell(row.get('kol_name'))} | {_markdown_cell(row.get('stage'))} | {_markdown_cell(row.get('sales'))} | {_markdown_cell(row.get('cost'))} |"
            for row in context.get("projects") or []
        )
        lines.append("")
    if "ledger" in selected:
        appendix = context.get("kpi_appendix") if isinstance(context.get("kpi_appendix"), dict) else {}
        lines.extend(
            [
                f"## {_localized(language, 'KPI Ledger', 'KPI ledger')}",
                "",
                f"{_localized(language, '来源行', 'Source rows')}: {int(appendix.get('source_count') or 0)}",
                "",
            ]
        )
    if "attribution" in selected:
        lines.extend(
            [
                f"## {_localized(language, '归因证据', 'Attribution evidence')}",
                "",
            ]
        )
        for metric in context.get("source_appendix") or []:
            lines.append(
                f"- **{_markdown_cell(metric.get('metric_label'))}:** "
                f"{_markdown_cell(metric.get('value'))} "
                f"({_localized(language, '来源', 'sources')}: {int(metric.get('source_count') or 0)})"
            )
        lines.append("")
    if "risks" in selected:
        lines.extend([f"## {_localized(language, '风险与待处理', 'Risks and actions')}", ""])
        alerts_rows = context.get("alerts") or []
        lines.extend(
            f"- **{_markdown_cell(row.get('title'))}:** {_markdown_cell(row.get('description'))}"
            for row in alerts_rows
        )
        if not alerts_rows:
            lines.append(_localized(language, "当前没有未处理提醒。", "No open alerts."))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["_markdown_cell", "_render_markdown_report"]
