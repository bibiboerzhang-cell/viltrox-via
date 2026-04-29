"""
services/intelligence/dashboard.py — Admin 情报大盘数据聚合
============================================================
聚合 B&H 数据成 admin dashboard 需要的形态.

包含:
  - 概览统计 (产品数/平均价/平均分/总评论)
  - 热门产品 (按 review_count 排序)
  - 满分产品 (rating = 5.0)
  - 价格分布 (入门/中端/高端/旗舰)
  - 系列分布 (PRO/LAB/EVO/AIR)
  - 卡口分布 (Sony E / Nikon Z / FUJI X / L-Mount)
"""
from __future__ import annotations

import re
from typing import Any

from app.db.connection import get_conn


# ──────────────────────────────────────────────
# 系列识别 (从标题提取)
# ──────────────────────────────────────────────

SERIES_PATTERNS = [
    ("LAB",     re.compile(r"\bLAB\b", re.I)),
    ("PRO",     re.compile(r"\bPro\b", re.I)),
    ("EVO",     re.compile(r"\bEVO\b", re.I)),
    ("AIR",     re.compile(r"\bAir\b", re.I)),
    ("VINTAGE", re.compile(r"\bVintage\b", re.I)),
]


def detect_series(title: str) -> str:
    """从产品标题提取系列名"""
    if not title:
        return "STANDARD"
    for name, pat in SERIES_PATTERNS:
        if pat.search(title):
            return name
    return "STANDARD"


# ──────────────────────────────────────────────
# 卡口识别
# ──────────────────────────────────────────────

MOUNT_PATTERNS = [
    ("Sony E",   re.compile(r"Sony\s*E|FE\b", re.I)),
    ("Nikon Z",  re.compile(r"Nikon\s*Z\b", re.I)),
    ("FUJI X",   re.compile(r"FUJIFILM|FUJI\s*X|\bX\s*mount", re.I)),
    ("L-Mount",  re.compile(r"L[- ]?Mount", re.I)),
    ("Canon RF", re.compile(r"Canon\s*RF", re.I)),
    ("M43",      re.compile(r"M4/?3|Micro\s*Four", re.I)),
]


def detect_mount(title: str) -> str:
    if not title:
        return "Unknown"
    for name, pat in MOUNT_PATTERNS:
        if pat.search(title):
            return name
    return "Unknown"


# ──────────────────────────────────────────────
# 价格分段
# ──────────────────────────────────────────────

def price_tier(price: float) -> str:
    if price <= 0:
        return "Unknown"
    if price < 200:
        return "Entry ($99-199)"
    if price < 400:
        return "Mid ($200-399)"
    if price < 700:
        return "High ($400-699)"
    return "Flagship ($700+)"


# ──────────────────────────────────────────────
# 焦段提取
# ──────────────────────────────────────────────

FOCAL_PATTERN = re.compile(r"(\d+)mm")

def extract_focal(title: str) -> int:
    """从 '16mm f/1.8' 提取 16"""
    if not title:
        return 0
    m = FOCAL_PATTERN.search(title)
    return int(m.group(1)) if m else 0


def focal_category(focal: int) -> str:
    if focal == 0:
        return "Unknown"
    if focal < 16:
        return "Ultra Wide (<16mm)"
    if focal < 35:
        return "Wide (16-34mm)"
    if focal < 70:
        return "Standard (35-69mm)"
    if focal < 135:
        return "Short Tele (70-134mm)"
    return "Tele (135mm+)"


# ──────────────────────────────────────────────
# 主 dashboard 聚合
# ──────────────────────────────────────────────

