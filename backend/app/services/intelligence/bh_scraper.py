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
BH_REVIEWS_ACTOR = "powerai/bhphotovideo-product-reviews-scraper"

# 单次评论抓取的产品数硬上限(每个产品 = 1 次付费 actor call,防烧钱)
BH_REVIEWS_HARD_MAX_PRODUCTS = 20


def _bh_available() -> bool:
    return _client is not None


def _flag_on(name: str, default: str = "0") -> bool:
    """env 布尔闸:烧钱动作默认关,手动开(和 config.py 同口径,读运行时 env)。"""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# ──────────────────────────────────────────────
# 主抓取函数 — 并行抓所有 category
# ──────────────────────────────────────────────

async def fetch_bh_viltrox_products(
    max_items: int = 500,
    use_backup: bool = False,
    force_refresh: bool = False,
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

    # 停用闸(用户令 2026-07-02):search actor 抓的产品列表与库内数据 100% 重复零增量,
    # search 默认停。停用时零 actor 调用,只回库存快照(可能过期);force_refresh 也不越闸。
    # 要恢复在 .env 设 BH_SNAPSHOT_ENABLED=1。函数与下方 TTL 闸都保留,可随时恢复。
    if not _flag_on("BH_SNAPSHOT_ENABLED", "0"):
        cached: list[dict] = []
        try:
            from app.services.intelligence.bh_repository import get_latest_bh_products
            cached = get_latest_bh_products(limit=max(50, int(max_items)))
        except Exception:
            logger.warning("bh_scraper.disabled_cache_read_failed", exc_info=True)
        logger.info("bh_scraper.search_disabled_cache_only", extra={"count": len(cached)})
        return cached

    # 成本闸(2026-07-01):一次调用并行跑 6 个 category = 6 次付费 actor(约 $2/调用,
    # 本周期已烧 $68/204 跑)。新鲜度守卫:库里最新快照 < BH_SNAPSHOT_TTL_DAYS(默认 6)天
    # -> 直接回库存快照、零 actor;force_refresh=True 强刷。所有调用方一并受控。
    # 注意:守卫返回的缓存数据若被调用方再 save_bh_snapshot,会刷新 snapshot_at 导致 TTL
    # 永不过期(数据停更但零成本)。周频真刷新由 Job6(每周一)驱动,TTL=6 保证周一必过期。
    if not force_refresh:
        try:
            import os as _os
            from datetime import datetime as _dt, timezone as _tz
            from app.services.intelligence.bh_repository import get_bh_summary, get_latest_bh_products

            _ttl = max(1, int(_os.environ.get("BH_SNAPSHOT_TTL_DAYS", "6")))
            _latest = str((get_bh_summary() or {}).get("latest_snapshot_at") or "")
            if _latest:
                _t = _dt.fromisoformat(_latest.replace("Z", "+00:00"))
                if _t.tzinfo is None:
                    _t = _t.replace(tzinfo=_tz.utc)
                _age = (_dt.now(_tz.utc) - _t).total_seconds() / 86400.0
                if _age < _ttl:
                    _cached = get_latest_bh_products(limit=max(50, int(max_items)))
                    if _cached:
                        logger.info("bh_scraper.fresh_snapshot_reuse", extra={"age_days": round(_age, 1), "count": len(_cached)})
                        return _cached
        except Exception:
            logger.warning("bh_scraper.freshness_guard_failed", exc_info=True)

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
            # C5 成本记账收口:search actor 每类目一跑都是真钱(~$2/次),统一记账
            # (幂等 by run_id;失败绝不影响抓取)。
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id=actor_id,
                    platform="bh",
                    operation="fetch_bh_products",
                    source="intelligence.bh_scraper",
                    dataset_item_count=len(items),
                )
            except Exception:
                logger.warning("bh_scraper.cost_record_failed", exc_info=True)
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
# 评论抓取(2026-07-02 转正:替代已停的 search actor,喂竞品口碑)
# ──────────────────────────────────────────────

