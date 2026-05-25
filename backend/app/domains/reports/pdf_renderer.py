"""V-KPI HTML/PDF renderer with local storage."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Template

from app.core.logging import get_logger

logger = get_logger(__name__)

REPORT_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{{ title }}</title>
<style>
@page { size: A4; margin: 16mm; @bottom-center { content: "Viltrox Marketing · " counter(page) " / " counter(pages); font-size: 9px; color: #9ca3af; } }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Helvetica", Arial, sans-serif; color:#111827; font-size: 11px; line-height:1.55; }
h1 { font-size: 25px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 20px 0 8px; border-bottom:1px solid #e5e7eb; padding-bottom:5px; }
.header { margin-bottom: 12px; }
.eyebrow { color:#2563eb; letter-spacing:2px; font-size:10px; font-weight:700; text-transform:uppercase; }
.sub { color:#64748b; }
.watermark { color:#94a3b8; font-size:9px; }
.summary { background:#f8fafc; border-left:4px solid #2563eb; padding:10px 12px; border-radius:8px; }
.grid { display: table; width:100%; border-collapse: separate; border-spacing: 8px; margin-left:-8px; }
.cell { display: table-cell; border:1px solid #e5e7eb; border-radius:10px; padding:10px; width:16%; }
.label { color:#64748b; font-size:9px; }
.value { font-size:19px; font-weight:800; margin-top:2px; }
table { width:100%; border-collapse:collapse; margin-top:6px; }
th, td { border:1px solid #e5e7eb; padding:6px 7px; vertical-align:top; }
th { background:#f8fafc; color:#334155; text-align:left; }
td.num, th.num { text-align:right; font-variant-numeric: tabular-nums; }
.badge { display:inline-block; padding:2px 6px; border-radius:999px; background:#eff6ff; color:#1d4ed8; font-size:9px; }
.alert { padding:7px 9px; border-radius:8px; background:#fff7ed; border:1px solid #fed7aa; margin:5px 0; }
.appendix { margin-top:24px; color:#64748b; font-size:9px; border-top:1px dashed #cbd5e1; padding-top:8px; }
.source-title { margin:12px 0 4px; font-size:12px; font-weight:800; color:#0f172a; }
.source-meta { color:#64748b; font-size:9px; margin-bottom:4px; }
.source-table td { font-size:9px; }
.small { color:#64748b; font-size:9px; }
</style>
</head>
<body>
<div class="header">
  <div class="eyebrow">Viltrox Marketing</div>
  <h1>{{ title }}</h1>
  <div class="sub">周期：{{ period_label }} · 数据来源：真实项目、短链、Shopify/Amazon 归因、成本和 KPI Ledger</div>
  <div class="watermark">员工水印：{{ watermark_user }} · 生成时间：{{ generated_at }} · Report UID：{{ report_uid }}</div>
</div>
<h2>1. 管理层总结</h2>
<div class="summary">{{ summary_text }}</div>
<h2>2. 核心指标</h2>
<div class="grid">
  {% for item in kpis %}<div class="cell"><div class="label">{{ item.label }}</div><div class="value">{{ item.value }}</div><div class="sub">{{ item.note }}</div></div>{% endfor %}
</div>
<h2>3. 项目进度漏斗</h2>
<table><thead><tr><th>阶段</th><th class="num">项目数</th></tr></thead><tbody>{% for row in funnel %}<tr><td>{{ row.stage }}</td><td class="num">{{ row.count }}</td></tr>{% else %}<tr><td colspan="2">当前周期暂无项目进度。</td></tr>{% endfor %}</tbody></table>
<h2>4. 员工贡献</h2>
<table><thead><tr><th>员工</th><th class="num">新增 KOL</th><th class="num">已发布</th><th class="num">本周销售额</th><th class="num">成本</th><th class="num">项目数</th></tr></thead><tbody>{% for row in staff_rows %}<tr><td>{{ row.name }}</td><td class="num">{{ row.kol_claims }}</td><td class="num">{{ row.published }}</td><td class="num">{{ row.sales }}</td><td class="num">{{ row.cost }}</td><td class="num">{{ row.projects }}</td></tr>{% else %}<tr><td colspan="6">暂无员工贡献数据。</td></tr>{% endfor %}</tbody></table>
<h2>5. 项目明细</h2>
<table><thead><tr><th>项目</th><th>KOL</th><th>阶段</th><th>负责人</th><th class="num">销售额</th><th class="num">成本</th><th>更新</th></tr></thead><tbody>{% for row in projects %}<tr><td>{{ row.project_name }}</td><td>{{ row.kol_name }}</td><td><span class="badge">{{ row.stage }}</span></td><td>{{ row.staff_name }}</td><td class="num">{{ row.sales }}</td><td class="num">{{ row.cost }}</td><td>{{ row.updated_at }}</td></tr>{% else %}<tr><td colspan="7">暂无项目明细。</td></tr>{% endfor %}</tbody></table>
<h2>6. 风险与待处理</h2>
{% for row in alerts %}<div class="alert"><strong>{{ row.title }}</strong><br><span>{{ row.description }}</span></div>{% else %}<p class="sub">当前没有未处理提醒。</p>{% endfor %}
<h2>7. 证据口径</h2>
<table><tbody><tr><th>播放量</th><td>来自已抓取内容 / 项目内容统计，不使用假 0 粉丝或假视频数据。</td></tr><tr><th>本周销售额</th><td>来自 Shopify / Amazon / 手动归因表，可下钻到 source_ref 和 evidence_json。</td></tr><tr><th>成本</th><td>发货时自动计入镜头成本；员工只登记快递费和推广费用；void 成本不计入。</td></tr></tbody></table>
<h2>8. Source Rows 附录</h2>
{% for metric in source_appendix %}
<div class="source-title">{{ metric.metric_label }} <span class="small">({{ metric.metric_key }})</span></div>
<div class="source-meta">指标值：{{ metric.value }} · 来源总数：{{ metric.source_count }} · PDF 仅显示前 {{ metric.rows|length }} 条关键来源</div>
<table class="source-table">
  <thead><tr><th>来源</th><th>项目</th><th>KOL</th><th>员工</th><th class="num">贡献</th><th>证据</th><th>发生时间</th><th>快照</th></tr></thead>
  <tbody>
  {% for row in metric.rows %}
    <tr><td>{{ row.source_type }} #{{ row.source_id }}</td><td>{{ row.project }}</td><td>{{ row.kol }}</td><td>{{ row.staff }}</td><td class="num">{{ row.amount }}</td><td>{{ row.evidence }}</td><td>{{ row.occurred_at }}</td><td>{{ row.snapshot }}</td></tr>
  {% else %}
    <tr><td colspan="8">当前指标没有 source rows。若这是新周期，请先运行 KPI/metric lineage。</td></tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="sub">当前 PDF 没有可附加的 source rows。请先生成 metric lineage，或确认当前周期存在真实业务数据。</p>
{% endfor %}
<h2>9. KPI Ledger 附录</h2>
{% if kpi_appendix and kpi_appendix.grouped %}
<table>
  <thead><tr><th>指标</th><th class="num">合计</th><th class="num">来源数</th><th>日期范围</th><th>口径</th></tr></thead>
  <tbody>
  {% for row in kpi_appendix.grouped %}
    <tr><td>{{ row.metric_label }} <span class="small">({{ row.metric_key }})</span></td><td class="num">{{ row.formatted_total }}</td><td class="num">{{ row.source_count }}</td><td>{{ row.first_date }} → {{ row.last_date }}</td><td>{{ '推荐 outcome，不重复计入主财务' if row.is_recommendation_metric else row.confidence }}</td></tr>
  {% endfor %}
  </tbody>
</table>
<div class="source-title">KPI Source Rows <span class="small">显示前 {{ kpi_appendix.source_rows|length }} 条</span></div>
<table class="source-table">
  <thead><tr><th>指标</th><th>项目</th><th>KOL</th><th>员工</th><th class="num">值</th><th>来源</th><th>公式 / 推荐</th><th>组件</th></tr></thead>
  <tbody>
  {% for row in kpi_appendix.source_rows %}
    <tr>
      <td>{{ row.metric_label }}</td>
      <td>{{ row.project }}</td>
      <td>{{ row.kol }}</td>
      <td>{{ row.staff }}</td>
      <td class="num">{{ row.value }}</td>
      <td>{{ row.source_type }} · {{ row.source_ref }}</td>
      <td>{% if row.formula %}{{ row.formula }}{% elif row.recommendation_id %}Rec #{{ row.recommendation_id }} / Outcome #{{ row.outcome_id }} / Launch #{{ row.launch_id }}{% else %}{{ row.confidence }}{% endif %}</td>
      <td>{{ row.component_summary or '-' }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="sub">当前周期暂无 KPI Ledger source rows。需要先运行 KPI rollup。</p>
{% endif %}
<div class="appendix">Metric Run：{{ metric_run_id or '-' }} · 文件包含员工水印和导出审计记录。</div>
</body>
</html>"""


def report_storage_dir() -> Path:
    raw = os.environ.get("VKPI_REPORT_STORAGE_PATH") or "runtime/vkpi-reports"
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_report_html(context: dict[str, Any]) -> str:
    return Template(REPORT_HTML_TEMPLATE).render(**context)


def render_pdf_bytes(html: str) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("WeasyPrint is not installed or system libraries are missing") from exc
    return HTML(string=html, base_url=str(Path.cwd())).write_pdf()


def store_bytes(content: bytes, *, filename: str) -> dict[str, Any]:
    path = report_storage_dir() / filename
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    return {
        "file_path": str(path),
        "file_size_bytes": len(content),
        "sha256_hex": digest,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_and_store_pdf(context: dict[str, Any], *, filename: str) -> dict[str, Any]:
    html = render_report_html(context)
    pdf = render_pdf_bytes(html)
    return {**store_bytes(pdf, filename=filename), "html": html}
