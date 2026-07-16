"""V-KPI 经销商地图(Dealer Map)路由 —— 美国相机零售商地理数据源。

前缀 /api/admin/vkpi/dealers;读 require_tab("vkpi","read")、写 require_tab("vkpi","write"),
镜像 vkpi_shopify / vkpi_inventory 的鉴权风格。
- GET  /api/admin/vkpi/dealers           列出经销商(limit / state 过滤)。
- GET  /api/admin/vkpi/dealers/locations  扁平 pin 数组(仅 lat/lng 齐全),点亮前端
  Dealers 地图模式(viewModes.dealers.apiEndpoint 已指向本路径,available:false 待接入)。
- POST /api/admin/vkpi/dealers/scrape-enqueue  有界触发(record_only 默认 = 纯预检,no blast)。

纯地理数据,无 touches_v6_fit;与 KOL Pool / 评分域物理隔离,绝不碰 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.commerce import dealer_directory_adapters, dealer_scrape
from app.domains.events import (
    candidate_staging,
    dealer_activity_view,
    radar_quality,
    us_coverage_registry,
)
from app.shared.us_jurisdiction_coverage import US_STATE_AND_DC_CODES


logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin/vkpi/dealers", tags=["vkpi-dealers"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "not found") from exc
    except dealer_scrape.DealerAlreadyExists as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "dealer_already_exists",
                "message": str(exc),
                "dealer_id": exc.dealer_id,
            },
        ) from exc
    except candidate_staging.CandidateStagingStateConflict as exc:
        raise HTTPException(
            status_code=409, detail=str(exc) or "candidate staging state conflict"
        ) from exc
    except candidate_staging.CandidateStagingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("dealer endpoint failed", exc_info=True)
        raise HTTPException(status_code=500, detail="internal dealer error") from exc


def _organization_id(staff) -> int:
    try:
        value = int((staff or {}).get("organization_id") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="organization scope is unresolved") from exc
    if value <= 0:
        raise HTTPException(status_code=403, detail="organization scope is unresolved")
    return value


def _staff_id(staff) -> int:
    try:
        value = int((staff or {}).get("id") or (staff or {}).get("staff_id") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=403, detail="staff identity is unresolved") from exc
    if value <= 0:
        raise HTTPException(status_code=403, detail="staff identity is unresolved")
    return value


def _bounded_body(body: dict, allowed: set[str]) -> None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="unsupported request fields: " + ", ".join(unknown),
        )


def _query_value(value):
    """Resolve FastAPI Query defaults during direct unit-level route calls."""
    return getattr(value, "default", value)


@router.get("")
def list_dealers_route(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100000),
    state: str | None = Query(default=None, max_length=2),
    city: str | None = Query(default=None, max_length=120),
    channel: str = Query(default="all", max_length=40),
    evidence_status: str = Query(default="all", max_length=40),
    product_evidence: str = Query(default="all", max_length=40),
    authorization: str = Query(default="all", max_length=40),
    brand: str | None = Query(default=None, max_length=64),
    published_only: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List dealers {dealers:[{id,name,address,city,state,lat,lng,source,created_at}]}."""
    brand = _query_value(brand)
    published_only = bool(_query_value(published_only))
    normalized_state = str(state or "").strip().upper() or None
    if normalized_state and normalized_state not in US_STATE_AND_DC_CODES:
        raise HTTPException(status_code=400, detail="state must be a US state/DC code")
    list_filters = {
        "limit": limit,
        "offset": offset,
        "state": normalized_state,
        "city": city,
        "channel": channel,
        "evidence_status": evidence_status,
        "product_evidence": product_evidence,
        "authorization": authorization,
    }
    if brand is not None:
        list_filters["brand"] = brand
    if published_only:
        list_filters["published_only"] = True
    dealers = _guard(dealer_scrape.list_dealers, **list_filters)
    count_filters = {
        "state": normalized_state,
        "city": city,
        "channel": channel,
        "evidence_status": evidence_status,
        "product_evidence": product_evidence,
        "authorization": authorization,
    }
    if brand is not None:
        count_filters["brand"] = brand
    if published_only:
        count_filters["published_only"] = True
    total_count = _guard(dealer_scrape.count_dealers, **count_filters)
    # Advance by the requested SQL page width, not the returned count. This
    # keeps pagination monotonic even if a legacy row is removed by the final
    # fail-closed projection check.
    next_offset = min(offset + limit, total_count)
    return {
        "dealers": dealers,
        "count": len(dealers),
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "page": {
            "limit": limit,
            "offset": offset,
            "returned": len(dealers),
            "next_offset": next_offset if next_offset < total_count else None,
            "has_more": next_offset < total_count,
        },
        "truth_boundaries": {
            "public_listing_proves_viltrox_authorization": False,
            "product_page_proves_current_inventory": False,
            "candidate_rows_appear_as_verified_map_pins": False,
        },
    }


