"""Dealer physical-location verification API.

This API records bounded human review only.  It never calls Google Places and
never writes Place IDs or Maps URLs.  Those fields remain pending until a
separately reviewed provider adapter is implemented.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.domains.commerce import dealer_location_verification


router = APIRouter(
    prefix="/api/admin/vkpi/dealers/location-verification",
    tags=["vkpi-dealer-location-verification"],
)


def _staff_id(staff) -> int:
    try:
        value = int((staff or {}).get("id") or (staff or {}).get("staff_id") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="staff identity is unresolved") from exc
    if value <= 0:
        raise HTTPException(status_code=403, detail="staff identity is unresolved")
    return value


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "not found") from exc
    except dealer_location_verification.LocationVerificationSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/contract")
def location_verification_contract(
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return dealer_location_verification.contract_status()


@router.get("/{dealer_id}")
def dealer_location_verification_detail(
    dealer_id: int,
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return _guard(dealer_location_verification.get_location_verification, dealer_id)


@router.post("/{dealer_id}/official-review")
def review_dealer_official_location(
    dealer_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    require_manager_staff(staff)
    unknown = sorted(
        set(body) - {"canonical_location_status", "physical_store_status", "note"}
    )
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="unsupported request fields: " + ", ".join(unknown),
        )
    return _guard(
        dealer_location_verification.review_official_location,
        dealer_id,
        body,
        actor_id=_staff_id(staff),
    )
