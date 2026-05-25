"""V-KPI KOL lifecycle, claim, and link center routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.domains import attribution as attribution_domain
from app.domains.kol import account as kol_account_domain
from app.domains.kol import claims as kol_claims_domain
from app.domains.kol import contacts as kol_contacts_domain
from app.domains.kol import lookup as kol_lookup_domain
from app.services.vkpi import scope
from app.domains.kol.natural_search import _natural_search_payload
from app.domains.kol.profile_payloads import _assessment_payload, _product_fit_payload

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-links"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.post("/kol/search/natural")
def natural_kol_search(body: dict, staff=Depends(require_tab("vkpi", "read"))):
    return _natural_search_payload(body or {}, staff=staff)


@router.post("/kols/lookup")
async def lookup_kol(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return await kol_lookup_domain.lookup_with_context(body or {}, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kols")
def list_kols(
    search: str = "",
    platform: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims_domain.list_kols(search=search, platform=platform, staff_id=staff_id, limit=limit, staff=staff)


@router.get("/kols/{kol_id}/dossier")
def kol_dossier(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return kol_account_domain.get_dossier(int(kol_id))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kols/{kol_id}/profile")
def kol_profile(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        result = kol_claims_domain.profile(int(kol_id), staff=staff)
        try:
            result["dossier"] = kol_account_domain.get_dossier(int(kol_id))
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
        kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
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
            kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
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
        kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return kol_contacts_domain.contact_rows(int(kol_id), include_wrong=include_wrong)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/contacts")
def add_kol_contact(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_contacts_domain.add_contact(int(kol_id), body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims_domain.update_kol_manual(int(kol_id), body, staff=staff)
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
        kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await kol_account_domain.scan_account(int(kol_id), max_posts=max(1, min(int(payload.get("max_posts") or 24), 80)))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/analyze-account")
async def analyze_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        kol_claims_domain.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        return await kol_account_domain.analyze_account(int(kol_id), product_sku=str(payload.get("product_sku") or ""), snapshot_id=payload.get("snapshot_id"))
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/claims")
def list_claims(
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_claims_domain.list_claims(status=status, limit=limit, staff=staff)


@router.post("/kols/{kol_id}/claim")
def claim_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims_domain.claim(kol_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/claims/{claim_id}/release")
def release_claim(claim_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_claims_domain.release(claim_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/claims/{claim_id}/reassign")
def reassign_claim(claim_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return kol_claims_domain.reassign(claim_id, body, staff=staff)
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
