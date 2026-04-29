"""
services/scoring/creator.py — 创作者画像 + 成长趋势
"""
from __future__ import annotations
import re

import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from app.core.config import CREATOR_DIR
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

def get_creator_profile(handle: str) -> dict:
    """Load creator's historical gear profile."""
    if not handle:
        return {}
    key = re.sub(r'[^a-z0-9_]', '_', handle.lower().lstrip('@'))
    path = CREATOR_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            logger.exception("failed to load creator profile | handle=%s", handle)
            return {}
    return {}

def update_creator_profile(handle: str, analysis: dict, platform: str):
    """Update creator's gear profile from new analysis."""
    if not handle or not analysis:
        return
    key = re.sub(r'[^a-z0-9_]', '_', handle.lower().lstrip('@'))
    path = CREATOR_DIR / f"{key}.json"
    try:
        profile = get_creator_profile(handle)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Update known gear
        if analysis.get("camera_body"):
            profile.setdefault("cameras", [])
            if analysis["camera_body"] not in profile["cameras"]:
                profile["cameras"].append(analysis["camera_body"])

        if analysis.get("viltrox_lens"):
            profile.setdefault("viltrox_lenses", [])
            if analysis["viltrox_lens"] not in profile["viltrox_lenses"]:
                profile["viltrox_lenses"].append(analysis["viltrox_lens"])

        vall = analysis.get("viltrox_products_all", [])
        if vall:
            profile.setdefault("all_viltrox_products", [])
            for p in vall:
                if p not in profile["all_viltrox_products"]:
                    profile["all_viltrox_products"].append(p)

        comp = analysis.get("competitor_brands", [])
        if comp:
            profile.setdefault("competitor_brands_seen", [])
            for b in comp:
                if b not in profile["competitor_brands_seen"]:
                    profile["competitor_brands_seen"].append(b)

        # ── Save quality score history for improvement context ──
        qs = analysis.get("quality_scores", {})
        if qs and isinstance(qs, dict):
            profile.setdefault("score_history", [])
            profile["score_history"].append({
                "ts": now,
                "scores": qs,
                "overall": analysis.get("quality_overall", 0),
                "genre": analysis.get("content_genre", ""),
            })
            # Keep only last 10 entries
            profile["score_history"] = profile["score_history"][-10:]

            # Compute running averages per dimension
            all_scores = profile["score_history"]
            dims = ["exposure","focus","stability","color_grade","composition",
                    "lighting","editing","storytelling","hook","viltrox_branding"]
            avg = {}
            for d in dims:
                vals = [s["scores"].get(d,0) for s in all_scores if s["scores"].get(d,0) > 0]
                avg[d] = round(sum(vals)/len(vals), 1) if vals else 0
            profile["avg_scores"] = avg
            # Identify persistent weak areas (avg < 7)
            profile["weak_areas"] = [d for d,v in avg.items() if 0 < v < 7]

        # ── Growth trend (requires 2+ entries) ──
        if len(profile.get("score_history", [])) >= 2:
            profile["trend"] = compute_creator_trend(profile["score_history"])

        profile["handle"] = handle
        profile["platform"] = platform
        profile["last_seen"] = now
        profile.setdefault("submission_count", 0)
        profile["submission_count"] += 1

        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.exception("profile update error | handle=%s | error=%s", handle, e)


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
        logger.exception("creator benchmark error | genre=%s | error=%s", genre, e)
        return {"percentile_tech": 0, "percentile_mkt": 0}


def get_all_benchmarks() -> dict:
    """Return all genre benchmark rows for API response."""
    try:
        conn = get_conn()
        rows = conn.execute("SELECT * FROM genre_benchmarks ORDER BY sample_count DESC").fetchall()
        return {r["genre"]: dict(r) for r in rows}
    except Exception:
        logger.exception("failed to load all benchmarks")
        return {}


# ──────────────────────────────────────────────
# Creator growth trend analysis
# ──────────────────────────────────────────────
def compute_creator_trend(score_history: list) -> dict:
    """
    Analyse score_history (list of {ts, scores, overall, genre}).
    Returns trend dict with direction, weak_streak, improving_dims, declining_dims.
    """
    if len(score_history) < 2:
        return {"direction": "new", "weak_streak": {}, "improving": [], "declining": []}

    dims = ["exposure","focus","stability","color_grade","composition",
            "lighting","editing","storytelling","hook","viltrox_branding"]

    recent  = score_history[-3:]   # last 3
    earlier = score_history[:-3]   # everything before

    def avg_dim(entries, dim):
        vals = [e["scores"].get(dim, 0) for e in entries if e["scores"].get(dim, 0) > 0]
        return sum(vals) / len(vals) if vals else 0

    improving, declining = [], []
    for d in dims:
        r = avg_dim(recent, d)
        e = avg_dim(earlier, d)
        if e > 0 and r > 0:
            delta = r - e
            if delta >= 0.5:
                improving.append({"dim": d, "delta": round(delta, 1)})
            elif delta <= -0.5:
                declining.append({"dim": d, "delta": round(delta, 1)})

    # Overall direction
    recent_overall  = sum(e.get("overall", 0) for e in recent) / len(recent)
    earlier_overall = sum(e.get("overall", 0) for e in earlier) / max(len(earlier), 1)
    delta_overall   = recent_overall - earlier_overall

    if delta_overall >= 0.5:
        direction = "improving"
    elif delta_overall <= -0.5:
        direction = "declining"
    else:
        direction = "stable"

    # Persistent weak areas: appeared weak (< 7) in last 3+ consecutive entries
    weak_streak = {}
    for d in dims:
        streak = 0
        for entry in reversed(score_history):
            v = entry["scores"].get(d, 0)
            if 0 < v < 7:
                streak += 1
            else:
                break
        if streak >= 3:
            weak_streak[d] = streak

    return {
        "direction":        direction,
        "delta_overall":    round(delta_overall, 1),
        "recent_overall":   round(recent_overall, 1),
        "improving":        sorted(improving, key=lambda x: -x["delta"])[:3],
        "declining":        sorted(declining, key=lambda x: x["delta"])[:3],
        "weak_streak":      weak_streak,
        "submission_count": len(score_history),
    }
