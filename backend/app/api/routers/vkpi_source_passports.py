"""Authenticated Dealer/Event source-passport API (migration 248)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.access import scope
from app.domains import source_passport_store


logger = get_logger(__name__)
router = APIRouter(
    prefix="/api/admin/vkpi/source-passports",
    tags=["vkpi-source-passports"],
)


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except source_passport_store.SourcePassportSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("source passport endpoint failed", exc_info=True)
        raise HTTPException(status_code=500, detail="internal source passport error") from exc


def _context(staff) -> tuple[int, int]:
    organization_id = _guard(scope.event_organization_id, staff)
    actor_id = scope.actor_staff_id(staff)
    if actor_id <= 0:
        raise HTTPException(status_code=403, detail="staff identity required")
    return int(organization_id), int(actor_id)


@router.get("")
def source_passport_list(
    entity_type: str | None = Query(default=None, max_length=40),
    entity_key: str | None = Query(default=None, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """List current workspace passports; freshness is recomputed at read time."""
    organization_id, _actor_id = _context(staff)
    return _guard(
        source_passport_store.list_passports,
        organization_id=organization_id,
        entity_type=entity_type,
        entity_key=entity_key,
        offset=offset,
        limit=limit,
    )


@router.put("")
def source_passport_upsert(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Upsert one reviewed passport and append an immutable revision."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    organization_id, actor_id = _context(staff)
    return _guard(
        source_passport_store.save_passport,
        body,
        organization_id=organization_id,
        reviewer_staff_id=actor_id,
    )


@router.post("/field-evidence")
def source_field_evidence_append(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_manager_tab("vkpi", "write")),
):
    """Append one value-hash provenance record; identical records are idempotent."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    organization_id, actor_id = _context(staff)
    return _guard(
        source_passport_store.append_field_evidence,
        body,
        organization_id=organization_id,
        reviewer_staff_id=actor_id,
    )


@router.get("/{passport_id}/field-evidence")
def source_field_evidence_list(
    passport_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """Read append-only field provenance for one current-workspace passport."""
    organization_id, _actor_id = _context(staff)
    return _guard(
        source_passport_store.list_field_evidence,
        passport_id,
        organization_id=organization_id,
        offset=offset,
        limit=limit,
    )


__all__ = ["router"]
