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

import os
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.legacy_scope import require_legacy_system_admin_scope
from app.api.routers.system_admin_model_readiness import build_readiness_audit_extension
from app.api.routers.system_admin_staff import router as staff_router
from app.core.security import require_admin_async as require_admin, verify_password
from app.db.connection import get_conn
from app.api.dependencies.perms import require_system_permission, require_tab
from app.services.audit_log import record_admin_action
from app.services.system import integrations as int_svc
from app.services.system import ai_usage as usage_svc
from app.services.system import provider_health as provider_svc
from app.services.system import runtime as rt_svc
from app.services.system import secrets_admin as secrets_svc
from app.services.system import trust_admin as trust_svc
from app.core.model_pricing import PRICING_USD_PER_1M_TOKENS
from app.core.model_registry import AVAILABLE_MODELS, current_task_model_binding, split_binding, validate_task_model
from app.platform.llm_runtime_errors import readiness_gate
from app.platform.models.readiness import (
    build_model_readiness_catalog,
    configured_providers_from_environment,
    exact_binding_readiness_from_environment,
    model_attestation_trust_root_status,
    readiness_evidence_from_environment,
)

router = APIRouter(prefix="/api/admin", tags=["system-admin"])
legacy_admin_router = APIRouter(
    dependencies=[Depends(require_legacy_system_admin_scope)],
)
router.include_router(staff_router)

SYSTEM_RESTART_ENABLED = os.environ.get("SYSTEM_RESTART_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
SYSTEMD_SERVICE_NAMES = {
    "public": os.environ.get("SYSTEMD_PUBLIC_SERVICE", "viltrox-2.0-public"),
    "admin": os.environ.get("SYSTEMD_ADMIN_SERVICE", "viltrox-2.0-admin"),
    "worker": os.environ.get("SYSTEMD_WORKER_SERVICE", "viltrox-2.0-worker"),
    "scheduler": os.environ.get("SYSTEMD_SCHEDULER_SERVICE", "viltrox-2.0-scheduler"),
}


def _confirm_admin_password(admin: dict, confirm_password: str | None) -> None:
    if not confirm_password:
        raise HTTPException(status_code=400, detail="confirm_password required")
    row = get_conn().execute("SELECT password_hash FROM users WHERE id = ?", (int(admin.get("id") or 0),)).fetchone()
    if not row or not verify_password(str(confirm_password), row["password_hash"]):
        raise HTTPException(status_code=403, detail="Invalid confirmation password")


# =========================================================================
# Integrations
# =========================================================================

@legacy_admin_router.get("/integrations")
def list_integrations(admin=Depends(require_tab("runtime", "read"))):
    return int_svc.list_all()


@legacy_admin_router.get("/integrations/{integration_id}")
def get_integration(integration_id: int, admin=Depends(require_tab("runtime", "read"))):
    return int_svc.get_detail(integration_id)


@legacy_admin_router.get("/integrations/{integration_id}/health")
async def integration_health(integration_id: int, admin=Depends(require_tab("runtime", "read"))):
    return await int_svc.live_health_check(integration_id)


@legacy_admin_router.get("/integrations/{integration_id}/metrics")
def integration_metrics(
    integration_id: int,
    window: Literal["1h", "24h", "7d"] = "24h",
    admin=Depends(require_tab("runtime", "read")),
):
    return int_svc.get_metrics(integration_id, window=window)


@legacy_admin_router.post("/integrations/health-check-all")
async def health_check_all(admin=Depends(require_tab("runtime", "write"))):
    return await int_svc.health_check_all()


@legacy_admin_router.post("/integrations/{integration_id}/test")
async def test_integration(integration_id: int, admin=Depends(require_tab("runtime", "write"))):
    return await int_svc.smoke_test(integration_id)


@legacy_admin_router.post("/integrations/{integration_id}/disable")
def disable_integration(
    integration_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("runtime", "write")),
):
    int_svc.set_enabled(integration_id, enabled=False)
    record_admin_action(
        actor=admin, action="disable_integration",
        target_type="integration", target_id=str(integration_id),
        request=request,
    )
    return {"ok": True}

