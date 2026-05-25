"""V-KPI KOL lifecycle, claim, and link center routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains import attribution as attribution_domain
from app.services.kol.account_dossier import analyze_kol_account, get_kol_dossier, scan_kol_account
from app.services.vkpi import kol_claims, scope
from app.domains.kol.payload_utils import _int, _json_loads
from app.domains.kol.natural_search import _natural_search_payload
from app.api.routers.vkpi_kol_links_profile import _assessment_payload, _contact_rows, _product_fit_payload

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-links"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.post("/kol/search/natural")
def natural_kol_search(body: dict, staff=Depends(require_tab("vkpi", "read"))):
    return _natural_search_payload(body or {}, staff=staff)


@router.post("/kols/lookup")
async def lookup_kol(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        result = kol_claims.lookup(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    kol = result.get("kol") or {}
    kol_id = int(kol.get("id") or 0) if isinstance(kol, dict) else 0
    if not kol_id:
        return result
    try:
        kol_claims.assert_kol_access(kol_id, staff, allow_unclaimed=True)
    except scope.ScopeDenied as exc:
        result["dossier"] = {}
        result["can_claim"] = False
        result["access_status"] = "claimed_by_other"
        result["access_message"] = str(exc) or "kol claimed by another staff"
        return result
    scan_result = None
    analysis_result = None
    if body.get("scan_account") or body.get("scan_if_missing"):
        max_posts = max(1, min(int(body.get("max_posts") or 24), 80))
        scan_result = await scan_kol_account(kol_id, max_posts=max_posts)
        if int(scan_result.get("content_count") or 0) > 0:
            analysis_result = await analyze_kol_account(kol_id, product_sku=str(body.get("product_sku") or ""))
    result["dossier"] = get_kol_dossier(kol_id)
    if scan_result is not None:
        result["scan_result"] = scan_result
    if analysis_result is not None:
        result["analysis_result"] = analysis_result
    return result


@router.get("/kols")
def list_kols(
    search: str = "",
    platform: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims.list_kols(search=search, platform=platform, staff_id=staff_id, limit=limit, staff=staff)


@router.get("/kols/{kol_id}/dossier")
def kol_dossier(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return get_kol_dossier(int(kol_id))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kols/{kol_id}/profile")
def kol_profile(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        result = kol_claims.profile(int(kol_id), staff=staff)
        try:
            result["dossier"] = get_kol_dossier(int(kol_id))
        except Exception:
            result["dossier"] = {}
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/assessment")
def kol_assessment(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return _assessment_payload(int(kol_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/product-fit")
def kol_product_fit(
    kol_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        try:
            kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        except LookupError:
            return _product_fit_preview_payload_for_pool(int(kol_id), limit)
        return _product_fit_payload(int(kol_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/contacts")
def kol_contacts(
    kol_id: int,
    include_wrong: bool = False,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return _contact_rows(int(kol_id), include_wrong=include_wrong)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/contacts")
def add_kol_contact(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    contact_type = str(body.get("contact_type") or body.get("type") or "").strip().lower()
    contact_value = str(body.get("contact_value") or body.get("value") or "").strip()
    if not contact_type or not contact_value:
        raise HTTPException(status_code=400, detail="contact_type and contact_value required")
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        ctx = _latest_kol_context(int(kol_id))
        kol = ctx["kol"]
        links = _json_loads(kol.get("contact_links_json"), [])
        if not isinstance(links, list):
            links = []
        existing_values = {
            str(item.get("value") or item.get("url") or "").strip().lower()
            for item in links
            if isinstance(item, dict)
        }
        payload: dict[str, Any] = {
            "contact_links": links,
            "notes": str(body.get("evidence") or body.get("note") or "").strip(),
        }
        if contact_type in {"email", "manager_email"} and "@" in contact_value:
            payload["contact_email"] = contact_value
        elif contact_type in {"phone", "whatsapp"}:
            payload["contact_phone"] = contact_value
        if contact_value.lower() not in existing_values:
            payload["contact_links"] = [
                *links,
                {
                    "label": contact_type,
                    "value": contact_value,
                    "url": contact_value if contact_value.startswith("http") else "",
                    "layer": _int(body.get("layer"), 5),
                    "source": str(body.get("source") or "manual_input"),
                    "confidence": _int(body.get("confidence"), 100),
                    "evidence": str(body.get("evidence") or ""),
                    "verified": True,
                    "status": "active",
                },
            ]
        kol_claims.update_kol_manual(int(kol_id), payload, staff=staff)
        return _contact_rows(int(kol_id), include_wrong=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.update_kol_manual(int(kol_id), body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/scan-account")
async def scan_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await scan_kol_account(int(kol_id), max_posts=max(1, min(int(payload.get("max_posts") or 24), 80)))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/analyze-account")
async def analyze_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        kol_claims.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await analyze_kol_account(int(kol_id), product_sku=str(payload.get("product_sku") or ""), snapshot_id=payload.get("snapshot_id"))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/claims")
def list_claims(
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims.list_claims(status=status, limit=limit, staff=staff)


@router.post("/kols/{kol_id}/claim")
def claim_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.claim(kol_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/claims/{claim_id}/release")
def release_claim(claim_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims.release(claim_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/claims/{claim_id}/reassign")
def reassign_claim(claim_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return kol_claims.reassign(claim_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/links")
def links(
    status: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return attribution_domain.list_links(limit=limit, status=status, staff=staff, staff_id=staff_id)


@router.get("/links/{link_id}")
def link_detail(link_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return attribution_domain.link_detail(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/links/{link_id}/clicks")
def link_clicks(
    link_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return attribution_domain.link_clicks(link_id, staff=staff, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/links/{link_id}/orders")
def link_orders(
    link_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return attribution_domain.link_orders(link_id, staff=staff, limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links")
def create_link(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution_domain.create_link(body, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/links/{link_id}")
def update_link(link_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution_domain.update_link(link_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/pause")
def pause_link(link_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution_domain.pause_link(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/archive")
def archive_link(link_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution_domain.archive_link(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/links/{link_id}/health-check")
def check_link(link_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return attribution_domain.health_check(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
