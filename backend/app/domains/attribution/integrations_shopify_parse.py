"""Shopify payload parsing + attribution context resolution helpers for V-KPI.

These are the low-level leaf helpers used by the integrations webhook/report
adapters to normalise Shopify order payloads and resolve the project/link/event
match. Split out of ``integrations.py`` verbatim to keep that module thin; the
public surface stays in ``integrations.py`` which re-exports these names.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.db.connection import get_conn


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip() or default))
    except (TypeError, ValueError):
        return default


def _money_cents(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    clean = text.replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return int(round(float(clean) * 100))
    except (TypeError, ValueError):
        return 0


def _note_attributes(payload: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for item in _as_list(payload.get("note_attributes")):
        record = _as_dict(item)
        key = str(record.get("name") or record.get("key") or "").strip().lower()
        if key:
            attrs[key] = str(record.get("value") or "").strip()
    return attrs


def _query_value(url: str, names: set[str]) -> str:
    if not url:
        return ""
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query or "")
    for name in names:
        values = query.get(name) or []
        for value in values:
            if str(value or "").strip():
                return str(value).strip()
    return ""


def _shopify_discount_codes(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in _as_list(payload.get("discount_codes")):
        code = str(_as_dict(item).get("code") or "").strip()
        if code:
            values.append(code)
    note = _note_attributes(payload)
    for key in ("discount_code", "code", "coupon"):
        if note.get(key):
            values.append(note[key])
    return list(dict.fromkeys(values))


def _shopify_line_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [_as_dict(item) for item in _as_list(payload.get("line_items"))]


def _first_sku(payload: dict[str, Any]) -> str:
    for item in _shopify_line_items(payload):
        sku = str(item.get("sku") or item.get("product_id") or "").strip()
        if sku:
            return sku
    return ""


def _find_link_by_click(click_id: str) -> dict[str, Any]:
    if not click_id:
        return {}
    row = get_conn().execute(
        """
        SELECT c.id AS click_row_id,
               c.event_id,
               l.id AS link_id,
               l.project_id,
               l.kol_id,
               l.staff_id,
               l.product_sku,
               l.campaign_name
        FROM vkpi_link_clicks c
        JOIN vkpi_links l ON l.id = c.link_id
        WHERE c.event_id=?
        LIMIT 1
        """,
        (click_id,),
    ).fetchone()
    return dict(row) if row else {}


def _find_project_by_discount(discount_codes: list[str]) -> dict[str, Any]:
    if not discount_codes:
        return {}
    lowered = [code.lower() for code in discount_codes if code]
    if not lowered:
        return {}
    placeholders = ",".join("?" for _ in lowered)
    row = get_conn().execute(
        f"""
        SELECT id AS project_id,
               kol_id,
               assigned_staff_id AS staff_id,
               product_sku,
               project_name AS campaign_name
        FROM vkpi_projects
        WHERE lower(shopify_discount_code) IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        tuple(lowered),
    ).fetchone()
    return dict(row) if row else {}


def _find_event_by_discount(discount_codes: list[str]) -> str:
    """Map a Shopify discount code to a vkpi_event id via vkpi_event_discount_codes.

    Returns the event id (VARCHAR) or '' when unmapped. The mapping table is
    additive (migration 144); a missing table is tolerated so attribution never
    fails on a fresh DB.
    """
    if not discount_codes:
        return ""
    lowered = [code.lower() for code in discount_codes if code]
    if not lowered:
        return ""
    placeholders = ",".join("?" for _ in lowered)
    try:
        row = get_conn().execute(
            f"""
            SELECT event_id
            FROM vkpi_event_discount_codes
            WHERE lower(discount_code) IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
            """,
            tuple(lowered),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    value = row["event_id"] if hasattr(row, "__getitem__") else None
    return str(value or "").strip()


def _find_link_by_utm(utm_campaign: str, product_sku: str = "") -> dict[str, Any]:
    if not utm_campaign and not product_sku:
        return {}
    where: list[str] = []
    params: list[Any] = []
    if utm_campaign:
        where.append("lower(utm_campaign)=lower(?)")
        params.append(utm_campaign)
    if product_sku:
        where.append("lower(product_sku)=lower(?)")
        params.append(product_sku)
    row = get_conn().execute(
        f"""
        SELECT id AS link_id,
               project_id,
               kol_id,
               staff_id,
               product_sku,
               campaign_name
        FROM vkpi_links
        WHERE {' OR '.join(where)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else {}


def _shopify_ref_context(payload: dict[str, Any]) -> dict[str, Any]:
    note = _note_attributes(payload)
    landing = _pick(payload.get("landing_site"), payload.get("referring_site"), payload.get("order_status_url"))
    click_id = _pick(
        note.get("vkpi_click_id"),
        note.get("click_id"),
        _query_value(landing, {"vkpi_click_id", "click_id"}),
    )
    utm_campaign = _pick(note.get("utm_campaign"), _query_value(landing, {"utm_campaign"}))
    utm_source = _pick(note.get("utm_source"), _query_value(landing, {"utm_source"}))
    discount_codes = _shopify_discount_codes(payload)
    product_sku = _first_sku(payload)
    match = _find_link_by_click(click_id)
    match_source = "vkpi_click_id" if match else ""
    if not match:
        match = _find_project_by_discount(discount_codes)
        match_source = "discount_code" if match else ""
    if not match:
        match = _find_link_by_utm(utm_campaign, product_sku)
        match_source = "utm_or_sku" if match else ""
    # Event-level attribution rides alongside project/link matching: a discount code
    # mapped to a vkpi_event surfaces its id into evidence_json (no schema change to
    # vkpi_sales_attributions — keeps the fingerprint intact).
    event_id = _find_event_by_discount(discount_codes)
    return {
        "click_id": click_id,
        "landing_site": landing,
        "utm_campaign": utm_campaign,
        "utm_source": utm_source,
        "discount_codes": discount_codes,
        "product_sku": product_sku,
        "match": match,
        "match_source": match_source,
        "event_id": event_id,
    }
