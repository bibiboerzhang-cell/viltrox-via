"""R13 · ROI / 曝光 / 转化 只读聚合骨架(零 LLM · 零写 · 零触 viltrox_fit_score / rule_v0)。

接 M5 商业数据:有真实 cost(vkpi_cost_ledger.amount_cents)/ revenue(vkpi_sales_attributions
.revenue_cents)时算真值;无 revenue 时诚实返回 status='awaiting_m5' + 关键字段 None
(绝不返回假 0 ROI —— 零 ROI 是商业谎言)。与 project.stage 解耦:任何阶段项目都聚合。
红线:ROI/曝光/转化是独立展示信号,绝不并入 fit;聚合失败必须降级返回,绝不阻断上层 API。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.shared.vkpi_decision_common import _confirmed_revenue_filter

logger = get_logger(__name__)


def _sum_cost(project_clause: str, params: list[Any]) -> int | None:
    if not table_exists("vkpi_cost_ledger"):
        return None
    try:
        row = get_conn().execute(
            f"SELECT COALESCE(SUM(amount_cents), 0) AS c FROM vkpi_cost_ledger "
            f"WHERE status='actual' AND approved_at IS NOT NULL {project_clause}",
            tuple(params),
        ).fetchone()
        return int(dict(row).get("c") or 0)
    except Exception:
        logger.debug("metrics.cost_read_failed", exc_info=True)
        return None


def _sum_revenue(project_clause: str, params: list[Any]) -> dict[str, Any]:
    if not table_exists("vkpi_sales_attributions"):
        return {"revenue_cents": None, "commission_cents": None, "orders": None}
    try:
        # 只算确认态收入(排除 unmatched/refund/estimated/manual),与归因页/dashboard 口径统一;
        # 否则未确认收入会灌进 ROI 分子,虚高项目/组合 ROI。
        # 按 currency 分组取主币种(revenue 最大,并列比单量),绝不把不同币种 cents 相加(无 FX 表);
        # 照 account_picker._build_attributed_gmv_roi / goaffpro._aggregate_confirmed_sales 口径。
        # NULLIF 施于 TEXT 的 currency 列(非 timestamptz)。
        rows = get_conn().execute(
            f"""
            SELECT COALESCE(NULLIF(currency, ''), 'USD') AS currency,
                   COALESCE(SUM(revenue_cents), 0) AS rev,
                   COALESCE(SUM(commission_cents), 0) AS com,
                   COUNT(*) AS n
            FROM vkpi_sales_attributions
            WHERE 1=1 {project_clause}
              AND {_confirmed_revenue_filter()}
            GROUP BY COALESCE(NULLIF(currency, ''), 'USD')
            ORDER BY rev DESC
            """,
            tuple(params),
        ).fetchall()
        by_currency = [
            {
                "currency": str(dict(r).get("currency") or "USD"),
                "revenue_cents": int(dict(r).get("rev") or 0),
                "commission_cents": int(dict(r).get("com") or 0),
                "orders": int(dict(r).get("n") or 0),
            }
            for r in rows
        ]
        if by_currency:
            primary = max(by_currency, key=lambda b: (b["revenue_cents"], b["orders"]))
        else:
            primary = {"currency": "USD", "revenue_cents": 0, "commission_cents": 0, "orders": 0}
        return {
            "revenue_cents": primary["revenue_cents"],
            "commission_cents": primary["commission_cents"],
            "orders": primary["orders"],
            "currency": primary["currency"],
            "by_currency": by_currency,
            "mixed_currency": len(by_currency) > 1,
        }
    except Exception:
        logger.debug("metrics.revenue_read_failed", exc_info=True)
        return {"revenue_cents": None, "commission_cents": None, "orders": None}


def _count_exposure(project_clause: str, params: list[Any]) -> int | None:
    """曝光代理:已识别内容帖数(matched/retrospective_ready)。表缺/出错 → None。"""
    if not table_exists("vkpi_project_content_posts"):
        return None
    try:
        row = get_conn().execute(
            f"""
            SELECT COUNT(*) AS n FROM vkpi_project_content_posts
            WHERE status IN ('matched', 'retrospective_ready') {project_clause}
            """,
            tuple(params),
        ).fetchone()
        return int(dict(row).get("n") or 0)
    except Exception:
        logger.debug("metrics.exposure_read_failed", exc_info=True)
        return None


def _shape(*, window_days: int, scope_label: str, cost_cents: int | None, revenue: dict[str, Any], exposure_count: int | None) -> dict[str, Any]:
    rev = revenue.get("revenue_cents")
    has_revenue = isinstance(rev, int) and rev > 0
    roi = None
    net_cents = None
    if isinstance(rev, int) and isinstance(cost_cents, int):
        net_cents = rev - cost_cents
        if cost_cents > 0:
            roi = round((rev - cost_cents) / cost_cents, 4)
    return {
        "roi": roi,                       # net/cost 比值;无 cost 或无 revenue → None
        "net_cents": net_cents,
        "revenue_cents": rev,
        "cost_cents": cost_cents,
        "commission_cents": revenue.get("commission_cents"),
        "orders": revenue.get("orders"),
        "currency": revenue.get("currency"),
        "mixed_currency": bool(revenue.get("mixed_currency")),
        "exposure_count": exposure_count,
        "conversion_rate": None,          # 需点击数据(R15 归因链/M5 接入后填),现诚实留空
        "data_window_days": int(window_days),
        "scope": scope_label,
        "status": "ready" if has_revenue else "awaiting_m5",
        "note": "ROI/曝光/转化为独立展示信号,绝不并入 viltrox_fit_score;无 revenue 时 awaiting_m5(非假 0)。",
    }


def aggregate_project_metrics(project_id: int, *, window_days: int = 30, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """单项目 ROI/曝光/转化(只读)。越权/不存在 → status='not_found'。聚合失败 → 降级 awaiting_m5。"""
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "not_found", "scope": "project"}
    try:
        # RBAC:确认 actor 能看到该项目。
        scope_sql, scope_params = scope.project_filter("p", staff)
        scope_clause = f"AND {scope_sql}" if scope_sql else ""
        visible = get_conn().execute(
            f"SELECT p.id FROM vkpi_projects p WHERE p.id = ? {scope_clause}",
            (pid, *scope_params),
        ).fetchone()
        if visible is None:
            return {"status": "not_found", "scope": "project", "project_id": pid}
        clause = "AND project_id = ?"
        cost = _sum_cost(clause, [pid])
        revenue = _sum_revenue(clause, [pid])
        exposure = _count_exposure(clause, [pid])
        out = _shape(window_days=window_days, scope_label="project", cost_cents=cost, revenue=revenue, exposure_count=exposure)
        out["project_id"] = pid
        return out
    except Exception:
        logger.warning("metrics.aggregate_project_failed", extra={"project_id": pid}, exc_info=True)
        return {"status": "awaiting_m5", "scope": "project", "project_id": pid, "roi": None, "revenue_cents": None, "cost_cents": None}


def aggregate_portfolio_metrics(*, window_days: int = 30, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """组合级(可见项目全量)ROI/曝光/转化(只读)。聚合失败 → 降级 awaiting_m5。"""
    try:
        scope_sql, scope_params = scope.project_filter("p", staff)
        if scope_sql:
            clause = f"AND project_id IN (SELECT p.id FROM vkpi_projects p WHERE {scope_sql})"
            params = list(scope_params)
        else:
            clause = ""
            params = []
        cost = _sum_cost(clause, params)
        revenue = _sum_revenue(clause, params)
        exposure = _count_exposure(clause, params)
        return _shape(window_days=window_days, scope_label="portfolio", cost_cents=cost, revenue=revenue, exposure_count=exposure)
    except Exception:
        logger.warning("metrics.aggregate_portfolio_failed", exc_info=True)
        return {"status": "awaiting_m5", "scope": "portfolio", "roi": None, "revenue_cents": None, "cost_cents": None}