def normalize_bh_review(
    item: dict,
    *,
    product_url: str,
    product_name: str = "",
    sku: str = "",
) -> Optional[dict]:
    """把 reviews actor 返回的原始字段标准化成 vkpi_bh_reviews 一行。

    product_url 一律用请求时的库内 canonical URL(不信 item 里的 url,防重键要稳定)。
    """
    if not isinstance(item, dict):
        return None

    def _get_first(*keys, default=""):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", 0, []):
                return v
        return default

    body = str(_get_first("content", "body", "text", "reviewText", "review_text", "description") or "")
    title = str(_get_first("title", "headline", "summary") or "")
    if not body.strip() and not title.strip():
        return None

    rating_raw = _get_first("rating", "stars", "score", "ratingValue", default=0)
    try:
        rating = float(rating_raw or 0)
    except (ValueError, TypeError):
        rating = 0.0

    return {
        "product_url":  str(product_url or "")[:500],
        "product_name": str(product_name or _get_first("productName", "product_name") or "")[:300],
        "sku":          str(sku or _get_first("sku", "SKU", "productId") or "")[:100],
        "rating":       round(rating, 2),
        "title":        title[:200],
        "body":         body[:4000],
        "author":       str(_get_first("author", "user", "nickname", "reviewer", "authorName") or "")[:100],
        "review_date":  str(_get_first("date", "reviewDate", "review_date", "createdAt") or "")[:40],
    }