@legacy_admin_router.post("/integrations/{integration_id}/enable")
def enable_integration(
    integration_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("runtime", "write")),
):
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

@legacy_admin_router.get("/runtime/workers")
async def runtime_workers(request: Request, admin=Depends(require_tab("runtime", "read"))):
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


@legacy_admin_router.get("/runtime/queues")
async def runtime_queues(request: Request, admin=Depends(require_tab("runtime", "read"))):
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


@legacy_admin_router.get("/runtime/route-performance")
def runtime_route_performance(
    limit: int = 20,
    order_by: Literal["p95", "error_rate", "requests"] = "p95",
    admin=Depends(require_tab("runtime", "read")),
):
    return rt_svc.route_performance(limit=limit, order_by=order_by)


@legacy_admin_router.get("/runtime/system-resources")
def runtime_resources(admin=Depends(require_tab("runtime", "read"))):
    return rt_svc.system_resources()


@legacy_admin_router.post("/runtime/scheduler/{job_id}/run-now")
async def run_scheduler_job(
    job_id: str,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("runtime", "write")),
):
    result = await rt_svc.run_job_now(job_id)
    record_admin_action(
        actor=admin, action="run_job_now",
        target_type="scheduler_job", target_id=job_id,
        request=request,
    )
    return result


@legacy_admin_router.get("/runtime/scheduler/{job_id}/history")
def scheduler_history(job_id: str, admin=Depends(require_tab("runtime", "read"))):
    return rt_svc.job_history(job_id)


@legacy_admin_router.post("/runtime/cache/{tier}/clear")
def clear_cache_tier(
    tier: str,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("runtime", "write")),
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

@legacy_admin_router.get("/system/providers")
def provider_status(admin=Depends(require_system_permission("system.api_keys", "read"))):
    return provider_svc.list_provider_status()


@legacy_admin_router.get("/system/usage")
def system_usage(
    days: int = Query(default=7, ge=1, le=90),
    admin=Depends(require_system_permission("system.usage", "read")),
):
    return usage_svc.usage_summary(days=days)


@legacy_admin_router.get("/system/rbac/status")
def system_rbac_status(
    include_staff: bool = Query(default=False),
    staff=Depends(require_system_permission("system.members", "read")),
):
    """RBAC 只读状态快照:staff 数/角色分布/权限等级分布/缺口检测。零写库。"""
    from app.domains.staff import rbac_status

    del staff
    return rbac_status.build_rbac_status(include_staff=include_staff)


@legacy_admin_router.post("/system/providers/{provider}/probe")
async def probe_provider(
    provider: str,
    body: dict | None = None,
    admin=Depends(require_system_permission("system.api_keys", "read")),
):
    body = body or {}
    result = await provider_svc.probe_provider(provider, api_key=body.get("api_key"))
    provider_svc.record_provider_probe(provider, bool(result.get("ok")), str(result.get("error") or ""))
    return result


