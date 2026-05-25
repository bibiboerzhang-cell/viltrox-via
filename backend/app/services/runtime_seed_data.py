"""Preview seed constants for local runtime demo data."""
from __future__ import annotations

PREVIEW_VIDEO_URL = "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4"
RUNTIME_SEED_MEMO_PREFIX = "runtime_seed:"

LEADERBOARD_CREATOR_SEEDS: tuple[dict[str, object], ...] = (
    {
        "email": "alexfilms@viltrox.local",
        "name": "AlexFilms",
        "creator_code": "V_001234",
        "handle": "alexfilms",
        "platform": "youtube",
        "gear_tag": "AF 56mm F1.7 Air",
        "product_series": "Air",
        "month_count": 14,
        "year_count": 52,
        "month_points": 48200,
        "year_points": 184200,
        "points_balance": 48200,
        "points_pending": 1200,
        "points_total": 184200,
        "trust_score": 91,
        "bio": "Commercial DP building compact rigs around the Viltrox Air primes.",
    },
    {
        "email": "photobylena@viltrox.local",
        "name": "PhotoByLena",
        "creator_code": "V_002891",
        "handle": "photobylena",
        "platform": "instagram",
        "gear_tag": "EPIC 35mm",
        "product_series": "EPIC",
        "month_count": 11,
        "year_count": 47,
        "month_points": 41500,
        "year_points": 161500,
        "points_balance": 41500,
        "points_pending": 950,
        "points_total": 161500,
        "trust_score": 88,
        "bio": "Portrait and travel creator testing the EPIC line in bright daylight.",
    },
    {
        "email": "travelvibes@viltrox.local",
        "name": "TravelVibes",
        "creator_code": "V_003344",
        "handle": "travelvibes",
        "platform": "tiktok",
        "gear_tag": "AF 20mm F2.8",
        "product_series": "AF",
        "month_count": 10,
        "year_count": 39,
        "month_points": 38900,
        "year_points": 148900,
        "points_balance": 38900,
        "points_pending": 860,
        "points_total": 148900,
        "trust_score": 84,
        "bio": "Fast-turn travel storyteller cutting handheld tests on the road.",
    },
)

PREVIEW_SOCIAL_ROWS: tuple[dict[str, object], ...] = (
    {"platform": "youtube", "handle": "alexfilms", "verified": 1, "verify_code": "", "status_note": "verified"},
    {"platform": "tiktok", "handle": "alex.creates", "verified": 0, "verify_code": "VLX-7C31B1", "status_note": "pending"},
    {"platform": "instagram", "handle": "alexphoto", "verified": 0, "verify_code": "", "status_note": "unverified"},
)

PREVIEW_ADDRESS_ROWS: tuple[dict[str, object], ...] = (
    {
        "name": "Jianbo Zhang",
        "phone": "+1 213 555 0199",
        "address1": "665 Jefferson Blvd",
        "address2": "Studio 4B",
        "city": "Los Angeles",
        "state": "CA",
        "country": "US",
        "postal_code": "90089",
        "is_default": 1,
    },
    {
        "name": "Jianbo Zhang",
        "phone": "+1 917 555 0113",
        "address1": "15 Kent Ave",
        "address2": "Apt 9C",
        "city": "Brooklyn",
        "state": "NY",
        "country": "US",
        "postal_code": "11249",
        "is_default": 0,
    },
)

REWARD_SEEDS: tuple[dict[str, object], ...] = (
    {
        "title": "Viltrox Cap",
        "description": "Limited edition cap for creator preview mode.",
        "category": "Merch",
        "points_cost": 800,
        "meta_label": "MERCH",
        "image_url": "",
        "stock": 14,
        "sort_order": 10,
    },
    {
        "title": "AF 56mm F1.4 C Lens Coupon",
        "description": "Preview reward coupon for lens redemption testing.",
        "category": "Lens",
        "points_cost": 2400,
        "meta_label": "LENS",
        "image_url": "",
        "stock": 5,
        "sort_order": 20,
    },
    {
        "title": "Lens Cleaning Kit",
        "description": "Compact cleaning kit with blower, cloth, and pen.",
        "category": "Accessory",
        "points_cost": 600,
        "meta_label": "ACCESSORY",
        "image_url": "",
        "stock": 32,
        "sort_order": 30,
    },
    {
        "title": "Creator Tote Bag",
        "description": "Canvas tote for booth runs, cables, and batteries.",
        "category": "Merch",
        "points_cost": 1200,
        "meta_label": "MERCH",
        "image_url": "",
        "stock": 0,
        "sort_order": 40,
    },
    {
        "title": "UV Filter Set",
        "description": "Multi-coated UV filters for quick product preview demos.",
        "category": "Accessory",
        "points_cost": 2400,
        "meta_label": "ACCESSORY",
        "image_url": "",
        "stock": 9,
        "sort_order": 50,
    },
)

PREVIEW_SEED_EMAILS = (
    "preview@viltrox.local",
    "alexfilms@viltrox.local",
    "photobylena@viltrox.local",
    "travelvibes@viltrox.local",
)
