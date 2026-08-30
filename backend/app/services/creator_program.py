"""
services/creator_program.py — VIP tiers + affiliate + wardrobe
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import SHOPIFY_AFFILIATE_BASE_URL
from app.core.security import invalidate_user_cache
from app.db.connection import get_conn, table_exists
from app.services.creator_program_tiers import (
    MANUAL_TIER_STATUSES,
    OUTFIT_UNLOCKS,
    PENDING_TIER,
    VIP_TIERS,
    compute_vip_snapshot,
    unlocked_outfits,
)
from app.services.trust import collect_trust_metrics, get_trust_snapshot


def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _table_exists(table_name: str) -> bool:
    name = str(table_name or "").strip()
    if not name:
        return False
    return table_exists(name)


def build_student_program_snapshot(user_id: int = 0) -> dict[str, Any]:
    if int(user_id or 0) <= 0:
        return {
            "status": "inactive",
            "school_id": "",
            "school_name": "",
            "student_id_code": "",
            "expires_at": "",
            "commission_rate_override": 0.0,
            "is_active": False,
        }
    if not _table_exists("student_verifications"):
        return {
            "status": "inactive",
            "school_id": "",
            "school_name": "",
            "student_id_code": "",
            "expires_at": "",
            "commission_rate_override": 0.0,
            "is_active": False,
        }
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT sv.*, s.school_name
            FROM student_verifications sv
            LEFT JOIN schools s ON s.school_id = sv.school_id
            WHERE sv.user_id=?
            ORDER BY sv.verified_at DESC, sv.id DESC
            LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        return {
            "status": "inactive",
            "school_id": "",
            "school_name": "",
            "student_id_code": "",
            "expires_at": "",
            "commission_rate_override": 0.0,
            "is_active": False,
        }
    status = str(row["status"] or "inactive")
    return {
        "status": status,
        "school_id": row["school_id"] or "",
        "school_name": row["school_name"] or "",
        "student_id_code": row["student_id_code"] or "",
        "expires_at": row["expires_at"] or "",
        "commission_rate_override": float(row["commission_rate_override"] or 0.0),
        "is_active": status == "active",
    }


def build_identity_cards_summary(user_id: int = 0) -> dict[str, Any]:
    if int(user_id or 0) <= 0 or not _table_exists("student_qr_codes"):
        return {"student_cards": [], "total_cards": 0}
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT qr_id, school_id, display_serial, status, card_image_url, claim_url, bound_at, metadata_json
            FROM student_qr_codes
            WHERE bound_user_id=?
            ORDER BY bound_at DESC, id DESC
            LIMIT 8
            """,
            (int(user_id),),
        ).fetchall()
    except Exception:
        rows = []
    cards = []
    for row in rows:
        metadata = _load_json(row["metadata_json"], {})
        public_vid = ""
        try:
            from app.services.student_identity import _public_vid_for_qr, get_school

            school = get_school(str(row["school_id"] or "")) or {}
            public_vid = _public_vid_for_qr(
                {
                    "qr_id": row["qr_id"] or "",
                    "school_id": row["school_id"] or "",
                    "display_serial": row["display_serial"] or "",
                    "metadata": metadata,
                },
                school,
            )
        except Exception:
            public_vid = ""
        cards.append(
            {
                "qr_id": row["qr_id"] or "",
                "school_id": row["school_id"] or "",
                "display_serial": row["display_serial"] or "",
                "public_vid": public_vid,
                "status": row["status"] or "",
                "card_image_url": row["card_image_url"] or "",
                "claim_url": row["claim_url"] or "",
                "bound_at": row["bound_at"] or "",
            }
        )
    return {"student_cards": cards, "total_cards": len(cards)}


