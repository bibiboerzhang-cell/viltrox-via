"""
services/memory/via_learning.py — Via daily autonomous learning
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from app.db.connection import get_conn
from app.db.repositories.via_control import (
    create_via_policy_version,
    get_via_reward_trace_by_idempotency_key,
    get_via_policy_version,
    insert_via_reward_trace,
    list_active_via_policy_versions,
    list_recent_via_retrieval_evidence,
    list_via_policy_version_history,
    list_via_rollout_alerts,
    list_via_memory_retention_stats,
    list_via_routing_provider_stats,
    list_recent_via_decisions,
    list_recent_via_outcomes,
    list_recent_via_reward_traces,
    list_via_policy_proposals,
    promote_via_policy_version,
    upsert_via_rollout_alert,
    upsert_via_policy_proposal,
)
from app.core.config import (
    VIA_ENABLE_DAILY_LEARNING,
    VIA_LEARNING_BH_MAX_ITEMS,
    VIA_LEARNING_COMMENT_LIMIT,
    VIA_LEARNING_COMMENT_SAMPLE,
    VIA_LEARNING_MAX_POSTS,
    VIA_OFFICIAL_FACEBOOK_HANDLE,
    VIA_OFFICIAL_INSTAGRAM_HANDLE,
    VIA_OFFICIAL_TIKTOK_HANDLE,
    VIA_OFFICIAL_YOUTUBE_HANDLE,
)
from app.services.creator_program import build_creator_program_snapshot
from app.services.intelligence import fetch_bh_viltrox_products, save_bh_snapshot
from app.services.intelligence.account_scan_service import scan_account
from app.services.memory.l3_store import (
    record_creator_memory_fact,
    record_feedback_signal,
    record_market_observation,
    record_product_signal,
    record_region_fact,
)
from app.services.scraping.apify import fetch_viltrox_comments, normalize_comment


PRODUCT_PATTERN = re.compile(
    r"\b(?:(?:AF|LAB|PRO|EVO|AIR|EPIC)\s+)?\d{1,3}(?:\.\d+)?mm\s+F\d(?:\.\d+)?(?:\s+(?:LAB|PRO|AIR|EVO|EPIC|FE|XF|Z|L|RF|E|MFT)){0,2}\b",
    flags=re.IGNORECASE,
)
VIA_EVALUATOR_VERSION = "via-offline-evaluator-v1"
_P1_SHADOW_ROLLOUT_RULES: dict[str, dict[str, Any]] = {
    "via.retrieval.selective": {
        "target": "retrieval_plan",
        "min_shadow_samples": 6,
        "promote_shadow_samples": 18,
        "min_change_rate": 0.2,
        "min_acceptance_rate": 0.55,
        "min_avg_reward": 0.45,
        "max_abuse_rate": 0.08,
    },
    "via.model.route": {
        "target": "model_choice",
        "min_shadow_samples": 8,
        "promote_shadow_samples": 20,
        "min_change_rate": 0.18,
        "min_acceptance_rate": 0.55,
        "min_avg_reward": 0.48,
        "max_abuse_rate": 0.08,
    },
}
_P1_LIVE_ROLLOUT_STEPS = [0.05, 0.15, 0.30, 0.60, 1.0]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:96] or "unknown"


def extract_product_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for match in PRODUCT_PATTERN.findall(text or ""):
        cleaned = "Viltrox " + re.sub(r"\s+", " ", match.strip())
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(cleaned)
    return candidates


def _official_accounts() -> list[dict[str, str]]:
    pairs = [
        ("instagram", VIA_OFFICIAL_INSTAGRAM_HANDLE),
        ("tiktok", VIA_OFFICIAL_TIKTOK_HANDLE),
        ("youtube", VIA_OFFICIAL_YOUTUBE_HANDLE),
        ("facebook", VIA_OFFICIAL_FACEBOOK_HANDLE),
    ]
    return [
        {"platform": platform, "handle": handle.strip()}
        for platform, handle in pairs
        if str(handle or "").strip()
    ]


def _pick_post_metrics(post: dict[str, Any]) -> dict[str, int]:
    return {
        "views": int(post.get("views") or 0),
        "likes": int(post.get("likes") or 0),
        "comments": int(post.get("comments") or 0),
        "shares": int(post.get("shares") or 0),
    }


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _load_internal_learning_inputs(window_days: int = 30) -> dict[str, list[dict[str, Any]]]:
    conn = get_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days)))

    def _filter_recent(rows: list[Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            created = _parse_timestamp(item.get("created_at") or item.get("updated_at"))
            if created and created < cutoff:
                continue
            items.append(item)
        return items

    submissions_rows = conn.execute(
        """
        SELECT id, user_id, platform, extracted_handle, product_series, product_label,
               views, likes, comments, shares, created_at, detection_status, title
        FROM submissions
        ORDER BY id DESC
        LIMIT 240
        """
    ).fetchall()
    ingest_rows = conn.execute(
        """
        SELECT id, source_platform, event_type, entity_type, external_id,
               creator_handle, region_code, created_at, ingest_status
        FROM platform_ingest_events
        ORDER BY id DESC
        LIMIT 240
        """
    ).fetchall()
    address_rows = conn.execute(
        """
        SELECT id, user_id, country, state, city, is_default
        FROM user_addresses
        ORDER BY id DESC
        LIMIT 160
        """
    ).fetchall()
    verified_social_rows = conn.execute(
        """
        SELECT id, user_id, platform, handle, verified, verified_at
        FROM user_social_accounts
        WHERE verified=1
        ORDER BY id DESC
        LIMIT 200
        """
    ).fetchall()
    return {
        "submissions": _filter_recent(list(submissions_rows)),
        "ingest_events": _filter_recent(list(ingest_rows)),
        "addresses": [dict(row) for row in address_rows],
        "verified_social_accounts": _filter_recent(list(verified_social_rows)),
    }


def _build_internal_learning_snapshot(
    submissions: list[dict[str, Any]],
    ingest_events: list[dict[str, Any]],
    addresses: list[dict[str, Any]],
    verified_social_accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    customer_profiles: dict[str, dict[str, Any]] = {}
    product_clusters: dict[str, dict[str, Any]] = {}
    region_clusters: dict[tuple[str, str, str], dict[str, Any]] = {}
    ingest_clusters: dict[str, dict[str, Any]] = {}

    for row in submissions:
        user_id = int(row.get("user_id") or 0)
        creator_handle = str(row.get("extracted_handle") or "").strip()
        platform = str(row.get("platform") or "unknown").strip() or "unknown"
        subject_key = creator_handle or (f"user:{user_id}" if user_id else f"submission:{row.get('id')}")
        profile = customer_profiles.setdefault(
            subject_key,
            {
                "subject_key": subject_key,
                "user_id": user_id,
                "creator_handle": creator_handle,
                "submission_count": 0,
                "total_views": 0,
                "total_likes": 0,
                "total_comments": 0,
                "total_shares": 0,
                "platforms": set(),
                "latest_at": "",
                "products": set(),
                "verified_accounts": [],
            },
        )
        profile["submission_count"] += 1
        profile["total_views"] += int(row.get("views") or 0)
        profile["total_likes"] += int(row.get("likes") or 0)
        profile["total_comments"] += int(row.get("comments") or 0)
        profile["total_shares"] += int(row.get("shares") or 0)
        profile["platforms"].add(platform)
        if row.get("created_at"):
            profile["latest_at"] = max(str(profile.get("latest_at") or ""), str(row.get("created_at") or ""))
        product_label = str(row.get("product_label") or row.get("product_series") or "").strip()
        if product_label:
            profile["products"].add(product_label)
            product = product_clusters.setdefault(
                product_label,
                {
                    "label": product_label,
                    "product_key": _slugify(product_label),
                    "submission_count": 0,
                    "total_views": 0,
                    "total_likes": 0,
                    "platforms": set(),
                    "alias_terms": set([product_label]),
                },
            )
            product["submission_count"] += 1
            product["total_views"] += int(row.get("views") or 0)
            product["total_likes"] += int(row.get("likes") or 0)
            product["platforms"].add(platform)
            product["alias_terms"].add(str(row.get("product_series") or "").strip())
            product["alias_terms"].add(str(row.get("product_label") or "").strip())

    for row in verified_social_accounts:
        user_id = int(row.get("user_id") or 0)
        creator_handle = str(row.get("handle") or "").strip()
        subject_key = creator_handle or (f"user:{user_id}" if user_id else f"verified:{row.get('id')}")
        profile = customer_profiles.setdefault(
            subject_key,
            {
                "subject_key": subject_key,
                "user_id": user_id,
                "creator_handle": creator_handle,
                "submission_count": 0,
                "total_views": 0,
                "total_likes": 0,
                "total_comments": 0,
                "total_shares": 0,
                "platforms": set(),
                "latest_at": "",
                "products": set(),
                "verified_accounts": [],
            },
        )
        verified_account = {
            "platform": str(row.get("platform") or "").strip(),
            "handle": creator_handle,
            "verified_at": str(row.get("verified_at") or "").strip(),
        }
        if verified_account not in profile["verified_accounts"]:
            profile["verified_accounts"].append(verified_account)
        if verified_account["platform"]:
            profile["platforms"].add(verified_account["platform"])

    for row in addresses:
        country = str(row.get("country") or "UNKNOWN").strip() or "UNKNOWN"
        state = str(row.get("state") or "").strip()
        city = str(row.get("city") or "").strip()
        region_key = (country, state, city)
        cluster = region_clusters.setdefault(
            region_key,
            {
                "region_code": country,
                "region_level": "city" if city else ("state" if state else "country"),
                "country": country,
                "state": state,
                "city": city,
                "address_count": 0,
                "default_count": 0,
                "users": set(),
            },
        )
        cluster["address_count"] += 1
        cluster["default_count"] += 1 if int(row.get("is_default") or 0) else 0
        if int(row.get("user_id") or 0):
            cluster["users"].add(int(row.get("user_id") or 0))

    for row in ingest_events:
        platform = str(row.get("source_platform") or "unknown").strip() or "unknown"
        cluster = ingest_clusters.setdefault(
            platform,
            {
                "platform": platform,
                "event_count": 0,
                "event_types": set(),
                "entity_types": set(),
                "region_codes": set(),
                "creator_handles": set(),
                "latest_at": "",
            },
        )
        cluster["event_count"] += 1
        cluster["event_types"].add(str(row.get("event_type") or "").strip())
        cluster["entity_types"].add(str(row.get("entity_type") or "").strip())
        cluster["region_codes"].add(str(row.get("region_code") or "").strip())
        cluster["creator_handles"].add(str(row.get("creator_handle") or "").strip())
        if row.get("created_at"):
            cluster["latest_at"] = max(str(cluster.get("latest_at") or ""), str(row.get("created_at") or ""))

    customer_items = []
    for item in customer_profiles.values():
        customer_items.append(
            {
                **item,
                "platforms": sorted(value for value in item["platforms"] if value),
                "products": sorted(value for value in item["products"] if value),
            }
        )
    customer_items.sort(key=lambda item: (-item["submission_count"], -item["total_views"], item["subject_key"]))

    product_items = []
    for item in product_clusters.values():
        product_items.append(
            {
                **item,
                "platforms": sorted(value for value in item["platforms"] if value),
                "alias_terms": sorted(value for value in item["alias_terms"] if value),
            }
        )
    product_items.sort(key=lambda item: (-item["submission_count"], -item["total_views"], item["label"]))

    region_items = []
    for item in region_clusters.values():
        region_items.append(
            {
                **item,
                "users": sorted(item["users"]),
                "user_count": len(item["users"]),
            }
        )
    region_items.sort(key=lambda item: (-item["address_count"], -item["user_count"], item["country"], item["state"], item["city"]))

    ingest_items = []
    for item in ingest_clusters.values():
        ingest_items.append(
            {
                **item,
                "event_types": sorted(value for value in item["event_types"] if value),
                "entity_types": sorted(value for value in item["entity_types"] if value),
                "region_codes": sorted(value for value in item["region_codes"] if value),
                "creator_handles": sorted(value for value in item["creator_handles"] if value),
            }
        )
    ingest_items.sort(key=lambda item: (-item["event_count"], item["platform"]))

    return {
        "customer_learning": {
            "profiles": customer_items[:16],
            "totals": {
                "profiles": len(customer_items),
                "verified_accounts": len(verified_social_accounts),
                "recent_submissions": len(submissions),
            },
        },
        "product_intelligence": {
            "products": product_items[:16],
            "ingest_platforms": ingest_items[:12],
            "totals": {
                "products": len(product_items),
                "ingest_events": len(ingest_events),
                "platforms": len(ingest_items),
            },
        },
        "market_regions": {
            "regions": region_items[:12],
            "totals": {
                "regions": len(region_items),
                "addresses": len(addresses),
            },
        },
    }


async def _store_internal_system_snapshot(window_days: int = 30) -> dict[str, Any]:
    inputs = await asyncio.to_thread(_load_internal_learning_inputs, window_days)
    snapshot = _build_internal_learning_snapshot(
        inputs["submissions"],
        inputs["ingest_events"],
        inputs["addresses"],
        inputs["verified_social_accounts"],
    )
    refs = {
        "observations": 0,
        "memory": 0,
        "feedback": 0,
        "products": 0,
        "regions": 0,
    }

    customer_profiles = snapshot["customer_learning"]["profiles"]
    product_clusters = snapshot["product_intelligence"]["products"]
    ingest_platforms = snapshot["product_intelligence"]["ingest_platforms"]
    region_clusters = snapshot["market_regions"]["regions"]

    await asyncio.to_thread(
        record_market_observation,
        source_platform="internal",
        subject_type="system_snapshot",
        subject_key="customer_learning",
        observation_type="customer_data_snapshot",
        summary=(
            f"Via sampled {snapshot['customer_learning']['totals']['recent_submissions']} recent submissions and "
            f"{snapshot['customer_learning']['totals']['verified_accounts']} verified social accounts "
            f"to refresh customer-side memory without mixing it into product intelligence."
        ),
        metrics=snapshot["customer_learning"]["totals"],
        evidence={"profiles": customer_profiles[:5]},
    )
    refs["observations"] += 1

    await asyncio.to_thread(
        record_market_observation,
        source_platform="internal",
        subject_type="system_snapshot",
        subject_key="product_intelligence",
        observation_type="product_signal_snapshot",
        summary=(
            f"Via refreshed {snapshot['product_intelligence']['totals']['products']} internal product clusters "
            f"and {snapshot['product_intelligence']['totals']['ingest_events']} ingest events for product intelligence."
        ),
        metrics=snapshot["product_intelligence"]["totals"],
        evidence={"products": product_clusters[:5], "ingest_platforms": ingest_platforms[:5]},
    )
    refs["observations"] += 1

    for item in customer_profiles[:12]:
        await asyncio.to_thread(
            record_creator_memory_fact,
            user_id=int(item.get("user_id") or 0),
            creator_handle=str(item.get("creator_handle") or ""),
            memory_kind="customer_profile_snapshot",
            fact_key="submission_performance",
            fact_value={
                "submission_count": item["submission_count"],
                "total_views": item["total_views"],
                "total_likes": item["total_likes"],
                "total_comments": item["total_comments"],
                "total_shares": item["total_shares"],
                "platforms": item["platforms"],
                "products": item["products"],
                "verified_accounts": item["verified_accounts"],
                "latest_at": item["latest_at"],
            },
            confidence=0.88,
            source_ref=f"internal-customer:{item['subject_key']}",
        )
        refs["memory"] += 1
        await asyncio.to_thread(
            record_feedback_signal,
            source_type="internal",
            source_id=item["subject_key"],
            event_type="customer_profile_snapshot",
            actor_role="via",
            user_id=int(item.get("user_id") or 0),
            payload={
                "submission_count": item["submission_count"],
                "platforms": item["platforms"],
                "products": item["products"],
            },
        )
        refs["feedback"] += 1

    for item in product_clusters[:12]:
        await asyncio.to_thread(
            record_product_signal,
            product_key=item["product_key"],
            label=item["label"],
            alias_terms=item["alias_terms"],
            feature_tags=["internal", "product_intelligence", *item["platforms"]],
            scene_tags=[],
            feature_type="internal_submission_snapshot",
            feature_vector={
                "submission_count": item["submission_count"],
                "total_views": item["total_views"],
                "total_likes": item["total_likes"],
            },
            detector_version="via-learning-v2",
            asset_role="internal_submission",
            storage_key=f"internal:{item['product_key']}",
        )
        refs["products"] += 1

    for item in region_clusters[:10]:
        await asyncio.to_thread(
            record_region_fact,
            region_code=item["region_code"],
            fact_type="customer_address_distribution",
            fact_value={
                "country": item["country"],
                "state": item["state"],
                "city": item["city"],
                "address_count": item["address_count"],
                "default_count": item["default_count"],
                "user_count": item["user_count"],
            },
            source_platform="internal",
            region_level=item["region_level"],
        )
        refs["regions"] += 1

    for item in ingest_platforms[:8]:
        await asyncio.to_thread(
            record_market_observation,
            source_platform="internal",
            subject_type="ingest_platform",
            subject_key=item["platform"],
            observation_type="platform_event_snapshot",
            summary=(
                f"{item['platform']} produced {item['event_count']} recent ingest events "
                f"across {len(item['event_types'])} event types."
            ),
            metrics={
                "event_count": item["event_count"],
                "event_type_count": len(item["event_types"]),
                "region_count": len(item["region_codes"]),
                "creator_count": len(item["creator_handles"]),
            },
            evidence={
                "event_types": item["event_types"],
                "entity_types": item["entity_types"],
                "region_codes": item["region_codes"][:8],
                "creator_handles": item["creator_handles"][:8],
            },
            observed_at=item["latest_at"],
        )
        refs["observations"] += 1

    return {
        "inputs": {
            "submissions": len(inputs["submissions"]),
            "ingest_events": len(inputs["ingest_events"]),
            "addresses": len(inputs["addresses"]),
            "verified_social_accounts": len(inputs["verified_social_accounts"]),
        },
        "domains": snapshot,
        "stored": refs,
    }


async def _store_account_snapshot(platform: str, handle: str, result: dict[str, Any]) -> dict[str, int]:
    posts = list(result.get("posts") or [])
    stats = dict(result.get("stats") or {})
    top_posts = sorted(
        posts,
        key=lambda item: (
            int(item.get("views") or 0),
            int(item.get("likes") or 0),
            int(item.get("comments") or 0),
        ),
        reverse=True,
    )[: min(8, len(posts))]
    summary = (
        f"Via scanned {len(posts)} official {platform} posts for @{handle}. "
        f"Views={stats.get('total_views', 0)}, likes={stats.get('total_likes', 0)}, comments={stats.get('total_comments', 0)}."
    )
    refs = {"observations": 0, "memory": 0, "feedback": 0, "products": 0}

    await asyncio.to_thread(
        record_market_observation,
        source_platform=platform,
        subject_type="official_account",
        subject_key=handle,
        observation_type="official_brand_feed_daily",
        summary=summary,
        metrics=stats,
        evidence={"top_posts": top_posts},
        observed_at=result.get("timestamp") or "",
    )
    refs["observations"] += 1

    await asyncio.to_thread(
        record_creator_memory_fact,
        creator_handle=handle,
        memory_kind="official_brand_feed",
        fact_key=f"{platform}:daily_snapshot",
        fact_value={
            "stats": stats,
            "top_posts": top_posts,
            "platform": platform,
        },
        confidence=0.91,
        source_ref=f"via:{platform}:{handle}",
    )
    refs["memory"] += 1

    await asyncio.to_thread(
        record_feedback_signal,
        source_type=platform,
        source_id=handle,
        event_type="official_account_snapshot",
        actor_role="via",
        payload={"stats": stats, "top_posts": top_posts},
    )
    refs["feedback"] += 1

    for post in top_posts:
        post_summary = (post.get("title") or "").strip() or f"{platform} post from @{handle}"
        post_key = str(post.get("url") or f"{handle}:{post.get('published') or post_summary[:24]}").strip()
        await asyncio.to_thread(
            record_market_observation,
            source_platform=platform,
            subject_type="official_post",
            subject_key=post_key,
            observation_type="official_post_snapshot",
            summary=post_summary[:300],
            metrics=_pick_post_metrics(post),
            evidence={
                "channel": handle,
                "url": post.get("url", ""),
                "published": post.get("published", ""),
                "thumbnail": post.get("thumbnail", ""),
                "type": post.get("type", ""),
            },
            observed_at=post.get("published") or result.get("timestamp") or "",
        )
        refs["observations"] += 1

        for label in extract_product_candidates(post_summary):
            await asyncio.to_thread(
                record_product_signal,
                product_key=_slugify(label),
                label=label,
                alias_terms=[label, handle],
                feature_tags=[platform, "official_brand_post"],
                scene_tags=[],
                feature_type="official_post_reference",
                feature_vector=_pick_post_metrics(post),
                detector_version="via-learning-v1",
                asset_role="official_post",
                storage_key=post.get("url", ""),
            )
            refs["products"] += 1

    return refs


async def _store_comment_snapshot(platform: str, account_handle: str, comments: list[dict[str, Any]]) -> dict[str, int]:
    refs = {"observations": 0, "memory": 0, "feedback": 0}
    normalized = [normalize_comment(comment, platform) for comment in comments[: max(1, VIA_LEARNING_COMMENT_LIMIT)]]
    sample = normalized[: max(1, VIA_LEARNING_COMMENT_SAMPLE)]
    if not normalized:
        return refs

    summary = (
        f"Via collected {len(normalized)} recent official {platform} comments for @{account_handle} "
        f"to track creator questions and market sentiment."
    )
    await asyncio.to_thread(
        record_market_observation,
        source_platform=platform,
        subject_type="official_comments",
        subject_key=account_handle,
        observation_type="community_comment_snapshot",
        summary=summary,
        metrics={
            "comment_count": len(normalized),
            "sample_count": len(sample),
        },
        evidence={"comments": sample},
    )
    refs["observations"] += 1

    await asyncio.to_thread(
        record_creator_memory_fact,
        creator_handle=account_handle,
        memory_kind="official_community_feedback",
        fact_key=f"{platform}:comment_snapshot",
        fact_value={
            "comment_count": len(normalized),
            "sample_comments": sample,
        },
        confidence=0.76,
        source_ref=f"via-comments:{platform}:{account_handle}",
    )
    refs["memory"] += 1

    await asyncio.to_thread(
        record_feedback_signal,
        source_type=platform,
        source_id=account_handle,
        event_type="official_comment_snapshot",
        actor_role="community",
        payload={"comments": sample, "count": len(normalized)},
    )
    refs["feedback"] += 1
    return refs


async def _store_bh_snapshot(products: list[dict[str, Any]]) -> dict[str, int]:
    refs = {"observations": 0, "products": 0, "feedback": 0, "regions": 0}
    if not products:
        return refs
    await save_bh_snapshot(products)

    prices = [float(item.get("price") or 0) for item in products if float(item.get("price") or 0) > 0]
    ratings = [float(item.get("rating") or 0) for item in products if float(item.get("rating") or 0) > 0]
    reviews = [int(item.get("review_count") or 0) for item in products]
    summary = (
        f"B&H daily Viltrox snapshot captured {len(products)} products. "
        f"Avg price={round(mean(prices), 2) if prices else 0}, "
        f"avg rating={round(mean(ratings), 2) if ratings else 0}, "
        f"reviews={sum(reviews)}."
    )
    ranked = sorted(
        products,
        key=lambda item: (
            float(item.get("rating") or 0),
            int(item.get("review_count") or 0),
        ),
        reverse=True,
    )[:10]

    await asyncio.to_thread(
        record_market_observation,
        source_platform="bh",
        subject_type="catalog",
        subject_key="viltrox",
        observation_type="daily_catalog_snapshot",
        summary=summary,
        metrics={
            "product_count": len(products),
            "avg_price": round(mean(prices), 2) if prices else 0,
            "avg_rating": round(mean(ratings), 2) if ratings else 0,
            "total_reviews": sum(reviews),
        },
        evidence={"top_products": ranked},
    )
    refs["observations"] += 1

    await asyncio.to_thread(
        record_region_fact,
        region_code="GLOBAL",
        fact_type="bh:daily_catalog_snapshot",
        fact_value={
            "product_count": len(products),
            "top_products": ranked,
        },
        source_platform="bh",
    )
    refs["regions"] += 1

    await asyncio.to_thread(
        record_feedback_signal,
        source_type="bh",
        source_id="viltrox",
        event_type="daily_catalog_snapshot",
        actor_role="market",
        payload={"top_products": ranked},
    )
    refs["feedback"] += 1

    for item in ranked:
        label = str(item.get("title") or "").strip()
        if not label:
            continue
        await asyncio.to_thread(
            record_product_signal,
            product_key=_slugify(item.get("sku") or label),
            label=label[:200],
            alias_terms=[item.get("sku", ""), label],
            feature_tags=["bh", "market_snapshot", "viltrox"],
            scene_tags=[],
            feature_type="bh_market_snapshot",
            feature_vector={
                "price": float(item.get("price") or 0),
                "rating": float(item.get("rating") or 0),
                "review_count": int(item.get("review_count") or 0),
                "in_stock": 1 if item.get("in_stock", True) else 0,
            },
            detector_version="via-learning-v1",
            asset_role="bh_product",
            storage_key=item.get("url", ""),
        )
        refs["products"] += 1

    return refs


def _filter_recent_control_rows(rows: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 1)))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        created_at = _parse_timestamp(row.get("created_at") or row.get("updated_at") or "")
        if created_at and created_at < cutoff:
            continue
        filtered.append(row)
    return filtered


def _load_json_doc(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _clean_affiliate_ref(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _extract_affiliate_order_candidates(row: Any, payload: dict[str, Any]) -> list[str]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    note_attributes = body.get("note_attributes") if isinstance(body.get("note_attributes"), list) else []
    candidates: list[str] = []
    for candidate in (
        row["creator_handle"],
        payload.get("ref_code"),
        payload.get("creator_code"),
        payload.get("creator_handle"),
        body.get("discount_code"),
        body.get("source_name"),
    ):
        cleaned = _clean_affiliate_ref(candidate)
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)
    for item in note_attributes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or item.get("key") or "").strip().lower()
        value = _clean_affiliate_ref(item.get("value"))
        if key in {"ref", "creator", "creator_code", "creator_id", "affiliate", "code"} and value and value not in candidates:
            candidates.append(value)
    return candidates


def _sync_affiliate_order_reward_traces(limit: int = 400, window_days: int = 21) -> dict[str, Any]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, external_id, creator_handle, occurred_at, processed_at, ingest_status, payload_json
            FROM platform_ingest_events
            WHERE source_platform='shopify' AND entity_type='order'
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        user_rows = conn.execute("SELECT * FROM users ORDER BY id DESC LIMIT ?", (max(200, int(limit) * 2),)).fetchall()
    except Exception:
        return {"imported": 0, "skipped": 0, "matched_users": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 1)))
    user_by_code = {_clean_affiliate_ref(row["creator_code"]): dict(row) for row in user_rows if _clean_affiliate_ref(row["creator_code"])}
    user_by_email = {_clean_affiliate_ref(row["email"]): dict(row) for row in user_rows if _clean_affiliate_ref(row["email"])}
    latest_decision_by_user: dict[int, dict[str, Any]] = {}
    program_cache: dict[int, dict[str, Any]] = {}
    for item in list_recent_via_decisions(max(160, int(limit) * 2)):
        user_id = int(item.get("user_id") or 0)
        if user_id > 0 and user_id not in latest_decision_by_user:
            latest_decision_by_user[user_id] = item

    imported = 0
    skipped = 0
    matched_users = 0
    for row in rows:
        occurred_at = _parse_timestamp(row["occurred_at"] or row["processed_at"] or "")
        if occurred_at and occurred_at < cutoff:
            continue
        payload = _load_json_doc(row["payload_json"], {})
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
        candidates = _extract_affiliate_order_candidates(row, payload)
        customer_email = _clean_affiliate_ref(customer.get("email"))
        if customer_email and customer_email not in candidates:
            candidates.append(customer_email)
        matched_user = None
        for candidate in candidates:
            matched_user = user_by_code.get(candidate) or user_by_email.get(candidate)
            if matched_user:
                break
        user_id = int((matched_user or {}).get("id") or 0)
        if user_id > 0:
            matched_users += 1
        creator_ref = next((candidate for candidate in candidates if candidate), "") or _clean_affiliate_ref(row["creator_handle"]) or f"order-{int(row['id'])}"
        latest_decision = latest_decision_by_user.get(user_id) if user_id > 0 else {}
        session_key = str((latest_decision or {}).get("session_key") or f"affiliate:{creator_ref}")
        order_id = str(row["external_id"] or body.get("id") or body.get("order_number") or row["id"]).strip()
        idempotency_key = f"shopify-order:{order_id}"
        if get_via_reward_trace_by_idempotency_key(idempotency_key):
            skipped += 1
            continue
        effective_rate = 0.0
        if matched_user:
            if user_id not in program_cache:
                program_cache[user_id] = build_creator_program_snapshot(dict(matched_user))
            program = program_cache[user_id]
            effective_rate = float(program.get("effective_commission_rate") or 0.0)
        order_total = float(body.get("current_total_price") or body.get("total_price") or payload.get("order_total") or 0.0)
        estimated_commission = round(order_total * effective_rate, 2) if effective_rate > 0 else 0.0
        insert_via_reward_trace(
            session_key=session_key,
            decision_id=str((latest_decision or {}).get("decision_id") or ""),
            user_id=user_id,
            event_type="affiliate_order",
            surface="affiliate",
            source="shopify",
            origin="platform_ingest",
            product_key=creator_ref,
            event_value=order_total,
            event_payload={
                "order_id": order_id,
                "ref_code": creator_ref,
                "financial_status": str(body.get("financial_status") or ""),
                "ingest_status": str(row["ingest_status"] or ""),
                "estimated_commission": estimated_commission,
            },
            idempotency_key=idempotency_key,
        )
        imported += 1
    return {"imported": imported, "skipped": skipped, "matched_users": matched_users}


def _summarize_retrieval_evidence(evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_rows = list(evidence_rows or [])
    if not evidence_rows:
        return {
            "evidence_count": 0,
            "avg_top_score": 0.0,
            "avg_score": 0.0,
            "avg_score_spread": 0.0,
            "source_mix": {},
            "retrieval_modes": {},
            "rerank_rate": 0.0,
            "score_drift": "stable",
        }
    source_mix = Counter()
    retrieval_modes = Counter(str(item.get("retrieval_mode") or "unknown") for item in evidence_rows)
    top_scores = [float(item.get("top_score") or 0.0) for item in evidence_rows]
    avg_scores = [float(item.get("avg_score") or 0.0) for item in evidence_rows]
    spreads = [float(item.get("score_spread") or 0.0) for item in evidence_rows]
    rerank_count = sum(1 for item in evidence_rows if bool(item.get("rerank_applied")))
    for item in evidence_rows:
        for source in list(item.get("selected_sources") or []):
            key = str(source or "").strip()
            if key:
                source_mix[key] += 1
    avg_score = mean(avg_scores) if avg_scores else 0.0
    score_drift = "stable"
    if avg_score < 0.34:
        score_drift = "low_confidence"
    elif (mean(spreads) if spreads else 0.0) > 0.42:
        score_drift = "high_spread"
    return {
        "evidence_count": len(evidence_rows),
        "avg_top_score": round(mean(top_scores), 4) if top_scores else 0.0,
        "avg_score": round(avg_score, 4),
        "avg_score_spread": round(mean(spreads), 4) if spreads else 0.0,
        "source_mix": dict(source_mix.most_common()),
        "retrieval_modes": dict(retrieval_modes.most_common()),
        "rerank_rate": round(rerank_count / max(1, len(evidence_rows)), 4),
        "score_drift": score_drift,
        "recent": evidence_rows[:16],
    }


def _summarize_routing_learner_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows or [])
    if not rows:
        return {"provider_count": 0, "bucket_count": 0, "providers": {}, "buckets": {}, "recent": []}
    provider_rollup: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"exposure": 0.0, "success": 0.0, "reward": 0.0, "guard": 0.0})
    bucket_rollup: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"exposure": 0.0, "reward": 0.0})
    for item in rows:
        provider = str(item.get("provider") or "unknown")
        bucket = str(item.get("bucket_key") or "unknown")
        provider_rollup[provider]["exposure"] += float(item.get("exposure_count") or 0)
        provider_rollup[provider]["success"] += float(item.get("success_count") or 0)
        provider_rollup[provider]["reward"] += float(item.get("reward_sum") or 0.0)
        provider_rollup[provider]["guard"] += float(item.get("guard_fail_count") or 0)
        bucket_rollup[bucket]["exposure"] += float(item.get("exposure_count") or 0)
        bucket_rollup[bucket]["reward"] += float(item.get("reward_sum") or 0.0)
    return {
        "provider_count": len(provider_rollup),
        "bucket_count": len(bucket_rollup),
        "providers": {
            key: {
                "exposure_count": int(value["exposure"]),
                "success_rate": round(value["success"] / max(1.0, value["exposure"]), 4),
                "avg_reward": round(value["reward"] / max(1.0, value["exposure"]), 4),
                "guard_fail_rate": round(value["guard"] / max(1.0, value["exposure"]), 4),
            }
            for key, value in provider_rollup.items()
        },
        "buckets": {
            key: {
                "exposure_count": int(value["exposure"]),
                "avg_reward": round(value["reward"] / max(1.0, value["exposure"]), 4),
            }
            for key, value in bucket_rollup.items()
        },
        "recent": rows[:16],
    }


def _apply_retention_decay(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for item in list(rows or []):
        row = dict(item)
        last_hit = _parse_timestamp(row.get("last_hit_at") or row.get("last_promoted_at") or "")
        age_days = (now - last_hit).days if last_hit else 0
        decay_state = "fresh"
        status = str(row.get("status") or "active")
        if age_days >= 45:
            decay_state = "inactive"
            status = "inactive"
        elif age_days >= 21:
            decay_state = "decaying"
        row["age_days"] = age_days
        row["decay_state"] = decay_state
        row["status"] = status
        items.append(row)
    return items


def _summarize_memory_retention(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _apply_retention_decay(rows)
    if not rows:
        return {"tracked": 0, "active": 0, "decaying": 0, "inactive": 0, "tiers": {}, "recent": []}
    tiers = Counter(str(item.get("memory_tier") or "unknown") for item in rows)
    decay = Counter(str(item.get("decay_state") or "fresh") for item in rows)
    avg_reward = mean([float(item.get("cumulative_reward") or 0.0) for item in rows]) if rows else 0.0
    confirmed = sum(int(item.get("confirmed_hits") or 0) for item in rows)
    reinforcements = sum(int(item.get("reinforcement_count") or 0) for item in rows)
    return {
        "tracked": len(rows),
        "active": int(decay.get("fresh", 0)),
        "decaying": int(decay.get("decaying", 0)),
        "inactive": int(decay.get("inactive", 0)),
        "tiers": dict(tiers.most_common()),
        "avg_cumulative_reward": round(avg_reward, 4),
        "confirmed_hits": confirmed,
        "reinforcement_count": reinforcements,
        "recent": rows[:16],
    }


def _summarize_control_window(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    reward_traces: list[dict[str, Any]] | None = None,
    retrieval_evidence: list[dict[str, Any]] | None = None,
    routing_stats: list[dict[str, Any]] | None = None,
    memory_retention: list[dict[str, Any]] | None = None,
    window_days: int,
) -> dict[str, Any]:
    reward_traces = list(reward_traces or [])
    retrieval_evidence = list(retrieval_evidence or [])
    routing_stats = list(routing_stats or [])
    memory_retention = list(memory_retention or [])
    decision_by_id = {
        str(item.get("decision_id") or ""): item
        for item in decisions
        if str(item.get("decision_id") or "").strip()
    }
    decision_types = Counter(str(item.get("decision_type") or "unknown") for item in decisions)
    triggers = Counter(str(item.get("trigger_type") or "unknown") for item in decisions)
    policies = Counter(str(item.get("policy_key") or "unknown") for item in decisions)
    reply_modes = Counter(
        str((item.get("chosen_action") or {}).get("mode") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "reply_mode"
    )
    providers = Counter(
        str((item.get("chosen_action") or {}).get("provider") or "")
        for item in decisions
        if str(item.get("decision_type") or "") in {"reply_mode", "model_choice"}
    )
    promotion_tiers = Counter(
        str((item.get("chosen_action") or {}).get("tier") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "memory_promotion"
    )
    shadow_targets = Counter(
        str((item.get("chosen_action") or {}).get("target") or item.get("trigger_type") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
    )
    shadow_changed = [
        item for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
        and bool((item.get("chosen_action") or {}).get("would_change"))
    ]
    rewards = [float(item.get("reward_score") or 0.0) for item in outcomes]
    accepted = [item for item in outcomes if bool(item.get("accepted"))]
    clicked = [item for item in outcomes if bool(item.get("clicked_product"))]
    added_to_cart = [item for item in outcomes if bool(item.get("added_to_cart"))]
    purchased = [item for item in outcomes if bool(item.get("purchased"))]
    abuse = [item for item in outcomes if int(item.get("abuse_flag") or 0) > 0]
    trace_types = Counter(str(item.get("event_type") or "unknown") for item in reward_traces)
    trace_value_total = sum(float(item.get("event_value") or 0.0) for item in reward_traces)
    trace_commission_total = sum(
        float((item.get("event_payload") or {}).get("estimated_commission") or 0.0)
        for item in reward_traces
    )
    fallback_count = reply_modes.get("fallback", 0)
    ai_dialogue_count = reply_modes.get("ai_dialogue", 0)
    fast_brain_count = reply_modes.get("fast_brain", 0)
    retrieval_rows = [item for item in decisions if str(item.get("decision_type") or "") == "retrieval_plan"]
    memory_required_rows = [
        item for item in decisions
        if str(item.get("decision_type") or "") == "intent_route"
        and bool((item.get("chosen_action") or {}).get("needs_memory"))
    ]
    vector_hit_rows = [
        item for item in retrieval_rows
        if str((item.get("chosen_action") or {}).get("plan") or "") == "vector_memory"
    ]
    model_choices = [item for item in decisions if str(item.get("decision_type") or "") == "model_choice"]
    retrieval_summary = _summarize_retrieval_evidence(retrieval_evidence)
    routing_summary = _summarize_routing_learner_stats(routing_stats)
    memory_summary = _summarize_memory_retention(memory_retention)

    enriched_outcomes: list[dict[str, Any]] = []
    for item in outcomes[:]:
        linked = decision_by_id.get(str(item.get("decision_id") or ""))
        enriched_outcomes.append(
            {
                **item,
                "decision_type": str((linked or {}).get("decision_type") or ""),
                "policy_key": str((linked or {}).get("policy_key") or ""),
                "trigger_type": str((linked or {}).get("trigger_type") or ""),
            }
        )

    return {
        "window_days": int(window_days),
        "metrics": {
            "decision_count": len(decisions),
            "outcome_count": len(outcomes),
            "accepted_count": len(accepted),
            "accepted_rate": round(len(accepted) / max(1, len(outcomes)), 4),
            "clicked_product_rate": round(len(clicked) / max(1, len(outcomes)), 4),
            "add_to_cart_rate": round(len(added_to_cart) / max(1, len(outcomes)), 4),
            "purchase_rate": round(len(purchased) / max(1, len(outcomes)), 4),
            "abuse_rate": round(len(abuse) / max(1, len(outcomes)), 4),
            "avg_reward": round(mean(rewards), 4) if rewards else 0.0,
            "avg_latency_ms": round(mean([float(item.get("latency_ms") or 0.0) for item in decisions]), 2) if decisions else 0.0,
            "estimated_cost_total": round(sum(float(item.get("cost_estimate") or 0.0) for item in decisions), 6),
            "fallback_count": int(fallback_count),
            "ai_dialogue_count": int(ai_dialogue_count),
            "fast_brain_count": int(fast_brain_count),
            "memory_required_count": len(memory_required_rows),
            "vector_hit_count": len(vector_hit_rows),
            "vector_hit_rate": round(len(vector_hit_rows) / max(1, len(memory_required_rows)), 4),
            "model_choice_count": len(model_choices),
            "shadow_eval_count": sum(shadow_targets.values()),
            "shadow_change_count": len(shadow_changed),
            "reward_trace_count": len(reward_traces),
            "compare_trace_count": int(trace_types.get("compare", 0)),
            "cart_trace_count": int(trace_types.get("add_to_cart", 0)),
            "purchase_trace_count": int(trace_types.get("purchase", 0)),
            "affiliate_order_trace_count": int(trace_types.get("affiliate_order", 0)),
            "reward_trace_value_total": round(trace_value_total, 2),
            "reward_trace_commission_total": round(trace_commission_total, 2),
            "retrieval_evidence_count": int(retrieval_summary.get("evidence_count") or 0),
            "routing_provider_count": int(routing_summary.get("provider_count") or 0),
            "memory_retention_tracked": int(memory_summary.get("tracked") or 0),
        },
        "decision_types": dict(decision_types.most_common()),
        "triggers": dict(triggers.most_common(12)),
        "policies": dict(policies.most_common()),
        "reply_modes": {key: value for key, value in reply_modes.items() if key},
        "providers": {key: value for key, value in providers.items() if key},
        "promotion_tiers": {key: value for key, value in promotion_tiers.items() if key},
        "shadow_targets": {key: value for key, value in shadow_targets.items() if key},
        "reward_trace_types": dict(trace_types.most_common()),
        "retrieval_evidence": retrieval_summary,
        "routing_learner": routing_summary,
        "memory_retention": memory_summary,
        "recent_decisions": decisions[:24],
        "recent_outcomes": enriched_outcomes[:24],
        "recent_reward_traces": reward_traces[:24],
    }


def _summarize_shadow_rollout_readiness(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    staged_versions: list[dict[str, Any]],
    *,
    window_days: int,
) -> list[dict[str, Any]]:
    outcomes_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    traces_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in outcomes:
        key = str(item.get("session_key") or "").strip()
        if key:
            outcomes_by_session[key].append(item)
    for item in reward_traces:
        key = str(item.get("session_key") or "").strip()
        if key:
            traces_by_session[key].append(item)

    readiness: list[dict[str, Any]] = []
    for version in staged_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(_P1_SHADOW_ROLLOUT_RULES.get(policy_key) or {})
        if not rules:
            continue
        version_key = str(version.get("version_key") or "").strip()
        target = str(rules.get("target") or "").strip()
        shadow_rows = [
            item
            for item in decisions
            if str(item.get("decision_type") or "") == "shadow_eval"
            and str((item.get("chosen_action") or {}).get("shadow_version_key") or "") == version_key
            and str((item.get("chosen_action") or {}).get("target") or item.get("trigger_type") or "") == target
        ]
        session_keys = {
            str(item.get("session_key") or "").strip()
            for item in shadow_rows
            if str(item.get("session_key") or "").strip()
        }
        linked_outcomes = [item for session_key in session_keys for item in outcomes_by_session.get(session_key, [])]
        linked_traces = [item for session_key in session_keys for item in traces_by_session.get(session_key, [])]
        trace_types = Counter(str(item.get("event_type") or "unknown") for item in linked_traces)
        changed_count = sum(1 for item in shadow_rows if bool((item.get("chosen_action") or {}).get("would_change")))
        accepted_count = sum(1 for item in linked_outcomes if bool(item.get("accepted")))
        abuse_count = sum(1 for item in linked_outcomes if int(item.get("abuse_flag") or 0) > 0)
        reward_values = [float(item.get("reward_score") or 0.0) for item in linked_outcomes]
        shadow_count = len(shadow_rows)
        accepted_rate = round(accepted_count / max(1, len(linked_outcomes)), 4)
        change_rate = round(changed_count / max(1, shadow_count), 4)
        abuse_rate = round(abuse_count / max(1, len(linked_outcomes)), 4)
        avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
        positive_signals = int(trace_types.get("compare", 0) + trace_types.get("add_to_cart", 0) + trace_types.get("purchase", 0) + trace_types.get("affiliate_order", 0))

        reasons: list[str] = []
        status = "hold"
        recommended_rollout_percentage = 0.0
        if shadow_count < int(rules.get("min_shadow_samples") or 1):
            reasons.append("need_more_shadow_samples")
        if not linked_outcomes:
            reasons.append("missing_outcome_feedback")
        if change_rate < float(rules.get("min_change_rate") or 0.0):
            reasons.append("shadow_delta_too_small")
        if linked_outcomes and accepted_rate < float(rules.get("min_acceptance_rate") or 0.0):
            reasons.append("acceptance_below_threshold")
        if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
            reasons.append("reward_below_threshold")
        if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
            reasons.append("abuse_rate_too_high")
        if shadow_count >= int(rules.get("min_shadow_samples") or 1) and not reasons:
            status = "eligible_for_limited_rollout"
            recommended_rollout_percentage = 0.05
            if shadow_count >= int(rules.get("promote_shadow_samples") or shadow_count) and positive_signals >= 2 and avg_reward >= float(rules.get("min_avg_reward") or 0.0) + 0.05:
                recommended_rollout_percentage = 0.15
                status = "eligible_for_broader_limited_rollout"
        readiness.append(
            {
                "policy_key": policy_key,
                "target": target,
                "version_key": version_key,
                "version_label": str(version.get("version_label") or ""),
                "status": status,
                "recommended_action": "promote_limited" if recommended_rollout_percentage > 0 else "hold",
                "recommended_rollout_percentage": recommended_rollout_percentage,
                "reasons": reasons,
                "metrics": {
                    "shadow_eval_count": shadow_count,
                    "shadow_change_count": changed_count,
                    "shadow_change_rate": change_rate,
                    "session_count": len(session_keys),
                    "accepted_rate": accepted_rate,
                    "avg_reward": avg_reward,
                    "abuse_rate": abuse_rate,
                    "compare_count": int(trace_types.get("compare", 0)),
                    "add_to_cart_count": int(trace_types.get("add_to_cart", 0)),
                    "purchase_count": int(trace_types.get("purchase", 0)),
                    "affiliate_order_count": int(trace_types.get("affiliate_order", 0)),
                    "positive_signal_count": positive_signals,
                },
                "thresholds": rules,
                "window_days": int(window_days or 14),
            }
        )
    return readiness


def _next_rollout_percentage(current: float) -> float:
    for step in _P1_LIVE_ROLLOUT_STEPS:
        if step > float(current or 0.0) + 1e-9:
            return step
    return 0.0


def _required_live_samples(current_rollout_percentage: float) -> int:
    pct = float(current_rollout_percentage or 0.0)
    if pct <= 0.05:
        return 6
    if pct <= 0.15:
        return 10
    if pct <= 0.30:
        return 16
    if pct <= 0.60:
        return 24
    return 32


def _summarize_live_rollout_health(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    live_versions: list[dict[str, Any]],
    *,
    window_days: int,
    version_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    version_history = list(version_history or [])
    outcomes_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    traces_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in outcomes:
        key = str(item.get("session_key") or "").strip()
        if key:
            outcomes_by_session[key].append(item)
    for item in reward_traces:
        key = str(item.get("session_key") or "").strip()
        if key:
            traces_by_session[key].append(item)

    health_rows: list[dict[str, Any]] = []
    for version in live_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(_P1_SHADOW_ROLLOUT_RULES.get(policy_key) or {})
        if not rules:
            continue
        config = dict(version.get("config") or {})
        rollout_mode = str(config.get("rollout_mode") or "").strip().lower()
        if rollout_mode != "limited":
            continue
        target = str(rules.get("target") or "").strip()
        version_label = str(version.get("version_label") or "")
        current_rollout_percentage = float(config.get("rollout_percentage") or 0.0)
        next_rollout_percentage = _next_rollout_percentage(current_rollout_percentage)
        target_rows = [
            item
            for item in decisions
            if str(item.get("decision_type") or "") == target
            and str(item.get("policy_key") or "") == policy_key
            and str(item.get("policy_version") or "") == version_label
        ]
        session_keys = {
            str(item.get("session_key") or "").strip()
            for item in target_rows
            if str(item.get("session_key") or "").strip()
        }
        linked_outcomes = [item for session_key in session_keys for item in outcomes_by_session.get(session_key, [])]
        linked_traces = [item for session_key in session_keys for item in traces_by_session.get(session_key, [])]
        trace_types = Counter(str(item.get("event_type") or "unknown") for item in linked_traces)
        accepted_count = sum(1 for item in linked_outcomes if bool(item.get("accepted")))
        abuse_count = sum(1 for item in linked_outcomes if int(item.get("abuse_flag") or 0) > 0)
        reward_values = [float(item.get("reward_score") or 0.0) for item in linked_outcomes]
        accepted_rate = round(accepted_count / max(1, len(linked_outcomes)), 4)
        abuse_rate = round(abuse_count / max(1, len(linked_outcomes)), 4)
        avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
        positive_signals = int(trace_types.get("compare", 0) + trace_types.get("add_to_cart", 0) + trace_types.get("purchase", 0) + trace_types.get("affiliate_order", 0))
        min_live_samples = _required_live_samples(current_rollout_percentage)
        reasons: list[str] = []
        status = "hold"
        if not linked_outcomes:
            reasons.append("missing_live_outcomes")
        if len(target_rows) < min_live_samples:
            reasons.append("need_more_live_samples")
        if linked_outcomes and accepted_rate < float(rules.get("min_acceptance_rate") or 0.0):
            reasons.append("acceptance_below_threshold")
        if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
            reasons.append("reward_below_threshold")
        if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
            reasons.append("abuse_rate_too_high")
        if current_rollout_percentage >= 0.15 and positive_signals <= 0:
            reasons.append("missing_positive_signals")
        previous_versions = [
            item for item in version_history
            if str(item.get("policy_key") or "") == policy_key
            and str(item.get("version_key") or "") != str(version.get("version_key") or "")
            and str(item.get("status") or "").lower() in {"superseded", "live"}
        ]
        previous_version = previous_versions[0] if previous_versions else {}
        previous_label = str(previous_version.get("version_label") or "")
        previous_rows = [
            item for item in decisions
            if str(item.get("decision_type") or "") == target
            and str(item.get("policy_key") or "") == policy_key
            and str(item.get("policy_version") or "") == previous_label
        ]
        previous_sessions = {
            str(item.get("session_key") or "").strip()
            for item in previous_rows
            if str(item.get("session_key") or "").strip()
        }
        previous_outcomes = [item for session_key in previous_sessions for item in outcomes_by_session.get(session_key, [])]
        prev_accept = round(sum(1 for item in previous_outcomes if bool(item.get("accepted"))) / max(1, len(previous_outcomes)), 4) if previous_outcomes else 0.0
        prev_reward = round(mean([float(item.get("reward_score") or 0.0) for item in previous_outcomes]), 4) if previous_outcomes else 0.0
        half_cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 14) // 2))
        current_recent = [item for item in linked_outcomes if (_parse_timestamp(item.get("created_at") or "") or datetime.now(timezone.utc)) >= half_cutoff]
        previous_recent = [item for item in previous_outcomes if (_parse_timestamp(item.get("created_at") or "") or datetime.now(timezone.utc)) >= half_cutoff]
        current_recent_accept = round(sum(1 for item in current_recent if bool(item.get("accepted"))) / max(1, len(current_recent)), 4) if current_recent else 0.0
        current_recent_reward = round(mean([float(item.get("reward_score") or 0.0) for item in current_recent]), 4) if current_recent else 0.0
        prev_recent_accept = round(sum(1 for item in previous_recent if bool(item.get("accepted"))) / max(1, len(previous_recent)), 4) if previous_recent else 0.0
        prev_recent_reward = round(mean([float(item.get("reward_score") or 0.0) for item in previous_recent]), 4) if previous_recent else 0.0
        rollback_candidate = bool(previous_outcomes) and (
            accepted_rate + 0.05 < prev_accept and avg_reward + 0.04 < prev_reward
            and current_recent_accept + 0.05 < prev_recent_accept
            and current_recent_reward + 0.04 < prev_recent_reward
        )
        if rollback_candidate:
            status = "rollback_candidate"
            reasons.append("underperforming_previous_stable")
        elif next_rollout_percentage <= 0 and not reasons:
            status = "at_full_rollout"
        elif not reasons:
            status = "healthy"
        health_rows.append(
            {
                "policy_key": policy_key,
                "target": target,
                "version_key": str(version.get("version_key") or ""),
                "version_label": version_label,
                "status": status,
                "current_rollout_percentage": current_rollout_percentage,
                "next_rollout_percentage": next_rollout_percentage,
                "recommended_action": "rollback_review" if status == "rollback_candidate" else ("advance_rollout" if status == "healthy" and next_rollout_percentage > 0 else "hold"),
                "reasons": reasons,
                "metrics": {
                    "live_decision_count": len(target_rows),
                    "session_count": len(session_keys),
                    "accepted_rate": accepted_rate,
                    "avg_reward": avg_reward,
                    "abuse_rate": abuse_rate,
                    "compare_count": int(trace_types.get("compare", 0)),
                    "add_to_cart_count": int(trace_types.get("add_to_cart", 0)),
                    "purchase_count": int(trace_types.get("purchase", 0)),
                    "affiliate_order_count": int(trace_types.get("affiliate_order", 0)),
                    "positive_signal_count": positive_signals,
                    "previous_accepted_rate": prev_accept,
                    "previous_avg_reward": prev_reward,
                    "current_recent_accepted_rate": current_recent_accept,
                    "current_recent_avg_reward": current_recent_reward,
                    "previous_recent_accepted_rate": prev_recent_accept,
                    "previous_recent_avg_reward": prev_recent_reward,
                },
                "thresholds": {
                    **rules,
                    "min_live_samples": min_live_samples,
                },
                "window_days": int(window_days or 14),
            }
        )
    return health_rows


def _persist_rollout_alerts(
    shadow_rows: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for item in shadow_rows:
        if str(item.get("status") or "") not in {"hold"}:
            continue
        alerts.append(
            upsert_via_rollout_alert(
                policy_key=str(item.get("policy_key") or ""),
                version_key=str(item.get("version_key") or ""),
                version_label=str(item.get("version_label") or ""),
                alert_type="shadow_hold",
                severity="medium",
                recommendation=str(item.get("recommended_action") or "hold"),
                reason_text=", ".join(list(item.get("reasons") or [])) or "shadow_not_ready",
                metrics=item.get("metrics") or {},
                observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    for item in live_rows:
        status = str(item.get("status") or "")
        if status not in {"hold", "rollback_candidate"}:
            continue
        alerts.append(
            upsert_via_rollout_alert(
                policy_key=str(item.get("policy_key") or ""),
                version_key=str(item.get("version_key") or ""),
                version_label=str(item.get("version_label") or ""),
                alert_type="rollback_candidate" if status == "rollback_candidate" else "rollout_hold",
                severity="high" if status == "rollback_candidate" else "medium",
                recommendation=str(item.get("recommended_action") or "hold"),
                reason_text=", ".join(list(item.get("reasons") or [])) or status,
                metrics=item.get("metrics") or {},
                observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    return alerts


async def list_via_shadow_rollout_readiness(
    *,
    window_days: int = 14,
    limit: int = 300,
    version_key: str = "",
    policy_key: str = "",
) -> list[dict[str, Any]]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(80, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(80, int(limit)))
    reward_traces = await asyncio.to_thread(list_recent_via_reward_traces, max(120, int(limit)))
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(reward_traces, window_days)
    staged_versions = [
        item
        for item in active_versions
        if str(item.get("status") or "").lower() == "staged"
        and (not policy_key or str(item.get("policy_key") or "") == str(policy_key or ""))
        and (not version_key or str(item.get("version_key") or "") == str(version_key or ""))
    ]
    return _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        staged_versions,
        window_days=window_days,
    )


async def list_via_live_rollout_health(
    *,
    window_days: int = 14,
    limit: int = 300,
    version_key: str = "",
    policy_key: str = "",
) -> list[dict[str, Any]]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(80, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(80, int(limit)))
    reward_traces = await asyncio.to_thread(list_recent_via_reward_traces, max(120, int(limit)))
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    version_history = await asyncio.to_thread(list_via_policy_version_history, max(120, int(limit) * 2), policy_key, "", "")
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(reward_traces, window_days)
    live_versions = [
        item
        for item in active_versions
        if str(item.get("status") or "").lower() == "live"
        and (not policy_key or str(item.get("policy_key") or "") == str(policy_key or ""))
        and (not version_key or str(item.get("version_key") or "") == str(version_key or ""))
    ]
    return _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        live_versions,
        window_days=window_days,
        version_history=version_history,
    )


async def get_via_rollout_alert_snapshot(
    *,
    limit: int = 80,
    policy_key: str = "",
    version_key: str = "",
    status: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(
        list_via_rollout_alerts,
        int(limit),
        str(policy_key or ""),
        str(version_key or ""),
        str(status or ""),
    )
    severity = Counter(str(item.get("severity") or "medium") for item in rows)
    return {
        "count": len(rows),
        "severity": dict(severity.most_common()),
        "items": rows[: int(limit)],
    }


async def get_via_retrieval_evidence_snapshot(
    *,
    window_days: int = 14,
    limit: int = 120,
    policy_key: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_recent_via_retrieval_evidence, max(40, int(limit)), str(policy_key or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_retrieval_evidence(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def get_via_routing_learner_snapshot(
    *,
    window_days: int = 21,
    limit: int = 120,
    bucket_key: str = "",
    target: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_via_routing_provider_stats, max(40, int(limit)), str(bucket_key or ""), str(target or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_routing_learner_stats(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def get_via_memory_retention_snapshot(
    *,
    window_days: int = 45,
    limit: int = 120,
    memory_tier: str = "",
    status: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_via_memory_retention_stats, max(40, int(limit)), str(memory_tier or ""), str(status or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_memory_retention(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def promote_via_policy_version_guarded(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
    window_days: int = 14,
    limit: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    staged = await asyncio.to_thread(get_via_policy_version, version_key)
    if not staged:
        raise ValueError("Policy version not found")
    policy_key = str(staged.get("policy_key") or "")
    readiness_items = await list_via_shadow_rollout_readiness(
        window_days=window_days,
        limit=limit,
        version_key=version_key,
        policy_key=policy_key,
    )
    readiness = readiness_items[0] if readiness_items else {}
    config_override: dict[str, Any] | None = None
    if policy_key in _P1_SHADOW_ROLLOUT_RULES:
        recommended_rollout = float((readiness.get("recommended_rollout_percentage") or 0.0) if readiness else 0.0)
        if not force and recommended_rollout <= 0:
            reason_text = ", ".join(list(readiness.get("reasons") or [])) if readiness else "shadow_not_ready"
            raise ValueError(f"Shadow readiness has not cleared promote gate: {reason_text}")
        if recommended_rollout > 0:
            config_override = dict(staged.get("config") or {})
            config_override.update(
                {
                    "rollout_mode": "limited",
                    "rollout_percentage": recommended_rollout,
                    "rollout_stage": "p1_guarded",
                    "shadow_source_version_key": version_key,
                    "shadow_window_days": int(window_days or 14),
                    "shadow_readiness": {
                        "status": str(readiness.get("status") or ""),
                        "metrics": readiness.get("metrics") or {},
                        "reasons": readiness.get("reasons") or [],
                    },
                }
            )
    result = await asyncio.to_thread(
        promote_via_policy_version,
        version_key,
        actor=actor,
        note=note,
        config_override=config_override,
    )
    result["shadow_readiness"] = readiness
    return result


async def advance_via_live_policy_rollout_guarded(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
    window_days: int = 14,
    limit: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_version = await asyncio.to_thread(get_via_policy_version, version_key)
    if not live_version:
        raise ValueError("Policy version not found")
    if str(live_version.get("status") or "").lower() != "live":
        raise ValueError("Only live versions can advance rollout")
    health_items = await list_via_live_rollout_health(
        window_days=window_days,
        limit=limit,
        version_key=version_key,
        policy_key=str(live_version.get("policy_key") or ""),
    )
    health = health_items[0] if health_items else {}
    next_rollout = float((health.get("next_rollout_percentage") or 0.0) if health else 0.0)
    if next_rollout <= 0:
        raise ValueError("This live version is already at full rollout")
    if not force and str(health.get("status") or "") != "healthy":
        reason_text = ", ".join(list(health.get("reasons") or [])) if health else "rollout_not_ready"
        raise ValueError(f"Live rollout has not cleared advance gate: {reason_text}")
    config = dict(live_version.get("config") or {})
    config.update(
        {
            "rollout_mode": "limited" if next_rollout < 1.0 else "full",
            "rollout_percentage": next_rollout,
            "rollout_stage": f"p1_ramp_{int(round(next_rollout * 100))}",
            "rollout_from_version_key": version_key,
            "live_rollout_health": {
                "status": str(health.get("status") or ""),
                "metrics": health.get("metrics") or {},
                "reasons": health.get("reasons") or [],
            },
        }
    )
    current_label = str(live_version.get("version_label") or "")
    next_label = f"{current_label}.r{int(round(next_rollout * 100))}" if current_label else f"rollout.r{int(round(next_rollout * 100))}"
    next_live = await asyncio.to_thread(
        create_via_policy_version,
        policy_key=str(live_version.get("policy_key") or ""),
        config=config,
        version_label=next_label,
        source_proposal_key=str(live_version.get("source_proposal_key") or f"rollout:{version_key}"),
        status="live",
        approved_by=str(live_version.get("approved_by") or actor or ""),
        approved_at=str(live_version.get("approved_at") or now),
        applied_by=str(actor or ""),
        applied_at=now,
        review_note=str(note or live_version.get("review_note") or f"Advance rollout from {current_label or version_key}"),
    )
    return {
        "previous_live_version": live_version,
        "live_version": next_live,
        "live_rollout_health": health,
    }


def _build_policy_proposals(control_summary: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = dict(control_summary.get("metrics") or {})
    proposals: list[dict[str, Any]] = []
    window_days = int(control_summary.get("window_days") or 14)
    reply_modes = dict(control_summary.get("reply_modes") or {})
    providers = dict(control_summary.get("providers") or {})
    promotion_tiers = dict(control_summary.get("promotion_tiers") or {})
    retrieval_evidence = dict(control_summary.get("retrieval_evidence") or {})
    routing_learner = dict(control_summary.get("routing_learner") or {})
    memory_retention = dict(control_summary.get("memory_retention") or {})

    if int(metrics.get("memory_required_count") or 0) >= 8 and float(metrics.get("vector_hit_rate") or 0.0) < 0.45:
        proposals.append(
            {
                "proposal_key": f"retrieval-{window_days}-{int(metrics.get('memory_required_count') or 0)}",
                "proposal_type": "retrieval_tuning",
                "policy_key": "via.retrieval.selective",
                "status": "proposed",
                "confidence": 0.78,
                "impact_score": 0.72,
                "evidence": {
                    "memory_required_count": int(metrics.get("memory_required_count") or 0),
                    "vector_hit_rate": float(metrics.get("vector_hit_rate") or 0.0),
                    "retrieval_evidence": {
                        "avg_score": float(retrieval_evidence.get("avg_score") or 0.0),
                        "score_drift": str(retrieval_evidence.get("score_drift") or "stable"),
                        "source_mix": retrieval_evidence.get("source_mix") or {},
                    },
                },
                "proposal": {
                    "summary": "Memory-required turns are outrunning useful vector hits. Add hybrid retrieval and trigger-based fallback ordering.",
                    "actions": [
                        "prioritize hybrid retrieval when memory_required and vector_hit_rate < 0.45",
                        "log retrieval score spread to support later rerank learning",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.retrieval.hybrid",
                        "retrieval_mode": "hybrid_vector_seed",
                        "vector_hit_threshold": 0.45,
                        "fallback_order": ["bundle_memory", "vector_memory", "seed_knowledge"],
                    },
                },
                "window_days": window_days,
            }
        )

    if int(metrics.get("model_choice_count") or 0) >= 6 and len([key for key in providers.keys() if key]) <= 1:
        observed_providers = [key for key in providers.keys() if key]
        rollout_providers = observed_providers if len(observed_providers) > 1 else ([observed_providers[0]] if observed_providers else []) + [item for item in ["openai", "gemini", "claude"] if item not in observed_providers]
        proposals.append(
            {
                "proposal_key": f"routing-{window_days}-{int(metrics.get('model_choice_count') or 0)}",
                "proposal_type": "routing_exploration",
                "policy_key": "via.model.route",
                "status": "proposed",
                "confidence": 0.74,
                "impact_score": 0.63,
                "evidence": {
                    "model_choice_count": int(metrics.get("model_choice_count") or 0),
                    "providers": providers,
                    "routing_learner": {
                        "provider_count": int(routing_learner.get("provider_count") or 0),
                        "providers": routing_learner.get("providers") or {},
                    },
                },
                "proposal": {
                    "summary": "Model routing has enough traffic to start exploration. Introduce bandit-style provider sampling before hard-coding one route.",
                    "actions": [
                        "sample secondary provider on 10-15% of eligible dialogue turns",
                        "compare reward_score, latency_ms, and cost_estimate by provider",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.routing.explore",
                        "execution_mode": "bandit_explore",
                        "exploration_ratio": 0.12,
                        "providers": rollout_providers[:3] or ["openai", "gemini", "claude"],
                    },
                },
                "window_days": window_days,
            }
        )

    if int(reply_modes.get("fallback") or 0) >= 3:
        proposals.append(
            {
                "proposal_key": f"fallback-{window_days}-{int(reply_modes.get('fallback') or 0)}",
                "proposal_type": "fallback_reduction",
                "policy_key": "via.reply.mode",
                "status": "proposed",
                "confidence": 0.69,
                "impact_score": 0.57,
                "evidence": {
                    "fallback_count": int(reply_modes.get("fallback") or 0),
                    "reply_modes": reply_modes,
                },
                "proposal": {
                    "summary": "Fallback replies are still showing up often enough to justify better graceful degradation.",
                    "actions": [
                        "add deterministic lightweight fallback copy for empty AI returns",
                        "capture provider-level error reasons into decision ledger",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.reply.fallback",
                        "fallback_mode": "deterministic_soft_landing",
                        "capture_provider_error_reason": True,
                    },
                },
                "window_days": window_days,
            }
        )

    episodic = int(promotion_tiers.get("episodic") or 0)
    semantic = int(promotion_tiers.get("semantic") or 0)
    if episodic >= 6 and semantic <= max(1, episodic // 4):
        proposals.append(
            {
                "proposal_key": f"memory-{window_days}-{episodic}-{semantic}",
                "proposal_type": "memory_promotion_tuning",
                "policy_key": "via.memory.promotion",
                "status": "proposed",
                "confidence": 0.76,
                "impact_score": 0.68,
                "evidence": {
                    "episodic": episodic,
                    "semantic": semantic,
                    "promotion_tiers": promotion_tiers,
                    "memory_retention": {
                        "tracked": int(memory_retention.get("tracked") or 0),
                        "decaying": int(memory_retention.get("decaying") or 0),
                        "confirmed_hits": int(memory_retention.get("confirmed_hits") or 0),
                    },
                },
                "proposal": {
                    "summary": "The system is storing plenty of episodes but not promoting enough stable traits into semantic memory.",
                    "actions": [
                        "promote repeated traits after two confirmed hits instead of waiting for three",
                        "track semantic retention hit rate in the next evaluation window",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.memory.semantic",
                        "semantic_confirmed_hit_threshold": 2,
                        "track_semantic_retention": True,
                    },
                },
                "window_days": window_days,
            }
        )

    if float(metrics.get("abuse_rate") or 0.0) > 0.08:
        proposals.append(
            {
                "proposal_key": f"risk-{window_days}-{int((metrics.get('abuse_rate') or 0)*1000)}",
                "proposal_type": "risk_review",
                "policy_key": "via.guard.policy",
                "status": "proposed",
                "confidence": 0.67,
                "impact_score": 0.61,
                "evidence": {
                    "abuse_rate": float(metrics.get("abuse_rate") or 0.0),
                    "triggers": control_summary.get("triggers") or {},
                },
                "proposal": {
                    "summary": "Guarded traffic is high enough to review sensitive-trigger phrasing and pre-guard education.",
                    "actions": [
                        "cluster top guarded prompts by trigger_type",
                        "add softer public-safe redirect copy for the most frequent guard buckets",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.risk.redirect",
                        "guard_copy_mode": "softer_public_redirect",
                        "cluster_guard_buckets": True,
                    },
                },
                "window_days": window_days,
            }
        )

    return proposals


async def run_via_offline_evaluator(window_days: int = 14, limit: int = 300) -> dict[str, Any]:
    reward_trace_sync = await asyncio.to_thread(_sync_affiliate_order_reward_traces, max(160, int(limit) * 2), max(window_days, 21))
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(40, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(40, int(limit)))
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(list_recent_via_reward_traces(limit=240), window_days)
    retrieval_evidence = _filter_recent_control_rows(list_recent_via_retrieval_evidence(limit=max(80, int(limit))), window_days)
    routing_stats = _filter_recent_control_rows(list_via_routing_provider_stats(limit=max(40, int(limit))), max(window_days, 21))
    memory_retention = _filter_recent_control_rows(list_via_memory_retention_stats(limit=max(40, int(limit))), max(window_days, 45))
    control_summary = _summarize_control_window(
        decisions,
        outcomes,
        reward_traces=reward_traces,
        retrieval_evidence=retrieval_evidence,
        routing_stats=routing_stats,
        memory_retention=memory_retention,
        window_days=window_days,
    )
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    policy_history = await asyncio.to_thread(list_via_policy_version_history, max(120, int(limit) * 2))
    staged_versions = [item for item in active_versions if str(item.get("status") or "").lower() == "staged"]
    live_versions = [item for item in active_versions if str(item.get("status") or "").lower() == "live"]
    shadow_rollout_readiness = _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        staged_versions,
        window_days=window_days,
    )
    live_rollout_health = _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        live_versions,
        window_days=window_days,
        version_history=policy_history,
    )
    rollout_alerts = await asyncio.to_thread(_persist_rollout_alerts, shadow_rollout_readiness, live_rollout_health)
    proposals = _build_policy_proposals(control_summary)
    stored = {"proposals": 0, "observations": 0, "feedback": 0}

    persisted: list[dict[str, Any]] = []
    for proposal in proposals:
        persisted_item = await asyncio.to_thread(
            upsert_via_policy_proposal,
            proposal_key=str(proposal.get("proposal_key") or ""),
            proposal_type=str(proposal.get("proposal_type") or ""),
            policy_key=str(proposal.get("policy_key") or ""),
            status=str(proposal.get("status") or "proposed"),
            confidence=float(proposal.get("confidence") or 0.0),
            impact_score=float(proposal.get("impact_score") or 0.0),
            evidence=proposal.get("evidence") or {},
            proposal=proposal.get("proposal") or {},
            window_days=int(proposal.get("window_days") or window_days),
            evaluator_version=VIA_EVALUATOR_VERSION,
        )
        persisted.append(persisted_item)
        stored["proposals"] += 1

    await asyncio.to_thread(
        record_market_observation,
        source_platform="via_control",
        subject_type="offline_evaluator",
        subject_key=f"window:{window_days}",
        observation_type="control_window_summary",
        summary=(
            f"Via offline evaluator reviewed {control_summary['metrics']['decision_count']} decisions "
            f"and {control_summary['metrics']['outcome_count']} outcomes across the last {window_days} days."
        ),
        metrics=control_summary.get("metrics") or {},
        evidence={
            "decision_types": control_summary.get("decision_types") or {},
            "reply_modes": control_summary.get("reply_modes") or {},
            "providers": control_summary.get("providers") or {},
            "shadow_targets": control_summary.get("shadow_targets") or {},
            "retrieval_evidence": control_summary.get("retrieval_evidence") or {},
            "routing_learner": control_summary.get("routing_learner") or {},
            "memory_retention": control_summary.get("memory_retention") or {},
            "shadow_rollout_readiness": shadow_rollout_readiness,
            "live_rollout_health": live_rollout_health,
            "rollout_alerts": rollout_alerts[:24],
            "reward_trace_sync": reward_trace_sync,
            "proposal_count": len(persisted),
        },
    )
    stored["observations"] += 1
    await asyncio.to_thread(
        record_feedback_signal,
        source_type="via_control",
        source_id=f"window:{window_days}",
        event_type="offline_evaluator_run",
        actor_role="via",
        payload={
            "metrics": control_summary.get("metrics") or {},
            "proposal_count": len(persisted),
            "evaluator_version": VIA_EVALUATOR_VERSION,
            "reward_trace_sync": reward_trace_sync,
        },
    )
    stored["feedback"] += 1

    return {
        "window_days": window_days,
        "metrics": control_summary.get("metrics") or {},
        "decision_types": control_summary.get("decision_types") or {},
        "triggers": control_summary.get("triggers") or {},
        "reply_modes": control_summary.get("reply_modes") or {},
        "providers": control_summary.get("providers") or {},
        "promotion_tiers": control_summary.get("promotion_tiers") or {},
        "shadow_targets": control_summary.get("shadow_targets") or {},
        "retrieval_evidence": control_summary.get("retrieval_evidence") or {},
        "routing_learner": control_summary.get("routing_learner") or {},
        "memory_retention": control_summary.get("memory_retention") or {},
        "shadow_rollout_readiness": shadow_rollout_readiness,
        "live_rollout_health": live_rollout_health,
        "rollout_alerts": rollout_alerts[: int(limit)],
        "recent_decisions": control_summary.get("recent_decisions") or [],
        "recent_outcomes": control_summary.get("recent_outcomes") or [],
        "proposals": persisted,
        "stored": stored,
        "reward_trace_sync": reward_trace_sync,
        "evaluator_version": VIA_EVALUATOR_VERSION,
    }


async def get_via_control_debug_snapshot(window_days: int = 14, limit: int = 24) -> dict[str, Any]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(24, int(limit) * 4))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(24, int(limit) * 4))
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(list_recent_via_reward_traces(limit=max(48, limit * 3)), window_days)
    retrieval_evidence = _filter_recent_control_rows(list_recent_via_retrieval_evidence(limit=max(48, limit * 4)), window_days)
    routing_stats = _filter_recent_control_rows(list_via_routing_provider_stats(limit=max(48, limit * 4)), max(window_days, 21))
    memory_retention = _filter_recent_control_rows(list_via_memory_retention_stats(limit=max(48, limit * 4)), max(window_days, 45))
    summary = _summarize_control_window(
        decisions,
        outcomes,
        reward_traces=reward_traces,
        retrieval_evidence=retrieval_evidence,
        routing_stats=routing_stats,
        memory_retention=memory_retention,
        window_days=window_days,
    )
    proposals = await asyncio.to_thread(list_via_policy_proposals, max(12, int(limit)))
    live_policies = await asyncio.to_thread(list_active_via_policy_versions)
    policy_history = await asyncio.to_thread(list_via_policy_version_history, max(24, int(limit) * 2))
    rollout_alerts = await asyncio.to_thread(list_via_rollout_alerts, max(24, int(limit) * 3))
    shadow_rollout_readiness = _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        [item for item in live_policies if str(item.get("status") or "").lower() == "staged"],
        window_days=window_days,
    )
    live_rollout_health = _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        [item for item in live_policies if str(item.get("status") or "").lower() == "live"],
        window_days=window_days,
        version_history=policy_history,
    )
    return {
        "window_days": window_days,
        "metrics": summary.get("metrics") or {},
        "decision_types": summary.get("decision_types") or {},
        "reply_modes": summary.get("reply_modes") or {},
        "providers": summary.get("providers") or {},
        "promotion_tiers": summary.get("promotion_tiers") or {},
        "triggers": summary.get("triggers") or {},
        "shadow_targets": summary.get("shadow_targets") or {},
        "retrieval_evidence": summary.get("retrieval_evidence") or {},
        "routing_learner": summary.get("routing_learner") or {},
        "memory_retention": summary.get("memory_retention") or {},
        "shadow_rollout_readiness": shadow_rollout_readiness,
        "live_rollout_health": live_rollout_health,
        "rollout_alerts": rollout_alerts[: max(int(limit), 12)],
        "recent_decisions": list(summary.get("recent_decisions") or [])[: int(limit)],
        "recent_outcomes": list(summary.get("recent_outcomes") or [])[: int(limit)],
        "proposals": proposals[: int(limit)],
        "live_policies": live_policies[: int(limit)],
        "policy_history": policy_history[: max(int(limit), 12)],
        "evaluator_version": VIA_EVALUATOR_VERSION,
    }


async def run_via_daily_learning() -> dict[str, Any]:
    if not VIA_ENABLE_DAILY_LEARNING:
        return {"ok": False, "skipped": True, "reason": "VIA_ENABLE_DAILY_LEARNING=0"}

    summary: dict[str, Any] = {
        "ok": True,
        "official_accounts": [],
        "comment_sources": [],
        "bh": {"fetched": 0},
        "internal": {},
        "stored": {
            "observations": 0,
            "memory": 0,
            "feedback": 0,
            "products": 0,
            "regions": 0,
        },
    }

    for account in _official_accounts():
        platform = account["platform"]
        handle = account["handle"]
        result = await scan_account(platform, handle, max_posts=VIA_LEARNING_MAX_POSTS)
        refs = await _store_account_snapshot(platform, handle, result)
        summary["official_accounts"].append(
            {
                "platform": platform,
                "handle": handle,
                "posts": len(result.get("posts") or []),
                "stats": result.get("stats") or {},
            }
        )
        for key, value in refs.items():
            summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    comment_platforms = [
        ("youtube", VIA_OFFICIAL_YOUTUBE_HANDLE),
        ("instagram", VIA_OFFICIAL_INSTAGRAM_HANDLE),
        ("tiktok", VIA_OFFICIAL_TIKTOK_HANDLE),
    ]
    for platform, handle in comment_platforms:
        if not handle:
            continue
        fetch_kwargs = {}
        if platform == "youtube":
            fetch_kwargs = {
                "max_videos": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_video": VIA_LEARNING_COMMENT_LIMIT,
                "channel_handle": handle,
            }
        elif platform == "instagram":
            fetch_kwargs = {
                "max_posts": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_post": VIA_LEARNING_COMMENT_LIMIT,
                "account_handle": handle,
            }
        elif platform == "tiktok":
            fetch_kwargs = {
                "max_videos": max(6, VIA_LEARNING_MAX_POSTS),
                "max_comments_per_video": VIA_LEARNING_COMMENT_LIMIT,
                "profile_handle": handle,
            }
        comments = await fetch_viltrox_comments(platform, **fetch_kwargs)
        refs = await _store_comment_snapshot(platform, handle, comments)
        summary["comment_sources"].append(
            {
                "platform": platform,
                "handle": handle,
                "comments": len(comments),
            }
        )
        for key, value in refs.items():
            summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    bh_products = await fetch_bh_viltrox_products(max_items=VIA_LEARNING_BH_MAX_ITEMS)
    summary["bh"]["fetched"] = len(bh_products)
    bh_refs = await _store_bh_snapshot(bh_products)
    for key, value in bh_refs.items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    internal = await _store_internal_system_snapshot(window_days=30)
    summary["internal"] = internal
    for key, value in internal.get("stored", {}).items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    evaluator = await run_via_offline_evaluator(window_days=14, limit=240)
    summary["evaluator"] = evaluator
    for key, value in evaluator.get("stored", {}).items():
        summary["stored"][key] = summary["stored"].get(key, 0) + int(value)

    return summary
