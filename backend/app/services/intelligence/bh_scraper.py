"""
services/intelligence/bh_scraper.py — B&H Viltrox 多 category 抓取
===================================================================
用 shahidirfan/B-H-Photo-Scraper actor 抓 B&H 上所有 Viltrox 产品.

方式: 并行抓 6 个 category, 合并所有结果.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Apify client
try:
    from apify_client import ApifyClient
    _APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
    _client: Optional[ApifyClient] = ApifyClient(_APIFY_TOKEN) if _APIFY_TOKEN else None
    if _client:
        logger.info("bh_scraper.client_ready")
    else:
        logger.warning("bh_scraper.token_missing")
except ImportError:
    _client = None
    logger.warning("bh_scraper.client_unavailable")


# ──────────────────────────────────────────────
# B&H Viltrox 所有 category
# actor 只吃 /c/buy/xxx/ci/XXX listing 格式, 不吃 search/browse
# ──────────────────────────────────────────────
BH_VILTROX_CATEGORIES = [
    ("mirrorless-lenses",        "https://www.bhphotovideo.com/c/buy/viltrox-mirrorless-lenses/ci/58791"),
    ("cine-lenses",              "https://www.bhphotovideo.com/c/buy/viltrox-cine-lenses/ci/58792"),
    ("led-lights",               "https://www.bhphotovideo.com/c/buy/viltrox-led-lights/ci/58825"),
    ("on-camera-monitors",       "https://www.bhphotovideo.com/c/buy/viltrox-on-camera-monitors/ci/58826"),
    ("teleconverters",           "https://www.bhphotovideo.com/c/buy/viltrox-teleconverters/ci/58823"),
    ("cine-lens-mount-adapters", "https://www.bhphotovideo.com/c/buy/viltrox-cine-lens-mount-adapters/ci/58821"),
]

# 兼容老代码: 默认 URL = 第一个 category
BH_VILTROX_SEARCH_URL = BH_VILTROX_CATEGORIES[0][1]

# Apify actor
BH_ACTOR_PRIMARY = "powerai/bhphotovideo-product-search-scraper"  # ✅ 验证可用
BH_ACTOR_BACKUP = "powerai/bhphotovideo-product-search-scraper"


def _bh_available() -> bool:
    return _client is not None


# ──────────────────────────────────────────────
# 主抓取函数 — 并行抓所有 category
# ──────────────────────────────────────────────

async def fetch_bh_viltrox_products(
    max_items: int = 500,
    use_backup: bool = False,
) -> list[dict]:
    """
    抓 B&H 上所有 Viltrox 产品 (6 个 category 并行).
    
    Args:
        max_items: 每个 category 最多抓多少 (总上限 ~= 6 × max_items)
        use_backup: 是否用备用 actor
    
    Returns:
        list of 标准化 product dicts
    """
    if not _bh_available():
        logger.warning("bh_scraper.apify_unavailable")
        return []
    
    actor_id = BH_ACTOR_BACKUP if use_backup else BH_ACTOR_PRIMARY
    logger.info(
        "bh_scraper.fetch_started",
        extra={"category_count": len(BH_VILTROX_CATEGORIES), "actor_id": actor_id, "max_items": max_items},
    )
    t0 = time.time()
    
    def _do_one(category_name: str, url: str):
        """同步抓一个 category"""
        try:
            run_input = {
                "searchUrls": [url],
                "maxItems": max_items,
            }
            run = _client.actor(actor_id).call(
                run_input=run_input,
                timeout_secs=300,
            )
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            logger.info("bh_scraper.category_complete", extra={"category_name": category_name, "item_count": len(items)})
            return items
        except Exception as e:
            logger.warning("bh_scraper.category_failed", extra={"category_name": category_name, "error": str(e)})
            return []
    
    # 并行抓所有 category
    tasks = [
        asyncio.to_thread(_do_one, cat_name, url)
        for cat_name, url in BH_VILTROX_CATEGORIES
    ]
    results_per_cat = await asyncio.gather(*tasks, return_exceptions=True)
    
    raw_items: list[dict] = []
    for r in results_per_cat:
        if isinstance(r, Exception):
            logger.warning("bh_scraper.category_task_failed", extra={"error": str(r)})
            continue
        if isinstance(r, list):
            raw_items.extend(r)
    
    elapsed = time.time() - t0
    logger.info(
        "bh_scraper.fetch_complete",
        extra={"raw_item_count": len(raw_items), "category_count": len(BH_VILTROX_CATEGORIES), "elapsed_sec": round(elapsed, 1)},
    )
    
    # 标准化 + 过滤 Viltrox + 去重
    normalized = [normalize_bh_product(item) for item in raw_items]
    normalized = [p for p in normalized if p]
    
    # 只保留真 Viltrox 产品
    viltrox_only = [
        p for p in normalized
        if "viltrox" in (p.get("title", "") or "").lower()
    ]
    
    # 按 SKU 去重 (不同 category 可能返回同一产品)
    seen_skus: set[str] = set()
    unique: list[dict] = []
    for p in viltrox_only:
        sku = p.get("sku", "")
        if sku and sku in seen_skus:
            continue
        if sku:
            seen_skus.add(sku)
        unique.append(p)
    
    logger.info("bh_scraper.dedup_complete", extra={"unique_count": len(unique)})
    return unique


# ──────────────────────────────────────────────
# 数据标准化
# ──────────────────────────────────────────────

def normalize_bh_product(item: dict) -> Optional[dict]:
    """把 actor 返回的原始字段标准化"""
    if not isinstance(item, dict):
        return None
    
    def _get_first(*keys, default=""):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", 0, []):
                return v
        return default
    
    title = _get_first("title", "name", "productName", "product_name", "seoShortDescription")
    if not title:
        return None
    
    price_raw = _get_first("price", "currentPrice", "salePrice", "list_price", default=0)
    try:
        if isinstance(price_raw, str):
            price = float(price_raw.replace("$", "").replace(",", "").strip())
        else:
            price = float(price_raw or 0)
    except (ValueError, TypeError):
        price = 0
    
    rating_raw = _get_first("rating", "stars", "averageRating", "rating_value", default=0)
    try:
        rating = float(rating_raw or 0)
    except (ValueError, TypeError):
        rating = 0
    
    reviews_raw = _get_first(
        "review_count", "reviewCount", "reviews", "numReviews", "totalReviews",
        default=0,
    )
    try:
        review_count = int(reviews_raw or 0)
    except (ValueError, TypeError):
        review_count = 0
    
    url = _get_first("url", "productUrl", "link", "href", "canonicalUrl")
    
    image_url = _get_first("image", "imageUrl", "image_url", "featured_image", "thumbnail")
    if isinstance(image_url, list) and image_url:
        first = image_url[0]
        if isinstance(first, dict):
            image_url = first.get("url", "")
        else:
            image_url = str(first)
    
    sku = _get_first("sku", "SKU", "productId", "id", "model")
    
    stock_raw = _get_first("in_stock", "inStock", "stockStatus", "availability", default=True)
    if isinstance(stock_raw, str):
        in_stock = stock_raw.lower() in ("in_stock", "instock", "available", "yes", "true", "in stock")
    else:
        in_stock = bool(stock_raw)
    
    return {
        "title":        str(title)[:300],
        "price":        round(price, 2),
        "rating":       round(rating, 2),
        "review_count": review_count,
        "url":          str(url)[:500],
        "image_url":    str(image_url)[:500],
        "in_stock":     in_stock,
        "sku":          str(sku)[:100],
        "scraped_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source":       "bh",
    }


# ──────────────────────────────────────────────
# 评论抓取 (占位, 未来扩展)
# ──────────────────────────────────────────────

async def fetch_bh_product_reviews(product_url: str, max_reviews: int = 100) -> list[dict]:
    """抓某个产品的评论. 用 powerai/bhphotovideo-product-reviews-scraper"""
    if not _bh_available():
        return []
    
    if "/reviews" not in product_url:
        reviews_url = product_url.rstrip("/") + "/reviews"
    else:
        reviews_url = product_url
    
    try:
        def _do():
            run = _client.actor("powerai/bhphotovideo-product-reviews-scraper").call(
                run_input={"startUrls": [{"url": reviews_url}], "maxItems": max_reviews},
                timeout_secs=180,
            )
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        items = await asyncio.to_thread(_do)
        return [
            {
                "rating":  float(item.get("rating", 0) or 0),
                "title":   str(item.get("title", "") or "")[:200],
                "content": str(item.get("content", "") or "")[:2000],
                "author":  str(item.get("author", "") or "")[:100],
                "verified": bool(item.get("verifiedBuyer", False)),
                "helpful_count": int(item.get("helpful", 0) or 0),
                "review_date": str(item.get("date", "") or ""),
            }
            for item in items
        ]
    except Exception as e:
        logger.warning("bh_scraper.review_fetch_failed", extra={"error": str(e)})
        return []


# ──────────────────────────────────────────────
# 测试入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        logger.info("bh_scraper.demo_started")
        products = await fetch_bh_viltrox_products(max_items=100)
        logger.info("bh_scraper.demo_product_count", extra={"count": len(products)})
        for i, p in enumerate(products[:15]):
            logger.info(
                "bh_scraper.demo_product",
                extra={
                    "index": i + 1,
                    "title": p.get("title", "?")[:60],
                    "price": p.get("price", 0),
                    "rating": p.get("rating", 0),
                    "review_count": p.get("review_count", 0),
                    "sku": p.get("sku", "?"),
                },
            )
    
    asyncio.run(main())
