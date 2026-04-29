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
