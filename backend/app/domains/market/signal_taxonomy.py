"""Market signal keyword taxonomy shared by provider smokes and ingestion.

The taxonomy is intentionally lowercase and de-duplicated at runtime so Google,
Reddit, RSS, and later LLM evidence summaries can use the same matching policy.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


KEYWORD_GROUPS: dict[str, list[str]] = {
    "tier1_lens_competitors": [
        "sigma",
        "tamron",
        "samyang",
        "rokinon",
        "ttartisan",
        "tt artisan",
        "7artisans",
        "laowa",
        "yongnuo",
        "meike",
    ],
    "tier1_lighting_competitors": ["aputure", "godox", "nanlite", "amaran"],
    "tier1_cinema_accessory": ["smallrig", "small rig", "tilta"],
    "tier1_cross_industry": ["dji", "insta360", "hollyland"],
    "tier2_lens": ["brightin star", "pergear", "kamlan", "neewer"],
    "tier2_light": ["profoto", "kinoflo", "arri light"],
    "tier2_gimbals": ["zhiyun", "moza", "hohem"],
    "tier2_wireless_video": ["vaxis", "teradek", "accsoon"],
    "tier2_cinema_high_end": ["arri", "red camera", "blackmagic", "z cam"],
    "viltrox_products": [
        "viltrox",
        "af 16mm",
        "af 20mm",
        "af 23mm",
        "af 27mm",
        "af 33mm",
        "af 35mm",
        "af 50mm",
        "af 56mm",
        "af 75mm",
        "af 85mm",
        "lab 135",
        "lab 35",
        "pro 135",
        "pro af",
        "epic",
        "viltrox epic",
        "weeylite",
        "weeylite sprite",
        "viltrox jy",
    ],
    "camera_ecosystem": [
        "sony",
        "fujifilm",
        "fuji",
        "nikon",
        "canon",
        "panasonic",
        "lumix",
        "m43",
        "micro four thirds",
    ],
    "generic_imaging_terms": [
        "lens",
        "autofocus",
        "anamorphic",
        "cinema",
        "mirrorless",
        "low light",
        "wide angle",
        "35mm",
        "50mm",
        "85mm",
        "f1.2",
        "f1.4",
        "f1.8",
        "full frame",
        "aps-c",
    ],
}

TIER1_GROUPS = {
    "tier1_lens_competitors",
    "tier1_lighting_competitors",
    "tier1_cinema_accessory",
    "tier1_cross_industry",
}
TIER2_GROUPS = {
    "tier2_lens",
    "tier2_light",
    "tier2_gimbals",
    "tier2_wireless_video",
    "tier2_cinema_high_end",
}


def dedupe_keywords(groups: dict[str, list[str]] | None = None) -> list[str]:
    seen: set[str] = set()
    keywords: list[str] = []
    for values in (groups or KEYWORD_GROUPS).values():
        for value in values:
            clean = str(value or "").strip().lower()
            if clean and clean not in seen:
                seen.add(clean)
                keywords.append(clean)
    return keywords


KEYWORDS = dedupe_keywords()

KEYWORD_TO_GROUPS: dict[str, list[str]] = {}
for group_name, values in KEYWORD_GROUPS.items():
    for value in values:
        clean = str(value or "").strip().lower()
        if clean:
            KEYWORD_TO_GROUPS.setdefault(clean, []).append(group_name)


def keyword_hits(text: str) -> list[str]:
    source = str(text or "")
    hits = {
        keyword
        for keyword in KEYWORDS
        if re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", source, flags=re.I)
    }
    return sorted(hits)


def keyword_groups(hits: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for hit in hits:
        for group_name in KEYWORD_TO_GROUPS.get(str(hit or "").strip().lower(), []):
            grouped.setdefault(group_name, []).append(str(hit).strip().lower())
    return grouped


def has_group(post: dict[str, Any], group_names: set[str]) -> bool:
    groups = post.get("keyword_groups") if isinstance(post.get("keyword_groups"), dict) else {}
    return any(name in groups for name in group_names)


def summarize_keyword_groups(posts: list[dict[str, Any]]) -> dict[str, Any]:
    keyword_counter = Counter(hit for post in posts for hit in (post.get("keyword_hits") or []))
    group_counters: dict[str, Counter[str]] = {group_name: Counter() for group_name in KEYWORD_GROUPS}
    for post in posts:
        groups = post.get("keyword_groups") if isinstance(post.get("keyword_groups"), dict) else {}
        for group_name, hits in groups.items():
            group_counters.setdefault(str(group_name), Counter()).update(str(hit).lower() for hit in hits)
    tier1_mentions = sum(sum(group_counters[name].values()) for name in TIER1_GROUPS)
    tier2_mentions = sum(sum(group_counters[name].values()) for name in TIER2_GROUPS)
    viltrox_mentions = sum(group_counters["viltrox_products"].values())
    return {
        "keyword_hit_posts": sum(1 for post in posts if post.get("keyword_hits")),
        "tier1_hit_posts": sum(1 for post in posts if has_group(post, TIER1_GROUPS)),
        "tier2_hit_posts": sum(1 for post in posts if has_group(post, TIER2_GROUPS)),
        "viltrox_product_hit_posts": sum(1 for post in posts if has_group(post, {"viltrox_products"})),
        "tier1_mentions": tier1_mentions,
        "tier2_mentions": tier2_mentions,
        "viltrox_product_mentions": viltrox_mentions,
        "tier2_recommended_next_run": tier1_mentions < 20,
        "top_keywords": keyword_counter.most_common(20),
        "top_keywords_by_group": {
            group_name: counter.most_common(20)
            for group_name, counter in group_counters.items()
        },
    }
