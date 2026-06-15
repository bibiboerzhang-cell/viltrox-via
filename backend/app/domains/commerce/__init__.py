"""V-KPI Commerce domain — Shopify 订单 ingest + GMV 聚合(Attributed GMV / ROI 真数据源)。"""
from app.domains.commerce import shopify_orders  # noqa: F401

__all__ = ["shopify_orders"]
