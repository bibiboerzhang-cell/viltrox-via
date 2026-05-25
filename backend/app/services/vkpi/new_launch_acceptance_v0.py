"""Service facade for P6.74 read-only new launch acceptance estimator v0."""
from __future__ import annotations

from app.domains.launch.acceptance import ESTIMATOR_VERSION, build_new_launch_acceptance_report
from app.services.vkpi import product_campaign_card, trend_detection_v0


def build_new_launch_acceptance_v0(
    *,
    sku: str = "",
    kol_limit: int = 200,
    top_n: int = 12,
    lookback_days: int = 14,
) -> dict:
    bounded_top = max(1, min(50, int(top_n or 12)))
    campaign_card = product_campaign_card.build_product_campaign_card(
        sku=sku,
        kol_limit=max(1, min(500, int(kol_limit or 200))),
        top_kols=max(bounded_top, 12),
    )
    trend_report = trend_detection_v0.build_trend_detection_v0(
        lookback_days=max(1, min(90, int(lookback_days or 14))),
        limit=5000,
        top_signals=50,
    )
    return build_new_launch_acceptance_report(
        campaign_card=campaign_card,
        trend_report=trend_report,
        sku=sku,
        kol_limit=kol_limit,
        top_n=bounded_top,
        lookback_days=lookback_days,
    )


__all__ = [
    "ESTIMATOR_VERSION",
    "build_new_launch_acceptance_v0",
    "product_campaign_card",
    "trend_detection_v0",
]