def build_affiliate_link(creator_code: str) -> str:
    code = str(creator_code or "").strip()
    if not code:
        return ""
    raw_base = str(SHOPIFY_AFFILIATE_BASE_URL or "https://viltrox.com/").strip()
    base = raw_base if "://" in raw_base else f"https://{raw_base}"
    parsed = urlparse(base)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["ref"] = code
    query.setdefault("utm_source", "v_os")
    query.setdefault("utm_medium", "affiliate")
    query.setdefault("utm_campaign", "creator_program")
    final_path = parsed.path or "/"
    return urlunparse(parsed._replace(path=final_path, query=urlencode(query)))


def _extract_ref_candidates(row: Any, payload: dict[str, Any]) -> list[str]:
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    note_attributes = body.get("note_attributes") if isinstance(body.get("note_attributes"), list) else []
    refs: list[str] = []
    for candidate in (
        row["creator_handle"],
        payload.get("ref_code"),
        payload.get("creator_code"),
        payload.get("creator_handle"),
        body.get("discount_code"),
        body.get("source_name"),
    ):
        text = str(candidate or "").strip()
        if text and text not in refs:
            refs.append(text)
    for item in note_attributes:
        if not isinstance(item, dict):
            continue
        key = str(item.get("name") or item.get("key") or "").strip().lower()
        value = str(item.get("value") or "").strip()
        if key in {"ref", "creator", "creator_code", "creator_id", "affiliate", "code"} and value and value not in refs:
            refs.append(value)
    return refs


def build_affiliate_snapshot(
    *,
    creator_code: str,
    user_id: int = 0,
    limit: int = 500,
    is_active: bool = False,
    is_student_active: bool = False,
    commission_rate_override: float = 0.0,
) -> dict[str, Any]:
    code = str(creator_code or "").strip()
    live_link = build_affiliate_link(code)
    effective_active = bool(is_active or is_student_active)
    effective_rate = max(float(commission_rate_override or 0.0), 0.0)
    stats = {
        "affiliate_link": live_link if effective_active else "",
        "preview_link": live_link,
        "ref_code": code,
        "orders_count": 0,
        "revenue_total": 0.0,
        "discount_total": 0.0,
        "quantity_total": 0,
        "last_order_at": "",
        "matched_event_ids": [],
        "is_ready": bool(code) and effective_active,
        "is_active": effective_active,
        "effective_commission_rate": effective_rate,
        "shopify_signal_ready": False,
        "activation_message": (
            "Affiliate link is active."
            if effective_active
            else "Unlock Bronze with 1 confirmed video before your creator sales link goes live."
        ),
    }
    if is_student_active and not is_active:
        stats["activation_message"] = "Student Creator lane is active, so your affiliate link is live at the student rate."
    if not code:
        return stats

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, creator_handle, payload_json, occurred_at, processed_at, ingest_status
        FROM platform_ingest_events
        WHERE source_platform='shopify' AND entity_type='order'
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    target = code.lower()
    for row in rows:
        payload = _load_json(row["payload_json"], {})
        candidates = [candidate.lower() for candidate in _extract_ref_candidates(row, payload)]
        if target not in candidates:
            continue
        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        stats["orders_count"] += 1
        stats["revenue_total"] += float(body.get("total_price") or body.get("current_total_price") or 0.0)
        stats["discount_total"] += float(body.get("total_discounts") or body.get("current_total_discounts") or 0.0)
        line_items = body.get("line_items") if isinstance(body.get("line_items"), list) else []
        stats["quantity_total"] += sum(int(float(item.get("quantity") or 0)) for item in line_items if isinstance(item, dict))
        stats["matched_event_ids"].append(int(row["id"]))
        occurred_at = str(row["occurred_at"] or row["processed_at"] or "").strip()
        if occurred_at and not stats["last_order_at"]:
            stats["last_order_at"] = occurred_at
        if str(row["ingest_status"] or "").strip().lower() == "done":
            stats["shopify_signal_ready"] = True

    stats["revenue_total"] = round(stats["revenue_total"], 2)
    stats["discount_total"] = round(stats["discount_total"], 2)
    return stats


