"""V-KPI message, content, terms, deliverable, shipment, and sample routes."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.api.dependencies.perms import require_tab
from app.core.config import UPLOAD_DIR
from app.core.logging import get_logger
from app.domains import evidence
from app.domains.access import scope

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-evidence-assets"])
EVIDENCE_UPLOAD_DIR = UPLOAD_DIR / "vkpi_evidence"
SAFE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".csv", ".xlsx", ".xls", ".txt", ".doc", ".docx"}
MAX_EVIDENCE_UPLOAD_BYTES = 25 * 1024 * 1024
logger = get_logger(__name__)


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


def _safe_name(name: str) -> str:
    stem = Path(name or "evidence").stem[:80] or "evidence"
    ext = Path(name or "").suffix.lower()
    clean_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "evidence"
    return clean_stem, ext


@router.post("/evidence/uploads")
async def upload_evidence_file(
    file: UploadFile = File(...),
    entity_type: str = Form(default="manual"),
    entity_id: str = Form(default=""),
    purpose: str = Form(default="note"),
    staff=Depends(require_tab("vkpi", "write")),
):
    filename = file.filename or "evidence"
    clean_stem, ext = _safe_name(filename)
    if ext and ext not in SAFE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="unsupported evidence file type")
    day = datetime.utcnow().strftime("%Y%m%d")
    target_dir = EVIDENCE_UPLOAD_DIR / day
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}-{clean_stem}{ext or '.bin'}"
    target_path = target_dir / stored_name
    size = 0
    with target_path.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_EVIDENCE_UPLOAD_BYTES:
                try:
                    target_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("failed to delete oversize evidence upload %s: %s", target_path, exc)
                raise HTTPException(status_code=413, detail="evidence file too large")
            out.write(chunk)
    return {
        "file_url": f"/uploads/vkpi_evidence/{day}/{stored_name}",
        "file_name": filename,
        "file_type": file.content_type or ext.lstrip(".") or "application/octet-stream",
        "size": size,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "purpose": purpose,
    }
@router.get("/messages")
def list_messages(
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return evidence.list_messages(project_id=project_id, kol_id=kol_id, staff_id=staff_id, limit=limit, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/messages")
def create_message(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.create_message(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/messages/{message_id}")
def get_message(message_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return evidence.get_message(message_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/messages/{message_id}/attachments")
def add_message_attachment(message_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.add_message_attachment(message_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/content")
def list_content(
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return evidence.list_content(project_id=project_id, kol_id=kol_id, staff_id=staff_id, limit=limit, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/content")
def create_content(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.create_content(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/content/{post_id}")
def get_content(post_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return evidence.get_content(post_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/content/{post_id}/assets")
def add_content_asset(post_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.add_content_asset(post_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/terms")
def list_terms(
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return evidence.list_terms(project_id=project_id, kol_id=kol_id, staff_id=staff_id, limit=limit, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/terms")
def upsert_terms(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.upsert_terms(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/terms/{terms_id}")
def get_terms(terms_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return evidence.get_terms(terms_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/deliverables")
def add_deliverable(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.add_deliverable(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/deliverables/{deliverable_id}")
def update_deliverable(deliverable_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.update_deliverable(deliverable_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/shipments")
def list_shipments(
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return evidence.list_shipments(project_id=project_id, kol_id=kol_id, staff_id=staff_id, limit=limit, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/shipments")
def create_shipment(body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.create_shipment(body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/shipments/{shipment_id}")
def get_shipment(shipment_id: int, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return evidence.get_shipment(shipment_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.patch("/shipments/{shipment_id}")
def update_shipment(shipment_id: int, body: dict, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.update_shipment(shipment_id, body, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.post("/shipments/{shipment_id}/receive")
def receive_shipment(shipment_id: int, body: dict | None = None, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return evidence.mark_shipment_received(shipment_id, body or {}, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/samples")
def list_samples(
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return evidence.list_samples(project_id=project_id, kol_id=kol_id, staff_id=staff_id, limit=limit, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
