"""
services/intelligence/bh_repository.py — B&H 数据存储 / 查询
==============================================================
表: bh_products (定义见 migrations/002_intelligence.sql)
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.core.logging import get_logger
from app.db.connection import get_conn, db_write

logger = get_logger(__name__)


def _save_bh_snapshot_sync(products: list[dict], snapshot_at: str) -> int:
    conn = get_conn()
    n = 0
    for p in products:
        try:
            conn.execute(
                """
                INSERT INTO bh_products
                    (title, price, rating, review_count, url, image_url,
                     in_stock, sku, scraped_at, snapshot_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.get("title", "")[:300],
                    float(p.get("price", 0) or 0),
                    float(p.get("rating", 0) or 0),
                    int(p.get("review_count", 0) or 0),
                    p.get("url", "")[:500],
                    p.get("image_url", "")[:500],
                    1 if p.get("in_stock", True) else 0,
                    p.get("sku", "")[:100],
                    p.get("scraped_at", snapshot_at),
                    snapshot_at,
                    json.dumps(p, ensure_ascii=False)[:5000],
                ),
            )
            n += 1
        except Exception as exc:
            logger.warning("bh.snapshot.insert_failed | error=%s", exc)
            continue
    conn.commit()
    return n


# ──────────────────────────────────────────────
# 写入
# ──────────────────────────────────────────────

async def save_bh_snapshot(products: list[dict]) -> int:
    """
    保存一批 B&H 产品快照.
    每次抓取产生一个新的 snapshot, 不覆盖历史.
    
    Returns: 写入的产品数
    """
    if not products:
        return 0
    
    snapshot_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return await db_write(lambda: _save_bh_snapshot_sync(products, snapshot_at))


# ──────────────────────────────────────────────
# 查询
# ──────────────────────────────────────────────

def get_latest_bh_products(limit: int = 50) -> list[dict]:
    """获取最新一次 snapshot 的所有产品"""
    conn = get_conn()
    
    # 找最新 snapshot_at
    row = conn.execute(
        "SELECT MAX(snapshot_at) FROM bh_products"
    ).fetchone()
    if not row or not row[0]:
        return []
    latest_snapshot = row[0]
    
    rows = conn.execute(
        """
        SELECT id, title, price, rating, review_count, url, image_url,
               in_stock, sku, scraped_at, snapshot_at
        FROM bh_products
        WHERE snapshot_at = ?
        ORDER BY rating DESC, review_count DESC
        LIMIT ?
        """,
        (latest_snapshot, limit),
    ).fetchall()
    
    return [dict(r) for r in rows]


def get_bh_summary() -> dict:
    """统计概览"""
    conn = get_conn()
    
    # 总产品数 (最新 snapshot)
    row = conn.execute("SELECT MAX(snapshot_at) FROM bh_products").fetchone()
    if not row or not row[0]:
        return {
            "total_products": 0,
            "snapshots": 0,
            "latest_snapshot_at": None,
            "avg_price": 0,
            "avg_rating": 0,
            "total_reviews": 0,
        }
    
    latest = row[0]
    
    summary = conn.execute(
        """
        SELECT 
            COUNT(*) as total,
            AVG(price) as avg_price,
            AVG(rating) as avg_rating,
            SUM(review_count) as total_reviews
        FROM bh_products
        WHERE snapshot_at = ? AND price > 0
        """,
        (latest,),
    ).fetchone()
    
    snapshot_count = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_at) FROM bh_products"
    ).fetchone()[0]
    
    return {
        "total_products": summary[0] or 0,
        "snapshots": snapshot_count,
        "latest_snapshot_at": latest,
        "avg_price": round(float(summary[1] or 0), 2),
        "avg_rating": round(float(summary[2] or 0), 2),
        "total_reviews": summary[3] or 0,
    }


def get_bh_price_history(sku: str = "", title_like: str = "", days: int = 30) -> list[dict]:
    """获取某产品的价格历史"""
    conn = get_conn()
    
    sql = """
        SELECT title, price, rating, review_count, snapshot_at
        FROM bh_products
        WHERE snapshot_at >= datetime('now', ?)
    """
    params: list[Any] = [f"-{days} days"]
    
    if sku:
        sql += " AND sku = ?"
        params.append(sku)
    elif title_like:
        sql += " AND title LIKE ?"
        params.append(f"%{title_like}%")
    
    sql += " ORDER BY snapshot_at DESC LIMIT 100"
    
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
# 评论(vkpi_bh_reviews,迁移 207)— 竞品口碑数据源
# ──────────────────────────────────────────────

