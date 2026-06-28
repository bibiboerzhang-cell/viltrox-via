"""Agent-OS 能力清单 —— 把本框架的能力收成一个自描述、可发现的统一面(只读)。

前端转接的单一入口:一次拉齐"系统能干什么 + 对应端点 + 数据是否就绪"。
红线:纯清单 + 轻量就绪度探测,零写、零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, table_exists

# 能力清单(code-defined)。status: ready=可用 / awaiting_data=待真数据 / live=按库探测。
_CAPABILITIES: list[dict[str, Any]] = [
    # ── 找人引擎 ──
    {"group": "找人", "name": "智能搜索", "endpoint": "POST /kol-smart-search", "status": "ready"},
    {"group": "找人", "name": "批准锁定候选", "endpoint": "POST /kol-search-sessions/{id}/approve", "status": "ready"},
    {"group": "找人", "name": "成本估算+风险", "endpoint": "POST /kol-search-sessions/{id}/cost-estimate", "status": "ready"},
    {"group": "找人", "name": "话术+SOW草案", "endpoint": "POST /kol-search-sessions/{id}/generate-outreach", "status": "ready"},
    {"group": "找人", "name": "建项目草案", "endpoint": "POST /kol-search-sessions/{id}/create-project-draft", "status": "ready"},
    {"group": "找人", "name": "KOL推荐卡(等级+为什么)", "endpoint": "GET /kol-pool/{id}/recommendation-card", "status": "ready"},
    # ── 行动 / 编排 ──
    {"group": "行动", "name": "今日建议(决策四件套+验收)", "endpoint": "GET /actions/inbox", "status": "ready"},
    {"group": "行动", "name": "执行台账(before/after)", "endpoint": "GET /actions/ledger/recent", "status": "ready"},
    {"group": "行动", "name": "编排器(一句话→计划,PLAN-ONLY)", "endpoint": "POST /agents/plan", "status": "ready"},
    {"group": "行动", "name": "工具白名单", "endpoint": "GET /agents/tools", "status": "ready"},
    {"group": "行动", "name": "Marketing Brain 90+评分卡", "endpoint": "GET /agents/marketing-brain/scorecard", "status": "ready"},
    # ── 履约 ──
    {"group": "履约", "name": "主链路就绪度(5断点)", "endpoint": "GET /projects/pipeline-readiness", "status": "ready"},
    {"group": "履约", "name": "发货审批门槛", "endpoint": "GET /projects/shipping-approvals", "status": "ready"},
    {"group": "履约", "name": "内容帖推进复盘", "endpoint": "POST /projects/{id}/content-posts/advance-retrospective", "status": "ready"},
    # ── 记忆 / 学习 ──
    {"group": "记忆", "name": "KOL provenance(为什么记住)", "endpoint": "GET /agents/kol/{id}/provenance", "status": "ready"},
    {"group": "记忆", "name": "学习闭环状态", "endpoint": "GET /agents/learning-status", "status": "ready"},
    # ── 度量 / 大盘 ──
    {"group": "度量", "name": "组合ROI/曝光/转化", "endpoint": "GET /metrics/portfolio", "status": "ready"},
    {"group": "度量", "name": "KOL ROI+下次权重", "endpoint": "GET /metrics/kol/{id}", "status": "ready"},
    {"group": "度量", "name": "高价值红人榜", "endpoint": "GET /metrics/high-value-kols", "status": "ready"},
    {"group": "度量", "name": "短链GMV归因", "endpoint": "GET /attribution/gmv-summary", "status": "ready"},
    {"group": "大盘", "name": "行业大盘", "endpoint": "GET /metrics/industry-board", "status": "ready"},
    {"group": "大盘", "name": "数据导出report", "endpoint": "GET /metrics/report", "status": "ready"},
    {"group": "工作台", "name": "运营每日digest", "endpoint": "GET /agents/workspace-digest", "status": "ready"},
    # ── 商业(待真数据) ──
    {"group": "商业", "name": "Shopify订单归因", "endpoint": "POST /webhooks/shopify/orders", "status": "awaiting_shopify_token", "live_table": "vkpi_shopify_orders"},
]


def get_capabilities(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """能力清单 + 轻量就绪度探测。按 group 分组。"""
    del staff
    items = []
    for cap in _CAPABILITIES:
        c = dict(cap)
        live = c.pop("live_table", None)
        if live and table_exists(live):
            try:
                n = dict(get_conn().execute(f"SELECT COUNT(*) AS n FROM {live}").fetchone()).get("n")
                if int(n or 0) > 0:
                    c["status"] = "ready"
                c["live_rows"] = int(n or 0)
            except Exception:
                pass
        items.append(c)
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in items:
        groups.setdefault(c["group"], []).append(c)
    ready = sum(1 for c in items if c["status"] == "ready")
    return {
        "status": "ok",
        "total": len(items),
        "ready": ready,
        "groups": groups,
        "note": "Agent-OS 能力自描述清单;纯只读 + 轻量探测;零触 viltrox_fit_score。",
    }
