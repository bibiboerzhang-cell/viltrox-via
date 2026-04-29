"""
services/intelligence/analytics.py — Cross-module analytics (batch 3)

Aggregates across submissions, orders, creators, and scoring to support the
Analytics admin page: trend series, correlations, pipeline health, cohorts,
and rank tables.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


# =========================================================================
# Trend (daily time series)
# =========================================================================

def trend_series(*, metric: str = "submissions", window_days: int = 30) -> dict:
    conn = get_conn()
    from_date = (datetime.utcnow() - timedelta(days=window_days)).isoformat()

    if metric == "submissions":
        q = """SELECT date(created_at) AS d, COUNT(*) AS v
               FROM submissions WHERE created_at >= ? GROUP BY d ORDER BY d"""
    elif metric == "gmv":
        q = """SELECT date(placed_at) AS d, SUM(subtotal_cents) AS v
               FROM orders WHERE placed_at >= ? AND status='paid' GROUP BY d ORDER BY d"""
    elif metric == "score":
        q = """SELECT date(created_at) AS d, AVG(final_score) AS v
               FROM submissions WHERE created_at >= ? AND final_score IS NOT NULL
               GROUP BY d ORDER BY d"""
    elif metric == "active_creators":
        q = """SELECT date(created_at) AS d, COUNT(DISTINCT extracted_handle) AS v
               FROM submissions WHERE created_at >= ? GROUP BY d ORDER BY d"""
    else:
        q = """SELECT date(created_at) AS d, COUNT(*) AS v
               FROM submissions WHERE created_at >= ? GROUP BY d ORDER BY d"""

    rows = conn.execute(q, (from_date,)).fetchall()
    return {
        "metric": metric,
        "series": [{"date": r["d"], "value": r["v"] or 0} for r in rows],
    }


# =========================================================================
# Correlations (cached)
# =========================================================================

def get_correlations() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT metric_a, metric_b, r, slope, lag_days, sample_size, computed_at
           FROM correlation_cache
           ORDER BY computed_at DESC
           LIMIT 50"""
    ).fetchall()
    # dedupe by metric pair (latest per pair)
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["metric_a"], r["metric_b"])
        if k not in best:
            best[k] = dict(r)
    return {"correlations": list(best.values())}


def compute_correlation(
    metric_a: str, metric_b: str, lag_days: int, window_days: int = 90
) -> dict:
    """
    Compute Pearson r between two daily series with optional lag (metric_a leads metric_b by lag_days).
    Writes result to correlation_cache.
    """
    a = trend_series(metric=metric_a, window_days=window_days)["series"]
    b = trend_series(metric=metric_b, window_days=window_days)["series"]

    a_map = {s["date"]: s["value"] for s in a}
    b_map = {s["date"]: s["value"] for s in b}

    pairs: list[tuple[float, float]] = []
    for d, va in a_map.items():
        d_shifted = (datetime.fromisoformat(d) + timedelta(days=lag_days)).date().isoformat()
        if d_shifted in b_map:
            pairs.append((float(va), float(b_map[d_shifted])))

    if len(pairs) < 5:
        return {"r": None, "sample_size": len(pairs)}

    n = len(pairs)
    sa = sum(p[0] for p in pairs)
    sb = sum(p[1] for p in pairs)
    sab = sum(p[0] * p[1] for p in pairs)
    saa = sum(p[0] ** 2 for p in pairs)
    sbb = sum(p[1] ** 2 for p in pairs)

    numer = n * sab - sa * sb
    denom = math.sqrt((n * saa - sa * sa) * (n * sbb - sb * sb))
    r = numer / denom if denom else 0.0
    slope = numer / (n * saa - sa * sa) if (n * saa - sa * sa) else 0.0

    conn = get_conn()
    conn.execute(
        """INSERT INTO correlation_cache
            (metric_a, metric_b, r, slope, lag_days, sample_size,
             window_start, window_end)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            metric_a, metric_b, r, slope, lag_days, n,
            (datetime.utcnow() - timedelta(days=window_days)).isoformat(),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()

    return {"r": round(r, 3), "slope": round(slope, 3), "sample_size": n, "lag_days": lag_days}


# =========================================================================
# Pipeline funnel
# =========================================================================

def pipeline_funnel(*, window: str = "7d") -> dict:
    days = 7 if window == "7d" else 30
    from_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = get_conn()

    submitted = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE created_at >= ?", (from_date,)
    ).fetchone()["n"]

    parsed_ok = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE created_at >= ? AND scraped_ok = 1",
        (from_date,),
    ).fetchone()["n"]

    ai_passed = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE created_at >= ? AND detection_status = 'VILTROX_DETECTED'",
        (from_date,),
    ).fetchone()["n"]

    scored = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE created_at >= ? AND final_score IS NOT NULL",
        (from_date,),
    ).fetchone()["n"]

    approved = conn.execute(
        "SELECT COUNT(*) AS n FROM submissions WHERE created_at >= ? AND recommendation = 'approve'",
        (from_date,),
    ).fetchone()["n"]

    orders_count = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE placed_at >= ? AND attribution_type != 'direct'",
        (from_date,),
    ).fetchone()["n"]

    gmv = conn.execute(
        "SELECT COALESCE(SUM(subtotal_cents),0) AS s FROM orders "
        "WHERE placed_at >= ? AND attribution_type != 'direct'",
        (from_date,),
    ).fetchone()["s"]

    return {
        "stages": [
            {"name": "submitted",    "count": submitted},
            {"name": "parsed_ok",    "count": parsed_ok},
            {"name": "ai_passed",    "count": ai_passed},
            {"name": "scored",       "count": scored},
            {"name": "approved",     "count": approved},
            {"name": "orders",       "count": orders_count},
        ],
        "gmv_cents": gmv,
        "window": window,
    }


# =========================================================================
# Rejection reasons
# =========================================================================

def rejection_reasons() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT memo AS reason, COUNT(*) AS n
           FROM submissions
           WHERE recommendation = 'reject' AND created_at > datetime('now','-30 days')
           GROUP BY memo ORDER BY n DESC LIMIT 20"""
    ).fetchall()
    return {"reasons": [{"reason": r["reason"] or "(unspecified)", "count": r["n"]} for r in rows]}