async def fetch_bh_reviews(
    product_urls: Optional[list] = None,
    limit_per_product: int = 30,
    max_products: int = BH_REVIEWS_HARD_MAX_PRODUCTS,
) -> dict:
    """抓一批产品的用户评论并 upsert 进 vkpi_bh_reviews(竞品口碑数据源)。

    照 search 同款调用模式:apify_client.actor().call() + record_apify_run_cost 记账 + 超时。
    - 输入:产品 URL 列表;缺省时从 bh_products 表选 Viltrox 自家 + 竞品条目
      (竞品口径 = app.domains.market.content_brain.COMPETITOR_BRANDS 标题匹配)。
    - 单次上限 20 个产品(每个产品 = 1 次付费 actor call,防烧钱)。
    - 闸:BH_REVIEWS_ENABLED 默认 "0"(烧钱动作默认关,手动开)。

    Returns: 汇总 dict {ok, products_requested, reviews_fetched, reviews_upserted,
             actor_cost_usd, per_product, [reason]}
    """
    summary: dict[str, Any] = {
        "ok": False,
        "products_requested": 0,
        "reviews_fetched": 0,
        "reviews_upserted": 0,
        "actor_cost_usd": 0.0,
        "per_product": [],
    }
    if not _flag_on("BH_REVIEWS_ENABLED", "0"):
        summary["reason"] = "BH_REVIEWS_ENABLED is off (default; set env to 1 to run this paid action)"
        logger.info("bh_reviews.disabled_by_env")
        return summary
    if not _bh_available():
        summary["reason"] = "apify client unavailable (APIFY_TOKEN missing)"
        logger.warning("bh_reviews.apify_unavailable")
        return summary

    # 选目标:显式传入 > 库内自选
    targets: list[dict]
    if product_urls:
        targets = [
            {"url": str(u or "").strip(), "title": "", "sku": ""}
            for u in product_urls
            if str(u or "").strip()
        ]
    else:
        from app.services.intelligence.bh_repository import select_bh_review_targets
        targets = select_bh_review_targets(limit=max_products)

    # URL 去重 + 硬上限
    seen: set[str] = set()
    unique_targets: list[dict] = []
    for t in targets:
        url = t.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        unique_targets.append(t)
    hard_cap = max(1, min(int(max_products or BH_REVIEWS_HARD_MAX_PRODUCTS), BH_REVIEWS_HARD_MAX_PRODUCTS))
    if len(unique_targets) > hard_cap:
        logger.warning(
            "bh_reviews.targets_truncated",
            extra={"requested": len(unique_targets), "hard_cap": hard_cap},
        )
        unique_targets = unique_targets[:hard_cap]
    summary["products_requested"] = len(unique_targets)
    if not unique_targets:
        summary["reason"] = "no review targets (bh_products empty and no urls passed)"
        logger.warning("bh_reviews.no_targets")
        return summary

    limit_per_product = max(1, min(int(limit_per_product or 30), 100))
    logger.info(
        "bh_reviews.fetch_started",
        extra={"product_count": len(unique_targets), "limit_per_product": limit_per_product, "actor_id": BH_REVIEWS_ACTOR},
    )
    t0 = time.time()
    sem = asyncio.Semaphore(4)

    def _do_one(target: dict) -> tuple[dict, list[dict], float]:
        """同步抓一个产品的评论;返回 (target, 标准化评论列表, 本次 actor 实际成本)。"""
        url = target.get("url", "")
        try:
            # 输入 schema 沿用占位实现的 startUrls + maxItems 口径;
            # 首次付费跑建议 scripts/run_bh_reviews_once.py --limit 1 先验证字段再放量。
            run = _client.actor(BH_REVIEWS_ACTOR).call(
                run_input={"startUrls": [{"url": url}], "maxItems": limit_per_product},
                timeout_secs=300,
            )
            # 记账同 search 款:usageTotalUsd 落 vkpi_ai_cost_ledger(provider:apify)
            try:
                from app.platform.industry_crawlers import record_apify_run_cost
                record_apify_run_cost(run, platform="bh", actor_id=BH_REVIEWS_ACTOR, operation="fetch_product_reviews")
            except Exception:
                logger.warning("bh_reviews.cost_record_failed", exc_info=True)
            cost = 0.0
            try:
                cost = float((run or {}).get("usageTotalUsd") or 0)
            except (TypeError, ValueError):
                cost = 0.0
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            reviews = [
                r for r in (
                    normalize_bh_review(
                        item,
                        product_url=url,
                        product_name=str(target.get("title", "") or ""),
                        sku=str(target.get("sku", "") or ""),
                    )
                    for item in items[: limit_per_product]
                )
                if r
            ]
            logger.info("bh_reviews.product_complete", extra={"url": url, "review_count": len(reviews)})
            return target, reviews, cost
        except Exception as e:
            logger.warning("bh_reviews.product_failed", extra={"url": url, "error": str(e)})
            return target, [], 0.0

    async def _guarded(target: dict):
        async with sem:
            return await asyncio.to_thread(_do_one, target)

    results = await asyncio.gather(*[_guarded(t) for t in unique_targets], return_exceptions=True)

    all_reviews: list[dict] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("bh_reviews.task_failed", extra={"error": str(r)})
            continue
        target, reviews, cost = r
        all_reviews.extend(reviews)
        summary["actor_cost_usd"] = round(float(summary["actor_cost_usd"]) + cost, 4)
        summary["per_product"].append({"url": target.get("url", ""), "fetched": len(reviews)})
    summary["reviews_fetched"] = len(all_reviews)

    if all_reviews:
        from app.services.intelligence.bh_repository import upsert_bh_reviews
        summary["reviews_upserted"] = await upsert_bh_reviews(all_reviews)

    summary["ok"] = True
    logger.info(
        "bh_reviews.fetch_complete",
        extra={
            "products": len(unique_targets),
            "reviews_fetched": summary["reviews_fetched"],
            "reviews_upserted": summary["reviews_upserted"],
            "actor_cost_usd": summary["actor_cost_usd"],
            "elapsed_sec": round(time.time() - t0, 1),
        },
    )
    return summary


async def fetch_bh_product_reviews(product_url: str, max_reviews: int = 100) -> list[dict]:
    """(旧占位签名,保留兼容)抓某个产品的评论. 新代码请用 fetch_bh_reviews(落库版)."""
    if not _flag_on("BH_REVIEWS_ENABLED", "0"):
        logger.info("bh_reviews.legacy_entry_disabled_by_env")
        return []
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
            found = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            # C5 成本记账收口:legacy reviews 入口也统一记账(幂等 by run_id;失败绝不影响抓取)。
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id="powerai/bhphotovideo-product-reviews-scraper",
                    platform="bh",
                    operation="fetch_product_reviews_legacy",
                    source="intelligence.bh_scraper",
                    dataset_item_count=len(found),
                )
            except Exception:
                logger.warning("bh_scraper.legacy_reviews_cost_record_failed", exc_info=True)
            return found
        
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
