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

__all__ = [
    "archive_link",
    "create_link",
    "health_check",
    "link_clicks",
    "link_detail",
    "link_orders",
    "list_links",
    "pause_link",
    "update_link",
]