# =========================================================================
# Product series performance
# =========================================================================

def product_series_performance() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT product_series AS series, COUNT(*) AS count,
                  AVG(final_score) AS avg_score
           FROM submissions
           WHERE created_at > datetime('now','-30 days')
             AND product_series IS NOT NULL AND product_series != ''
           GROUP BY product_series ORDER BY count DESC"""
    ).fetchall()

    # GMV per series — best effort via items_json in orders
    series_gmv: dict[str, int] = {}
    orders = conn.execute(
        "SELECT subtotal_cents, items_json FROM orders "
        "WHERE placed_at > datetime('now','-30 days') AND status='paid'"
    ).fetchall()
    for o in orders:
        try:
            import json as _json
            items = _json.loads(o["items_json"] or "[]")
            for it in items:
                name = (it.get("name") or "").upper()
                for key in ["LAB", "AIR", "Z-MOUNT"]:
                    if key in name:
                        series_gmv[key] = series_gmv.get(key, 0) + o["subtotal_cents"]
        except Exception:
            continue

    out = []
    for r in rows:
        series = r["series"]
        out.append({
            "series": series,
            "submissions": r["count"],
            "avg_score": round(r["avg_score"] or 0, 1),
            "gmv_cents": series_gmv.get((series or "").upper(), 0),
        })
    return {"series": out}


# =========================================================================
# Cohort retention
# =========================================================================

def cohort_retention(*, cohort_type: str = "signup_month") -> dict:
    """Signup month → 30d/60d/90d retention (% of cohort that submitted within that window)."""
    conn = get_conn()
    cohorts = conn.execute(
        """SELECT strftime('%Y-%m', created_at) AS cohort, COUNT(*) AS size
           FROM users WHERE created_at > datetime('now','-6 months')
           GROUP BY cohort ORDER BY cohort"""
    ).fetchall()

    results = []
    for c in cohorts:
        cohort = c["cohort"]
        size = c["size"]
        active_30 = conn.execute(
            """SELECT COUNT(DISTINCT u.id) AS n FROM users u
               WHERE strftime('%Y-%m', u.created_at) = ?
                 AND EXISTS (SELECT 1 FROM submissions s
                             WHERE s.extracted_handle = u.creator_code
                               AND s.created_at BETWEEN u.created_at
                               AND datetime(u.created_at, '+30 days'))""",
            (cohort,),
        ).fetchone()["n"]
        results.append({
            "cohort": cohort,
            "size": size,
            "active_30d_pct": round(active_30 / max(size, 1) * 100, 1),
        })
    return {"cohorts": results}


# =========================================================================
# Creator rankings
# =========================================================================

def creator_rankings(*, metric: str = "combined", limit: int = 50) -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT extracted_handle AS handle, COUNT(*) AS submissions,
                  AVG(final_score) AS avg_score,
                  SUM(views) AS views
           FROM submissions
           WHERE created_at > datetime('now','-30 days')
             AND extracted_handle IS NOT NULL
           GROUP BY extracted_handle
           ORDER BY views DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        handle = r["handle"]
        gmv_row = conn.execute(
            "SELECT SUM(subtotal_cents) AS g FROM orders "
            "WHERE attribution_source = ? AND placed_at > datetime('now','-30 days')",
            (handle,),
        ).fetchone()
        out.append({
            "handle": handle,
            "submissions": r["submissions"],
            "avg_score": round(r["avg_score"] or 0, 1),
            "views": r["views"] or 0,
            "gmv_cents": gmv_row["g"] or 0,
        })
    return {"creators": out}
