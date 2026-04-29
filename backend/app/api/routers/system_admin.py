"""
api/routers/system_admin.py — System admin endpoints (batch 5)

Domains:
    /api/admin/integrations/*   external service health + metrics
    /api/admin/runtime/*        workers, queues, routes, cache, scheduler
    /api/admin/trust/*          trust events, rules, flagged users
    /api/admin/users/{id}/*     block/unblock/flag/adjust
    /api/admin/staff/*          members, roles, audit log, api tokens

Existing (reuse from intelligence.py):
    GET /api/intelligence/system/cache
    POST /api/intelligence/system/cache/clear
    GET /api/intelligence/system/rate-limit
    GET /api/intelligence/system/scheduler
    GET /api/intelligence/system/health
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.security import require_admin_async as require_admin
from app.api.dependencies.perms import require_system_permission, require_tab
from app.services.audit_log import record_admin_action
from app.services.system import integrations as int_svc
from app.services.system import provider_health as provider_svc
from app.services.system import runtime as rt_svc
from app.services.system import trust_admin as trust_svc
from app.services.system import staff as staff_svc
from app.core.model_pricing import PRICING_USD_PER_1M_TOKENS
from app.core.model_registry import AVAILABLE_MODELS, TASK_MODEL_BINDING, validate_task_model

router = APIRouter(prefix="/api/admin", tags=["system-admin"])


# =========================================================================
# Integrations
# =========================================================================

@router.get("/integrations")
def list_integrations(admin=Depends(require_admin)):
    return int_svc.list_all()


@router.get("/integrations/{integration_id}")
def get_integration(integration_id: int, admin=Depends(require_admin)):
    return int_svc.get_detail(integration_id)


@router.get("/integrations/{integration_id}/health")
async def integration_health(integration_id: int, admin=Depends(require_admin)):
    return await int_svc.live_health_check(integration_id)


@router.get("/integrations/{integration_id}/metrics")
def integration_metrics(
    integration_id: int,
    window: Literal["1h", "24h", "7d"] = "24h",
    admin=Depends(require_admin),
):
    return int_svc.get_metrics(integration_id, window=window)


@router.post("/integrations/health-check-all")
async def health_check_all(admin=Depends(require_admin)):
    return await int_svc.health_check_all()


@router.post("/integrations/{integration_id}/test")
async def test_integration(integration_id: int, admin=Depends(require_admin)):
    return await int_svc.smoke_test(integration_id)


@router.post("/integrations/{integration_id}/disable")
def disable_integration(integration_id: int, request: Request, admin=Depends(require_admin)):
    int_svc.set_enabled(integration_id, enabled=False)
    record_admin_action(
        actor=admin, action="disable_integration",
        target_type="integration", target_id=str(integration_id),
        request=request,
    )
    return {"ok": True}


@router.post("/integrations/{integration_id}/enable")
def enable_integration(integration_id: int, request: Request, admin=Depends(require_admin)):
    int_svc.set_enabled(integration_id, enabled=True)
    record_admin_action(
        actor=admin, action="enable_integration",
        target_type="integration", target_id=str(integration_id),
        request=request,
    )
    return {"ok": True}


# =========================================================================
# Runtime
# =========================================================================

@router.get("/runtime/workers")
async def runtime_workers(request: Request, admin=Depends(require_admin)):
    worker_snapshot = rt_svc.worker_states()
    queue = getattr(request.app.state, "job_queue", None)
    queue_stats = await queue.runtime_stats() if queue is not None else {"backend": "none"}
    summary = queue_stats.get("summary") if isinstance(queue_stats, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        **worker_snapshot,
        "backend": queue_stats.get("backend", "none") if isinstance(queue_stats, dict) else "none",
        "worker_pool": {
            "configured_concurrency": summary.get("configured_concurrency", 0),
            "worker_processes": summary.get("worker_processes", 0),
            "worker_async_consumers": summary.get("worker_async_consumers", 0),
        },
        "queue_summary": summary,
        "groups": queue_stats.get("groups", []) if isinstance(queue_stats, dict) else [],
    }


@router.get("/runtime/queues")
async def runtime_queues(request: Request, admin=Depends(require_admin)):
    ledger_depths = rt_svc.queue_depths()
    queue = getattr(request.app.state, "job_queue", None)
    queue_stats = await queue.runtime_stats() if queue is not None else {"backend": "none"}
    summary = queue_stats.get("summary") if isinstance(queue_stats, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    return {
        **ledger_depths,
        "backend": queue_stats.get("backend", "none") if isinstance(queue_stats, dict) else "none",
        "stream_key": queue_stats.get("stream_key", "") if isinstance(queue_stats, dict) else "",
        "group": queue_stats.get("group", "") if isinstance(queue_stats, dict) else "",
        "queue_depth": queue_stats.get("queue_depth", 0) if isinstance(queue_stats, dict) else 0,
        "summary": summary,
        "groups": queue_stats.get("groups", []) if isinstance(queue_stats, dict) else [],
        "dead_letter_stream": queue_stats.get("dead_letter_stream", "") if isinstance(queue_stats, dict) else "",
    }


@router.get("/runtime/route-performance")
def runtime_route_performance(
    limit: int = 20,
    order_by: Literal["p95", "error_rate", "requests"] = "p95",
    admin=Depends(require_admin),
):
    return rt_svc.route_performance(limit=limit, order_by=order_by)


@router.get("/runtime/system-resources")
def runtime_resources(admin=Depends(require_admin)):
    return rt_svc.system_resources()


@router.post("/runtime/scheduler/{job_id}/run-now")
async def run_scheduler_job(
    job_id: str, request: Request, admin=Depends(require_admin)
):
    result = await rt_svc.run_job_now(job_id)
    record_admin_action(
        actor=admin, action="run_job_now",
        target_type="scheduler_job", target_id=job_id,
        request=request,
    )
    return result


@router.get("/runtime/scheduler/{job_id}/history")
def scheduler_history(job_id: str, admin=Depends(require_admin)):
    return rt_svc.job_history(job_id)


@router.post("/runtime/cache/{tier}/clear")
def clear_cache_tier(
    tier: str, request: Request, admin=Depends(require_admin)
):
    result = rt_svc.clear_cache(tier)
    record_admin_action(
        actor=admin, action="clear_cache",
        target_type="cache_tier", target_id=tier,
        detail=result, request=request,
    )
    return result


# =========================================================================
# System key/model management support
# =========================================================================

@router.get("/system/providers")
def provider_status(admin=Depends(require_system_permission("system.api_keys", "read"))):
    return provider_svc.list_provider_status()


@router.post("/system/providers/{provider}/probe")
async def probe_provider(
    provider: str,
    body: dict | None = None,
    admin=Depends(require_system_permission("system.api_keys", "read")),
):
    body = body or {}
    result = await provider_svc.probe_provider(provider, api_key=body.get("api_key"))
    provider_svc.record_provider_probe(provider, bool(result.get("ok")), str(result.get("error") or ""))
    return result


@router.get("/system/models")
def system_models(admin=Depends(require_system_permission("system.models", "read"))):
    return {
        "available_models": AVAILABLE_MODELS,
        "task_model_binding": TASK_MODEL_BINDING,
        "pricing_usd_per_1m_tokens": PRICING_USD_PER_1M_TOKENS,
    }


@router.post("/system/models/switch")
def switch_system_model(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.models", "write")),
):
    task = str(body.get("task") or "")
    model = str(body.get("model") or "")
    if not validate_task_model(task, model):
        raise HTTPException(status_code=400, detail="unsupported task/model binding")
    # The project intentionally keeps model bindings code-reviewed. This endpoint
    # validates the requested switch and writes audit intent; applying it still
    # requires a reviewed config change or deployment step.
    record_admin_action(
        actor=admin,
        action="request_model_switch",
        target_type="model_binding",
        target_id=task,
        detail={"model": model},
        request=request,
    )
    return {"ok": True, "requires_deploy": True, "task": task, "model": model}


# =========================================================================
# Trust
# =========================================================================

@router.get("/trust/events")
def trust_events(
    pos: bool | None = None,
    user_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
    admin=Depends(require_admin),
):
    return trust_svc.list_events(
        pos=pos, user_id=user_id,
        from_date=from_date, to_date=to_date, limit=limit,
    )


@router.get("/trust/users")
def trust_users(
    status: Literal["trusted", "watching", "flagged", "blocked"] | None = None,
    order_by: Literal["score_asc", "violations_desc", "recent"] = "score_asc",
    admin=Depends(require_admin),
):
    return trust_svc.list_users(status=status, order_by=order_by)


@router.get("/trust/users/{user_id}")
def trust_user_detail(user_id: int, admin=Depends(require_admin)):
    return trust_svc.user_detail(user_id)


@router.get("/trust/distribution")
def trust_distribution(admin=Depends(require_admin)):
    return trust_svc.distribution()


@router.get("/trust/rules")
def trust_rules(admin=Depends(require_admin)):
    return trust_svc.list_rules()


@router.put("/trust/rules/{rule_id}")
def update_trust_rule(
    rule_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    trust_svc.update_rule(rule_id, body, admin["id"])
    record_admin_action(
        actor=admin, action="update_trust_rule",
        target_type="trust_rule", target_id=str(rule_id),
        detail=body, request=request,
    )
    return {"ok": True}


@router.put("/trust/thresholds")
def update_thresholds(body: dict, request: Request, admin=Depends(require_admin)):
    trust_svc.set_thresholds(body, admin["id"])
    record_admin_action(
        actor=admin, action="update_trust_thresholds",
        target_type="trust_threshold", target_id="global",
        detail=body, request=request,
    )
    return {"ok": True}


# =========================================================================
# User moderation actions
# =========================================================================

@router.post("/users/{user_id}/block")
def block_user(
    user_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    reason = body.get("reason")
    if not reason:
        raise HTTPException(400, "reason required")
    trust_svc.block_user(user_id, reason, admin["id"])
    record_admin_action(
        actor=admin, action="block_user",
        target_type="user", target_id=str(user_id),
        detail={"reason": reason}, request=request,
    )
    return {"ok": True}


@router.post("/users/{user_id}/unblock")
def unblock_user(
    user_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    trust_svc.unblock_user(user_id, body.get("reason", ""), admin["id"])
    record_admin_action(
        actor=admin, action="unblock_user",
        target_type="user", target_id=str(user_id),
        request=request,
    )
    return {"ok": True}


@router.post("/users/{user_id}/flag")
def flag_user(
    user_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    reason = body.get("reason") or "admin_review"
    trust_svc.flag_user(user_id, reason, admin["id"])
    record_admin_action(
        actor=admin, action="flag_user",
        target_type="user", target_id=str(user_id),
        detail={"reason": reason}, request=request,
    )
    return {"ok": True}


@router.post("/users/{user_id}/clear-flag")
def clear_flag(user_id: int, request: Request, admin=Depends(require_admin)):
    trust_svc.clear_flag(user_id, admin["id"])
    record_admin_action(
        actor=admin, action="clear_user_flag",
        target_type="user", target_id=str(user_id),
        request=request,
    )
    return {"ok": True}


@router.post("/users/{user_id}/adjust-score")
def adjust_score(
    user_id: int, body: dict, request: Request, admin=Depends(require_admin)
):
    delta = body.get("delta")
    reason = body.get("reason")
    if delta is None or not reason:
        raise HTTPException(400, "delta and reason required")
    trust_svc.adjust_score(user_id, int(delta), reason, admin["id"])
    record_admin_action(
        actor=admin, action="adjust_trust_score",
        target_type="user", target_id=str(user_id),
        detail={"delta": delta, "reason": reason}, request=request,
    )
    return {"ok": True}


# =========================================================================
# Staff
# =========================================================================

@router.get("/staff")
def list_staff(admin=Depends(require_tab("system", "read"))):
    return staff_svc.list_members()


@router.post("/staff")
def add_staff(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    result = staff_svc.invite(body, inviter_id=admin["id"])
    record_admin_action(
        actor=admin, action="invite_staff",
        target_type="staff", target_id=str(result.get("id")),
        detail={"email": body.get("email"), "role": body.get("role")},
        request=request,
    )
    return result


@router.post("/staff/invite")
def invite_staff(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.members", "write")),
):
    result = staff_svc.invite(body, inviter_id=admin["id"])
    record_admin_action(
        actor=admin, action="invite_staff",
        target_type="staff", target_id=str(result.get("id")),
        detail={"email": body.get("email"), "role": body.get("role")},
        request=request,
    )
    return result


@router.post("/staff/accept-invite")
def accept_staff_invite(body: dict):
    try:
        return staff_svc.accept_invite(
            str(body.get("invite_token") or ""),
            str(body.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/staff/{staff_id}")
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


@router.post("/staff/{staff_id}/permissions")
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


@router.post("/staff/{staff_id}/suspend")
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


@router.post("/staff/{staff_id}/reactivate")
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


@router.get("/staff/roles")
def list_roles(admin=Depends(require_tab("system", "read"))):
    return staff_svc.list_roles()


@router.get("/staff/permission-matrix")
def permission_matrix(admin=Depends(require_tab("system", "read"))):
    return staff_svc.permission_matrix()


@router.get("/staff/audit-log")
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


@router.get("/staff/api-tokens")
def list_tokens(admin=Depends(require_system_permission("system.api_keys", "read"))):
    return staff_svc.list_api_tokens()


@router.post("/staff/api-tokens")
def create_token(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.api_keys", "write")),
):
    """Returns the full token ONLY on creation — prefix thereafter."""
    result = staff_svc.create_api_token(body, created_by=admin["id"])
    record_admin_action(
        actor=admin, action="create_api_token",
        target_type="api_token", target_id=str(result.get("id")),
        detail={"name": body.get("name"), "scope": body.get("scope")},
        request=request,
    )
    return result


@router.delete("/staff/api-tokens/{token_id}")
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
