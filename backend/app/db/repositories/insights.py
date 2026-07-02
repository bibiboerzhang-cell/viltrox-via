"""
db/repositories/insights.py — 洞察缓存 CRUD
"""
from __future__ import annotations

import json
from datetime import datetime

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.scoring.benchmark import get_all_benchmarks

logger = get_logger(__name__)

# ──────────────────────────────────────────────
def compute_market_insights(days: int = 90) -> dict:
    """
    Aggregate insights from submissions DB.
    Cached in insights_cache table, refreshed when called.
    """
    try:
        conn = get_conn()
        cutoff = f"datetime('now', '-{days} days')"

        # Content genre distribution
        genre_dist = conn.execute(f"""
            SELECT content_genre, COUNT(*) as n,
                   ROUND(AVG(tech_score),1) as avg_tech,
                   ROUND(AVG(marketing_score),1) as avg_mkt,
                   ROUND(AVG(overall_score),1) as avg_overall
            FROM submissions
            WHERE content_genre != '' AND content_genre IS NOT NULL
              AND created_at >= {cutoff}
            GROUP BY content_genre ORDER BY n DESC
        """).fetchall()

        # Platform distribution
        platform_dist = conn.execute(f"""
            SELECT platform, COUNT(*) as n,
                   ROUND(AVG(final_score),1) as avg_score
            FROM submissions
            WHERE created_at >= {cutoff}
            GROUP BY platform ORDER BY n DESC
        """).fetchall()

        # Top product series
        series_dist = conn.execute(f"""
            SELECT product_series, COUNT(*) as n,
                   ROUND(AVG(marketing_score),1) as avg_mkt
            FROM submissions
            WHERE product_series != '' AND created_at >= {cutoff}
            GROUP BY product_series ORDER BY n DESC LIMIT 10
        """).fetchall()

        # High-score video traits (top 20% by overall_score)
        threshold_row = conn.execute(
            "SELECT overall_score FROM submissions WHERE overall_score > 0 "
            "ORDER BY overall_score DESC LIMIT 1"
        ).fetchone()
        top_threshold = (threshold_row[0] * 0.8) if threshold_row else 300

        top_genres = conn.execute(f"""
            SELECT content_genre, COUNT(*) as n,
                   ROUND(AVG(tech_score),1) as tech,
                   ROUND(AVG(marketing_score),1) as mkt
            FROM submissions
            WHERE overall_score >= ? AND content_genre != ''
              AND created_at >= {cutoff}
            GROUP BY content_genre ORDER BY n DESC LIMIT 5
        """, (top_threshold,)).fetchall()

        # Score trend by week
        weekly_trend = conn.execute(f"""
            SELECT strftime('%Y-W%W', created_at) as week,
                   COUNT(*) as n,
                   ROUND(AVG(tech_score),1) as avg_tech,
                   ROUND(AVG(marketing_score),1) as avg_mkt,
                   ROUND(AVG(overall_score),1) as avg_overall
            FROM submissions
            WHERE created_at >= {cutoff} AND tech_score > 0
            GROUP BY week ORDER BY week ASC
        """).fetchall()

        # Creator growth leaders (most improved in last 30 days vs prior 30)
        growth_leaders = []
        handles = conn.execute("""
            SELECT extracted_handle, COUNT(*) as n
            FROM submissions
            WHERE extracted_handle != '' AND extracted_handle IS NOT NULL
            GROUP BY extracted_handle HAVING n >= 3
            ORDER BY n DESC LIMIT 30
        """).fetchall()

        for row in handles:
            handle = row["extracted_handle"]
            recent_avg = conn.execute("""
                SELECT ROUND(AVG(overall_score),1) FROM submissions
                WHERE extracted_handle=? AND created_at >= datetime('now','-30 days')
            """, (handle,)).fetchone()[0]
            prior_avg = conn.execute("""
                SELECT ROUND(AVG(overall_score),1) FROM submissions
                WHERE extracted_handle=?
                  AND created_at < datetime('now','-30 days')
                  AND created_at >= datetime('now','-60 days')
            """, (handle,)).fetchone()[0]
            if recent_avg and prior_avg and recent_avg > prior_avg:
                growth_leaders.append({
                    "handle": handle,
                    "recent_avg": recent_avg,
                    "prior_avg":  prior_avg,
                    "delta":      round(recent_avg - prior_avg, 1),
                })
        growth_leaders.sort(key=lambda x: -x["delta"])

        # Common weak areas across all creators
        all_va = conn.execute(
            "SELECT video_analysis FROM submissions WHERE video_analysis IS NOT NULL "
            "AND created_at >= datetime('now','-30 days') LIMIT 200"
        ).fetchall()
        dim_totals = {}
        dim_counts = {}
        # Competitor aggregation
        comp_brand_count   = {}   # brand → total appearances
        comp_brand_videos  = {}   # brand → set of video ids
        comp_product_count = {}   # "brand model" → count
        comp_context_count = {}   # context type → count
        comp_win_loss      = {}   # brand → {wins, losses, neutral}

        all_va_with_id = conn.execute(
            "SELECT id, video_analysis FROM submissions WHERE video_analysis IS NOT NULL "
            "AND created_at >= datetime('now','-90 days') LIMIT 500"
        ).fetchall()

        for row in all_va_with_id:
            vid_id = row[0]
            try:
                va = json.loads(row[1])
                qs = va.get("quality_scores", {})
                for d, v in qs.items():
                    if isinstance(v, (int, float)) and v > 0:
                        dim_totals[d] = dim_totals.get(d, 0) + v
                        dim_counts[d] = dim_counts.get(d, 0) + 1

                # Aggregate competitor_products (new detailed format)
                for cp in va.get("competitor_products", []):
                    brand   = (cp.get("brand") or "").strip()
                    model   = (cp.get("model") or "").strip()
                    context = (cp.get("context") or "mentioned-only").strip()
                    result_s= (cp.get("sentiment") or "neutral").strip()
                    if not brand:
                        continue
                    comp_brand_count[brand] = comp_brand_count.get(brand, 0) + 1
                    comp_brand_videos.setdefault(brand, set()).add(vid_id)
                    if model:
                        key = f"{brand} {model}"
                        comp_product_count[key] = comp_product_count.get(key, 0) + 1
                    comp_context_count[context] = comp_context_count.get(context, 0) + 1
                    wl = comp_win_loss.setdefault(brand, {"wins":0,"losses":0,"neutral":0})
                    if result_s == "viltrox_wins":    wl["wins"]    += 1
                    elif result_s == "viltrox_loses": wl["losses"]  += 1
                    else:                             wl["neutral"] += 1

                # Fallback: competitor_brands list (old format)
                for brand in va.get("competitor_brands", []):
                    if brand and brand not in comp_brand_count:
                        comp_brand_count[brand] = comp_brand_count.get(brand, 0) + 1
                        comp_brand_videos.setdefault(brand, set()).add(vid_id)

                # Also scan competitor_mentions
                for cm in va.get("competitor_mentions", []):
                    brand = (cm.get("brand") or "").strip()
                    if brand:
                        comp_brand_count[brand] = comp_brand_count.get(brand, 0) + 1
                        comp_brand_videos.setdefault(brand, set()).add(vid_id)

            except Exception as exc:
                logger.debug("insights video_analysis row skipped: %s", exc)

        common_weak = sorted(
            [{"dim": d, "avg": round(dim_totals[d]/dim_counts[d], 1)}
             for d in dim_totals if dim_counts[d] >= 5],
            key=lambda x: x["avg"]
        )[:5]

        # Build competitor leaderboard
        comp_leaderboard = sorted([
            {
                "brand":       brand,
                "appearances": comp_brand_count[brand],
                "videos":      len(comp_brand_videos.get(brand, set())),
                "win_loss":    comp_win_loss.get(brand, {"wins":0,"losses":0,"neutral":0}),
            }
            for brand in comp_brand_count
        ], key=lambda x: -x["appearances"])[:15]

        comp_products_top = sorted([
            {"product": k, "count": v}
            for k, v in comp_product_count.items()
        ], key=lambda x: -x["count"])[:10]

        # B&H 用户评论口碑(vkpi_bh_reviews,迁移 207;reviews actor 手动脚本喂数)
        # 表未建或空时返回零值段,不影响其余洞察。
        try:
            from app.services.intelligence.bh_repository import get_bh_reviews_summary
            bh_review_sentiment = get_bh_reviews_summary(top_products=10)
        except Exception as exc:
            logger.debug("insights bh review summary skipped: %s", exc)
            bh_review_sentiment = {}

        result = {
            "period_days":          days,
            "genre_dist":           [dict(r) for r in genre_dist],
            "platform_dist":        [dict(r) for r in platform_dist],
            "series_dist":          [dict(r) for r in series_dist],
            "top_genres_highscore": [dict(r) for r in top_genres],
            "weekly_trend":         [dict(r) for r in weekly_trend],
            "growth_leaders":       growth_leaders[:10],
            "common_weak_dims":     common_weak,
            "competitor_brands":    comp_leaderboard,
            "competitor_products":  comp_products_top,
            "competitor_contexts":  comp_context_count,
            "bh_review_sentiment":  bh_review_sentiment,
            "benchmarks":           get_all_benchmarks(),
            "computed_at":          datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Cache it
        try:
            conn2 = get_conn()
            conn2.execute(
                "INSERT INTO insights_cache(key,value,updated_at) VALUES('main',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (json.dumps(result, ensure_ascii=False),
                 result["computed_at"])
            )
            conn2.commit()
        except Exception as exc:
            logger.warning("insights cache write failed: %s", exc)

        return result
    except Exception as e:
        logger.exception("insights.compute_market_insights_failed", extra={"days": days})
        return {"error": str(e)}