def _clean_affiliate_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.lstrip("@").lower()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _format_address_line(data: dict[str, Any]) -> str:
    parts = [
        _first_non_empty(data.get("address1"), data.get("address_1")),
        _first_non_empty(data.get("address2"), data.get("address_2")),
        _first_non_empty(data.get("city")),
        _first_non_empty(data.get("province"), data.get("state")),
        _first_non_empty(data.get("zip"), data.get("postal_code")),
        _first_non_empty(data.get("country_code"), data.get("country")),
    ]
    return ", ".join(part for part in parts if part)


def _summarize_line_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No line items"
    chunks = []
    for item in items[:3]:
        title = _first_non_empty(item.get("title"), item.get("name"), item.get("sku"), "Gear")
        qty = int(float(item.get("quantity") or 0) or 0)
        chunks.append(f"{title} x{max(1, qty)}")
    if len(items) > 3:
        chunks.append(f"+{len(items)-3} more")
    return " · ".join(chunks)


def _load_affiliate_ops_sources(conn: Any, limit: int) -> tuple[list[Any], list[Any], list[Any]]:
    rows = conn.execute(
        """
        SELECT id, external_id, creator_handle, region_code, payload_json,
               occurred_at, processed_at, ingest_status
        FROM platform_ingest_events
        WHERE source_platform='shopify' AND entity_type='order'
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    user_rows = conn.execute(
        """
        SELECT id, email, name, creator_code, points_total, points_balance, tier_status, role
        FROM users
        ORDER BY id DESC
        """
    ).fetchall()
    address_rows = conn.execute(
        """
        SELECT *
        FROM user_addresses
        ORDER BY user_id ASC, is_default DESC, id DESC
        """
    ).fetchall()
    return rows, user_rows, address_rows


def _index_affiliate_ops_users(
    user_rows: list[Any],
    address_rows: list[Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[int, dict[str, Any]]]:
    user_by_code = {
        _clean_affiliate_ref(row["creator_code"]): dict(row)
        for row in user_rows
        if _clean_affiliate_ref(row["creator_code"])
    }
    user_by_email = {
        _clean_affiliate_ref(row["email"]): dict(row)
        for row in user_rows
        if _clean_affiliate_ref(row["email"])
    }
    default_address_by_user: dict[int, dict[str, Any]] = {}
    for row in address_rows:
        uid = int(row["user_id"] or 0)
        if uid > 0 and uid not in default_address_by_user:
            default_address_by_user[uid] = dict(row)
    return user_by_code, user_by_email, default_address_by_user


def _affiliate_ops_order_parts(
    row: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], str, str]:
    payload = _load_json(row["payload_json"], {})
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
    shipping = body.get("shipping_address") if isinstance(body.get("shipping_address"), dict) else {}
    billing = body.get("billing_address") if isinstance(body.get("billing_address"), dict) else {}
    line_items = [item for item in body.get("line_items", []) if isinstance(item, dict)] if isinstance(body.get("line_items"), list) else []
    ref_candidates = _extract_ref_candidates(row, payload)
    ref_code = _clean_affiliate_ref(next((item for item in ref_candidates if item), payload.get("ref_code", "")))
    creator_handle = _clean_affiliate_ref(row["creator_handle"])
    return body, customer, shipping, billing, line_items, ref_code, creator_handle


def _match_affiliate_ops_user(
    ref_code: str,
    creator_handle: str,
    customer_email: Any,
    user_by_code: dict[str, dict[str, Any]],
    user_by_email: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for candidate in [ref_code, creator_handle, _clean_affiliate_ref(customer_email)]:
        if not candidate:
            continue
        matched_user = user_by_code.get(candidate) or user_by_email.get(candidate)
        if matched_user:
            return matched_user
    return None


def _cached_affiliate_creator_program(
    matched_user: dict[str, Any],
    creator_cache: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    uid = int(matched_user["id"])
    if uid not in creator_cache:
        metrics = collect_trust_metrics(uid)
        vip = compute_vip_snapshot(
            int(matched_user["points_total"] or matched_user["points_balance"] or 0),
            int(metrics.get("confirmed_videos", 0) or 0),
            str(matched_user.get("tier_status") or "pending"),
        )
        creator_cache[uid] = {
            "vip": vip,
            "trust_score": float(metrics.get("stored_trust_score", 0) or 0.0),
            "student": build_student_program_snapshot(uid),
            "user": matched_user,
        }
    return creator_cache[uid]


def _affiliate_ops_creator_context(
    matched_user: dict[str, Any] | None,
    customer: dict[str, Any],
    creator_handle: str,
    ref_code: str,
    creator_cache: dict[int, dict[str, Any]],
) -> tuple[str, str, float, str, float, dict[str, Any] | None]:
    creator_name = _first_non_empty(
        customer.get("first_name"), customer.get("last_name"), customer.get("email"), creator_handle, ref_code
    )
    creator_email = _first_non_empty(customer.get("email"))
    if not matched_user:
        return creator_name, creator_email, 0.0, "Unmatched", 0.0, None

    creator_program = _cached_affiliate_creator_program(matched_user, creator_cache)
    student_program = creator_program.get("student") or {}
    creator_name = _first_non_empty(matched_user.get("name"), matched_user.get("email"), creator_name)
    creator_email = _first_non_empty(matched_user.get("email"), creator_email)
    trust_score = float(creator_program["trust_score"] or 0.0)
    vip = creator_program["vip"] or {}
    tier_label = str(vip.get("tier_label") or "Pending")
    vip_rate = float(vip.get("commission_rate") or 0.0) if vip.get("is_active") else 0.0
    student_rate = float(student_program.get("commission_rate_override") or 0.0) if student_program.get("is_active") else 0.0
    return creator_name, creator_email, trust_score, tier_label, max(vip_rate, student_rate), student_program


def _affiliate_ops_money(
    body: dict[str, Any],
    line_items: list[dict[str, Any]],
    commission_rate: float,
) -> tuple[str, str, float, float, int, bool, float]:
    financial_status = _first_non_empty(body.get("financial_status"), body.get("display_financial_status")).lower()
    fulfillment_status = _first_non_empty(body.get("fulfillment_status"), body.get("display_fulfillment_status"))
    order_total = float(body.get("current_total_price") or body.get("total_price") or 0.0)
    discount_total = float(body.get("current_total_discounts") or body.get("total_discounts") or 0.0)
    quantity_total = sum(int(float(item.get("quantity") or 0) or 0) for item in line_items)
    is_paid = financial_status in {"paid", "partially_paid", "authorized"}
    estimated_commission = round(order_total * commission_rate, 2) if is_paid else 0.0
    return financial_status, fulfillment_status, order_total, discount_total, quantity_total, is_paid, estimated_commission


def _affiliate_ops_address(
    row: Any,
    shipping: dict[str, Any],
    billing: dict[str, Any],
    matched_user: dict[str, Any] | None,
    default_address_by_user: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], str, str]:
    if shipping or billing:
        address_data = shipping or billing
    elif matched_user:
        address_data = default_address_by_user.get(int(matched_user["id"])) or {}
    else:
        address_data = {}
    address_data = address_data or {}
    address_line = _format_address_line(address_data)
    region_label = _first_non_empty(
        row["region_code"],
        address_data.get("country_code"),
        address_data.get("country"),
        "Unknown",
    )
    return address_data, address_line, region_label


def _update_affiliate_ops_summary(
    summary: dict[str, Any],
    order_total: float,
    discount_total: float,
    estimated_commission: float,
    is_paid: bool,
) -> None:
    summary["tracked_orders"] += 1
    summary["revenue_total"] += order_total
    summary["discount_total"] += discount_total
    summary["estimated_commission_total"] += estimated_commission
    if is_paid:
        summary["paid_orders"] += 1


def _update_affiliate_creator_rollup(
    creator_rollup: dict[str, dict[str, Any]],
    ref_code: str,
    creator_handle: str,
    creator_email: str,
    creator_name: str,
    tier_label: str,
    student_program: dict[str, Any] | None,
    trust_score: float,
    order_total: float,
    estimated_commission: float,
) -> str:
    creator_key = _clean_affiliate_ref(_first_non_empty(ref_code, creator_handle, creator_email, creator_name))
    if not creator_key:
        return ""
    bucket = creator_rollup.setdefault(
        creator_key,
        {
            "creator_ref": creator_key,
            "creator_name": creator_name or "Unknown creator",
            "creator_email": creator_email,
            "tier_label": tier_label,
            "student_id_code": (student_program or {}).get("student_id_code") or "",
            "trust_score": round(trust_score, 1),
            "orders_count": 0,
            "revenue_total": 0.0,
            "estimated_commission_total": 0.0,
        },
    )
    bucket["orders_count"] += 1
    bucket["revenue_total"] += order_total
    bucket["estimated_commission_total"] += estimated_commission
    return creator_key


def _update_affiliate_region_rollup(
    region_rollup: dict[str, dict[str, Any]],
    region_label: str,
    order_total: float,
) -> None:
    bucket = region_rollup.setdefault(
        region_label,
        {"region": region_label, "orders_count": 0, "revenue_total": 0.0},
    )
    bucket["orders_count"] += 1
    bucket["revenue_total"] += order_total


def _build_affiliate_ops_item(
    *,
    row: Any,
    body: dict[str, Any],
    customer: dict[str, Any],
    address_data: dict[str, Any],
    address_line: str,
    line_items: list[dict[str, Any]],
    ref_code: str,
    creator_key: str,
    creator_name: str,
    creator_email: str,
    tier_label: str,
    student_program: dict[str, Any] | None,
    trust_score: float,
    commission_rate: float,
    estimated_commission: float,
    order_total: float,
    discount_total: float,
    quantity_total: int,
    financial_status: str,
    fulfillment_status: str,
    region_label: str,
) -> dict[str, Any]:
    return {
        "event_id": int(row["id"]),
        "order_id": _first_non_empty(row["external_id"], body.get("order_number"), body.get("id")),
        "ref_code": ref_code,
        "creator_ref": creator_key,
        "creator_name": creator_name,
        "creator_email": creator_email,
        "tier_label": tier_label,
        "student_id_code": (student_program or {}).get("student_id_code") or "",
        "trust_score": round(trust_score, 1),
        "commission_rate": round(commission_rate * 100, 2),
        "estimated_commission": estimated_commission,
        "order_total": round(order_total, 2),
        "discount_total": round(discount_total, 2),
        "quantity_total": int(quantity_total),
        "financial_status": financial_status or "unknown",
        "fulfillment_status": fulfillment_status or "pending",
        "ingest_status": str(row["ingest_status"] or "queued"),
        "occurred_at": _first_non_empty(row["occurred_at"], row["processed_at"]),
        "customer_email": _first_non_empty(customer.get("email"), body.get("email")),
        "shipping_name": _first_non_empty(
            address_data.get("name"),
            " ".join(part for part in [customer.get("first_name"), customer.get("last_name")] if str(part or "").strip()),
            customer.get("first_name"),
        ),
        "shipping_phone": _first_non_empty(address_data.get("phone"), customer.get("phone")),
        "shipping_address": address_line,
        "shipping_city": _first_non_empty(address_data.get("city")),
        "shipping_state": _first_non_empty(address_data.get("province"), address_data.get("state")),
        "shipping_country": _first_non_empty(address_data.get("country_code"), address_data.get("country")),
        "shipping_postal_code": _first_non_empty(address_data.get("zip"), address_data.get("postal_code")),
        "region_code": region_label,
        "line_items_summary": _summarize_line_items(line_items),
    }


def _finalize_affiliate_ops_snapshot(
    summary: dict[str, Any],
    items: list[dict[str, Any]],
    creator_rollup: dict[str, dict[str, Any]],
    region_rollup: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    summary["revenue_total"] = round(summary["revenue_total"], 2)
    summary["discount_total"] = round(summary["discount_total"], 2)
    summary["estimated_commission_total"] = round(summary["estimated_commission_total"], 2)
    summary["matched_creators"] = sum(1 for item in creator_rollup.values() if item["orders_count"] > 0)
    summary["tracked_countries"] = len(region_rollup)
    creators = sorted(
        [
            {
                **item,
                "revenue_total": round(item["revenue_total"], 2),
                "estimated_commission_total": round(item["estimated_commission_total"], 2),
            }
            for item in creator_rollup.values()
        ],
        key=lambda item: (item["estimated_commission_total"], item["revenue_total"], item["orders_count"]),
        reverse=True,
    )
    regions = sorted(
        [
            {**item, "revenue_total": round(item["revenue_total"], 2)}
            for item in region_rollup.values()
        ],
        key=lambda item: (item["revenue_total"], item["orders_count"]),
        reverse=True,
    )
    return {
        "summary": summary,
        "items": items,
        "creators": creators[:12],
        "regions": regions[:12],
    }


def build_affiliate_ops_snapshot(*, limit: int = 250) -> dict[str, Any]:
    conn = get_conn()
    rows, user_rows, address_rows = _load_affiliate_ops_sources(conn, limit)
    user_by_code, user_by_email, default_address_by_user = _index_affiliate_ops_users(user_rows, address_rows)

    creator_cache: dict[int, dict[str, Any]] = {}
    creator_rollup: dict[str, dict[str, Any]] = {}
    region_rollup: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    summary = {
        "tracked_orders": 0,
        "paid_orders": 0,
        "matched_creators": 0,
        "revenue_total": 0.0,
        "discount_total": 0.0,
        "estimated_commission_total": 0.0,
        "tracked_countries": 0,
    }

    for row in rows:
        body, customer, shipping, billing, line_items, ref_code, creator_handle = _affiliate_ops_order_parts(row)
        matched_user = _match_affiliate_ops_user(
            ref_code, creator_handle, customer.get("email"), user_by_code, user_by_email
        )
        creator_name, creator_email, trust_score, tier_label, commission_rate, student_program = (
            _affiliate_ops_creator_context(matched_user, customer, creator_handle, ref_code, creator_cache)
        )
        (
            financial_status,
            fulfillment_status,
            order_total,
            discount_total,
            quantity_total,
            is_paid,
            estimated_commission,
        ) = _affiliate_ops_money(body, line_items, commission_rate)
        address_data, address_line, region_label = _affiliate_ops_address(
            row, shipping, billing, matched_user, default_address_by_user
        )

        _update_affiliate_ops_summary(summary, order_total, discount_total, estimated_commission, is_paid)
        creator_key = _update_affiliate_creator_rollup(
            creator_rollup,
            ref_code,
            creator_handle,
            creator_email,
            creator_name,
            tier_label,
            student_program,
            trust_score,
            order_total,
            estimated_commission,
        )
        _update_affiliate_region_rollup(region_rollup, region_label, order_total)
        items.append(
            _build_affiliate_ops_item(
                row=row,
                body=body,
                customer=customer,
                address_data=address_data,
                address_line=address_line,
                line_items=line_items,
                ref_code=ref_code,
                creator_key=creator_key,
                creator_name=creator_name,
                creator_email=creator_email,
                tier_label=tier_label,
                student_program=student_program,
                trust_score=trust_score,
                commission_rate=commission_rate,
                estimated_commission=estimated_commission,
                order_total=order_total,
                discount_total=discount_total,
                quantity_total=quantity_total,
                financial_status=financial_status,
                fulfillment_status=fulfillment_status,
                region_label=region_label,
            )
        )

    return _finalize_affiliate_ops_snapshot(summary, items, creator_rollup, region_rollup)


def sync_creator_program_state(user_id: int, *, reason: str = "program_sync") -> dict[str, Any]:
    conn = get_conn()
    user_row = conn.execute(
        """
        SELECT id, creator_code, points_balance, points_total, tier_status, role
        FROM users
        WHERE id=?
        """,
        (int(user_id),),
    ).fetchone()
    if not user_row:
        return {}

    trust = get_trust_snapshot(int(user_id), persist_if_stale=True, reason=reason, context={"user_id": int(user_id)}).as_dict()
    confirmed_videos = int(((trust.get("metrics") or {}).get("confirmed_videos", 0)) or 0)
    role = str(user_row["role"] or "creator")
    current_tier_status = str(user_row["tier_status"] or "pending").strip().lower()
    if current_tier_status in MANUAL_TIER_STATUSES:
        desired_tier_status = current_tier_status
    else:
        desired_tier_status = "active" if role == "admin" or confirmed_videos >= 1 else "pending"
    if str(user_row["tier_status"] or "pending").lower() != desired_tier_status:
        conn.execute("UPDATE users SET tier_status=? WHERE id=?", (desired_tier_status, int(user_id)))
        conn.commit()
        invalidate_user_cache(int(user_id))
    return {
        "user_id": int(user_id),
        "tier_status": desired_tier_status,
        "confirmed_videos": confirmed_videos,
        "trust": trust,
        "points_total": int(user_row["points_total"] or user_row["points_balance"] or 0),
        "creator_code": str(user_row["creator_code"] or "").strip(),
    }


def build_creator_program_snapshot(user: dict[str, Any] | None) -> dict[str, Any]:
    user_dict = dict(user or {})
    user_id = int(user_dict.get("id") or 0)
    if user_id <= 0:
        return {
            "vip": compute_vip_snapshot(0, 0, "pending"),
            "affiliate": build_affiliate_snapshot(creator_code="", is_active=False),
            "student": build_student_program_snapshot(0),
            "effective_commission_rate": 0.0,
            "identity_cards": build_identity_cards_summary(0),
            "wardrobe": unlocked_outfits(0, is_active=False),
            "trust": {"score": 0, "label": "Starter guard", "band_key": "starter", "limits": {}, "metrics": {}},
        }

    state = sync_creator_program_state(user_id, reason="creator_program_view")
    points_total = int(state.get("points_total", 0) or 0)
    confirmed_videos = int(state.get("confirmed_videos", 0) or 0)
    tier_status = str(state.get("tier_status") or "pending")
    creator_code = str(state.get("creator_code") or user_dict.get("creator_code") or "").strip()

    vip = compute_vip_snapshot(points_total, confirmed_videos, tier_status)
    student = build_student_program_snapshot(user_id)
    effective_commission_rate = max(
        float(vip.get("commission_rate") or 0.0) if bool(vip.get("is_active")) else 0.0,
        float(student.get("commission_rate_override") or 0.0) if bool(student.get("is_active")) else 0.0,
    )
    affiliate = build_affiliate_snapshot(
        creator_code=creator_code,
        user_id=user_id,
        is_active=bool(vip["is_active"]),
        is_student_active=bool(student.get("is_active")),
        commission_rate_override=effective_commission_rate,
    )
    return {
        "vip": vip,
        "affiliate": affiliate,
        "student": student,
        "effective_commission_rate": effective_commission_rate,
        "identity_cards": build_identity_cards_summary(user_id),
        "wardrobe": unlocked_outfits(points_total, is_active=bool(vip["is_active"])),
        "trust": state.get("trust") or {},
    }
