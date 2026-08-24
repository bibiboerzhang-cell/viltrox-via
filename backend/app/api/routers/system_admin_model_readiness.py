"""Small response builders for the system model-readiness admin endpoint."""
from __future__ import annotations

from typing import Any

from app.core.model_registry import tasks_by_allowed_binding


def build_readiness_audit_extension(
    *,
    audited_items: list[dict[str, Any]],
    task_bindings: dict[str, str],
    task_model_readiness: dict[str, dict[str, Any]],
    readiness: dict[str, Any],
    trust_roots: dict[str, Any],
    evidence_source: dict[str, Any],
) -> dict[str, Any]:
    """Separate formal signed evidence from the effective runtime model gate."""

    by_binding = {str(item.get("binding") or ""): item for item in audited_items}
    signed_blocked = [
        {
            "binding": item["binding"],
            "code": item["runtime_gate"]["code"],
            "category": item["runtime_gate"]["category"],
            "failure_reasons": item["runtime_gate"]["failure_reasons"],
        }
        for item in audited_items
        if item.get("production_ready") is not True
    ]
    # Include declared fallbacks because the runtime may legally select them.
    active_names = sorted(tasks_by_allowed_binding(task_bindings))
    active_items = [
        by_binding.get(binding)
        or {
            "binding": binding,
            "production_ready": False,
            "runtime_authorization": {
                "allowed_by_model_readiness": False,
                "source": "blocked",
            },
        }
        for binding in active_names
    ]
    active_ready = sum(
        item.get("production_ready") is True for item in active_items
    )
    active_authorized = sum(
        (item.get("runtime_authorization") or {}).get(
            "allowed_by_model_readiness"
        ) is True
        for item in active_items
    )
    runtime_blocked = [
        str(item.get("binding") or "")
        for item in active_items
        if (item.get("runtime_authorization") or {}).get(
            "allowed_by_model_readiness"
        ) is not True
    ]
    task_ready = sum(
        item.get("production_ready") is True
        for item in task_model_readiness.values()
    )
    task_authorized = sum(
        (item.get("runtime_authorization") or {}).get(
            "allowed_by_model_readiness"
        ) is True
        for item in task_model_readiness.values()
    )
    active_scope = {
        "binding_count": len(active_names),
        "bindings": active_names,
        "task_assignment_count": len(task_bindings),
        "production_ready_count": active_ready,
        "signed_evidence_blocked_count": len(active_names) - active_ready,
        "runtime_authorized_count": active_authorized,
        "runtime_blocked_count": len(runtime_blocked),
        "runtime_blocked_bindings": runtime_blocked,
        "task_production_ready_count": task_ready,
        "task_runtime_authorized_count": task_authorized,
        "claim_status": (
            "validated"
            if active_names and active_ready == len(active_names)
            else "descriptive_only"
        ),
    }
    return {
        # Backward-compatible alias: formal catalog gate, not runtime blockage.
        "blocked_count": len(signed_blocked),
        "blocked_count_semantics": (
            "registered_candidates_not_production_ready_under_formal_gate"
        ),
        "signed_evidence_blocked_count": len(signed_blocked),
        "runtime_model_gate_blocked_count": len(runtime_blocked),
        "operator_acknowledged_count": sum(
            (item.get("runtime_authorization") or {}).get(
                "operator_acknowledged"
            ) is True
            for item in audited_items
        ),
        "model_readiness_authorized_count": sum(
            (item.get("runtime_authorization") or {}).get(
                "allowed_by_model_readiness"
            ) is True
            for item in audited_items
        ),
        "blocked_bindings": signed_blocked,
        "active_scope": active_scope,
        "blocker_categories": {
            "provider_credentials": {
                "configured_candidates": readiness["configured_count"],
                "registered_candidates": readiness["candidate_count"],
                "claim_status": "presence_only_not_entitlement_probe",
            },
            "attestation_trust_roots": {
                "ready": trust_roots.get(
                    "ready_to_verify_signed_evidence"
                ) is True,
                "failure_reasons": trust_roots.get("failure_reasons") or [],
            },
            "signed_evidence": {
                "ready_active_bindings": active_ready,
                "active_bindings": len(active_names),
                "evidence_source": evidence_source.get("source")
                or "not_configured",
            },
            "runtime_model_gate": {
                "authorized_active_bindings": active_authorized,
                "blocked_active_bindings": len(runtime_blocked),
            },
            "budget_and_feature_gates": {
                "status": "independent_not_evaluated_by_this_read_only_endpoint",
            },
        },
    }


__all__ = ["build_readiness_audit_extension"]
