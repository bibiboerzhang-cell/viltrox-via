"""V-KPI Commerce domain — Shopify 订单 ingest + GMV 聚合(Attributed GMV / ROI 真数据源)。"""
from app.domains.commerce import shopify_connect  # noqa: F401
from app.domains.commerce import shopify_discounts  # noqa: F401
from app.domains.commerce import shopify_orders  # noqa: F401

__all__ = ["shopify_orders", "shopify_connect", "shopify_discounts"]
