"""Stable row-to-API mappers for the Events domain service.

These helpers are kept free of database access so the main CRUD service can
remain focused on access control and transaction behavior.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def short_date(raw: Any) -> str:
    """Convert a stored timestamp into the honest display value ``M/D``."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if isinstance(raw, str) else raw
        return f"{dt.month}/{dt.day}"
    except Exception:
        return str(raw)


def material_row(row: Any) -> dict[str, Any]:
    """Map a ``vkpi_event_materials`` row to the frontend contract."""
    data = dict(row)
    return {
        "id": data.get("id"),
        "category": data.get("category") or "display",
        "name": data.get("name") or "",
        "source": data.get("source") or "ship",
        "qty": int(data.get("qty") or 0),
        "status": data.get("status") or "pending",
        "owner": data.get("owner") or "",
        "note": data.get("note") or "",
        "trackingNo": data.get("tracking_no") or "",
        "fileUrl": data.get("file_url") or "",
        "alert": data.get("alert") or "",
        "updatedAt": short_date(data.get("updated_at")),
    }


def product_row(row: Any) -> dict[str, Any]:
    """Map a ``vkpi_event_products`` row to the frontend contract."""
    data = dict(row)
    return {
        "id": data.get("id"),
        "category": data.get("category") or "lens",
        "name": data.get("name") or "",
        "source": data.get("source") or "new_purchase",
        "qty": int(data.get("qty") or 0),
        "status": data.get("status") or "ordered",
        "owner": data.get("owner") or "",
        "note": data.get("note") or "",
        "trackingNo": data.get("tracking_no") or "",
        "arriveBy": data.get("arrive_by") or "",
        "returnAfter": bool(data.get("return_after")),
    }