def build_bh_dashboard() -> dict:
    """
    构建完整 admin dashboard 数据.
    只用最新 snapshot.
    """
    conn = get_conn()
    
    # 找最新 snapshot
    row = conn.execute("SELECT MAX(snapshot_at) FROM bh_products").fetchone()
    if not row or not row[0]:
        return {
            "has_data": False,
            "message": "No B&H data yet. Run /api/admin/intel/bh/refresh-now first.",
        }
    
    latest_snapshot = row[0]
    
    # 拿全部最新产品
    rows = conn.execute(
        """
        SELECT id, title, price, rating, review_count, url, image_url, 
               in_stock, sku, snapshot_at
        FROM bh_products
        WHERE snapshot_at = ?
        ORDER BY review_count DESC, rating DESC
        """,
        (latest_snapshot,),
    ).fetchall()
    
    products = [dict(r) for r in rows]
    
    if not products:
        return {"has_data": False, "message": "Empty snapshot"}
    
    # ── 概览 ──
    total = len(products)
    priced_products = [p for p in products if p["price"] > 0]
    rated_products = [p for p in products if p["rating"] > 0]
    
    avg_price = round(sum(p["price"] for p in priced_products) / len(priced_products), 2) if priced_products else 0
    avg_rating = round(sum(p["rating"] for p in rated_products) / len(rated_products), 2) if rated_products else 0
    total_reviews = sum(p["review_count"] for p in products)
    in_stock_count = sum(1 for p in products if p.get("in_stock"))
    
    # ── Top 热门 (按 review_count) ──
    top_popular = sorted(products, key=lambda p: p["review_count"], reverse=True)[:5]
    
    # ── 满分产品 ──
    top_rated = [p for p in products if p["rating"] >= 4.8][:10]
    top_rated.sort(key=lambda p: (p["rating"], p["review_count"]), reverse=True)
    
    # ── 价格分布 ──
    price_buckets: dict[str, list] = {}
    for p in products:
        tier = price_tier(p["price"])
        price_buckets.setdefault(tier, []).append(p)
    
    price_dist = [
        {
            "tier": tier,
            "count": len(items),
            "avg_rating": round(sum(p["rating"] for p in items if p["rating"] > 0) / max(1, sum(1 for p in items if p["rating"] > 0)), 2),
            "products": [p["title"][:50] for p in items[:3]],
        }
        for tier, items in sorted(price_buckets.items())
    ]
    
    # ── 系列分布 ──
    series_buckets: dict[str, list] = {}
    for p in products:
        s = detect_series(p["title"])
        series_buckets.setdefault(s, []).append(p)
    
    series_dist = [
        {
            "series": s,
            "count": len(items),
            "min_price": min(p["price"] for p in items if p["price"] > 0) if any(p["price"] > 0 for p in items) else 0,
            "max_price": max(p["price"] for p in items),
            "avg_rating": round(sum(p["rating"] for p in items if p["rating"] > 0) / max(1, sum(1 for p in items if p["rating"] > 0)), 2),
        }
        for s, items in sorted(series_buckets.items(), key=lambda x: -len(x[1]))
    ]
    
    # ── 卡口分布 ──
    mount_buckets: dict[str, int] = {}
    for p in products:
        m = detect_mount(p["title"])
        mount_buckets[m] = mount_buckets.get(m, 0) + 1
    
    mount_dist = [
        {"mount": m, "count": c}
        for m, c in sorted(mount_buckets.items(), key=lambda x: -x[1])
    ]
    
    # ── 焦段分布 ──
    focal_buckets: dict[str, int] = {}
    for p in products:
        f = extract_focal(p["title"])
        fc = focal_category(f)
        focal_buckets[fc] = focal_buckets.get(fc, 0) + 1
    
    focal_dist = [
        {"range": fc, "count": c}
        for fc, c in sorted(focal_buckets.items(), key=lambda x: -x[1])
    ]
    
    # ── 返回 ──
    return {
        "has_data": True,
        "snapshot_at": latest_snapshot,
        "overview": {
            "total_products": total,
            "in_stock": in_stock_count,
            "avg_price": avg_price,
            "avg_rating": avg_rating,
            "total_reviews": total_reviews,
            "snapshot_count": conn.execute(
                "SELECT COUNT(DISTINCT snapshot_at) FROM bh_products"
            ).fetchone()[0],
        },
        "top_popular": [
            {
                "title": p["title"],
                "price": p["price"],
                "rating": p["rating"],
                "review_count": p["review_count"],
                "url": p["url"],
                "image_url": p["image_url"],
                "series": detect_series(p["title"]),
                "mount": detect_mount(p["title"]),
                "focal": extract_focal(p["title"]),
            }
            for p in top_popular
        ],
        "top_rated": [
            {
                "title": p["title"],
                "price": p["price"],
                "rating": p["rating"],
                "review_count": p["review_count"],
                "url": p["url"],
                "image_url": p["image_url"],
                "series": detect_series(p["title"]),
            }
            for p in top_rated
        ],
        "price_distribution": price_dist,
        "series_distribution": series_dist,
        "mount_distribution": mount_dist,
        "focal_distribution": focal_dist,
        "all_products": [
            {
                "title": p["title"],
                "price": p["price"],
                "rating": p["rating"],
                "review_count": p["review_count"],
                "url": p["url"],
                "sku": p["sku"],
                "in_stock": bool(p.get("in_stock")),
                "series": detect_series(p["title"]),
                "mount": detect_mount(p["title"]),
                "focal": extract_focal(p["title"]),
            }
            for p in products
        ],
    }