@legacy_admin_router.post("/system/keys/rotate")
async def rotate_provider_key(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.api_keys", "write")),
):
    _confirm_admin_password(admin, body.get("confirm_password"))
    provider = str(body.get("provider") or "").strip().lower()
    new_key = str(body.get("new_key") or "").strip()
    if not provider or not new_key:
        raise HTTPException(status_code=400, detail="provider and new_key required")
    sandbox = await provider_svc.probe_provider(provider, api_key=new_key)
    provider_svc.record_provider_probe(provider, bool(sandbox.get("ok")), str(sandbox.get("error") or ""))
    if not sandbox.get("ok"):
        raise HTTPException(status_code=400, detail=f"sandbox probe failed: {sandbox.get('error') or 'unknown'}")
    try:
        result = secrets_svc.rotate_provider_key(
            provider,
            new_key,
            move_current_to_previous=bool(body.get("move_current_to_previous", True)),
            actor_email=str(admin.get("email") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="rotate_provider_key",
        target_type="provider_key",
        target_id=provider,
        detail={
            "env_key": result.get("env_key"),
            "key_prefix": result.get("key_prefix"),
            "previous_set": result.get("previous_set"),
            "requires_restart": True,
        },
        request=request,
    )
    return result


@legacy_admin_router.post("/system/restart")
def restart_system_roles(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.restart", "write")),
):
    _confirm_admin_password(admin, body.get("confirm_password"))
    roles_raw = body.get("roles") or []
    if not isinstance(roles_raw, list):
        raise HTTPException(status_code=400, detail="roles must be a list")
    roles = [str(item).strip().lower() for item in roles_raw if str(item).strip()]
    if not roles:
        raise HTTPException(status_code=400, detail="at least one role required")
    unknown = [role for role in roles if role not in SYSTEMD_SERVICE_NAMES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unsupported roles: {', '.join(unknown)}")
    results = []
    for role in roles:
        service_name = SYSTEMD_SERVICE_NAMES[role]
        if not SYSTEM_RESTART_ENABLED:
            results.append({"role": role, "service": service_name, "status": "dry_run"})
            continue
        try:
            completed = subprocess.run(
                ["systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            results.append({
                "role": role,
                "service": service_name,
                "status": "failed",
                "stderr": str(exc)[-500:],
            })
            continue
        if completed.returncode != 0:
            results.append({
                "role": role,
                "service": service_name,
                "status": "failed",
                "stderr": (completed.stderr or completed.stdout or "")[-500:],
            })
            continue
        active = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        status = "restarted" if active.returncode == 0 and active.stdout.strip() == "active" else "failed"
        results.append({
            "role": role,
            "service": service_name,
            "status": status,
            "systemctl_state": active.stdout.strip() or active.stderr.strip(),
        })
    record_admin_action(
        actor=admin,
        action="restart_system_roles",
        target_type="systemd",
        target_id=",".join(roles),
        detail={"enabled": SYSTEM_RESTART_ENABLED, "results": results},
        request=request,
    )
    return {"ok": all(item["status"] in {"dry_run", "restarted"} for item in results), "enabled": SYSTEM_RESTART_ENABLED, "results": results}


@legacy_admin_router.get("/system/models")
def system_models(admin=Depends(require_system_permission("system.models", "read"))):
    del admin
    # Operator acknowledgements are a temporary, exact-binding runtime
    # authorization.  They must be visible alongside (never rewritten into)
    # signed production-readiness evidence, otherwise the settings page says
    # "all blocked" while the gateway is actually allowed to call a model.
    from app.platform import llm_gateway

    operator_ack_bindings = llm_gateway._readiness_operator_ack_bindings()
    registered_bindings = [
        f"{provider}/{model}"
        for provider, models in AVAILABLE_MODELS.items()
        for model in models
    ]
    evidence, evidence_source = readiness_evidence_from_environment()
    readiness = build_model_readiness_catalog(
        registered_bindings,
        evidence_by_binding=evidence,
        configured_providers=configured_providers_from_environment(),
    )
    audited_items = []
    for item in readiness["items"]:
        binding = str(item.get("binding") or "")
        signed_ready = item.get("production_ready") is True
        operator_ack = item.get("configured") is True and binding in operator_ack_bindings
        audited_items.append({
            **item,
            "runtime_gate": readiness_gate(item, evidence_source),
            "runtime_authorization": {
                "allowed_by_model_readiness": bool(signed_ready or operator_ack),
                "source": "signed_evidence" if signed_ready else "operator_ack" if operator_ack else "blocked",
                "operator_acknowledged": operator_ack,
                "temporary": bool(operator_ack and not signed_ready),
                "budget_and_feature_gates_still_apply": True,
                "claim_status": item.get("claim_status") or "descriptive_only",
            },
        })
    readiness = {**readiness, "items": audited_items}
    by_binding = {item["binding"]: item for item in audited_items}
    task_bindings = current_task_model_binding()
    task_model_readiness = {}
    for task, binding in task_bindings.items():
        item = by_binding.get(binding) or {
            "binding": binding,
            "availability": "unverified",
            "production_ready": False,
            "claim_status": "descriptive_only",
            "failure_reasons": ["binding_not_in_registered_catalog"],
        }
        task_model_readiness[task] = {
            **item,
            "runtime_gate": item.get("runtime_gate")
            or readiness_gate(item, evidence_source),
        }
    trust_roots = model_attestation_trust_root_status()
    audit_extension = build_readiness_audit_extension(
        audited_items=audited_items,
        task_bindings=task_bindings,
        task_model_readiness=task_model_readiness,
        readiness=readiness,
        trust_roots=trust_roots,
        evidence_source=evidence_source,
    )
    return {
        "status": readiness["status"],
        "claim_status": readiness["claim_status"],
        "available_models": AVAILABLE_MODELS,
        "available_models_semantics": "registered_candidates_only_not_verified_availability",
        "registered_models": registered_bindings,
        "task_model_binding": task_bindings,
        "task_model_readiness": task_model_readiness,
        "readiness_audit": {
            "version": "model_readiness_audit_v2",
            "candidate_count": readiness["candidate_count"],
            "configured_count": readiness["configured_count"],
            "probed_count": readiness["probed_count"],
            "evaluated_count": readiness["evaluated_count"],
            "production_ready_count": readiness["production_ready_count"],
            **audit_extension,
            "attestation_trust_roots": trust_roots,
            "evidence_source": {
                "source": evidence_source.get("source") or "not_configured",
                "parsed": evidence_source.get("parsed") is True,
                "error": evidence_source.get("error"),
                "binding_count": int(evidence_source.get("binding_count") or 0),
                "secret_values_exposed": False,
            },
        },
        "model_readiness": readiness,
        "readiness_evidence_source": evidence_source,
        "pricing_usd_per_1m_tokens": PRICING_USD_PER_1M_TOKENS,
    }


def _exact_binding_readiness(
    binding: str,
    *,
    expected_tasks: tuple[str, ...] | None = None,
) -> tuple[dict, dict]:
    """Use the exact same authoritative gate as the LLM gateway."""
    return exact_binding_readiness_from_environment(
        binding,
        expected_tasks=expected_tasks,
    )


@legacy_admin_router.get("/system/models/readiness")
def system_model_readiness(
    binding: str = Query(..., min_length=3, max_length=220),
    admin=Depends(require_system_permission("system.models", "read")),
):
    """Return the exact fail-closed gate without probing or exposing secrets."""

    del admin
    provider, model = split_binding(binding)
    if not provider or not model:
        raise HTTPException(status_code=400, detail="binding must be provider/model")
    item, evidence_source = _exact_binding_readiness(f"{provider}/{model}")
    return {
        "binding": f"{provider}/{model}",
        "runtime_gate": readiness_gate(item, evidence_source),
        "readiness": item,
        "evidence_source": {
            "source": evidence_source.get("source") or "not_configured",
            "parsed": evidence_source.get("parsed") is True,
            "error": evidence_source.get("error"),
            "binding_count": int(evidence_source.get("binding_count") or 0),
            "secret_values_exposed": False,
        },
    }


@legacy_admin_router.post("/system/models/switch")
async def switch_system_model(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_system_permission("system.models", "write")),
):
    _confirm_admin_password(admin, body.get("confirm_password"))
    task = str(body.get("task") or "")
    model = str(body.get("model") or "")
    if not validate_task_model(task, model):
        raise HTTPException(status_code=400, detail="unsupported task/model binding")
    readiness, evidence_source = _exact_binding_readiness(
        model,
        expected_tasks=(task,),
    )
    if readiness.get("production_ready") is not True:
        # A provider-level health check cannot prove access to this exact model,
        # output validity, or evaluation quality.  Block before any provider
        # probe or configuration write.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "model_binding_not_production_ready",
                "binding": model,
                "state": readiness.get("state") or "unverified",
                "availability": readiness.get("availability") or "unverified",
                "claim_status": readiness.get("claim_status") or "descriptive_only",
                "failure_reasons": readiness.get("failure_reasons") or ["readiness_unverified"],
                "runtime_gate": readiness_gate(readiness, evidence_source),
                "evidence_source": evidence_source.get("source") or "not_configured",
                "note": "provider health alone is not exact-model readiness evidence",
            },
        )
    provider, _model_name = split_binding(model)
    sandbox = await provider_svc.probe_provider(provider)
    provider_svc.record_provider_probe(provider, bool(sandbox.get("ok")), str(sandbox.get("error") or ""))
    if not sandbox.get("ok"):
        raise HTTPException(status_code=400, detail=f"provider probe failed: {sandbox.get('error') or 'unknown'}")
    try:
        result = secrets_svc.set_task_model_binding(task, model, actor_email=str(admin.get("email") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="switch_model_binding",
        target_type="model_binding",
        target_id=task,
        detail={"model": model, "env_keys": result.get("env_keys"), "requires_restart": True},
        request=request,
    )
    return result


# =========================================================================
# Trust
# =========================================================================

@legacy_admin_router.get("/trust/events")
def trust_events(
    pos: bool | None = None,
    user_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 100,
    admin=Depends(require_tab("command", "read")),
):
    return trust_svc.list_events(
        pos=pos, user_id=user_id,
        from_date=from_date, to_date=to_date, limit=limit,
    )


@legacy_admin_router.get("/trust/users")
def trust_users(
    status: Literal["trusted", "watching", "flagged", "blocked"] | None = None,
    order_by: Literal["score_asc", "violations_desc", "recent"] = "score_asc",
    admin=Depends(require_tab("command", "read")),
):
    return trust_svc.list_users(status=status, order_by=order_by)


@legacy_admin_router.get("/trust/users/{user_id}")
def trust_user_detail(user_id: int, admin=Depends(require_tab("command", "read"))):
    return trust_svc.user_detail(user_id)


@legacy_admin_router.get("/trust/distribution")
def trust_distribution(admin=Depends(require_tab("command", "read"))):
    return trust_svc.distribution()


@legacy_admin_router.get("/trust/rules")
def trust_rules(admin=Depends(require_tab("command", "read"))):
    return trust_svc.list_rules()


@legacy_admin_router.put("/trust/rules/{rule_id}")
def update_trust_rule(
    rule_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
):
    trust_svc.update_rule(rule_id, body, admin["id"])
    record_admin_action(
        actor=admin, action="update_trust_rule",
        target_type="trust_rule", target_id=str(rule_id),
        detail=body, request=request,
    )
    return {"ok": True}


@legacy_admin_router.put("/trust/thresholds")
def update_thresholds(
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
):
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

@legacy_admin_router.post("/users/{user_id}/block")
def block_user(
    user_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
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


@legacy_admin_router.post("/users/{user_id}/unblock")
def unblock_user(
    user_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
):
    trust_svc.unblock_user(user_id, body.get("reason", ""), admin["id"])
    record_admin_action(
        actor=admin, action="unblock_user",
        target_type="user", target_id=str(user_id),
        request=request,
    )
    return {"ok": True}


@legacy_admin_router.post("/users/{user_id}/flag")
def flag_user(
    user_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
):
    reason = body.get("reason") or "admin_review"
    trust_svc.flag_user(user_id, reason, admin["id"])
    record_admin_action(
        actor=admin, action="flag_user",
        target_type="user", target_id=str(user_id),
        detail={"reason": reason}, request=request,
    )
    return {"ok": True}


@legacy_admin_router.post("/users/{user_id}/clear-flag")
def clear_flag(
    user_id: int,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
):
    trust_svc.clear_flag(user_id, admin["id"])
    record_admin_action(
        actor=admin, action="clear_user_flag",
        target_type="user", target_id=str(user_id),
        request=request,
    )
    return {"ok": True}


@legacy_admin_router.post("/users/{user_id}/adjust-score")
def adjust_score(
    user_id: int,
    body: dict,
    request: Request,
    admin=Depends(require_admin),
    _staff=Depends(require_tab("command", "write")),
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


# Keep only public token acceptance/status outside this dependency; every route
# declared in this module is legacy-global and is guarded here.
router.include_router(legacy_admin_router)
