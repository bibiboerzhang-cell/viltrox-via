"""Viltrox Marketing public redirect and webhook API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.domains.attribution import link_center
from app.services.vkpi import integrations

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi"])
public_router = APIRouter(prefix="/go", tags=["vkpi-public"])
webhook_router = APIRouter(prefix="/api/vkpi/webhooks", tags=["vkpi-webhooks"])


@public_router.get("/{slug}")
def redirect_link(slug: str, request: Request):
    session_id = str(request.cookies.get("vkpi_click_sid") or "")
    try:
        result = link_center.resolve_link(slug, request, session_id=session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    response = RedirectResponse(url=result["destination_url"], status_code=int(result["redirect_mode"] or 302))
    response.set_cookie("vkpi_click_sid", result["session_id"], max_age=60 * 60 * 24 * 90, samesite="lax")
    return response


@webhook_router.post("/shopify/orders")
@webhook_router.post("/shopify")
async def shopify_order_webhook(request: Request):
    raw_body = await request.body()
    try:
        return integrations.ingest_shopify_order_webhook(
            request.headers,
            raw_body,
            getattr(getattr(request, "client", None), "host", "") or "",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@webhook_router.post("/shopify/refunds")
async def shopify_refund_webhook(request: Request):
    raw_body = await request.body()
    try:
        return integrations.ingest_shopify_refund_webhook(
            request.headers,
            raw_body,
            getattr(getattr(request, "client", None), "host", "") or "",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
