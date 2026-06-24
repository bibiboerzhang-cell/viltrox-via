"""G2 · 数据导出 report 生成器(只读)—— "问什么给什么",本地数据 → 结构化 report。

v1:按 report_type 从本地聚合组装结构化 report(sections),前端可渲染或导出 PDF/Excel。
(LLM 向量本地 RAG 自由问答留后续;v1 先把"调取最近阶段 ROI / 产品价值"等高频问法做成结构化报告。)
红线:全程只读;零触 viltrox_fit_score;无数据块诚实标空,绝不编造。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

REPORT_TYPES = {
    "industry_overview": "行业大盘概览(商业 + KOL + 市场)",
    "kol_performance": "高价值红人表现(ROI + 推荐权重)",
    "commerce": "商业转化(组合 ROI + 短链 GMV)",
    "pipeline": "数据主链路就绪度",
}


def list_report_types() -> list[dict[str, str]]:
    return [{"report_type": k, "title": v} for k, v in REPORT_TYPES.items()]


def generate_report(report_type: str, *, staff: dict[str, Any] | None = None, window_days: int = 30) -> dict[str, Any]:
    """生成结构化 report(只读)。未知 type → status='unknown_report_type'。"""
    rt = str(report_type or "").strip()
    if rt not in REPORT_TYPES:
        return {"status": "unknown_report_type", "available_types": list(REPORT_TYPES.keys())}

    sections: list[dict[str, Any]] = []
    try:
        if rt == "industry_overview":
            from app.domains.market import industry_board

            b = industry_board.get_industry_board(staff, window_days=window_days)
            sections = [
                {"name": "商业盘", "data": b.get("commerce", {})},
                {"name": "KOL 盘", "data": b.get("kol", {})},
                {"name": "市场盘", "data": b.get("market", {})},
                {"name": "市场预估", "data": b.get("market_estimate", {})},
            ]
        elif rt == "kol_performance":
            from app.domains.kol import roi_aggregate

            hv = roi_aggregate.list_high_value_kols(limit=20, staff=staff)
            sections = [{"name": "高价值红人榜", "data": {"items": hv.get("items", []), "count": hv.get("count", 0)}}]
        elif rt == "commerce":
            from app.domains.attribution import gmv_aggregation
            from app.domains.metrics import aggregation as metrics

            sections = [
                {"name": "组合 ROI", "data": metrics.aggregate_portfolio_metrics(staff=staff)},
                {"name": "短链 GMV", "data": gmv_aggregation.aggregate_link_gmv(staff=staff).get("totals", {})},
            ]
        elif rt == "pipeline":
            from app.domains.projects import pipeline_sequence

            p = pipeline_sequence.pipeline_readiness(staff=staff)
            sections = [{"name": "主链路就绪度", "data": p}]
    except Exception:
        logger.warning("report_generator.section_failed", extra={"report_type": rt}, exc_info=True)
        sections = [{"name": "降级", "data": {"error": "部分数据聚合失败,已降级"}}]

    return {
        "status": "ok",
        "report_type": rt,
        "title": REPORT_TYPES[rt],
        "window_days": int(window_days),
        "sections": sections,
        "export_formats": ["json", "pdf", "excel"],  # 渲染/导出在前端
        "note": "结构化 report(本地数据);PDF/Excel 由前端渲染导出;零触 viltrox_fit_score。",
    }
