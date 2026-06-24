"""G1 · 行业大盘聚合(只读)—— "市场预估 / 提前盘判断"的数据底座。

统一聚合公司全盘 + 市场信号,一屏看清:商业(组合 ROI / 短链 GMV / 订单)、KOL(高价值榜 / 池规模)、
市场(竞品信号 / 产品盘)。喂行业大盘页。红线:全程只读;零触 viltrox_fit_score;
"市场预估"在真订单数据(Shopify)接入前诚实标 awaiting_data,绝不编造预测。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _scalar(sql: str, params: tuple[Any, ...] = ()) -> int | None:
    try:
        row = get_conn().execute(sql, params).fetchone()
        return int(dict(row).get("n") or 0) if row else 0
    except Exception:
        logger.debug("industry_board.scalar_failed", extra={"sql": sql[:50]}, exc_info=True)
        return None


def get_industry_board(staff: dict[str, Any] | None = None, *, window_days: int = 30) -> dict[str, Any]:
    """行业大盘概览(只读聚合)。各块独立降级,绝不阻断。"""
    # 商业盘:复用 portfolio metrics + 短链 GMV。
    commerce: dict[str, Any] = {}
    try:
        from app.domains.metrics import aggregation as metrics

        commerce["portfolio"] = metrics.aggregate_portfolio_metrics(staff=staff)
    except Exception:
        logger.debug("industry_board.portfolio_failed", exc_info=True)
        commerce["portfolio"] = {"status": "awaiting_m5"}
    try:
        from app.domains.attribution import gmv_aggregation

        gmv = gmv_aggregation.aggregate_link_gmv(staff=staff)
        commerce["gmv"] = gmv.get("totals", {})
    except Exception:
        logger.debug("industry_board.gmv_failed", exc_info=True)
        commerce["gmv"] = {}

    # KOL 盘:高价值榜 + 池规模。
    kol: dict[str, Any] = {"pool_size": _scalar("SELECT COUNT(*) AS n FROM vkpi_kol_pool") if table_exists("vkpi_kol_pool") else None}
    try:
        from app.domains.kol import roi_aggregate

        kol["high_value"] = roi_aggregate.list_high_value_kols(limit=8, staff=staff).get("items", [])
    except Exception:
        logger.debug("industry_board.high_value_failed", exc_info=True)
        kol["high_value"] = []

    # 市场盘:竞品信号 + 产品盘(真数据)。
    market: dict[str, Any] = {
        "competitor_signals": _scalar("SELECT COUNT(*) AS n FROM vkpi_competitor_signals") if table_exists("vkpi_competitor_signals") else None,
        "products": _scalar("SELECT COUNT(*) AS n FROM vkpi_products") if table_exists("vkpi_products") else None,
        "sales_attributions": _scalar("SELECT COUNT(*) AS n FROM vkpi_sales_attributions") if table_exists("vkpi_sales_attributions") else None,
        "shopify_orders": _scalar("SELECT COUNT(*) AS n FROM vkpi_shopify_orders") if table_exists("vkpi_shopify_orders") else None,
    }

    # 市场预估:真订单(Shopify)接入前诚实标 awaiting_data(绝不编造预测)。
    has_orders = bool(market.get("shopify_orders")) or bool(market.get("sales_attributions"))
    estimate_status = "ready" if has_orders else "awaiting_data"

    return {
        "status": "ok",
        "window_days": int(window_days),
        "commerce": commerce,
        "kol": kol,
        "market": market,
        "market_estimate": {
            "status": estimate_status,
            "note": "市场预估需真实订单数据(Shopify shpat_ token)接入;未接入前诚实标 awaiting_data,不编造预测。",
        },
        "note": "行业大盘只读聚合;各块独立降级;零触 viltrox_fit_score。",
    }