def select_bh_review_targets(limit: int = 20) -> list[dict]:
    """从 bh_products 最新快照选评论抓取目标:Viltrox 自家 + 主要竞品条目。

    竞品口径 = 库内既有 app.domains.market.content_brain.COMPETITOR_BRANDS 标题匹配
    (先判 viltrox 再判竞品,避免 "Viltrox ... for Sony E" 被误归竞品桶)。
    现状:search actor 只抓过 Viltrox 类目,库内暂无竞品条目;竞品桶为空时如实只回
    Viltrox 桶,等竞品产品源接入 bh_products 后本函数自动带上竞品。
    上限硬夹 20(每个产品 = 1 次付费 actor call)。
    """
    conn = get_conn()
    row = conn.execute("SELECT MAX(snapshot_at) FROM bh_products").fetchone()
    if not row or not row[0]:
        return []

    rows = conn.execute(
        """
        SELECT title, url, sku, review_count
        FROM bh_products
        WHERE snapshot_at = ? AND url != ''
        ORDER BY review_count DESC, rating DESC
        """,
        (row[0],),
    ).fetchall()

    try:
        from app.domains.market.content_brain import COMPETITOR_BRANDS
        competitor_terms = {str(b).strip().lower() for b in COMPETITOR_BRANDS if str(b).strip()}
    except Exception:
        logger.warning("bh.reviews.competitor_brands_unavailable", exc_info=True)
        competitor_terms = set()

    viltrox_bucket: list[dict] = []
    competitor_bucket: list[dict] = []
    seen_urls: set[str] = set()
    for r in rows:
        item = dict(r)
        url = str(item.get("url", "") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = str(item.get("title", "") or "")
        title_lower = title.lower()
        target = {
            "url": url,
            "title": title,
            "sku": str(item.get("sku", "") or ""),
            "review_count": int(item.get("review_count", 0) or 0),
        }
        if "viltrox" in title_lower:
            target["bucket"] = "viltrox"
            viltrox_bucket.append(target)
        elif any(term in title_lower for term in competitor_terms):
            target["bucket"] = "competitor"
            competitor_bucket.append(target)

    safe_limit = max(1, min(int(limit or 20), 20))
    comp_take = competitor_bucket[: safe_limit // 2]
    vil_take = viltrox_bucket[: safe_limit - len(comp_take)]
    selected = vil_take + comp_take
    logger.info(
        "bh.reviews.targets_selected",
        extra={"viltrox": len(vil_take), "competitor": len(comp_take), "limit": safe_limit},
    )
    return selected


def _upsert_bh_reviews_sync(reviews: list[dict], fetched_at: str) -> int:
    conn = get_conn()
    n = 0
    for r in reviews:
        try:
            body = str(r.get("body", "") or "")
            conn.execute(
                """
                INSERT INTO vkpi_bh_reviews
                    (product_url, product_name, sku, rating, title, body,
                     author, review_date, body_prefix, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (product_url, author, review_date, body_prefix)
                DO UPDATE SET
                    product_name = excluded.product_name,
                    sku = excluded.sku,
                    rating = excluded.rating,
                    title = excluded.title,
                    body = excluded.body,
                    fetched_at = excluded.fetched_at
                """,
                (
                    str(r.get("product_url", "") or "")[:500],
                    str(r.get("product_name", "") or "")[:300],
                    str(r.get("sku", "") or "")[:100],
                    float(r.get("rating", 0) or 0),
                    str(r.get("title", "") or "")[:200],
                    body[:4000],
                    str(r.get("author", "") or "")[:100],
                    str(r.get("review_date", "") or "")[:40],
                    body[:80],
                    fetched_at,
                ),
            )
            n += 1
        except Exception:
            logger.warning("bh.reviews.upsert_row_failed", exc_info=True)
            continue
    conn.commit()
    return n


async def upsert_bh_reviews(reviews: list[dict]) -> int:
    """批量 upsert 评论进 vkpi_bh_reviews;防重键 (product_url, author, review_date, body 前 80 字)。

    Returns: 处理成功的行数(含冲突更新)。
    """
    if not reviews:
        return 0
    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return await db_write(lambda: _upsert_bh_reviews_sync(reviews, fetched_at))


def get_bh_reviews_summary(top_products: int = 10) -> dict:
    """B&H 用户评论聚合(竞品口碑读端最小可见,喂 market_insights)。

    表未建(迁移 207 未 apply)或无数据时返回零值,不抛错。
    """
    empty = {
        "total_reviews": 0,
        "products_covered": 0,
        "avg_rating": 0,
        "low_star_count": 0,
        "latest_fetched_at": None,
        "top_products": [],
    }
    try:
        conn = get_conn()
        overall = conn.execute(
            "SELECT COUNT(*), AVG(rating), COUNT(DISTINCT product_url), MAX(fetched_at) FROM vkpi_bh_reviews"
        ).fetchone()
        total = int(overall[0] or 0)
        if total == 0:
            return empty
        low_star = conn.execute(
            "SELECT COUNT(*) FROM vkpi_bh_reviews WHERE rating > 0 AND rating <= 2"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT product_url,
                   MAX(product_name) AS product_name,
                   COUNT(*) AS review_count,
                   AVG(rating) AS avg_rating
            FROM vkpi_bh_reviews
            GROUP BY product_url
            ORDER BY review_count DESC
            LIMIT ?
            """,
            (max(1, int(top_products or 10)),),
        ).fetchall()
        top = []
        for r in rows:
            item = dict(r)
            top.append({
                "product_url": item.get("product_url", ""),
                "product_name": item.get("product_name", ""),
                "review_count": int(item.get("review_count", 0) or 0),
                "avg_rating": round(float(item.get("avg_rating", 0) or 0), 2),
            })
        return {
            "total_reviews": total,
            "products_covered": int(overall[2] or 0),
            "avg_rating": round(float(overall[1] or 0), 2),
            "low_star_count": int(low_star or 0),
            "latest_fetched_at": overall[3],
            "top_products": top,
        }
    except Exception:
        logger.debug("bh.reviews.summary_unavailable", exc_info=True)
        return empty


def get_bh_top_rated(limit: int = 10) -> list[dict]:
    """评分最高的产品 (最新 snapshot)"""
    conn = get_conn()
    row = conn.execute("SELECT MAX(snapshot_at) FROM bh_products").fetchone()
    if not row or not row[0]:
        return []
    
    rows = conn.execute(
        """
        SELECT title, price, rating, review_count, url
        FROM bh_products
        WHERE snapshot_at = ? AND rating > 0
        ORDER BY rating DESC, review_count DESC
        LIMIT ?
        """,
        (row[0], limit),
    ).fetchall()
    return [dict(r) for r in rows]
