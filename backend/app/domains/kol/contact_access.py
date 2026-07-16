"""Permission-aware projection and audit boundary for KOL contacts."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.core.permissions import check_contact_reveal_permission
from app.domains.kol.pool_common import _mask_contact_record, _mask_email

logger = get_logger(__name__)

_EMAIL_KEYS = {"email", "contact_email", "business_email", "public_email", "manager_email"}
_PHONE_KEYS = {"phone", "contact_phone", "phone_number", "mobile", "whatsapp"}


def _mask_email_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_mask_email(item) for item in value]
    return _mask_email(value)


def _staff_id(staff: dict[str, Any] | None) -> int:
    context = staff if isinstance(staff, dict) else {}
    try:
        return int(context.get("staff_id") or context.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def authorize_plaintext_contacts(
    staff: dict[str, Any] | None,
    *,
    resource_type: str,
    resource_id: str | int,
    page_path: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Authorize and audit a plaintext contact read.

    Missing legacy permissions, missing staff identity, and audit failures all
    fail closed.  Callers must use the returned boolean to choose their DTO
    projection; permission alone is not enough to disclose plaintext.
    """
    if not check_contact_reveal_permission(staff):
        return False
    actor = _staff_id(staff)
    if not actor:
        return False
    try:
        from app.domains.audit.service import log_sensitive_access

        result = log_sensitive_access(
            staff_id=actor,
            action_type="view_kol_contact",
            resource_type=str(resource_type or "kol"),
            resource_id=str(resource_id),
            page_path=str(page_path or ""),
            metadata={"contact_plaintext": True, **(metadata or {})},
        )
    except Exception:
        logger.warning("kol contact audit failed; returning masked projection", exc_info=True)
        return False
    return bool(isinstance(result, dict) and result.get("status") == "logged" and result.get("id"))


def project_contact_rows(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Project the ``/kols/{id}/contacts`` response without mutating input."""
    result = dict(payload or {})
    contacts = result.get("contacts")
    if not isinstance(contacts, list):
        return result
    rows = contacts
    result["contacts"] = [dict(row) for row in rows] if reveal else [_mask_contact_record(row) for row in rows]
    result["contact_masked"] = not reveal
    return result


def mask_contact_payload(value: Any, *, contact_type: str = "") -> Any:
    """Recursively redact known contact fields in search/cache DTOs."""
    if isinstance(value, list):
        return [mask_contact_payload(item, contact_type=contact_type) for item in value]
    if not isinstance(value, dict):
        return value
    current_type = str(value.get("contact_type") or value.get("type") or contact_type or "")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.lower()
        if lowered in _EMAIL_KEYS or lowered.endswith("_emails") or lowered == "contact_emails":
            result[key] = _mask_email_value(raw_value)
        elif lowered in _PHONE_KEYS:
            result[key] = _mask_contact_record(raw_value, contact_type="phone")
        elif lowered == "contact_value":
            result[key] = _mask_contact_record(raw_value, contact_type=current_type)
        elif lowered in {"contact_channels", "other_contacts", "contacts"}:
            result[key] = _mask_contact_record(raw_value, contact_type=current_type)
        else:
            result[key] = mask_contact_payload(raw_value, contact_type=current_type)
    return result


def project_profile_contacts(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Mask KOL contact fields embedded in the larger profile response."""
    result = dict(payload or {})
    if isinstance(result.get("kol"), dict):
        kol = dict(result["kol"])
        if not reveal:
            for key in ("contact_email", "email"):
                if kol.get(key):
                    kol[key] = _mask_email(kol.get(key))
            for key in ("contact_phone", "phone"):
                if kol.get(key):
                    kol[key] = _mask_contact_record(kol.get(key), contact_type="phone")
            for key in ("contact_links_json", "contact_raw_json", "contact_channels", "other_contacts_json"):
                if kol.get(key):
                    kol[key] = _mask_contact_record(kol.get(key))
        kol["contact_masked"] = not reveal
        result["kol"] = kol

    if isinstance(result.get("contacts"), dict):
        contacts = dict(result["contacts"])
        profile_url = contacts.get("profile_url")
        if not reveal:
            contacts = _mask_contact_record(contacts)
            if profile_url:
                contacts["profile_url"] = profile_url
        contacts["contact_masked"] = not reveal
        result["contacts"] = contacts
    if not reveal and isinstance(result.get("dossier"), dict):
        result["dossier"] = mask_contact_payload(result["dossier"])
    return result


def project_pool_contact_write(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Project manual pool-contact write responses, which contain a contact list."""
    result = dict(payload or {})
    if not reveal and isinstance(result.get("contacts"), list):
        result["contacts"] = [_mask_contact_record(item) for item in result["contacts"]]
    result["contact_masked"] = not reveal
    return result


def project_email_status(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    result = dict(payload or {})
    if not reveal and result.get("email"):
        result["email"] = _mask_email(result.get("email"))
    result["contact_masked"] = not reveal
    return result
