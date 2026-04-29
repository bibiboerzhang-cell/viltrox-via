"""
services/memory/creator_memory.py — Level 3 seed memory snapshot
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from app.db.connection import get_conn
from app.services.scoring.creator import get_creator_profile


def _unique_preserve(values: List[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _normalize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "handle": profile.get("handle", ""),
        "platform": profile.get("platform", ""),
        "last_seen": profile.get("last_seen", ""),
        "submission_count": profile.get("submission_count", 0),
        "cameras": profile.get("cameras", []),
        "viltrox_lenses": profile.get("viltrox_lenses", []),
        "all_viltrox_products": profile.get("all_viltrox_products", []),
        "competitor_brands_seen": profile.get("competitor_brands_seen", []),
        "avg_scores": profile.get("avg_scores", {}),
        "weak_areas": profile.get("weak_areas", []),
        "trend": profile.get("trend", {}),
        "score_history": profile.get("score_history", [])[-5:],
    }


def build_creator_memory_snapshot(user_id: int) -> Dict[str, Any]:
    conn = get_conn()

    account_rows = conn.execute(
        "SELECT platform, handle, verified FROM user_social_accounts WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    submission_rows = conn.execute(
        """
        SELECT id, created_at, platform, extracted_handle, title, detection_status,
               overall_score, final_score, points_awarded, content_genre
        FROM submissions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (user_id,),
    ).fetchall()

    handles = _unique_preserve(
        [str(row["handle"] or "").strip() for row in account_rows]
        + [str(row["extracted_handle"] or "").strip() for row in submission_rows]
    )
    profiles = [
        _normalize_profile(profile)
        for profile in (get_creator_profile(handle) for handle in handles)
        if profile
    ]

    weak_counter: Counter[str] = Counter()
    cameras: List[str] = []
    lenses: List[str] = []
    products: List[str] = []
    competitors: List[str] = []
    trends: List[Dict[str, Any]] = []

    for profile in profiles:
        weak_counter.update(profile.get("weak_areas", []))
        cameras.extend(profile.get("cameras", []))
        lenses.extend(profile.get("viltrox_lenses", []))
        products.extend(profile.get("all_viltrox_products", []))
        competitors.extend(profile.get("competitor_brands_seen", []))
        if profile.get("trend"):
            trends.append(profile["trend"])

    primary_trend = {}
    if trends:
        primary_trend = sorted(
            trends,
            key=lambda item: (
                item.get("submission_count", 0),
                item.get("recent_overall", 0),
            ),
            reverse=True,
        )[0]

    recent_submissions = [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "platform": row["platform"],
            "handle": row["extracted_handle"],
            "title": row["title"],
            "status": row["detection_status"],
            "overall_score": row["overall_score"],
            "final_score": row["final_score"],
            "points_awarded": row["points_awarded"],
            "content_genre": row["content_genre"],
        }
        for row in submission_rows
    ]

    return {
        "ready": bool(handles or profiles or recent_submissions),
        "handles": handles,
        "linked_accounts": [dict(row) for row in account_rows],
        "memory_summary": {
            "known_cameras": _unique_preserve(cameras)[:10],
            "known_viltrox_lenses": _unique_preserve(lenses)[:10],
            "known_products": _unique_preserve(products)[:12],
            "competitor_brands_seen": _unique_preserve(competitors)[:12],
            "persistent_weak_areas": [
                {"area": area, "count": count}
                for area, count in weak_counter.most_common(5)
            ],
            "trend": primary_trend,
        },
        "profiles": profiles,
        "recent_submissions": recent_submissions,
    }
