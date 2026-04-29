"""
services/kol/metrics.py — KOL dashboard metric definitions.
"""
from __future__ import annotations


def engagement_rate(likes: int = 0, comments: int = 0, shares: int = 0, views: int = 0) -> float:
    views_i = max(0, int(views or 0))
    if views_i == 0:
        return 0.0
    return round((max(0, int(likes or 0)) + max(0, int(comments or 0)) + max(0, int(shares or 0))) / views_i, 6)


def cpv(cost_cents: int = 0, total_views: int = 0) -> float:
    """CPV = campaign.cost_cents / sum(content.views for content in campaign)."""
    views_i = max(0, int(total_views or 0))
    if views_i == 0:
        return 0.0
    return round(max(0, int(cost_cents or 0)) / 100 / views_i, 6)


def roi(cost_cents: int = 0, revenue_cents: int = 0) -> float:
    """ROI = (sum(attribution.revenue_cents) - cost_cents) / cost_cents."""
    cost_i = max(0, int(cost_cents or 0))
    if cost_i == 0:
        return 0.0
    return round((max(0, int(revenue_cents or 0)) - cost_i) / cost_i, 6)


def cents_to_usd(cents: int = 0) -> float:
    """All backend money stays in cents; frontend displays USD."""
    return round(int(cents or 0) / 100, 2)
