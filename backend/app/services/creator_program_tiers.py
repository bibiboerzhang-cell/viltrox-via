"""
Creator program tier and wardrobe calculations.
"""
from __future__ import annotations

from typing import Any


PENDING_TIER = {
    "key": "pending",
    "label": "Pending",
    "badge_text": "AWAITING VIDEO",
    "min_points": 0,
    "min_confirmed_videos": 1,
    "commission_rate": 0.0,
    "points_multiplier": 1.0,
}

VIP_TIERS = [
    {
        "key": "bronze",
        "label": "Bronze",
        "min_points": 0,
        "min_confirmed_videos": 1,
        "commission_rate": 0.05,
        "points_multiplier": 1.0,
        "badge_text": "BRONZE",
    },
    {
        "key": "silver",
        "label": "Silver",
        "min_points": 500,
        "min_confirmed_videos": 3,
        "commission_rate": 0.06,
        "points_multiplier": 1.2,
        "badge_text": "SILVER",
    },
    {
        "key": "gold",
        "label": "Gold",
        "min_points": 2000,
        "min_confirmed_videos": 8,
        "commission_rate": 0.07,
        "points_multiplier": 1.5,
        "badge_text": "GOLD",
    },
    {
        "key": "platinum",
        "label": "Platinum",
        "min_points": 5000,
        "min_confirmed_videos": 20,
        "commission_rate": 0.10,
        "points_multiplier": 2.0,
        "badge_text": "PLATINUM",
    },
]

VIP_TIER_MAP = {str(item["key"]).strip().lower(): item for item in VIP_TIERS}
MANUAL_TIER_STATUSES = set(VIP_TIER_MAP.keys())

OUTFIT_UNLOCKS = [
    {"key": "viltrox_core_black", "label": "Core Black", "unlock_points": 0},
    {"key": "studio_signal_orange", "label": "Studio Signal", "unlock_points": 500},
    {"key": "field_runner", "label": "Field Runner", "unlock_points": 2000},
    {"key": "catographer_pro", "label": "Catographer Pro", "unlock_points": 5000},
]


def _count_threshold_progress(current: int, floor: int, ceiling: int) -> float:
    if ceiling <= floor:
        return 1.0
    return max(0.0, min(1.0, (current - floor) / max(1, ceiling - floor)))


def compute_vip_snapshot(
    points_total: int | float = 0,
    confirmed_videos: int = 0,
    tier_status: str = "pending",
) -> dict[str, Any]:
    current_points = max(0, int(float(points_total or 0)))
    current_videos = max(0, int(confirmed_videos or 0))
    status_key = str(tier_status or "pending").strip().lower()
    manual_tier = VIP_TIER_MAP.get(status_key)
    is_active = bool(manual_tier) or (status_key == "active" and current_videos >= 1)

    if not is_active:
        points_progress = _count_threshold_progress(current_points, 0, 500)
        video_progress = _count_threshold_progress(current_videos, 0, 1)
        return {
            "tier_status": "pending",
            "is_active": False,
            "tier_key": PENDING_TIER["key"],
            "tier_label": PENDING_TIER["label"],
            "badge_text": PENDING_TIER["badge_text"],
            "current_points": current_points,
            "confirmed_videos": current_videos,
            "threshold_points": 0,
            "threshold_videos": 1,
            "commission_rate": 0.0,
            "points_multiplier": 1.0,
            "next_tier_key": "bronze",
            "next_tier_label": "Bronze",
            "next_threshold_points": 0,
            "next_threshold_videos": 1,
            "points_to_next": 0,
            "videos_to_next": max(0, 1 - current_videos),
            "progress_ratio": round((points_progress + video_progress) / 2, 3),
            "points_progress_ratio": round(points_progress, 3),
            "video_progress_ratio": round(video_progress, 3),
            "is_top_tier": False,
            "activation_message": "Submit and pass your first video to unlock Bronze and activate affiliate features.",
        }

    current_tier = manual_tier or VIP_TIERS[0]
    next_tier = None
    manual_index = VIP_TIERS.index(manual_tier) if manual_tier else -1
    for idx, tier in enumerate(VIP_TIERS):
        if current_points >= int(tier["min_points"]) and current_videos >= int(tier["min_confirmed_videos"]) and idx >= manual_index:
            current_tier = tier
            next_tier = VIP_TIERS[idx + 1] if idx + 1 < len(VIP_TIERS) else None

    if manual_tier and current_tier is manual_tier:
        manual_idx = VIP_TIERS.index(manual_tier)
        next_tier = VIP_TIERS[manual_idx + 1] if manual_idx + 1 < len(VIP_TIERS) else None

    floor_points = int(current_tier["min_points"])
    floor_videos = int(current_tier["min_confirmed_videos"])
    ceiling_points = int((next_tier or current_tier)["min_points"])
    ceiling_videos = int((next_tier or current_tier)["min_confirmed_videos"])
    points_to_next = max(0, ceiling_points - current_points) if next_tier else 0
    videos_to_next = max(0, ceiling_videos - current_videos) if next_tier else 0
    points_progress = 1.0 if not next_tier else _count_threshold_progress(current_points, floor_points, ceiling_points)
    video_progress = 1.0 if not next_tier else _count_threshold_progress(current_videos, floor_videos, ceiling_videos)

    return {
        "tier_status": status_key if manual_tier else "active",
        "is_active": True,
        "tier_key": current_tier["key"],
        "tier_label": current_tier["label"],
        "badge_text": current_tier["badge_text"],
        "current_points": current_points,
        "confirmed_videos": current_videos,
        "threshold_points": floor_points,
        "threshold_videos": floor_videos,
        "commission_rate": float(current_tier["commission_rate"]),
        "points_multiplier": float(current_tier["points_multiplier"]),
        "next_tier_key": (next_tier or {}).get("key", ""),
        "next_tier_label": (next_tier or {}).get("label", ""),
        "next_threshold_points": int((next_tier or {}).get("min_points", floor_points)),
        "next_threshold_videos": int((next_tier or {}).get("min_confirmed_videos", floor_videos)),
        "points_to_next": points_to_next,
        "videos_to_next": videos_to_next,
        "progress_ratio": round(min(points_progress, video_progress), 3),
        "points_progress_ratio": round(points_progress, 3),
        "video_progress_ratio": round(video_progress, 3),
        "is_top_tier": next_tier is None,
        "activation_message": "Creator lane active.",
    }


def unlocked_outfits(points_total: int | float = 0, *, is_active: bool = False) -> list[dict[str, Any]]:
    current_points = max(0, int(float(points_total or 0)))
    return [
        {
            **item,
            "unlocked": bool(is_active) and current_points >= int(item["unlock_points"]),
        }
        for item in OUTFIT_UNLOCKS
    ]
