"""里程碑1 · 工作台 digest —— 聚合"今天该看什么"(只读,全复用已建模块)。

get_workspace_digest(staff) 一次拉齐运营每天要看的四块:
- today_actions:今日建议 top N(Action Inbox)
- pipeline:数据主链路 5 断点就绪度
- portfolio:组合级 ROI/曝光/转化
- recent_executions:最近执行台账
喂个人工作台 / 指挥台首页。红线:全程只读;聚合失败降级返回,绝不阻断;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def _safe(fn, fallback):
    try:
        return fn()
    except Exception:
        logger.debug("workspace_digest.section_failed", exc_info=True)
        return fallback


def get_workspace_digest(staff: dict[str, Any] | None = None, *, action_limit: int = 5) -> dict[str, Any]:
    """运营每日 digest(只读聚合)。任一块失败不拖垮其余块。"""
    from app.domains.actions import inbox
    from app.domains.metrics import aggregation as metrics
    from app.domains.projects import pipeline_sequence

    from app.domains.kol import roi_aggregate

    today = _safe(lambda: inbox.list_inbox(staff, status="suggested", limit=max(1, min(int(action_limit or 5), 20))), {"items": [], "available": False})
    ledger = _safe(lambda: inbox.read_execution_ledger(staff, limit=5), {"items": [], "available": False})
    pipeline = _safe(lambda: pipeline_sequence.pipeline_readiness(staff=staff), {"breakpoints": [], "total_ready": 0})
    portfolio = _safe(lambda: metrics.aggregate_portfolio_metrics(staff=staff), {"status": "awaiting_m5"})
    high_value = _safe(lambda: roi_aggregate.list_high_value_kols(limit=8, staff=staff), {"items": [], "available": False})

    return {
        "today_actions": {
            "items": today.get("items", []),
            "count": today.get("count", len(today.get("items", []))),
            "by_category": today.get("by_category", {}),
            "scope": today.get("scope"),
        },
        "pipeline": {
            "breakpoints": pipeline.get("breakpoints", []),
            "total_ready": pipeline.get("total_ready", 0),
        },
        "portfolio": portfolio,
        "recent_executions": {
            "items": ledger.get("items", []),
            "by_outcome": ledger.get("by_outcome", {}),
        },
        "high_value_kols": {
            "items": high_value.get("items", []),
            "count": high_value.get("count", 0),
        },
        "note": "运营每日 digest;只读聚合,各块独立降级;零触 viltrox_fit_score。",
    }
