"""Staff administration routes mounted under /api/admin/staff."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.legacy_scope import require_legacy_system_admin_scope
from app.api.dependencies.perms import require_system_permission, require_tab
from app.core.security import require_admin_async as require_admin
from app.services.audit_log import record_admin_action
from app.services.auth.email import email_service_available
from app.services.auth.tokens import _token_ttl
from app.services.system import staff as staff_svc

router = APIRouter()
public_staff_router = APIRouter(prefix="/staff")
legacy_staff_router = APIRouter(
    prefix="/staff",
    dependencies=[Depends(require_legacy_system_admin_scope)],
)


@legacy_staff_router.get("")
def list_staff(admin=Depends(require_tab("system", "read"))):
    return staff_svc.list_members()


@legacy_staff_router.get("/invite/capabilities")
def staff_invite_capabilities(admin=Depends(require_tab("system", "read"))):
    email_available = email_service_available()
    allowed_domains = staff_svc._load_allowed_domains()
    external_emails_allowed = staff_svc._allow_any_external()
    delivery_methods = ["manual_link"]
    if email_available:
        delivery_methods.insert(0, "email_magic_link")
    return {
        "email_available": email_available,
        "external_emails_allowed": external_emails_allowed,
        "allowed_domains": allowed_domains,
        "token_ttl_hours": int(_token_ttl("staff_invite") / 3600),
        "manual_activation_link_available": True,
        "delivery_methods": delivery_methods,
        "site_url_configured": bool(os.environ.get("SITE_URL", "").strip()),
    }


@legacy_staff_router.post("")
def add_staff(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.invite(body, inviter_id=admin["id"])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin, action="invite_staff",
        target_type="staff", target_id=str(result.get("id")),
        detail={"email": body.get("email"), "role": body.get("role")},
        request=request,
    )
    return result


@legacy_staff_router.post("/invite")
def invite_staff(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.invite(body, inviter_id=admin["id"])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin, action="invite_staff",
        target_type="staff", target_id=str(result.get("id")),
        detail={"email": body.get("email"), "role": body.get("role")},
        request=request,
    )
    return result


@legacy_staff_router.post("/invite/activation-link")
def create_activation_link(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.create_activation_link(body, inviter_id=admin["id"])
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="create_staff_activation_link",
        target_type="staff",
        target_id=str(result.get("staff_id")),
        detail={
            "email": result.get("email"),
            "role": result.get("role"),
            "delivery_method": result.get("delivery_method"),
            "token_hint": result.get("token_hint"),
        },
        request=request,
    )
    return result


@public_staff_router.post("/accept-invite")
def accept_staff_invite(body: dict):
    try:
        return staff_svc.accept_invite(
            str(body.get("invite_token") or ""),
            str(body.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@public_staff_router.get("/invite/status")
def staff_invite_status(token: str = ""):
    return staff_svc.invite_token_status(token)


@legacy_staff_router.patch("/{staff_id}")
def update_staff(
    staff_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    staff_svc.update(staff_id, body)
    record_admin_action(
        actor=admin, action="update_staff",
        target_type="staff", target_id=str(staff_id),
        detail=body, request=request,
    )
    return {"ok": True}


@legacy_staff_router.post("/{staff_id}/permissions")
def update_staff_permissions(
    staff_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        staff_svc.update_permissions(
            staff_id,
            body.get("permissions") or {},
            actor_is_owner=bool(staff.get("is_owner")),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_admin_action(
        actor=admin, action="update_staff_permissions",
        target_type="staff", target_id=str(staff_id),
        detail={"permissions": body.get("permissions") or {}}, request=request,
    )
    return {"ok": True}


@legacy_staff_router.post("/{staff_id}/suspend")
def suspend_staff(
    staff_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    staff_svc.suspend(staff_id, body.get("reason", ""))
    record_admin_action(
        actor=admin, action="suspend_staff",
        target_type="staff", target_id=str(staff_id),
        detail=body, request=request,
    )
    return {"ok": True}


@legacy_staff_router.post("/{staff_id}/reactivate")
def reactivate_staff(
    staff_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    staff_svc.reactivate(staff_id)
    record_admin_action(
        actor=admin, action="reactivate_staff",
        target_type="staff", target_id=str(staff_id),
        request=request,
    )
    return {"ok": True}


@legacy_staff_router.post("/{staff_id}/resend-invite")
def resend_staff_invite(
    staff_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.resend_invite(staff_id, inviter_id=admin["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="resend_staff_invite",
        target_type="staff",
        target_id=str(staff_id),
        detail={"email": result.get("email")},
        request=request,
    )
    return result


@legacy_staff_router.post("/{staff_id}/activation-link")
def create_existing_staff_activation_link(
    staff_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.create_existing_activation_link(staff_id, inviter_id=admin["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="create_existing_staff_activation_link",
        target_type="staff",
        target_id=str(staff_id),
        detail={
            "email": result.get("email"),
            "delivery_method": result.get("delivery_method"),
            "token_hint": result.get("token_hint"),
        },
        request=request,
    )
    return result


@legacy_staff_router.post("/{staff_id}/reset-password-link")
def create_staff_password_reset_link(
    staff_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        result = staff_svc.create_password_reset_link(staff_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="create_staff_password_reset_link",
        target_type="staff",
        target_id=str(staff_id),
        detail={
            "email": result.get("email"),
            "email_sent": result.get("email_sent"),
            "delivery_method": result.get("delivery_method"),
            "token_hint": result.get("token_hint"),
        },
        request=request,
    )
    return result


@legacy_staff_router.delete("/{staff_id}")
def delete_staff_member(
    staff_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    try:
        staff_svc.delete_member(staff_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except staff_svc.StaffDeleteBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="delete_staff",
        target_type="staff",
        target_id=str(staff_id),
        request=request,
    )
    return {"ok": True}


@legacy_staff_router.get("/roles")
def list_roles(admin=Depends(require_tab("system", "read"))):
    return staff_svc.list_roles()


@legacy_staff_router.get("/permission-matrix")
def permission_matrix(admin=Depends(require_tab("system", "read"))):
    return staff_svc.permission_matrix()


@legacy_staff_router.get("/audit-log")
def audit_log(
    actor_id: int | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
    admin=Depends(require_tab("system", "read")),
):
    return staff_svc.get_audit_log(
        actor_id=actor_id, action=action,
        target_type=target_type, target_id=target_id,
        from_date=from_date, to_date=to_date, limit=limit,
    )


@legacy_staff_router.get("/api-tokens")
def list_tokens(admin=Depends(require_system_permission("system.api_keys", "read"))):
    return staff_svc.list_api_tokens()


@legacy_staff_router.post("/api-tokens")
def create_token(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.api_keys", "write")),
):
    """Returns the full token ONLY on creation - prefix thereafter."""
    result = staff_svc.create_api_token(body, created_by=admin["id"])
    record_admin_action(
        actor=admin, action="create_api_token",
        target_type="api_token", target_id=str(result.get("id")),
        detail={"name": body.get("name"), "scope": body.get("scope")},
        request=request,
    )
    return result


@legacy_staff_router.delete("/api-tokens/{token_id}")
def revoke_token(
    token_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.api_keys", "write")),
):
    staff_svc.revoke_api_token(token_id, admin["id"])
    record_admin_action(
        actor=admin, action="revoke_api_token",
        target_type="api_token", target_id=str(token_id),
        request=request,
    )
    return {"ok": True}


# Public token acceptance/status remain unguarded by design.  Management
# invitations are guarded too: provisioning is tenant-aware, but its required
# admin audit record remains legacy-global until that table is tenant-scoped.
router.include_router(public_staff_router)
router.include_router(legacy_staff_router)
