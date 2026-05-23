"""Planning and gated execution helpers for qualified KOL Apify batch refreshes.

Execution is explicitly opt-in. P1.X.B first locks the chunking, actor input,
dataset-to-KOL mapping, and result contracts so later execution cannot
accidentally fall back to the old full-pool refresh path.
"""
from __future__ import annotations

import asyncio
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.vkpi import refresh_tier


SUPPORTED_BATCH_PLATFORMS = {"instagram", "youtube", "facebook", "reddit", "x", "tiktok"}
DEFAULT_CHUNK_SIZES = {
    "instagram": 50,
    "youtube": 50,
    "facebook": 50,
    "reddit": 50,
    "x": 50,
    "tiktok": 25,
}
DEFAULT_MAX_CONCURRENT_RUNS = 2
HARD_MAX_CONCURRENT_RUNS = 3
DEFAULT_RUN_TIMEOUT_SECONDS = 600
DEFAULT_RETRY_CHUNK_SIZE = 5

DEFAULT_ACTORS = {
    "instagram": "apify~instagram-scraper",
    "youtube": "streamers~youtube-scraper",
    "facebook": "apify~facebook-posts-scraper",
    "reddit": "trudax~reddit-scraper-lite",
    "x": "apidojo~tweet-scraper",
    "tiktok": "clockworks~tiktok-scraper",
}

ACTOR_ENV_KEYS = {
    "instagram": "APIFY_INSTAGRAM_POSTS_ACTOR_ID",
    "youtube": "APIFY_YOUTUBE_ACTOR_ID",
    "facebook": "APIFY_FACEBOOK_POSTS_ACTOR_ID",
    "reddit": "APIFY_REDDIT_ACTOR_ID",
    "x": "APIFY_X_ACTOR_ID",
    "tiktok": "APIFY_TIKTOK_ACTOR_ID",
}


class ApifyBatchExecutionBlocked(RuntimeError):
    """Raised when code attempts provider execution without the explicit gate."""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_platform(value: Any) -> str:
    raw = _text(value).lower()
    if raw in {"twitter", "twitter/x"}:
        return "x"
    return raw


def max_concurrent_runs(value: Any = None) -> int:
    configured = _int(value, DEFAULT_MAX_CONCURRENT_RUNS)
    return max(1, min(HARD_MAX_CONCURRENT_RUNS, configured))


def chunk_size_for_platform(platform: str, overrides: dict[str, int] | None = None) -> int:
    platform_key = normalize_platform(platform)
    configured = (overrides or {}).get(platform_key, DEFAULT_CHUNK_SIZES.get(platform_key, 25))
    return max(1, min(100, _int(configured, 25)))


def parse_chunk_overrides(value: Any) -> dict[str, int]:
    overrides: dict[str, int] = {}
    if isinstance(value, dict):
        source = value.items()
    else:
        source = []
        for part in str(value or "").split(","):
            if "=" not in part:
                continue
            key, raw_size = part.split("=", 1)
            source.append((key, raw_size))
    for raw_platform, raw_size in source:
        platform = normalize_platform(raw_platform)
        if platform in SUPPORTED_BATCH_PLATFORMS:
            overrides[platform] = chunk_size_for_platform(
                platform,
                {platform: _int(raw_size, DEFAULT_CHUNK_SIZES.get(platform, 25))},
            )
    return overrides


def actor_id_for_platform(platform: str) -> str:
    platform_key = normalize_platform(platform)
    env_key = ACTOR_ENV_KEYS.get(platform_key, "")
    configured = os.environ.get(env_key, "").strip() if env_key else ""
    return (configured or DEFAULT_ACTORS.get(platform_key) or "").replace("/", "~")


