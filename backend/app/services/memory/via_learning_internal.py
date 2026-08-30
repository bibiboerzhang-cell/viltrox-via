"""Internal and provider snapshot storage for Via learning."""
from __future__ import annotations

from app.services.memory.via_learning_common import *


def __snapshot_customer_profile(
    subject_key: str,
    user_id: int,
    creator_handle: str,
) -> dict[str, Any]:
    return {
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
    }


def __snapshot_submission_identity(row: dict[str, Any]) -> tuple[int, str, str, str]:
    user_id = int(row.get("user_id") or 0)
    creator_handle = str(row.get("extracted_handle") or "").strip()
    platform = str(row.get("platform") or "unknown").strip() or "unknown"
    subject_key = creator_handle or (f"user:{user_id}" if user_id else f"submission:{row.get('id')}")
    return user_id, creator_handle, platform, subject_key


def __snapshot_apply_submission_metrics(
    profile: dict[str, Any],
    row: dict[str, Any],
    platform: str,
) -> None:
    profile["submission_count"] += 1
    profile["total_views"] += int(row.get("views") or 0)
    profile["total_likes"] += int(row.get("likes") or 0)
    profile["total_comments"] += int(row.get("comments") or 0)
    profile["total_shares"] += int(row.get("shares") or 0)
    profile["platforms"].add(platform)
    if row.get("created_at"):
        profile["latest_at"] = max(str(profile.get("latest_at") or ""), str(row.get("created_at") or ""))


def __snapshot_product_cluster(product_label: str) -> dict[str, Any]:
    return {
        "label": product_label,
        "product_key": _slugify(product_label),
        "submission_count": 0,
        "total_views": 0,
        "total_likes": 0,
        "platforms": set(),
        "alias_terms": set([product_label]),
    }


def __snapshot_apply_product_metrics(
    product: dict[str, Any],
    row: dict[str, Any],
    platform: str,
) -> None:
    product["submission_count"] += 1
    product["total_views"] += int(row.get("views") or 0)
    product["total_likes"] += int(row.get("likes") or 0)
    product["platforms"].add(platform)
    product["alias_terms"].add(str(row.get("product_series") or "").strip())
    product["alias_terms"].add(str(row.get("product_label") or "").strip())


def __snapshot_accumulate_submission(
    customer_profiles: dict[str, dict[str, Any]],
    product_clusters: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> None:
    user_id, creator_handle, platform, subject_key = __snapshot_submission_identity(row)
    profile = customer_profiles.setdefault(
        subject_key,
        __snapshot_customer_profile(subject_key, user_id, creator_handle),
    )
    __snapshot_apply_submission_metrics(profile, row, platform)
    product_label = str(row.get("product_label") or row.get("product_series") or "").strip()
    if product_label:
        profile["products"].add(product_label)
        product = product_clusters.setdefault(product_label, __snapshot_product_cluster(product_label))
        __snapshot_apply_product_metrics(product, row, platform)


def __snapshot_verified_identity(row: dict[str, Any]) -> tuple[int, str, str]:
    user_id = int(row.get("user_id") or 0)
    creator_handle = str(row.get("handle") or "").strip()
    subject_key = creator_handle or (f"user:{user_id}" if user_id else f"verified:{row.get('id')}")
    return user_id, creator_handle, subject_key


def __snapshot_accumulate_verified_account(
    customer_profiles: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> None:
    user_id, creator_handle, subject_key = __snapshot_verified_identity(row)
    profile = customer_profiles.setdefault(
        subject_key,
        __snapshot_customer_profile(subject_key, user_id, creator_handle),
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


def __snapshot_accumulate_address(
    region_clusters: dict[tuple[str, str, str], dict[str, Any]],
    row: dict[str, Any],
) -> None:
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


def __snapshot_accumulate_ingest_event(
    ingest_clusters: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> None:
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


def __snapshot_nonempty_sorted(values: set[Any]) -> list[Any]:
    return sorted(value for value in values if value)


def __snapshot_customer_items(customer_profiles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            **item,
            "platforms": __snapshot_nonempty_sorted(item["platforms"]),
            "products": __snapshot_nonempty_sorted(item["products"]),
        }
        for item in customer_profiles.values()
    ]
    items.sort(key=lambda item: (-item["submission_count"], -item["total_views"], item["subject_key"]))
    return items


def __snapshot_product_items(product_clusters: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            **item,
            "platforms": __snapshot_nonempty_sorted(item["platforms"]),
            "alias_terms": __snapshot_nonempty_sorted(item["alias_terms"]),
        }
        for item in product_clusters.values()
    ]
    items.sort(key=lambda item: (-item["submission_count"], -item["total_views"], item["label"]))
    return items


def __snapshot_region_items(
    region_clusters: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    items = [
        {
            **item,
            "users": sorted(item["users"]),
            "user_count": len(item["users"]),
        }
        for item in region_clusters.values()
    ]
    items.sort(
        key=lambda item: (
            -item["address_count"],
            -item["user_count"],
            item["country"],
            item["state"],
            item["city"],
        )
    )
    return items


def __snapshot_ingest_items(ingest_clusters: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            **item,
            "event_types": __snapshot_nonempty_sorted(item["event_types"]),
            "entity_types": __snapshot_nonempty_sorted(item["entity_types"]),
            "region_codes": __snapshot_nonempty_sorted(item["region_codes"]),
            "creator_handles": __snapshot_nonempty_sorted(item["creator_handles"]),
        }
        for item in ingest_clusters.values()
    ]
    items.sort(key=lambda item: (-item["event_count"], item["platform"]))
    return items


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
        __snapshot_accumulate_submission(customer_profiles, product_clusters, row)

    for row in verified_social_accounts:
        __snapshot_accumulate_verified_account(customer_profiles, row)

    for row in addresses:
        __snapshot_accumulate_address(region_clusters, row)

    for row in ingest_events:
        __snapshot_accumulate_ingest_event(ingest_clusters, row)

    customer_items = __snapshot_customer_items(customer_profiles)
    product_items = __snapshot_product_items(product_clusters)
    region_items = __snapshot_region_items(region_clusters)
    ingest_items = __snapshot_ingest_items(ingest_clusters)

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


__all__ = [name for name in globals() if not name.startswith("__")]
