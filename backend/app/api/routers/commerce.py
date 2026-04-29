"""
api/routers/commerce.py — Commerce admin endpoints (batch 2)

Wire into app/main.py:
    from app.api.routers import commerce
    app.include_router(commerce.router)

Endpoints grouped:
    /api/admin/orders/*        list/detail/attribution override/flag
    /api/admin/webhook-events  shopify webhook event log
    /api/admin/attribution/*   funnel, by-creator, by-platform, utm
    /api/admin/payouts/*       cycles, pending, process, disputes
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.logging import get_logger
from app.core.security import require_admin_async as require_admin
from app.db.connection import get_conn
from app.services.commerce import attribution as attr_svc
from app.services.commerce import orders as orders_svc
from app.services.commerce import payouts as payouts_svc
from app.services.audit_log import record_admin_action

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["commerce-admin"])


# =========================================================================
# Orders
# =========================================================================

@router.get("/orders")
def list_orders(
    status: str | None = None,
    attribution_type: Literal["creator", "student", "direct"] | None = None,
    platform: str | None = None,
    country: str | None = None,
    utm_source: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    limit: int = 50,
    admin=Depends(require_admin),
):
    """List orders with filters."""
    return orders_svc.list_orders(
        status=status,
        attribution_type=attribution_type,
        platform=platform,
        country=country,
        utm_source=utm_source,
        from_date=from_date,
        to_date=to_date,
        page=page,
        limit=limit,
    )


@router.get("/orders/unattributed")
def list_unattributed(limit: int = 100, admin=Depends(require_admin)):
    return orders_svc.list_orders(attribution_type="direct", limit=limit)


@router.post("/orders/backfill")
async def backfill_orders(
    request: Request,
    body: dict | None = None,
    admin=Depends(require_admin),
):
    limit = int((body or {}).get("limit") or 500)
    cutoff_at = str((body or {}).get("cutoff_at") or "").strip() or None
    include_history = bool((body or {}).get("include_history") or False)
    result = await orders_svc.backfill_orders_from_events(
        limit=limit,
        cutoff_at=cutoff_at,
        include_history=include_history,
    )
    record_admin_action(
        actor=admin,
        action="backfill_orders",
        target_type="orders",
        target_id="shopify",
        detail=result,
        request=request,
    )
    return result


@router.get("/orders/{order_id}")
def get_order(order_id: int, admin=Depends(require_admin)):
    order = orders_svc.get_order_detail(order_id)
    if not order:
        raise HTTPException(404, "Order not found")
    return order


@router.post("/orders/{order_id}/attribute")
def override_attribution(
    order_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
):
    """Manually reassign attribution to a creator/student handle."""
    creator_handle = body.get("creator_handle")
    reason = body.get("reason", "")
    if not creator_handle:
        raise HTTPException(400, "creator_handle required")

    result = orders_svc.override_attribution(order_id, creator_handle, reason, admin["id"])
    record_admin_action(
        actor=admin,
        action="override_attribution",
        target_type="order",
        target_id=str(order_id),
        detail={"creator_handle": creator_handle, "reason": reason},
        request=request,
    )
    return result


@router.post("/orders/{order_id}/flag")
def flag_order(
    order_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
):
    reason = body.get("reason")
    if reason not in {"fraud", "bot", "duplicate"}:
        raise HTTPException(400, "reason must be fraud|bot|duplicate")

    result = orders_svc.flag_order(order_id, reason, admin["id"])
    record_admin_action(
        actor=admin, action="flag_order",
        target_type="order", target_id=str(order_id),
        detail={"reason": reason}, request=request,
    )
    return result


# =========================================================================
# Webhook events
# =========================================================================

@router.get("/webhook-events")
def list_webhook_events(
    source: str | None = None,
    status: Literal["ok", "warn", "err"] | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    limit: int = 50,
    admin=Depends(require_admin),
):
    return orders_svc.list_webhook_events(
        source=source, status=status,
        from_date=from_date, to_date=to_date,
        page=page, limit=limit,
    )


# =========================================================================
# Attribution
# =========================================================================

@router.get("/attribution/overview")
def attribution_overview(
    from_date: str | None = None,
    to_date: str | None = None,
    admin=Depends(require_admin),
):
    return attr_svc.compute_overview(from_date, to_date)


@router.get("/attribution/funnel")
def attribution_funnel(
    from_date: str | None = None,
    to_date: str | None = None,
    admin=Depends(require_admin),
):
    return attr_svc.compute_funnel(from_date, to_date)


@router.get("/attribution/by-creator")
def attribution_by_creator(
    order_by: Literal["gmv", "ctr", "orders"] = "gmv",
    limit: int = 50,
    admin=Depends(require_admin),
):
    return attr_svc.creator_performance(order_by=order_by, limit=limit)


@router.get("/attribution/by-platform")
def attribution_by_platform(admin=Depends(require_admin)):
    return attr_svc.platform_breakdown()


@router.get("/attribution/utm-sources")
def attribution_utm(
    from_date: str | None = None,
    to_date: str | None = None,
    admin=Depends(require_admin),
):
    return attr_svc.utm_performance(from_date, to_date)


# =========================================================================
# Payouts
# =========================================================================

@router.get("/payouts/cycles")
def list_cycles(admin=Depends(require_admin)):
    return payouts_svc.list_cycles()


@router.get("/payouts/cycle/{cycle_id}")
def get_cycle(cycle_id: str, admin=Depends(require_admin)):
    cycle = payouts_svc.get_cycle_detail(cycle_id)
    if not cycle:
        raise HTTPException(404, "Cycle not found")
    return cycle


@router.post("/payouts/cycle/{cycle_id}/approve-all")
def approve_all_in_cycle(cycle_id: str, request: Request, admin=Depends(require_admin)):
    result = payouts_svc.approve_all(cycle_id, admin["id"])
    record_admin_action(
        actor=admin, action="approve_all_payouts",
        target_type="payout_cycle", target_id=cycle_id,
        detail=result, request=request,
    )
    return result


@router.post("/payouts/cycle/{cycle_id}/process")
def process_cycle(cycle_id: str, request: Request, admin=Depends(require_admin)):
    """Move all approved payouts in cycle to paid. Triggers PayPal/bank send."""
    result = payouts_svc.process_cycle(cycle_id, admin["id"])
    record_admin_action(
        actor=admin, action="process_payout_cycle",
        target_type="payout_cycle", target_id=cycle_id,
        detail={"processed_count": result.get("processed_count")},
        request=request,
    )
    return result


@router.get("/payouts/user/{user_id}/history")
def payout_history(user_id: int, admin=Depends(require_admin)):
    return payouts_svc.user_history(user_id)


@router.post("/payouts/{payout_id}/approve")
def approve_payout(payout_id: int, request: Request, admin=Depends(require_admin)):
    result = payouts_svc.approve_one(payout_id, admin["id"])
    record_admin_action(
        actor=admin, action="approve_payout",
        target_type="payout", target_id=str(payout_id),
        request=request,
    )
    return result


@router.post("/payouts/{payout_id}/hold")
def hold_payout(
    payout_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    reason = body.get("reason")
    if not reason:
        raise HTTPException(400, "reason required")
    result = payouts_svc.hold_one(payout_id, reason, admin["id"])
    record_admin_action(
        actor=admin, action="hold_payout",
        target_type="payout", target_id=str(payout_id),
        detail={"reason": reason}, request=request,
    )
    return result


@router.post("/payouts/{payout_id}/release")
def release_payout(payout_id: int, request: Request, admin=Depends(require_admin)):
    result = payouts_svc.release_one(payout_id, admin["id"])
    record_admin_action(
        actor=admin, action="release_payout",
        target_type="payout", target_id=str(payout_id),
        request=request,
    )
    return result


@router.post("/payouts/{payout_id}/adjust")
def adjust_payout(
    payout_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    new_amount = body.get("new_amount_cents")
    reason = body.get("reason")
    if new_amount is None or not reason:
        raise HTTPException(400, "new_amount_cents and reason required")
    result = payouts_svc.adjust_one(payout_id, new_amount, reason, admin["id"])
    record_admin_action(
        actor=admin, action="adjust_payout",
        target_type="payout", target_id=str(payout_id),
        detail={"new_amount_cents": new_amount, "reason": reason},
        request=request,
    )
    return result


@router.get("/payouts/disputes")
def list_disputes(
    status: Literal["open", "resolved_uphold", "resolved_overturn"] | None = "open",
    admin=Depends(require_admin),
):
    return payouts_svc.list_disputes(status=status)


@router.post("/payouts/disputes/{dispute_id}/resolve")
def resolve_dispute(
    dispute_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    resolution = body.get("resolution")
    note = body.get("note", "")
    if resolution not in {"uphold", "overturn"}:
        raise HTTPException(400, "resolution must be uphold|overturn")
    result = payouts_svc.resolve_dispute(dispute_id, resolution, note, admin["id"])
    record_admin_action(
        actor=admin, action="resolve_dispute",
        target_type="payout_dispute", target_id=str(dispute_id),
        detail={"resolution": resolution}, request=request,
    )
    return result