def compute_stale_before(raw_value: str = "", stale_days: int = 0, *, now: datetime | None = None) -> str:
    raw = _text(raw_value)
    if raw:
        return raw
    days = max(0, _int(stale_days, 0))
    if days <= 0:
        return ""
    anchor = now or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return (anchor.astimezone(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_handle(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if raw.startswith("@"):
        raw = raw[1:]
    return raw.strip().strip("/")


def _canonical_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urllib.parse.urlparse(raw)
        path = re.sub(r"/+", "/", parsed.path).rstrip("/")
        return urllib.parse.urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))
    return raw.lower().strip("/")


def _url_for_target(platform: str, handle: str, profile_url: str) -> str:
    if profile_url.startswith("http://") or profile_url.startswith("https://"):
        return profile_url
    handle = _strip_handle(handle)
    if not handle:
        return ""
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "youtube":
        if handle.startswith("UC"):
            return f"https://www.youtube.com/channel/{handle}"
        return f"https://www.youtube.com/@{handle.lstrip('@')}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    if platform == "x":
        return f"https://x.com/{handle}"
    if platform == "reddit":
        return f"https://www.reddit.com/user/{handle}"
    return profile_url


def build_refresh_target(row: dict[str, Any]) -> dict[str, Any]:
    platform = normalize_platform(row.get("platform"))
    kol_pool_id = _int(row.get("kol_pool_id") or row.get("id"))
    handle = _strip_handle(row.get("handle"))
    profile_url = _text(row.get("profile_url"))
    url = _url_for_target(platform, handle, profile_url)
    if platform not in SUPPORTED_BATCH_PLATFORMS:
        return {"ok": False, "reason": "unsupported_platform", "platform": platform, "kol_pool_id": kol_pool_id}
    if not kol_pool_id:
        return {"ok": False, "reason": "missing_kol_pool_id", "platform": platform}
    if not url:
        return {"ok": False, "reason": "missing_profile_url", "platform": platform, "kol_pool_id": kol_pool_id}
    return {
        "ok": True,
        "kol_pool_id": kol_pool_id,
        "platform": platform,
        "handle": handle,
        "profile_url": url,
        "refresh_tier": _text(row.get("refresh_tier")),
        "refresh_reason": _text(row.get("refresh_reason")),
    }


def build_actor_input(platform: str, targets: list[dict[str, Any]], *, max_posts: int = 1) -> dict[str, Any]:
    platform_key = normalize_platform(platform)
    urls = [_text(target.get("profile_url")) for target in targets if _text(target.get("profile_url"))]
    handles = [_strip_handle(target.get("handle")) for target in targets if _strip_handle(target.get("handle"))]
    limit = max(1, min(50, _int(max_posts, 1)))
    if platform_key == "instagram":
        return {"directUrls": urls, "resultsType": "posts", "resultsLimit": limit, "addParentData": False}
    if platform_key == "tiktok":
        return {
            "profiles": handles,
            "resultsPerPage": limit,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        }
    if platform_key == "youtube":
        return {"startUrls": [{"url": url} for url in urls], "maxResults": limit, "maxResultsShorts": 0, "maxResultStreams": 0}
    if platform_key == "facebook":
        return {
            "startUrls": [{"url": url} for url in urls],
            "resultsLimit": limit,
            "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
    if platform_key in {"reddit", "x"}:
        return {"startUrls": [{"url": url} for url in urls], "maxItems": limit}
    return {"directUrls": urls, "maxResults": limit}


def plan_apify_batches(
    rows: list[dict[str, Any]],
    *,
    max_posts: int = 1,
    chunk_overrides: dict[str, int] | None = None,
    max_concurrent: int | None = None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    for row in rows:
        target = build_refresh_target(row)
        if not target.get("ok"):
            skipped.append(target)
            continue
        grouped.setdefault(str(target["platform"]), []).append(target)

    batches: list[dict[str, Any]] = []
    for platform in sorted(grouped):
        targets = grouped[platform]
        chunk_size = chunk_size_for_platform(platform, chunk_overrides)
        for index in range(0, len(targets), chunk_size):
            chunk = targets[index : index + chunk_size]
            chunk_index = index // chunk_size + 1
            batches.append(
                {
                    "batch_key": f"{platform}-{chunk_index}",
                    "platform": platform,
                    "chunk_index": chunk_index,
                    "chunk_size": chunk_size,
                    "target_count": len(chunk),
                    "kol_pool_ids": [int(target["kol_pool_id"]) for target in chunk],
                    "urls": [str(target["profile_url"]) for target in chunk],
                    "actor_id": actor_id_for_platform(platform),
                    "run_input": build_actor_input(platform, chunk, max_posts=max_posts),
                    "targets": chunk,
                }
            )

    return {
        "strategy": "apify_batch_first",
        "max_concurrent_runs": max_concurrent_runs(max_concurrent),
        "total_targets": sum(len(items) for items in grouped.values()),
        "batch_count": len(batches),
        "batches": batches,
        "skipped": skipped,
        "platforms": {platform: len(items) for platform, items in sorted(grouped.items())},
    }


def qualified_apify_batch_plan(
    *,
    limit: int = 50,
    offset: int = 0,
    stale_before: str = "",
    stale_days: int = 0,
    platforms: set[str] | None = None,
    tiers: set[str] | None = None,
    max_posts: int = 1,
    max_concurrent: int | None = None,
    chunk_overrides: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Plan Apify batch runs from the qualified tier selector without providers."""
    safe_limit = max(1, min(1200, _int(limit, 50)))
    safe_offset = max(0, min(5000, _int(offset, 0)))
    safe_max_posts = max(1, min(3, _int(max_posts, 1)))
    selected_tiers = refresh_tier._tier_filter(tiers)
    selected_platforms = {normalize_platform(item) for item in (platforms or set()) if _text(item)}
    cutoff = compute_stale_before(stale_before, stale_days)
    source_counts = refresh_tier.qualified_source_counts(tiers=selected_tiers, platforms=selected_platforms)
    rows = refresh_tier.qualified_refresh_rows(
        limit=safe_limit,
        offset=safe_offset,
        stale_before=cutoff,
        platforms=selected_platforms,
        tiers=selected_tiers,
    )
    plan = plan_apify_batches(
        rows,
        max_posts=safe_max_posts,
        chunk_overrides=chunk_overrides,
        max_concurrent=max_concurrent,
    )
    return {
        "mode": "plan_only",
        "selector": "qualified",
        "execution_enabled": False,
        "reason": "apify_batch_execution_not_enabled",
        "tiers": sorted(selected_tiers),
        "limit": safe_limit,
        "offset": safe_offset,
        "stale_before": cutoff,
        "max_posts": safe_max_posts,
        **source_counts,
        **plan,
    }


def _candidate_keys(value: Any) -> set[str]:
    raw = _text(value)
    if not raw:
        return set()
    keys = {raw.lower().lstrip("@")}
    canonical = _canonical_url(raw)
    if canonical:
        keys.add(canonical)
        parsed = urllib.parse.urlparse(raw)
        path = parsed.path.strip("/")
        if path:
            first = path.split("/", 1)[0].lstrip("@").lower()
            if first:
                keys.add(first)
    return {key for key in keys if key}


def _item_candidate_values(item: dict[str, Any]) -> list[Any]:
    author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    channel = item.get("channel") if isinstance(item.get("channel"), dict) else {}
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return [
        item.get("inputUrl"),
        item.get("url"),
        item.get("profileUrl"),
        item.get("profile_url"),
        item.get("channelUrl"),
        item.get("channelName"),
        item.get("username"),
        item.get("userName"),
        item.get("handle"),
        item.get("name"),
        item.get("screenName"),
        author.get("name"),
        author.get("username"),
        author.get("profileUrl"),
        channel.get("url"),
        channel.get("name"),
        owner.get("username"),
    ]


def build_target_lookup(targets: list[dict[str, Any]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for target in targets:
        kol_pool_id = _int(target.get("kol_pool_id"))
        if not kol_pool_id:
            continue
        for value in (target.get("handle"), target.get("profile_url")):
            for key in _candidate_keys(value):
                lookup.setdefault(key, kol_pool_id)
    return lookup


def map_dataset_items_to_targets(items: list[dict[str, Any]], targets: list[dict[str, Any]]) -> dict[str, Any]:
    lookup = build_target_lookup(targets)
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for item in items:
        match_key = ""
        kol_pool_id = 0
        for value in _item_candidate_values(item):
            for key in _candidate_keys(value):
                if key in lookup:
                    match_key = key
                    kol_pool_id = lookup[key]
                    break
            if kol_pool_id:
                break
        if kol_pool_id:
            matched.append({"kol_pool_id": kol_pool_id, "match_key": match_key, "item": item})
        else:
            unmatched.append(item)
    return {"matched": matched, "unmatched": unmatched, "matched_count": len(matched), "unmatched_count": len(unmatched)}


def summarize_batch_execution(plan: dict[str, Any], results: list[dict[str, Any]], *, executed: bool) -> dict[str, Any]:
    batches = [batch for batch in (plan.get("batches") or []) if isinstance(batch, dict)]
    targets_by_batch = {str(batch.get("batch_key") or ""): [target for target in (batch.get("targets") or []) if isinstance(target, dict)] for batch in batches}
    results_by_batch = {str(result.get("batch_key") or ""): result for result in results if isinstance(result, dict)}
    kol_statuses: list[dict[str, Any]] = []
    retry_kol_pool_ids: list[int] = []
    matched_items = 0
    unmatched_items = 0

    for batch in batches:
        batch_key = str(batch.get("batch_key") or "")
        platform = str(batch.get("platform") or "")
        result = results_by_batch.get(batch_key, {})
        provider_status = str(result.get("provider_status") or ("pending" if not executed else "unknown"))
        sync_status = str(result.get("sync_status") or ("pending" if not executed else "unknown"))
        mapped = result.get("mapped") if isinstance(result.get("mapped"), dict) else {}
        matched_ids = {int(item.get("kol_pool_id") or 0) for item in (mapped.get("matched") or []) if int(item.get("kol_pool_id") or 0)}
        matched_items += _int(mapped.get("matched_count"))
        unmatched_items += _int(mapped.get("unmatched_count"))

        for target in targets_by_batch.get(batch_key, []):
            kol_pool_id = _int(target.get("kol_pool_id"))
            if not kol_pool_id:
                continue
            if not executed:
                status = "planned"
                retry = False
                reason = "not_executed"
            elif provider_status == "ok" and kol_pool_id in matched_ids:
                status = "matched"
                retry = False
                reason = sync_status or "synced"
            elif provider_status == "ok":
                status = "unmatched"
                retry = True
                reason = "dataset_item_not_mapped"
            elif provider_status == "not_configured":
                status = "not_configured"
                retry = True
                reason = "apify_not_configured"
            else:
                status = "error"
                retry = True
                reason = str(result.get("error") or sync_status or provider_status or "batch_error")[:500]
            if retry:
                retry_kol_pool_ids.append(kol_pool_id)
            kol_statuses.append(
                {
                    "kol_pool_id": kol_pool_id,
                    "batch_key": batch_key,
                    "platform": platform,
                    "status": status,
                    "provider_status": provider_status,
                    "sync_status": sync_status,
                    "reason": reason,
                }
            )

    return {
        "executed": bool(executed),
        "strategy": plan.get("strategy") or "apify_batch_first",
        "batch_count": len(batches),
        "target_count": sum(len(targets) for targets in targets_by_batch.values()),
        "result_count": len(results),
        "matched_items": matched_items,
        "unmatched_items": unmatched_items,
        "failed_batches": sum(1 for item in results if str(item.get("provider_status") or "") == "error"),
        "not_configured_batches": sum(1 for item in results if str(item.get("provider_status") or "") == "not_configured"),
        "no_result_batches": sum(1 for item in results if str(item.get("sync_status") or "") == "no_results"),
        "retry_kol_pool_ids": sorted(set(retry_kol_pool_ids)),
        "retry_count": len(set(retry_kol_pool_ids)),
        "kol_statuses": kol_statuses,
    }


def build_retry_plan(
    plan: dict[str, Any],
    summary: dict[str, Any],
    *,
    max_targets: int = 100,
    retry_chunk_size: int = DEFAULT_RETRY_CHUNK_SIZE,
) -> dict[str, Any]:
    """Build a smaller retry batch plan from a normalized execution summary."""
    target_by_id: dict[int, dict[str, Any]] = {}
    for batch in (plan.get("batches") or []):
        if not isinstance(batch, dict):
            continue
        for target in batch.get("targets") or []:
            if isinstance(target, dict) and _int(target.get("kol_pool_id")):
                target_by_id[_int(target.get("kol_pool_id"))] = target

    retry_rows: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    retry_ids: set[int] = set()
    for status_row in summary.get("kol_statuses") or []:
        if not isinstance(status_row, dict):
            continue
        status = str(status_row.get("status") or "")
        kol_pool_id = _int(status_row.get("kol_pool_id"))
        if status in {"planned", "matched"} or not kol_pool_id:
            continue
        target = target_by_id.get(kol_pool_id)
        if not target:
            blocked.append({**status_row, "blocked_reason": "target_not_found_in_plan"})
            continue
        if status == "not_configured":
            blocked.append({**status_row, "blocked_reason": "provider_not_configured"})
            continue
        if kol_pool_id in retry_ids:
            continue
        retry_ids.add(kol_pool_id)
        retry_rows.append(
            {
                **target,
                "id": kol_pool_id,
                "kol_pool_id": kol_pool_id,
                "refresh_reason": f"retry:{status}:{str(status_row.get('reason') or '')[:120]}",
                "previous_batch_key": status_row.get("batch_key"),
                "previous_status": status,
            }
        )
        if len(retry_rows) >= max(1, min(500, _int(max_targets, 100))):
            break

    retry_chunk = max(1, min(25, _int(retry_chunk_size, DEFAULT_RETRY_CHUNK_SIZE)))
    chunk_overrides = {platform: retry_chunk for platform in SUPPORTED_BATCH_PLATFORMS}
    retry_plan = plan_apify_batches(
        retry_rows,
        max_posts=max(1, min(3, _int(plan.get("max_posts"), 1))),
        chunk_overrides=chunk_overrides,
        max_concurrent=1,
    )
    return {
        "mode": "retry_plan_only",
        "execution_enabled": False,
        "source_strategy": plan.get("strategy") or "apify_batch_first",
        "retry_target_count": len(retry_rows),
        "blocked_count": len(blocked),
        "blocked": blocked[:50],
        **retry_plan,
    }


def run_apify_batch(
    batch: dict[str, Any],
    *,
    api_token: str | None = None,
    timeout_secs: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute one planned Apify batch and map dataset items to targets."""
    token = (api_token or os.environ.get("APIFY_TOKEN") or "").strip()
    if not token:
        return {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "provider_status": "not_configured",
            "sync_status": "not_configured",
            "items": [],
            "mapped": {"matched": [], "unmatched": [], "matched_count": 0, "unmatched_count": 0},
            "message": "APIFY_TOKEN is not configured",
        }
    try:
        from apify_client import ApifyClient  # type: ignore
    except ImportError:
        return {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "provider_status": "error",
            "sync_status": "error",
            "items": [],
            "mapped": {"matched": [], "unmatched": [], "matched_count": 0, "unmatched_count": 0},
            "error": "apify-client not installed",
        }

    actor_id = _text(batch.get("actor_id"))
    run_input = batch.get("run_input") if isinstance(batch.get("run_input"), dict) else {}
    targets = [target for target in (batch.get("targets") or []) if isinstance(target, dict)]
    try:
        client = ApifyClient(token)
        run = client.actor(actor_id).call(
            run_input=run_input,
            timeout_secs=max(30, min(1800, _int(timeout_secs, DEFAULT_RUN_TIMEOUT_SECONDS))),
            wait_secs=max(30, min(1800, _int(timeout_secs, DEFAULT_RUN_TIMEOUT_SECONDS))),
        )
        status = str((run or {}).get("status") or "").upper()
        if not run or status != "SUCCEEDED":
            return {
                "batch_key": batch.get("batch_key"),
                "platform": batch.get("platform"),
                "actor_id": actor_id,
                "provider_status": "error",
                "sync_status": "error",
                "items": [],
                "mapped": {"matched": [], "unmatched": [], "matched_count": 0, "unmatched_count": 0},
                "error": f"Apify actor did not finish: {status or 'unknown'}",
                "run": {"id": (run or {}).get("id"), "status": status},
            }
        dataset_id = run.get("defaultDatasetId")
        items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
        dict_items = [item for item in items if isinstance(item, dict)]
        mapped = map_dataset_items_to_targets(dict_items, targets)
        sync_status = "synced" if mapped.get("matched_count") else "no_results"
        return {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "actor_id": actor_id,
            "provider_status": "ok",
            "sync_status": sync_status,
            "item_count": len(dict_items),
            "items": dict_items,
            "mapped": mapped,
            "run": {
                "id": run.get("id"),
                "status": status,
                "defaultDatasetId": dataset_id,
            },
        }
    except Exception as exc:  # pragma: no cover - live provider path
        return {
            "batch_key": batch.get("batch_key"),
            "platform": batch.get("platform"),
            "actor_id": actor_id,
            "provider_status": "error",
            "sync_status": "error",
            "items": [],
            "mapped": {"matched": [], "unmatched": [], "matched_count": 0, "unmatched_count": 0},
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        }


async def execute_apify_batch_plan(
    plan: dict[str, Any],
    *,
    allow_provider_calls: bool = False,
    api_token: str | None = None,
    timeout_secs: int = DEFAULT_RUN_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a plan with bounded concurrency. Provider calls are opt-in."""
    batches = [batch for batch in (plan.get("batches") or []) if isinstance(batch, dict)]
    if not allow_provider_calls:
        summary = summarize_batch_execution(plan, [], executed=False)
        retry_plan = build_retry_plan(plan, summary)
        return {
            "executed": False,
            "reason": "provider_calls_not_allowed",
            "batch_count": len(batches),
            "max_concurrent_runs": max_concurrent_runs(plan.get("max_concurrent_runs")),
            "summary": summary,
            "retry_plan": retry_plan,
            "results": [],
        }
    sem = asyncio.Semaphore(max_concurrent_runs(plan.get("max_concurrent_runs")))

    async def _run(batch: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await asyncio.to_thread(run_apify_batch, batch, api_token=api_token, timeout_secs=timeout_secs)

    raw_results = await asyncio.gather(*[_run(batch) for batch in batches], return_exceptions=True)
    results: list[dict[str, Any]] = []
    for index, result in enumerate(raw_results):
        if isinstance(result, Exception):
            batch = batches[index] if index < len(batches) else {}
            results.append(
                {
                    "batch_key": batch.get("batch_key"),
                    "platform": batch.get("platform"),
                    "provider_status": "error",
                    "sync_status": "error",
                    "error": f"{type(result).__name__}: {str(result)[:500]}",
                }
            )
        else:
            results.append(result)
    summary = summarize_batch_execution(plan, results, executed=True)
    retry_plan = build_retry_plan(plan, summary)
    return {
        "executed": True,
        "batch_count": len(batches),
        "max_concurrent_runs": max_concurrent_runs(plan.get("max_concurrent_runs")),
        "synced_batches": sum(1 for item in results if str(item.get("sync_status") or "") == "synced"),
        "failed_batches": sum(1 for item in results if str(item.get("provider_status") or "") == "error"),
        "not_configured_batches": sum(1 for item in results if str(item.get("provider_status") or "") == "not_configured"),
        "matched_items": sum(_int((item.get("mapped") or {}).get("matched_count")) for item in results if isinstance(item.get("mapped"), dict)),
        "summary": summary,
        "retry_plan": retry_plan,
        "results": results,
    }
