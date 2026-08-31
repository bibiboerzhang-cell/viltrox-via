"""Small result and ledger projections for the business-about provider lane."""
from __future__ import annotations

from typing import Any, Callable


def about_profile_from_result(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items") or []
    profile = items[0] if items and isinstance(items[0], dict) else {}
    videos = result.get("videos") if isinstance(result.get("videos"), list) else []
    first_video = videos[0] if videos and isinstance(videos[0], dict) else {}
    raw_links = (
        first_video.get("channelDescriptionLinks")
        or ((first_video.get("aboutChannelInfo") or {}).get("channelDescriptionLinks"))
        or []
    )
    link_lines: list[str] = []
    for link in raw_links if isinstance(raw_links, list) else []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if url and "://" not in url and "." in url and " " not in url:
            url = "https://" + url
        if url:
            link_lines.append(
                f"{str(link.get('text') or '').strip()}: {url}".lstrip(": ").strip()
            )
    if link_lines:
        profile = {**profile, "about": "\n".join(link_lines)}
    return profile


def record_about_scrape_cost(
    *,
    kol_pool_id: int,
    staff: dict[str, Any] | None,
    apify_run_ref: str,
    platform: str,
    budget_scope: str,
    record_cost: Callable[..., Any],
    logger: Any,
) -> None:
    try:
        record_cost(
            scope=budget_scope,
            ai_provider="apify",
            model_name="about_scrape",
            cost_usd=0.0,
            kol_pool_id=int(kol_pool_id),
            staff_id=int((staff or {}).get("staff_id") or (staff or {}).get("user_id") or 0) or None,
            metadata={
                "operation": "business_email_about_scrape",
                "apify_run_ref": apify_run_ref,
                "platform": platform,
            },
        )
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
