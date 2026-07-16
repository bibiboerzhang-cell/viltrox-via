"""Attribution domain facade."""

from app.domains.attribution.links import (
    archive_link,
    create_link,
    health_check,
    link_clicks,
    link_detail,
    link_orders,
    list_links,
    pause_link,
    update_link,
)
from app.domains.attribution.revenue import (
    amazon_attribution_detail,
    amazon_summary,
    create_attribution,
    create_manual_attribution,
    import_amazon,
    list_amazon_attributions,
    list_attributions,
    summary,
    unmatched,
    verify_manual_attribution,
)

__all__ = [
    "archive_link",
    "create_link",
    "health_check",
    "link_clicks",
    "link_detail",
    "link_orders",
    "list_links",
    "amazon_attribution_detail",
    "amazon_summary",
    "create_attribution",
    "create_manual_attribution",
    "import_amazon",
    "list_amazon_attributions",
    "list_attributions",
    "pause_link",
    "summary",
    "unmatched",
    "verify_manual_attribution",
    "update_link",
]
