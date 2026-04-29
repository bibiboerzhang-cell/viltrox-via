"""
services/scoring/benchmark.py — 体裁基准线 + 百分位系统
"""
from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# Benchmark & percentile system
# ──────────────────────────────────────────────
def update_genre_benchmark(genre: str, tech_score: float, marketing_score: float) -> dict:
    """
    Recompute P25/P50/P75/P90 for this genre using all DB rows.
    Called after each submission. Returns percentiles for the new score.
    """
    if not genre or tech_score <= 0:
        return {"percentile_tech": 0, "percentile_mkt": 0}
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT tech_score, marketing_score FROM submissions "
            "WHERE content_genre=? AND tech_score>0 ORDER BY tech_score",
            (genre,)
        ).fetchall()

        if len(rows) < 3:
                        return {"percentile_tech": 50, "percentile_mkt": 50}

        tech_vals = sorted([r[0] for r in rows if r[0] and r[0] > 0])
        mkt_vals  = sorted([r[1] for r in rows if r[1] and r[1] > 0])

        def percentile(vals, v):
            if not vals:
                return 50
            below = sum(1 for x in vals if x < v)
            return round(below / len(vals) * 100)

        def pct_at(vals, p):
            if not vals:
                return 0
            idx = max(0, int(len(vals) * p / 100) - 1)
            return round(vals[idx], 1)

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("""
            INSERT INTO genre_benchmarks
                (genre, sample_count, p25_tech, p50_tech, p75_tech, p90_tech,
                 p25_mkt, p50_mkt, p75_mkt, p90_mkt, avg_overall, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(genre) DO UPDATE SET
                sample_count=excluded.sample_count,
                p25_tech=excluded.p25_tech, p50_tech=excluded.p50_tech,
                p75_tech=excluded.p75_tech, p90_tech=excluded.p90_tech,
                p25_mkt=excluded.p25_mkt,  p50_mkt=excluded.p50_mkt,
                p75_mkt=excluded.p75_mkt,  p90_mkt=excluded.p90_mkt,
                avg_overall=excluded.avg_overall, updated_at=excluded.updated_at
        """, (
            genre, len(tech_vals),
            pct_at(tech_vals,25), pct_at(tech_vals,50),
            pct_at(tech_vals,75), pct_at(tech_vals,90),
            pct_at(mkt_vals,25),  pct_at(mkt_vals,50),
            pct_at(mkt_vals,75),  pct_at(mkt_vals,90),
            round(sum(tech_vals)/len(tech_vals), 1) if tech_vals else 0,
            now
        ))
        conn.commit()
        return {
            "percentile_tech": percentile(tech_vals, tech_score),
            "percentile_mkt":  percentile(mkt_vals,  marketing_score),
        }
    except Exception as e:
        logger.exception("benchmark update error | genre=%s | error=%s", genre, e)
        return {"percentile_tech": 0, "percentile_mkt": 0}


def get_all_benchmarks() -> dict:
    """Return all genre benchmark rows for API response."""
    try:
        conn = get_conn()
        rows = conn.execute("SELECT * FROM genre_benchmarks ORDER BY sample_count DESC").fetchall()
        return {r["genre"]: dict(r) for r in rows}
    except Exception:
        logger.exception("failed to fetch genre benchmarks")
        return {}