@router.get("/locations")
def dealer_locations_route(
    state: str | None = Query(default=None, max_length=2),
    city: str | None = Query(default=None, max_length=120),
    channel: str = Query(default="all", max_length=40),
    evidence_status: str = Query(default="all", max_length=40),
    product_evidence: str = Query(default="all", max_length=40),
    authorization: str = Query(default="all", max_length=40),
    brand: str | None = Query(default=None, max_length=64),
    published_only: bool = Query(default=True),
    bbox: str | None = Query(default=None, max_length=120),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Flat pin array (lat/lng-present only) for the Dealers map mode.

    Returns {pins:[{name,address,city,state,lat,lng,color}]}.
    """
    brand = _query_value(brand)
    published_only = bool(_query_value(published_only))
    bbox = _query_value(bbox)
    normalized_state = str(state or "").strip().upper() or None
    if normalized_state and normalized_state not in US_STATE_AND_DC_CODES:
        raise HTTPException(status_code=400, detail="state must be a US state/DC code")
    normalized_bbox = _guard(dealer_scrape.parse_bbox, bbox)
    pins = _guard(
        dealer_scrape.list_dealer_pins,
        state=normalized_state,
        city=city,
        channel=channel,
        evidence_status=evidence_status,
        product_evidence=product_evidence,
        authorization=authorization,
        brand=brand,
        published_only=published_only,
        bbox=normalized_bbox,
    )
    return {
        "pins": pins,
        "count": len(pins),
        "evidence_status": evidence_status,
        "brand": str(brand or "").strip().lower() or None,
        "published_only": published_only,
        "bbox": list(normalized_bbox) if normalized_bbox is not None else None,
        "candidate_rows_excluded": evidence_status == "public_listing_verified",
        "manual_published_candidates_are_truth_labeled": True,
    }


@router.get("/coverage")
def dealer_coverage_route(
    stale_after_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Describe coverage of registered Dealer rows without a global claim.

    ``organization_id`` comes only from the authenticated staff context.  The
    response keeps public listing, exact identity/passport evidence and Viltrox
    authorization as separate counters.
    """
    organization_id = _organization_id(staff)
    return _guard(
        dealer_scrape.dealer_coverage_summary,
        organization_id=organization_id,
        stale_after_days=stale_after_days,
    )


@router.get("/us-source-registry")
def dealer_us_source_registry(staff=Depends(require_tab("vkpi", "read"))):
    """Return fail-closed manufacturer directories used only for discovery.

    Nikon, Canon, Sony, or FUJIFILM authorization never becomes Viltrox
    authorization, product presence, inventory, or sales evidence.
    """
    del staff
    report = _guard(us_coverage_registry.dealer_registry)
    adapters = _guard(dealer_directory_adapters.adapter_contracts)
    persistence = _guard(dealer_scrape.reviewed_persistence_status)
    return {
        **report,
        "contract": {
            **(report.get("contract") or {}),
            "database_accessed": True,
            "business_rows_written": 0,
        },
        "adapter_readiness": adapters.get("registry_adapter_readiness", {}),
        "adapter_source_readiness": adapters.get("source_readiness", []),
        "reviewed_persistence_readiness": persistence,
    }


@router.get("/candidate-staging")
def dealer_candidate_staging_summary(staff=Depends(require_tab("vkpi", "read"))):
    """Read the current workspace's Dealer candidate queue without raw payloads."""
    return _guard(
        candidate_staging.staging_summary,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
    )


@router.post("/candidate-staging/preview")
def dealer_candidate_staging_preview(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Validate one Dealer candidate envelope; never persist or promote it."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(
        candidate_staging.preview_candidate,
        body,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
    )


@router.get("/candidate-staging/items")
def dealer_candidate_staging_items(
    review_status: str | None = Query(default=None, max_length=24),
    promotion_gate_status: str | None = Query(default=None, max_length=40),
    include_payload: bool = Query(default=False),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    limit: int = Query(default=50, ge=1, le=100),
    staff=Depends(require_manager_tab("vkpi", "read")),
):
    """Bounded manager queue; raw candidate payload is opt-in and manager-only."""
    require_manager_staff(staff)
    return _guard(
        candidate_staging.list_candidates,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
        review_status=review_status,
        promotion_gate_status=promotion_gate_status,
        include_payload=include_payload,
        offset=offset,
        limit=limit,
    )


@router.post("/candidate-staging")
def dealer_candidate_stage(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Persist one candidate only after explicit ``record_only=false``."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    require_manager_staff(staff)
    return _guard(
        candidate_staging.stage_candidate,
        body,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
    )


@router.get("/candidate-staging/{candidate_id}")
def dealer_candidate_staging_detail(
    candidate_id: str,
    staff=Depends(require_manager_tab("vkpi", "read")),
):
    require_manager_staff(staff)
    return _guard(
        candidate_staging.get_candidate,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
    )


@router.post("/candidate-staging/{candidate_id}/evidence-links")
def dealer_candidate_link_evidence(
    candidate_id: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    _bounded_body(body, {"field_evidence_id", "evidence_role"})
    require_manager_staff(staff)
    return _guard(
        candidate_staging.link_field_evidence,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
        field_evidence_id=body.get("field_evidence_id"),
        evidence_role=body.get("evidence_role"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.patch("/candidate-staging/{candidate_id}/review")
def dealer_candidate_review(
    candidate_id: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    _bounded_body(body, {"decision"})
    require_manager_staff(staff)
    return _guard(
        candidate_staging.review_candidate,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
        decision=body.get("decision"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.post("/candidate-staging/{candidate_id}/manual-promotion-receipt")
def dealer_candidate_manual_promotion_receipt(
    candidate_id: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Record an exact existing Dealer target; this never creates/promotes it."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    _bounded_body(body, {"target_id"})
    require_manager_staff(staff)
    return _guard(
        candidate_staging.record_manual_promotion_receipt,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="dealer_location",
        target_id=body.get("target_id"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.get("/quality-audit")
def dealer_quality_audit_route(
    staff=Depends(require_tab("vkpi", "read")),
):
    """Offline/read-only evidence and import-readiness contract.

    The response never probes source sites or PostgreSQL and cannot be used as
    proof of global Dealer coverage, authorization, inventory, sales or impact.
    """
    del staff
    return _guard(dealer_scrape.reviewed_candidates_quality_audit)


@router.get("/remediation-queue")
def dealer_remediation_queue_route(
    entity_type: str | None = Query(default=None, max_length=80),
    field: str | None = Query(default=None, max_length=120),
    issue_code: str | None = Query(default=None, max_length=160),
    severity: str | None = Query(default=None, max_length=20),
    freshness_status: str | None = Query(default=None, max_length=40),
    due_status: str | None = Query(default=None, max_length=60),
    blocks_import: bool | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Bounded preview-only Dealer evidence tasks; no crawl, SQL, or mutation."""
    del staff
    queue = _guard(dealer_scrape.reviewed_candidates_remediation_queue)
    return _guard(
        radar_quality.query_remediation_queue,
        queue,
        scope="dealer",
        entity_type=entity_type,
        field=field,
        issue_code=issue_code,
        severity=severity,
        freshness_status=freshness_status,
        due_status=due_status,
        blocks_import=blocks_import,
        offset=offset,
        limit=limit,
    )


@router.post("/scrape-enqueue")
def scrape_enqueue_route(
    body=Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    """Bounded dealer scrape trigger (single batch <= 20).

    Body {source?, limit?(<=20), record_only?(default true)}. record_only=true returns
    a plan only — no network blast, no DB write. Returns
    {ok,source,requested,inserted,skipped,geocoded,pending_geocode,errors:[],...}.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    source = body.get("source")
    limit = body.get("limit", dealer_scrape._MAX_BATCH)
    persist = body.get("record_only") is False
    if persist:
        require_manager_staff(staff)
    return _guard(
        dealer_scrape.scrape_dealers_enqueue,
        limit=limit,
        record_only=False if persist else True,
        source=source,
    )


@router.post("")
def create_dealer_route(
    body=Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """手动新增单个经销商(按 name+address 幂等 upsert)。

    Body supports contact, website/social, brands, Viltrox deployment and
    activity metadata.  A manual row remains unverified even when a manager
    explicitly publishes it to the local map.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    require_manager_staff(staff)
    _bounded_body(
        body,
        {
            "name", "address", "city", "state", "country", "postal_code",
            "lat", "lng", "source", "location_source_url", "brand_listing_url",
            "phone", "contact_email", "store_hours", "public_services",
            "website_url", "social_links", "brands", "viltrox_deployment", "activity",
        },
    )
    name = str(body.get("name") or "").strip()
    address = str(body.get("address") or "").strip()
    if not name or not address:
        raise HTTPException(status_code=400, detail="name + address required")
    state = str(body.get("state") or "").strip().upper()
    if state and state not in US_STATE_AND_DC_CODES:
        raise HTTPException(status_code=400, detail="state must be a US state/DC code")
    payload = {
        "name": name,
        "address": address,
        "city": str(body.get("city") or "").strip(),
        "state": state,
        "lat": body.get("lat"),
        "lng": body.get("lng"),
        "source": str(body.get("source") or "manual_add"),
        "country": str(body.get("country") or "US").strip().upper(),
        "postal_code": str(body.get("postal_code") or "").strip() or None,
        "location_source_url": body.get("location_source_url"),
        "brand_listing_url": body.get("brand_listing_url"),
        "phone": str(body.get("phone") or "").strip() or None,
        "contact_email": str(body.get("contact_email") or "").strip() or None,
        "store_hours": str(body.get("store_hours") or "").strip() or None,
        "public_services": str(body.get("public_services") or "").strip() or None,
        "source_status": "unverified",
        "authorization_status": "needs_viltrox_confirmation",
        "verification_note": "Manual entry; public location, product-page presence, and Viltrox authorization have not been reviewed.",
        "_create_only": True,
    }
    managed_fields = {
        key: body[key]
        for key in (
            "website_url", "social_links", "brands", "viltrox_deployment", "activity"
        )
        if key in body
    }
    staff_id = _staff_id(staff)
    if managed_fields:
        _guard(
            dealer_scrape.validate_new_dealer_management_fields,
            managed_fields,
            actor_id=staff_id,
        )
        return _guard(
            dealer_scrape.create_managed_dealer,
            payload,
            managed_fields,
            actor_id=staff_id,
        )
    created = _guard(dealer_scrape.upsert_dealer, payload)
    dealer_id = created.get("id")
    if dealer_id in (None, ""):
        # A real upsert always returns an id.  Preserve the narrow fake seam
        # used by legacy unit tests instead of fabricating an entity.
        return created
    return _guard(dealer_scrape.get_dealer, dealer_id)


@router.get("/{dealer_id}/activities")
def dealer_activities_route(
    dealer_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    include_past: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Read exact Event Radar links for one Dealer; no fuzzy name matching."""
    return _guard(
        dealer_activity_view.list_dealer_activities,
        dealer_id,
        organization_id=_organization_id(staff),
        limit=limit,
        include_past=include_past,
    )


@router.get("/{dealer_id}")
def dealer_detail_route(
    dealer_id: int,
    staff=Depends(require_tab("vkpi", "read")),
):
    """Return one clickable map/detail record with bounded truth fields."""
    del staff
    return _guard(dealer_scrape.get_dealer, dealer_id)


@router.patch("/{dealer_id}")
def update_dealer_route(
    dealer_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Edit Dealer details, brands, Viltrox rollout and activity metadata."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    require_manager_staff(staff)
    _bounded_body(
        body,
        {
            "name", "address", "city", "state", "country", "postal_code",
            "lat", "lng", "source", "location_source_url", "brand_listing_url",
            "phone", "contact_email", "store_hours", "public_services",
            "website_url", "social_links", "verification_note", "brands",
            "viltrox_deployment", "activity",
        },
    )
    state = str(body.get("state") or "").strip().upper() if "state" in body else ""
    if state and state not in US_STATE_AND_DC_CODES:
        raise HTTPException(status_code=400, detail="state must be a US state/DC code")
    normalized = dict(body)
    if "state" in body:
        normalized["state"] = state or None
    return _guard(
        dealer_scrape.update_dealer,
        dealer_id,
        normalized,
        actor_id=_staff_id(staff),
    )


@router.post("/{dealer_id}/publish")
def publish_dealer_route(
    dealer_id: int,
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Publish one location to the local map; evidence status is unchanged."""
    require_manager_staff(staff)
    return _guard(
        dealer_scrape.set_dealer_publication,
        dealer_id,
        published=True,
        actor_id=_staff_id(staff),
    )


@router.post("/{dealer_id}/unpublish")
def unpublish_dealer_route(
    dealer_id: int,
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Remove one location from the local map without deleting its record."""
    require_manager_staff(staff)
    return _guard(
        dealer_scrape.set_dealer_publication,
        dealer_id,
        published=False,
        actor_id=_staff_id(staff),
    )
