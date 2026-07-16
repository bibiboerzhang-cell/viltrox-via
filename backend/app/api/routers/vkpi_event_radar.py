"""Source-backed external Event Radar API.

Read endpoints expose reviewed external opportunities.  Writes are limited to
explicit catalog import, human decision, and atomic promotion to ``vkpi_events``.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.events import candidate_staging, radar, radar_quality, us_coverage_registry


logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin/vkpi/event-radar", tags=["vkpi-event-radar"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "not found") from exc
    except candidate_staging.CandidateStagingStateConflict as exc:
        raise HTTPException(
            status_code=409, detail=str(exc) or "candidate staging state conflict"
        ) from exc
    except candidate_staging.CandidateStagingSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except radar.EventRadarStateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc) or "event radar state conflict") from exc
    except radar.EventRadarSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=exc.code) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "event radar scope denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("event radar endpoint failed", exc_info=True)
        raise HTTPException(status_code=500, detail="internal event radar error") from exc


def _organization_id(staff) -> int:
    return int(_guard(radar.organization_id_for_staff, staff))


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


@router.get("")
def list_event_radar(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    lane: str | None = Query(default=None),
    source_kind: str | None = Query(default=None, max_length=40),
    decision_status: str | None = Query(default=None),
    evidence_status: str | None = Query(default=None, max_length=20),
    time_window: str | None = Query(default=None, max_length=20),
    country: str | None = Query(default=None, min_length=2, max_length=2),
    region: str | None = Query(default=None, min_length=2, max_length=2),
    include_past: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
):
    organization_id = _organization_id(staff)
    return _guard(
        radar.list_opportunities,
        limit=limit,
        offset=offset,
        lane=lane,
        source_kind=source_kind,
        decision_status=decision_status,
        evidence_status=evidence_status,
        time_window=time_window,
        country=country,
        region=region,
        include_past=include_past,
        organization_id=organization_id,
    )


@router.get("/summary")
def event_radar_summary(staff=Depends(require_tab("vkpi", "read"))):
    return _guard(radar.summary, organization_id=_organization_id(staff))


@router.get("/source-health")
def event_radar_source_health(
    limit: int = Query(default=200, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    del staff
    return _guard(radar.source_health, limit=limit)


@router.get("/us-source-registry")
def event_radar_us_source_registry(staff=Depends(require_tab("vkpi", "read"))):
    """Return disabled US Event discovery sources without crawling or writes.

    These source registrations are a review queue, not proof of nationwide
    completeness or Viltrox participation in any listed activity.
    """
    del staff
    return _guard(us_coverage_registry.event_registry)


@router.get("/candidate-staging")
def event_candidate_staging_summary(staff=Depends(require_tab("vkpi", "read"))):
    """Read the current workspace's Event candidate queue without raw payloads."""
    return _guard(
        candidate_staging.staging_summary,
        organization_id=_organization_id(staff),
        candidate_type="event_opportunity",
    )


@router.post("/candidate-staging/preview")
def event_candidate_staging_preview(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Validate one Event candidate envelope; never persist or promote it."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    return _guard(
        candidate_staging.preview_candidate,
        body,
        organization_id=_organization_id(staff),
        candidate_type="event_opportunity",
    )


@router.get("/candidate-staging/items")
def event_candidate_staging_items(
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
        candidate_type="event_opportunity",
        review_status=review_status,
        promotion_gate_status=promotion_gate_status,
        include_payload=include_payload,
        offset=offset,
        limit=limit,
    )


@router.post("/candidate-staging")
def event_candidate_stage(
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
        candidate_type="event_opportunity",
    )


@router.get("/candidate-staging/{candidate_id}")
def event_candidate_staging_detail(
    candidate_id: str,
    staff=Depends(require_manager_tab("vkpi", "read")),
):
    """Manager-only raw candidate/evidence detail, scoped to this workspace."""
    require_manager_staff(staff)
    return _guard(
        candidate_staging.get_candidate,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="event_opportunity",
    )


@router.post("/candidate-staging/{candidate_id}/evidence-links")
def event_candidate_link_evidence(
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
        candidate_type="event_opportunity",
        field_evidence_id=body.get("field_evidence_id"),
        evidence_role=body.get("evidence_role"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.patch("/candidate-staging/{candidate_id}/review")
def event_candidate_review(
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
        candidate_type="event_opportunity",
        decision=body.get("decision"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.post("/candidate-staging/{candidate_id}/manual-promotion-receipt")
def event_candidate_manual_promotion_receipt(
    candidate_id: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Record an exact existing Event target; this never creates/promotes it."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    _bounded_body(body, {"target_id"})
    require_manager_staff(staff)
    return _guard(
        candidate_staging.record_manual_promotion_receipt,
        candidate_id,
        organization_id=_organization_id(staff),
        candidate_type="event_opportunity",
        target_id=body.get("target_id"),
        reviewer_staff_id=_staff_id(staff),
    )


@router.get("/quality-audit")
def event_radar_quality_audit(staff=Depends(require_tab("vkpi", "read"))):
    """Combined Event/Dealer quality evidence, computed without I/O or writes."""
    del staff
    return _guard(radar.quality_audit)


@router.get("/remediation-queue")
def event_radar_remediation_queue(
    scope: str | None = Query(default=None, max_length=40),
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
    """Bounded preview-only task view; never probes sources, SQL, or workers."""
    del staff
    queue = _guard(radar.remediation_queue)
    return _guard(
        radar_quality.query_remediation_queue,
        queue,
        scope=scope,
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


@router.post("/refresh-preview")
def event_radar_refresh_preview(staff=Depends(require_tab("vkpi", "read"))):
    del staff
    return _guard(radar.preview_reviewed_catalog)


@router.post("/refresh")
def event_radar_refresh(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    # Safe-by-default contract.  A caller must explicitly send record_only=false
    # to persist the bundled reviewed catalog; this endpoint does not crawl the web.
    persist = body.get("record_only") is False
    if not persist:
        # This is the same offline catalog validation as /refresh-preview.  It
        # remains available while migration 244 is pending and performs no SQL.
        return _guard(radar.import_reviewed_catalog, record_only=True)
    require_manager_staff(staff)
    return _guard(
        radar.import_reviewed_catalog,
        record_only=False,
        organization_id=_organization_id(staff),
    )


@router.get("/{opportunity_id}/changes")
def event_radar_changes(
    opportunity_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    return _guard(
        radar.list_changes,
        opportunity_id,
        limit=limit,
        organization_id=_organization_id(staff),
    )


@router.patch("/{opportunity_id}/decision")
def event_radar_decision(
    opportunity_id: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    require_manager_staff(staff)
    return _guard(
        radar.decide,
        opportunity_id,
        decision_status=str(body.get("decision_status") or ""),
        note=str(body.get("note") or ""),
        staff=staff,
    )


@router.post("/{opportunity_id}/promote")
def event_radar_promote(
    opportunity_id: str,
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    require_manager_staff(staff)
    return _guard(radar.promote, opportunity_id, staff=staff)


__all__ = ["router"]
