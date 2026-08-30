"""V-KPI KOL lifecycle, claim, and link center routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.domains import attribution as attribution_domain
from app.domains import kol as kol_domain
from app.domains.access import scope
from app.domains.kol import lookup_recovery
from app.domains.kol.claim_access import assert_kol_access
from app.services.kol.account_dossier import list_kol_comments, list_kol_posts
from app.services.kol.account_dossier_adapter import DEFAULT_ACCOUNT_DOSSIER_ADAPTER

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-links"])


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


@router.post("/kol/search/natural")
def natural_kol_search(body: dict, staff=Depends(require_tab("vkpi", "read"))):
    return kol_domain.natural_search_payload(body or {}, staff=staff)


@router.post("/kols/lookup")
async def lookup_kol(request: Request, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    """Resolve a KOL and optionally scan/analyze.

    The response carries ``search_session_id`` (and ``task_id``) so the client
    can re-fetch the durable result via ``GET /kols/lookup/sessions/{id}`` after
    page navigation, and the run shows up on the 任务进度 board with live stages.
    """
    try:
        payload = dict(body or {})
        scan_requested = bool(payload.get("scan_account") or payload.get("scan_if_missing"))
        if scan_requested and getattr(request.app.state, "job_queue", None) is None:
            raise HTTPException(status_code=503, detail="durable job queue unavailable")
        # Identity + stored dossier are immediate. Provider scan/analyze is a
        # progressive follow-up so a lookup request cannot hold a paid actor.
        if scan_requested:
            payload["scan_account"] = False
            payload["scan_if_missing"] = False
        result = await kol_domain.lookup_with_context(
            payload,
            staff=staff,
            dossier_port=DEFAULT_ACCOUNT_DOSSIER_ADAPTER,
        )
        kol = result.get("kol") if isinstance(result.get("kol"), dict) else {}
        kol_id = int(kol.get("id") or 0)
        if scan_requested and kol_id:
            task_id = await request.app.state.job_queue.enqueue(
                "kol_dossier_scan",
                {
                    "kol_id": kol_id,
                    "max_posts": max(1, min(int(body.get("max_posts") or 24), 80)),
                    "staff": dict(staff or {}),
                    "product_sku": str(body.get("product_sku") or ""),
                    "surface": "kol_lookup",
                },
                lock_key=f"kol_dossier_scan:{kol_id}",
                timeout_seconds=1800,
            )
            result["scan_result"] = {"status": "queued", "job_id": task_id, "progressive": True}
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kols/lookup/sessions/{session_id}")
def lookup_session_recovery(session_id: int, staff=Depends(require_tab("vkpi", "read"))):
    """Recover a lookup's durable terminal state/results after navigation.

    Returns the search session (status ready/failed/partial, reason, summary,
    items) plus the matching ``kol_lookup`` ledger stage so the UI can rehydrate
    a result that out-lived the original request.
    """
    try:
        return lookup_recovery.recover_session(int(session_id), staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols")
def list_kols(
    search: str = "",
    platform: str = "",
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_domain.list_kols(search=search, platform=platform, staff_id=staff_id, limit=limit, staff=staff)


@router.get("/kols/{kol_id}/dossier")
def kol_dossier(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return kol_domain.dossier_for_request(
            int(kol_id),
            staff=staff,
            dossier_port=DEFAULT_ACCOUNT_DOSSIER_ADAPTER,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kols/{kol_id}/posts")
def kol_posts(
    kol_id: int,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return list_kol_posts(int(kol_id), limit=limit, offset=offset, prefer_main_id=True)


@router.get("/kols/{kol_id}/comments")
def kol_comments(
    kol_id: int,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    post_url: str = "",
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return list_kol_comments(int(kol_id), limit=limit, offset=offset, post_url=post_url, prefer_main_id=True)


@router.get("/kols/{kol_id}/profile")
def kol_profile(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return kol_domain.profile_with_dossier(
            int(kol_id),
            staff=staff,
            dossier_port=DEFAULT_ACCOUNT_DOSSIER_ADAPTER,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/kols/{kol_id}/assessment")
def kol_assessment(kol_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return kol_domain.assessment_for_request(int(kol_id), staff=staff)
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
        return kol_domain.product_fit_for_request(int(kol_id), limit=limit, staff=staff)
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
        return kol_domain.contact_rows_for_request(int(kol_id), include_wrong=include_wrong, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/contacts")
def add_kol_contact(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_domain.add_contact(int(kol_id), body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_domain.update_kol_manual(int(kol_id), body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/scan-account")
async def scan_kol(
    request: Request,
    kol_id: int,
    body: dict | None = None,
    staff=Depends(require_tab("vkpi", "write")),
):
    payload = body or {}
    try:
        assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
        queue = getattr(request.app.state, "job_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="durable job queue unavailable")
        task_id = await queue.enqueue(
            "kol_dossier_scan",
            {
                "kol_id": int(kol_id),
                "max_posts": max(1, min(int(payload.get("max_posts") or 24), 80)),
                "staff": dict(staff or {}),
                "surface": "vkpi_kol_links",
            },
            lock_key=f"kol_dossier_scan:{int(kol_id)}",
            timeout_seconds=1800,
        )
        return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/kols/{kol_id}/analyze-account")
async def analyze_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    payload = body or {}
    try:
        return await kol_domain.analyze_account_for_request(
            int(kol_id),
            product_sku=str(payload.get("product_sku") or ""),
            snapshot_id=payload.get("snapshot_id"),
            staff=staff,
            dossier_port=DEFAULT_ACCOUNT_DOSSIER_ADAPTER,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/claims")
def list_claims(
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return kol_domain.list_claims(status=status, limit=limit, staff=staff)


@router.post("/kols/{kol_id}/claim")
def claim_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_domain.claim(kol_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/claims/{claim_id}/release")
def release_claim(claim_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return kol_domain.release(claim_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/claims/{claim_id}/reassign")
def reassign_claim(claim_id: int, body: dict, staff=Depends(require_tab("vkpi", "admin"))):
    try:
        return kol_domain.reassign(claim_id, body, staff=staff)
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
def check_link(link_id: int, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return attribution_domain.health_check(link_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
