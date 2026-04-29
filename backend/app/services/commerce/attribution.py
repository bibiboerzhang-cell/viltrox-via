"""
services/commerce/attribution.py — Attribution funnel + analytics

Data flow:
  impressions (from social platform metrics, not yet wired — estimated)
  -> clicks (attribution_clicks table)
  -> shopify visits (we can infer from session_id presence in clicks)
  -> add_to_cart (shopify webhook 'cart/update' — future)
  -> orders (orders table with attribution_source != 'direct')
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn


def _default_window() -> tuple[str, str]:
    to_ = datetime.utcnow()
    from_ = to_ - timedelta(days=30)
    return from_.isoformat(), to_.isoformat()


# =========================================================================
# Overview
# =========================================================================

def compute_overview(from_date: str | None, to_date: str | None) -> dict:
    from_date, to_date = from_date or _default_window()[0], to_date or _default_window()[1]
    conn = get_conn()

    buckets = conn.execute(
        """
        SELECT attribution_type,
               COUNT(*) AS orders,
               COALESCE(SUM(subtotal_cents), 0) AS gmv,
               COALESCE(SUM(commission_cents), 0) AS commission
        FROM orders
        WHERE placed_at BETWEEN ? AND ?
          AND status = 'paid'
        GROUP BY attribution_type
        """,
        (from_date, to_date),
    ).fetchall()

    by_type: dict[str, dict[str, int]] = {
        "creator": {"orders": 0, "gmv": 0, "commission": 0},
        "student": {"orders": 0, "gmv": 0, "commission": 0},
        "direct":  {"orders": 0, "gmv": 0, "commission": 0},
    }
    for b in buckets:
        if b["attribution_type"] in by_type:
            by_type[b["attribution_type"]] = {
                "orders": b["orders"],
                "gmv": b["gmv"],
                "commission": b["commission"],
            }

    total_gmv = sum(v["gmv"] for v in by_type.values()) or 1

    breakdown = {
        k: {
            **v,
            "pct": round(v["gmv"] / total_gmv * 100, 1),
            "rate_bps": 1000 if k == "student" else 800 if k == "creator" else 0,
        }
        for k, v in by_type.items()
    }

    top_creators = creator_performance(limit=6)["creators"]

    commission_breakdown = {
        "regular_8pct": by_type["creator"]["commission"],
        "student_10pct": by_type["student"]["commission"],
        "special": 0,
    }

    # Hourly pattern
    hourly = conn.execute(
        """
        SELECT CAST(strftime('%H', placed_at) AS INTEGER) AS hr, COUNT(*) AS n
        FROM orders
        WHERE placed_at BETWEEN ? AND ? AND attribution_type != 'direct'
        GROUP BY hr
        """,
        (from_date, to_date),
    ).fetchall()
    hours = [0] * 24
    for h in hourly:
        hours[h["hr"] or 0] = h["n"]

    return {
        "breakdown": breakdown,
        "top_creators": top_creators,
        "commission_breakdown": commission_breakdown,
        "hourly_pattern": hours,
        "window": {"from": from_date, "to": to_date},
    }


# =========================================================================
# Funnel
# =========================================================================

def compute_funnel(from_date: str | None, to_date: str | None) -> dict:
    from_date, to_date = from_date or _default_window()[0], to_date or _default_window()[1]
    conn = get_conn()

    # Clicks
    click_total = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT session_id) AS visits "
        "FROM attribution_clicks WHERE clicked_at BETWEEN ? AND ?",
        (from_date, to_date),
    ).fetchone()
    clicks = click_total["n"] or 0
    visits = click_total["visits"] or 0

    # Orders with attribution
    orders_row = conn.execute(
        "SELECT COUNT(*) AS n FROM orders "
        "WHERE placed_at BETWEEN ? AND ? AND attribution_type != 'direct'",
        (from_date, to_date),
    ).fetchone()
    orders = orders_row["n"] or 0

    # Impressions (estimated — clicks × 20 if we don't have real tracking)
    # TODO: replace with real impression tracking from platform_metrics if added
    views = clicks * 20 if clicks else 0

    # Add-to-cart (not yet wired) — estimate 1.7x orders
    atc = int(orders * 1.7)

    stages = [
        {"name": "views",      "label": "Content views", "count": views},
        {"name": "clicks",     "label": "Link clicks",    "count": clicks},
        {"name": "visits",     "label": "Shopify visits", "count": visits},
        {"name": "add_to_cart","label": "Add to cart",    "count": atc},
        {"name": "orders",     "label": "Orders",         "count": orders},
    ]
    # compute rates
    for i, s in enumerate(stages):
        if i == 0:
            s["rate"] = 100.0
            s["drop_to_next"] = None
        else:
            prev = stages[i - 1]["count"] or 1
            s["rate"] = round(s["count"] / prev * 100, 1)
            stages[i - 1]["drop_to_next"] = round(
                (1 - s["count"] / prev) * 100, 1
            )

    insights = _funnel_insights(stages)

    return {"stages": stages, "insights": insights, "window": {"from": from_date, "to": to_date}}


def _funnel_insights(stages: list[dict]) -> list[dict]:
    insights: list[dict] = []
    # Worst drop
    drops = [(s["name"], stages[i + 1]["name"], s.get("drop_to_next") or 0)
             for i, s in enumerate(stages[:-1])]
    worst = max(drops, key=lambda x: x[2], default=None)
    if worst and worst[2] > 60:
        insights.append({
            "severity": "amber",
            "text": f"Biggest drop: {worst[0]} → {worst[1]} ({worst[2]}%). "
                    f"Investigate mobile redirect speed, UTM tags, regional store mismatches."
        })
    return insights


# =========================================================================
# Per-creator, platform, UTM
# =========================================================================

def creator_performance(*, order_by: str = "gmv", limit: int = 50) -> dict:
    conn = get_conn()
    col_map = {
        "gmv": "gmv DESC",
        "orders": "orders DESC",
        "ctr": "ctr DESC",
    }
    ordering = col_map.get(order_by, "gmv DESC")

    # Note: ctr requires click counts per ref_code
    rows = conn.execute(
        f"""
        SELECT
            o.attribution_source AS handle,
            o.attribution_type   AS type,
            COUNT(*)             AS orders,
            SUM(o.subtotal_cents) AS gmv,
            SUM(o.commission_cents) AS commission,
            (SELECT COUNT(*) FROM attribution_clicks c
               WHERE c.ref_code = o.attribution_source) AS clicks
        FROM orders o
        WHERE o.status = 'paid' AND o.attribution_type != 'direct'
          AND CAST(o.placed_at AS TIMESTAMP) > datetime('now', '-30 days')
        GROUP BY o.attribution_source, o.attribution_type
        ORDER BY {ordering}
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    out = []
    for r in rows:
        clicks = r["clicks"] or 0
        ctr = round((r["orders"] / clicks) * 100, 1) if clicks else 0.0
        out.append({
            "handle": r["handle"],
            "type": r["type"],
            "orders": r["orders"],
            "gmv": r["gmv"],
            "commission": r["commission"],
            "clicks": clicks,
            "ctr_pct": ctr,
            "aov_cents": int(r["gmv"] / max(r["orders"], 1)),
        })
    return {"creators": out}


