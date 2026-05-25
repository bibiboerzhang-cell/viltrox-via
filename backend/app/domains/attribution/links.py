"""Short-link center use cases."""
from __future__ import annotations

from typing import Any

from app.domains.attribution import link_center
from app.domains.access import scope


def list_links(
    *,
    status: str = "",
    staff_id: int | None = None,
    limit: int = 50,
    staff: dict[str, Any],
) -> dict[str, Any]:
    return link_center.list_links(limit=limit, status=status, staff=staff, staff_id_filter=staff_id)


def link_detail(link_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.link_detail(link_id, staff=staff)


def link_clicks(link_id: int, *, limit: int = 100, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.link_clicks(link_id, staff=staff, limit=limit)


def link_orders(link_id: int, *, limit: int = 100, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.link_orders(link_id, staff=staff, limit=limit)


def create_link(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.create_link(body, staff=staff)


def update_link(link_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.update_link(link_id, body, staff=staff)


def pause_link(link_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.set_status(link_id, "paused", staff=staff)


def archive_link(link_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    return link_center.set_status(link_id, "archived", staff=staff)


def health_check(link_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    scope.assert_link_access(link_id, staff)
    return link_center.health_check(link_id, staff=staff)
