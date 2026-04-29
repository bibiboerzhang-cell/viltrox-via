"""
services/intelligence — 商品 / 竞品 / 市场情报系统

子模块:
  - bh_scraper:    B&H Photo Video Apify 抓取
  - bh_repository: B&H 数据存储与查询

未来可扩展:
  - amazon_scraper
  - adorama_scraper
  - competitor_monitor (Sigma/Tamron)
"""
from app.services.intelligence.bh_scraper import (
    fetch_bh_viltrox_products,
    fetch_bh_product_reviews,
    normalize_bh_product,
)
from app.services.intelligence.bh_repository import (
    save_bh_snapshot,
    get_latest_bh_products,
    get_bh_summary,
    get_bh_price_history,
    get_bh_top_rated,
)
from app.services.intelligence.viltrox_matrix import (
    build_viltrox_overview,
    reset_viltrox_official_roster,
    scan_viltrox_official_matrix_now,
)

__all__ = [
    "fetch_bh_viltrox_products",
    "fetch_bh_product_reviews",
    "normalize_bh_product",
    "save_bh_snapshot",
    "get_latest_bh_products",
    "get_bh_summary",
    "get_bh_price_history",
    "get_bh_top_rated",
    "build_viltrox_overview",
    "reset_viltrox_official_roster",
    "scan_viltrox_official_matrix_now",
]