def platform_breakdown() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT utm_source AS source,
               COUNT(*) AS orders,
               SUM(subtotal_cents) AS gmv,
               (SELECT COUNT(*) FROM attribution_clicks c
                WHERE c.utm_source = o.utm_source
                  AND CAST(c.clicked_at AS TIMESTAMP) > datetime('now', '-30 days')) AS clicks
        FROM orders o
        WHERE status = 'paid' AND CAST(placed_at AS TIMESTAMP) > datetime('now', '-30 days')
        GROUP BY utm_source
        """,
    ).fetchall()

    return {
        "platforms": [
            {
                "source": r["source"] or "(none)",
                "orders": r["orders"],
                "gmv": r["gmv"],
                "clicks": r["clicks"] or 0,
                "conversion_pct": round(
                    (r["orders"] / max(r["clicks"] or 1, 1)) * 100, 2
                ),
            }
            for r in rows
        ]
    }


def utm_performance(from_date: str | None, to_date: str | None) -> dict:
    from_date, to_date = from_date or _default_window()[0], to_date or _default_window()[1]
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT utm_source AS source, utm_medium AS medium,
               COUNT(*) AS orders, SUM(subtotal_cents) AS gmv
        FROM orders
        WHERE placed_at BETWEEN ? AND ? AND status = 'paid'
        GROUP BY utm_source, utm_medium
        ORDER BY gmv DESC
        """,
        (from_date, to_date),
    ).fetchall()

    results = []
    for r in rows:
        # sessions = distinct session_ids with this source+medium
        session_row = conn.execute(
            "SELECT COUNT(DISTINCT session_id) AS n FROM attribution_clicks "
            "WHERE utm_source = ? AND utm_medium = ? AND clicked_at BETWEEN ? AND ?",
            (r["source"], r["medium"], from_date, to_date),
        ).fetchone()
        sessions = session_row["n"] or 0
        results.append({
            "source": r["source"] or "(none)",
            "medium": r["medium"] or "(none)",
            "sessions": sessions,
            "orders": r["orders"],
            "gmv": r["gmv"],
            "conversion_pct": round(
                (r["orders"] / sessions) * 100, 2
            ) if sessions else 0.0,
        })
    return {"utm_sources": results}
