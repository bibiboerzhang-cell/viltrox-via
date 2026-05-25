"""KOL profile payload assembly helpers."""
from __future__ import annotations

from typing import Any

from app.domains.kol.payload_utils import _int


def build_activity_timeline(
    *,
    claim_history: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    content_posts: list[dict[str, Any]],
    sales: list[dict[str, Any]],
    kpi_ledger: list[dict[str, Any]],
    recommendation_outcomes: list[dict[str, Any]],
    link_clicks: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    activity_timeline: list[dict[str, Any]] = []
    for item in claim_history[:10]:
        activity_timeline.append({"type": "claim", "at": item.get("claimed_at"), "label": item.get("status"), "data": item})
    for item in projects[:20]:
        activity_timeline.append({"type": "project", "at": item.get("updated_at") or item.get("created_at"), "label": item.get("project_name"), "data": item})
    for item in messages[:20]:
        activity_timeline.append({"type": "message", "at": item.get("captured_at") or item.get("created_at"), "label": item.get("snippet") or item.get("body"), "data": item})
    for item in content_posts[:20]:
        activity_timeline.append({"type": "content", "at": item.get("published_at") or item.get("created_at"), "label": item.get("title") or item.get("post_url"), "data": item})
    for item in sales[:20]:
        activity_timeline.append({"type": "sale", "at": item.get("occurred_at") or item.get("created_at"), "label": item.get("source_ref"), "data": item})
    for item in kpi_ledger[:20]:
        activity_timeline.append({"type": "kpi", "at": item.get("created_at") or item.get("ledger_date"), "label": item.get("metric_key"), "data": item})
    for item in recommendation_outcomes[:20]:
        activity_timeline.append({"type": "recommendation_outcome", "at": item.get("recommended_at") or item.get("outcome_finalized_at"), "label": item.get("recommendation_uid"), "data": item})
    for item in link_clicks[:20]:
        activity_timeline.append({"type": "link_click", "at": item.get("clicked_at"), "label": item.get("event_id"), "data": item})
    for item in audit_events[:20]:
        activity_timeline.append({"type": "audit", "at": item.get("created_at"), "label": item.get("action_type"), "data": item})
    activity_timeline.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return activity_timeline[:100]


def build_link_summary(
    links: list[dict[str, Any]],
    link_clicks: list[dict[str, Any]],
    link_orders: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "link_count": len(links),
        "click_count": sum(_int(item.get("click_count")) for item in links),
        "valid_click_count": sum(_int(item.get("valid_click_count")) for item in links),
        "bot_click_count": sum(_int(item.get("bot_click_count")) for item in links),
        "unique_click_count": sum(1 for item in link_clicks if _int(item.get("is_unique"))),
        "order_count": len({str(item.get("source_ref") or item.get("shopify_order_id") or item.get("attribution_id")) for item in link_orders}),
        "revenue_cents": sum(_int(item.get("revenue_cents")) for item in link_orders),
    }


def build_contacts(kol: dict[str, Any], contact_links: Any, contact_raw: Any) -> dict[str, Any]:
    return {
        "email": kol.get("contact_email") or "",
        "phone": kol.get("contact_phone") or "",
        "profile_url": kol.get("profile_url") or kol.get("channel_url") or "",
        "links": contact_links if isinstance(contact_links, list) else [],
        "raw": contact_raw if isinstance(contact_raw, dict) else {},
    }


def build_profile_summary(
    *,
    snapshot: dict[str, Any],
    kol: dict[str, Any],
    posts: list[dict[str, Any]],
    report: dict[str, Any],
    raw_report: dict[str, Any],
    revenue_cents: int,
    cost_cents: int,
    show_financials: bool,
    projects: list[dict[str, Any]],
    links: list[dict[str, Any]],
    link_clicks: list[dict[str, Any]],
    link_orders: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    content_posts: list[dict[str, Any]],
    claim_history: list[dict[str, Any]],
    kpi_ledger: list[dict[str, Any]],
    kpi_summary: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    recommendation_outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "follower_count": _int(snapshot.get("follower_count"), _int(kol.get("follower_count"))),
        "content_count": _int(snapshot.get("content_count"), len(posts)),
        "total_views": _int(snapshot.get("total_views")),
        "total_likes": _int(snapshot.get("total_likes")),
        "avg_views": _int(snapshot.get("avg_views"), _int(kol.get("avg_views"))),
        "engagement_rate": snapshot.get("engagement_rate") if snapshot else None,
        "account_score": _int(report.get("account_score")) if report else 0,
        "audience_fit": _int(report.get("audience_fit")) if report else 0,
        "product_fit": _int(report.get("product_fit")) if report else 0,
        "risk_level": report.get("risk_level") if report else "",
        "user_persona": raw_report.get("user_persona") or raw_report.get("persona") or "",
        "persona_reason": raw_report.get("persona_reason") or "",
        "recommended_action": report.get("recommended_action") if report else "",
        "revenue_cents": revenue_cents,
        "cost_cents": cost_cents if show_financials else None,
        "roi": round(revenue_cents / cost_cents, 4) if show_financials and cost_cents else None,
        "financials_hidden": not show_financials,
        "project_count": len(projects),
        "link_count": len(links),
        "link_click_count": len(link_clicks),
        "link_order_count": len(link_orders),
        "message_count": len(messages),
        "published_content_count": len(content_posts),
        "claim_count": len(claim_history),
        "kpi_source_count": len(kpi_ledger),
        "kpi_metric_count": len(kpi_summary),
        "recommendation_count": len(recommendations),
        "recommendation_outcome_count": len(recommendation_outcomes),
    }
