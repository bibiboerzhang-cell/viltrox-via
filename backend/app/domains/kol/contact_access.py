"""Permission-aware projection and audit boundary for KOL contacts."""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger
from app.core.permissions import check_contact_reveal_permission

logger = get_logger(__name__)

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
    ip: str = "",
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
    permission_check: Callable[[dict[str, Any] | None], bool] | None = None,
) -> bool:
    """Authorize and audit a plaintext contact read.

    Missing legacy permissions, missing staff identity, and audit failures all
    fail closed.  Callers must use the returned boolean to choose their DTO
    projection; permission alone is not enough to disclose plaintext.
    """
    predicate = permission_check or check_contact_reveal_permission
    if not predicate(staff):
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
            ip=str(ip or ""),
            user_agent=str(user_agent or "")[:500],
            metadata={"contact_plaintext": True, **(metadata or {})},
        )
    except Exception:
        logger.warning("kol contact audit failed; returning masked projection", exc_info=True)
        return False
    return bool(isinstance(result, dict) and result.get("status") == "logged" and result.get("id"))


def _value_free(payload: Any) -> Any:
    from app.domains.kol.contact_system import value_free_contact_projection

    return value_free_contact_projection(payload)


def project_contact_rows(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Keep legacy contact routes value-free; ``reveal`` cannot bypass POST."""
    del reveal
    result = _value_free(dict(payload or {}))
    result["contacts"] = []
    result["contact_masked"] = True
    result["contact_projection_reason"] = "summary_only"
    return result


def mask_contact_payload(value: Any, *, contact_type: str = "") -> Any:
    """Compatibility name for the value-free legacy/cache DTO boundary."""
    del contact_type
    return _value_free(value)


def project_profile_contacts(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Project legacy profile/dossier responses without any contact value."""
    del reveal
    source = dict(payload or {})
    raw_contacts = source.get("contacts") if isinstance(source.get("contacts"), dict) else {}
    profile_url = ""
    if raw_contacts.get("profile_url"):
        from app.domains.kol.contact_system import project_public_profile_url

        profile_url = project_public_profile_url(raw_contacts.get("profile_url"))
    result = _value_free(source)
    result["contacts"] = {
        "profile_url": profile_url,
        "contact_masked": True,
        "contact_projection_reason": "summary_only",
    }
    result["contact_masked"] = True
    result["contact_projection_reason"] = "summary_only"
    return result


def project_pool_contact_write(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    """Manual writes acknowledge state but never echo a contact value."""
    del reveal
    result = _value_free(dict(payload or {}))
    result["contacts"] = []
    result["contact_masked"] = True
    result["contact_projection_reason"] = "summary_only"
    return result


def project_email_status(payload: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    del reveal
    result = _value_free(dict(payload or {}))
    result["contact_masked"] = True
    result["contact_projection_reason"] = "summary_only"
    return result
