"""Marketing Advisor application service with fail-closed live-AI seams."""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.core.model_registry import current_task_model_binding
from app.db.connection import get_conn, table_exists
from app.domains.advisor import intelligent_bridge, repository
from app.domains.advisor.scope import AdvisorScope
from app.platform import llm_gateway, llm_production
from app.platform.models.readiness import exact_binding_readiness_from_environment


_TASK_BINDING = "via_chat"
_PURPOSE = "marketing_advisor"
_COST_SCOPE = "cron:marketing_advisor"
_EXTERNAL_ENABLE_ENV = "VKPI_ADVISOR_EXTERNAL_AI_ENABLED"
_PROVIDER_MAX_OUTPUT_TOKENS = 900


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _binding() -> tuple[str, str, str]:
    binding = str(current_task_model_binding().get(_TASK_BINDING) or "").strip()
    provider, separator, model = binding.partition("/")
    return provider.strip().lower(), model.strip(), binding if separator else ""


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _request_hash(
    *,
    content: str,
    context_refs: Any,
    requested_actions: Any,
    allow_external_ai: bool,
) -> str:
    return _canonical_hash(
        {
            "content": str(content or "").strip()[:20_000],
            "context_refs": repository.sanitize_context_refs(context_refs),
            "requested_actions": repository.sanitize_action_drafts(requested_actions),
            "allow_external_ai": bool(allow_external_ai),
        }
    )


def _safe_provider_context(bridge: dict[str, Any]) -> dict[str, Any]:
    raw = bridge.get("provider_context")
    source = raw if isinstance(raw, dict) else {}

    def allowed(items: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or item.get("external_share_allowed") is not True:
                continue
            result.append({key: item.get(key) for key in keys if item.get(key) not in (None, "")})
        return result

    return {
        "memories": allowed(
            source.get("memories"),
            ("evidence_id", "memory_kind", "memory_key", "summary"),
        ),
        "context_refs": allowed(
            source.get("context_refs"),
            ("evidence_id", "entity_type", "entity_id", "label", "platform", "observed_at"),
        ),
        "history": allowed(
            source.get("history"),
            ("evidence_id", "role", "content"),
        ),
    }


def _provider_prompt(question: str, bridge: dict[str, Any]) -> tuple[str, set[str]]:
    context = _safe_provider_context(bridge)
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for group in context.values()
        for item in group
        if str(item.get("evidence_id") or "")
    }
    payload = {
        "question": str(question or "").strip()[:20_000],
        "owner_scoped_context": context,
        "rules": {
            "claim_status": "descriptive_only",
            "never_invent_gmv_roi_inventory_cost_or_business_outcomes": True,
            "never_execute_actions": True,
            "use_only_supplied_evidence_ids": True,
        },
        "response_schema": {
            "answer": "non-empty string <= 12000 chars",
            "evidence_ids": "array containing only supplied evidence ids",
            "confidence": "number from 0 to 1",
        },
    }
    return (
        "You are the private V-KPI Marketing Advisor. Return exactly one JSON object; "
        "do not make external calls, do not claim actions were executed, and keep all "
        "business conclusions descriptive unless supplied evidence proves them.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        evidence_ids,
    )


def _json_validator(allowed_evidence_ids: set[str]):
    def validate(value: Any) -> tuple[bool, str]:
        if not isinstance(value, dict):
            return False, "advisor response must be an object"
        answer = value.get("answer")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 12_000:
            return False, "advisor answer is missing or too long"
        evidence_ids = value.get("evidence_ids")
        if not isinstance(evidence_ids, list) or len(evidence_ids) > 24:
            return False, "advisor evidence_ids must be a bounded array"
        if any(str(item) not in allowed_evidence_ids for item in evidence_ids):
            return False, "advisor response referenced evidence outside the owner-scoped envelope"
        confidence = value.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return False, "advisor confidence must be numeric"
        if not 0 <= float(confidence) <= 1:
            return False, "advisor confidence must be between zero and one"
        return True, ""

    return validate


def _provider_preflight(prompt: str) -> dict[str, Any]:
    provider, model, binding = _binding()
    if not binding:
        return {
            "provider_calls_allowed": False,
            "provider_gate_reason": "advisor_task_binding_missing",
            "providers": [],
        }
    return llm_gateway.budget_preflight(
        prompt,
        purpose=_PURPOSE,
        max_output_tokens=_PROVIDER_MAX_OUTPUT_TOKENS,
        preferred_provider=provider,
        model_override=model,
        model_fallbacks=(),
        require_runtime_verified=True,
        execution_class=llm_gateway.PRODUCTION_EXECUTION_CLASS,
        cost_tag=_COST_SCOPE,
        skip_monthly_env_check=False,
        # The Advisor has its own explicit budget scope. Missing that row must
        # block even when global/provider pools still have headroom.
        require_configured=True,
    )


def _provider_reason(preflight: dict[str, Any], *, operator_enabled: bool) -> str:
    if not operator_enabled:
        return "advisor_external_ai_operator_disabled"
    reason = str(preflight.get("provider_gate_reason") or "provider_calls_blocked")
    if reason == "model_binding_blocked":
        return "advisor_exact_model_not_production_ready"
    if reason in {"budget_hard_stop", "monthly_env_budget_disabled"}:
        return "advisor_budget_not_authorized"
    return f"advisor_{reason}"[:160]


def _readiness_blockers(
    *,
    persistence_ready: bool,
    bridge: dict[str, Any],
    operator_enabled: bool,
    preflight: dict[str, Any],
    selected: dict[str, Any],
) -> list[str]:
    """Return every independent blocker instead of hiding behind the first one."""

    blockers: list[str] = []
    if not persistence_ready:
        blockers.append("advisor_claim_schema_unavailable")
    if bridge.get("ready") is not True:
        blockers.append(str(bridge.get("reason") or "advisor_knowledge_bridge_unavailable"))
    if not operator_enabled:
        blockers.append("advisor_external_ai_operator_disabled")
    if preflight.get("force_offline") is True:
        blockers.append("advisor_force_offline")
    if selected and selected.get("configured") is not True:
        blockers.append("advisor_provider_not_connected")
    if selected and str(selected.get("binding_gate_reason") or "") != "ready":
        blockers.append("advisor_exact_model_not_production_ready")
    if selected and selected.get("budget_allowed") is not True:
        blockers.append("advisor_budget_not_authorized")
    if not selected:
        blockers.append("advisor_provider_candidate_unavailable")
    return list(dict.fromkeys(item for item in blockers if item))


def _exact_model_evidence(binding: str) -> dict[str, Any]:
    """Expose bounded, secret-free exact-model evidence; never call a provider."""

    if not binding:
        return {
            "binding": "",
            "production_ready": False,
            "claim_status": "descriptive_only",
            "failure_reasons": ["binding_missing"],
            "probe": {"observed": False, "failure_reasons": ["probe_evidence_missing"]},
            "evaluation": {
                "observed": False,
                "sample_count": 0,
                "failure_reasons": ["evaluation_evidence_missing"],
            },
            "evidence_source": {"source": "not_configured", "parsed": False, "error": None},
        }
    try:
        item, source = exact_binding_readiness_from_environment(binding)
    except Exception:  # noqa: BLE001 - malformed operator evidence must fail closed
        return {
            "binding": binding,
            "production_ready": False,
            "claim_status": "descriptive_only",
            "failure_reasons": ["readiness_check_failed"],
            "probe": {"observed": False, "failure_reasons": ["probe_unreadable"]},
            "evaluation": {
                "observed": False,
                "sample_count": 0,
                "failure_reasons": ["evaluation_unreadable"],
            },
            "evidence_source": {"source": "unreadable", "parsed": False, "error": "readiness_check_failed"},
        }
    probe = item.get("probe") if isinstance(item.get("probe"), dict) else {}
    evaluation = item.get("evaluation") if isinstance(item.get("evaluation"), dict) else {}
    evidence_source = source if isinstance(source, dict) else {}
    return {
        "binding": str(item.get("binding") or binding),
        "state": str(item.get("state") or "unverified"),
        "configured": bool(item.get("configured")),
        "probed": bool(item.get("probed")),
        "evaluated": bool(item.get("evaluated")),
        "production_ready": bool(item.get("production_ready")),
        "availability": str(item.get("availability") or "unverified"),
        "claim_status": str(item.get("claim_status") or "descriptive_only"),
        "failure_reasons": [str(reason) for reason in item.get("failure_reasons") or []][:16],
        "probe": {
            "observed": bool(item.get("probed")),
            "as_of": probe.get("as_of"),
            "response_model": probe.get("response_model"),
            "attestation_verified": bool(probe.get("attestation_verified")),
            "failure_reasons": [str(reason) for reason in probe.get("failure_reasons") or []][:12],
        },
        "evaluation": {
            "observed": bool(item.get("evaluated")),
            "as_of": evaluation.get("as_of"),
            "sample_count": int(evaluation.get("sample_count") or 0),
            "integrity_verified": bool(evaluation.get("integrity_verified")),
            "attestation_verified": bool(evaluation.get("attestation_verified")),
            "failure_reasons": [str(reason) for reason in evaluation.get("failure_reasons") or []][:12],
        },
        "evidence_source": {
            "source": str(evidence_source.get("source") or "not_configured"),
            "parsed": evidence_source.get("parsed") is True,
            "error": str(evidence_source.get("error") or "") or None,
            "secret_values_exposed": False,
        },
    }


def _provider_connectivity_snapshot(provider: str) -> dict[str, Any]:
    """Read the last persisted credential/connectivity probe without probing now."""

    provider_key = {"gemini": "google", "claude": "anthropic"}.get(
        str(provider or "").strip().lower(), str(provider or "").strip().lower()
    )
    fallback = {
        "provider": provider_key,
        "status": "unknown",
        "healthy": False,
        "last_ok_at": None,
        "updated_at": None,
        "consecutive_failures": 0,
        "source": "provider_status",
        "provider_called": False,
        "exact_model_evidence": False,
    }
    if not provider_key:
        return fallback
    try:
        if not table_exists("provider_status"):
            return fallback
        row = get_conn().execute(
            "SELECT provider, latest_status, last_ok_at, updated_at, consecutive_failures "
            "FROM provider_status WHERE provider=?",
            (provider_key,),
        ).fetchone()
        if row is None:
            return fallback
        item = dict(row)
        status = str(item.get("latest_status") or "unknown")
        return {
            **fallback,
            "status": status,
            "healthy": status == "healthy",
            "last_ok_at": item.get("last_ok_at"),
            "updated_at": item.get("updated_at"),
            "consecutive_failures": int(item.get("consecutive_failures") or 0),
        }
    except Exception:  # noqa: BLE001 - readiness remains fail-closed and non-500
        return fallback


def readiness() -> dict[str, Any]:
    persistence_ready = repository.schema_ready() and repository.claim_schema_ready()
    bridge = intelligent_bridge.readiness()
    operator_enabled = _truthy(os.environ.get(_EXTERNAL_ENABLE_ENV))
    try:
        preflight = _provider_preflight(
            '{"question":"synthetic readiness check","owner_scoped_context":{}}'
        )
        budget_checked = True
    except Exception:
        preflight = {
            "provider_calls_allowed": False,
            "provider_gate_reason": "preflight_failed",
            "providers": [],
        }
        budget_checked = False
    provider_ready = bool(operator_enabled and preflight.get("provider_calls_allowed"))
    providers = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    selected = providers[0] if providers else {}
    binding = str(selected.get("binding") or _binding()[2])
    model_evidence = _exact_model_evidence(binding)
    provider_connectivity = _provider_connectivity_snapshot(
        str(selected.get("provider") or _binding()[0])
    )
    ai_off_path_ready = bool(persistence_ready and bridge.get("ready"))
    blockers = _readiness_blockers(
        persistence_ready=persistence_ready,
        bridge=bridge,
        operator_enabled=operator_enabled,
        preflight=preflight,
        selected=selected,
    )
    status = "ready" if persistence_ready and bridge.get("ready") and provider_ready else "degraded"
    return {
        "status": status,
        "core_status": "ready" if ai_off_path_ready else "blocked",
        "external_ai_status": "ready" if provider_ready else "blocked",
        "ai_off_path_ready": ai_off_path_ready,
        "external_ai_ready": provider_ready,
        "provider_ready": provider_ready,
        "provider_called": False,
        "reason": "" if provider_ready else _provider_reason(preflight, operator_enabled=operator_enabled),
        "blockers": blockers,
        "binding": binding,
        "binding_gate_reason": selected.get("binding_gate_reason") or "blocked",
        "budget_checked": budget_checked,
        "budget_authorized": bool(selected.get("budget_allowed")) if selected else False,
        "estimated_max_call_cost_usd": selected.get("estimated_cost_usd"),
        "operator_enabled": operator_enabled,
        "provider_policy": "explicit_message_opt_in+exact_model_readiness+dedicated_budget+durable_claim",
        "knowledge_bridge_ready": bool(bridge.get("ready")),
        "knowledge_bridge_reason": str(bridge.get("reason") or ""),
        "knowledge_bridge_mode": str(bridge.get("mode") or ""),
        "legacy_global_search_used": False,
        "persistence_ready": persistence_ready,
        "persistence_reason": "" if persistence_ready else "advisor_claim_schema_unavailable",
        "action_mode": "draft_only",
        "capabilities": {
            "conversation_persistence": {
                "ready": persistence_ready,
                "provider_required": False,
            },
            "owner_scoped_memory_and_context": {
                "ready": bool(bridge.get("ready")),
                "provider_required": False,
                "scope_dimensions": list(bridge.get("scope_dimensions") or []),
            },
            "local_context_recall": {
                "ready": ai_off_path_ready,
                "provider_required": False,
                "provider_calls_allowed": False,
            },
            "model_generated_advice": {
                "ready": provider_ready,
                "provider_required": True,
                "reason": "" if provider_ready else "advisor_external_model_generation_blocked",
            },
            "external_model_generation": {
                "ready": provider_ready,
                "requires_explicit_message_opt_in": True,
                "requires_durable_idempotency_key": True,
                "blockers": [item for item in blockers if item.startswith("advisor_")],
            },
            "business_actions": {
                "ready": persistence_ready,
                "mode": "draft_only",
                "execution_allowed": False,
            },
        },
        "provider_connectivity": provider_connectivity,
        "exact_model_evidence": model_evidence,
        "retryable": not provider_ready,
        "claim_status": "descriptive_only",
    }


def _replay_response(replay: dict[str, Any]) -> dict[str, Any]:
    messages = replay.get("messages") if isinstance(replay.get("messages"), list) else []
    assistant = next(
        (item for item in reversed(messages) if isinstance(item, dict) and item.get("role") == "assistant"),
        {},
    )
    metadata = assistant.get("metadata_json") if isinstance(assistant.get("metadata_json"), dict) else {}
    provider_called = bool(metadata.get("provider_called"))
    result_status = "ok" if assistant.get("status") == "ready" else "degraded"
    provider = {**readiness(), "provider_called": provider_called}
    return {
        "status": result_status,
        "reason": str(assistant.get("provider_reason") or ""),
        "provider": provider,
        "messages": messages,
        "draft_actions": replay.get("draft_actions") or [],
        "idempotent_replay": True,
        "claim_state": "completed",
        "claim_status": "descriptive_only",
        "knowledge_bridge": {
            "status": str(metadata.get("bridge_status") or "ready"),
            "mode": str(metadata.get("bridge_mode") or "advisor_owner_scope_v1"),
            "reason": str(metadata.get("bridge_reason") or ""),
        },
    }


def create_message_turn(
    scope: AdvisorScope,
    thread_uid: str,
    *,
    content: str,
    context_refs: Any = None,
    requested_actions: Any = None,
    client_request_id: str = "",
    allow_external_ai: bool = False,
) -> dict[str, Any]:
    if not repository.schema_ready():
        raise repository.AdvisorSchemaUnavailable(
            "migration 250_vkpi_marketing_advisor_memory.sql is not applied"
        )
    request_key = str(client_request_id or "").strip()[:120]
    claim: dict[str, Any] = {"status": "not_required", "state": "unclaimed"}
    claim_token = ""
    if request_key:
        claim = repository.claim_turn_request(
            scope,
            thread_uid,
            request_key,
            request_sha256=_request_hash(
                content=content,
                context_refs=context_refs,
                requested_actions=requested_actions,
                allow_external_ai=allow_external_ai,
            ),
        )
        if claim.get("status") == "replay":
            return _replay_response(claim.get("replay") or {})
        if claim.get("status") != "acquired":
            return {
                "status": "blocked" if claim.get("status") == "blocked" else "pending",
                "reason": claim.get("reason") or "request_in_progress",
                "provider": readiness(),
                "messages": [],
                "draft_actions": [],
                "idempotent_replay": False,
                "claim_state": claim.get("state"),
                "claim_status": "descriptive_only",
                "retryable": claim.get("status") == "in_progress",
                "knowledge_bridge": {"status": "pending", "mode": "", "reason": ""},
            }
        claim_token = str(claim.get("claim_token") or "")

    # Use this request's refs for this request.  Previously the bridge read the
    # thread before the new refs were persisted, so changed selections affected
    # only the following turn.
    bridged = intelligent_bridge.answer(
        content,
        scope,
        thread_uid=thread_uid,
        context_refs=context_refs,
    )
    prompt, allowed_evidence_ids = _provider_prompt(content, bridged)
    operator_enabled = _truthy(os.environ.get(_EXTERNAL_ENABLE_ENV))
    try:
        preflight = _provider_preflight(prompt)
    except Exception:
        preflight = {
            "provider_calls_allowed": False,
            "provider_gate_reason": "preflight_failed",
            "providers": [],
        }
    provider_requested = bool(allow_external_ai)
    provider_allowed = bool(
        provider_requested
        and request_key
        and operator_enabled
        and preflight.get("provider_calls_allowed")
    )
    provider_called = False
    assistant_content = str(bridged.get("answer") or "")
    assistant_status = "degraded"
    provider_status = "not_requested" if not provider_requested else "blocked"
    provider_reason = (
        "advisor_external_ai_not_requested"
        if not provider_requested
        else "advisor_idempotency_key_required"
        if not request_key
        else _provider_reason(preflight, operator_enabled=operator_enabled)
    )
    provider_metadata: dict[str, Any] = {
        "provider_requested": provider_requested,
        "provider_streaming": False,
        "response_contract": "json_v1",
    }
    evidence_ids_used: list[str] = []
    memory_used = bool(
        (bridged.get("provider_context") or {}).get("memories")
        if isinstance(bridged.get("provider_context"), dict)
        else False
    )

    if provider_allowed:
        provider, model, binding = _binding()
        repository.mark_turn_provider_started(
            scope,
            thread_uid,
            request_key,
            claim_token,
            provider_binding=binding,
        )
        provider_called = True  # durable conservative boundary: request may be sent
        try:
            result = llm_production.generate_json(
                prompt,
                provider=provider,
                model=model,
                purpose=_PURPOSE,
                max_output_tokens=_PROVIDER_MAX_OUTPUT_TOKENS,
                cost_tag=_COST_SCOPE,
                triggered_by=scope.staff_id,
                metadata={
                    "surface": "marketing_advisor",
                    "organization_id": scope.organization_id,
                    "staff_id": scope.staff_id,
                    "thread_uid": thread_uid,
                    "client_request_sha256": _canonical_hash(request_key),
                    "claim_status": "descriptive_only",
                },
                staff={"id": scope.staff_id},
                required_keys=("answer", "evidence_ids", "confidence"),
                validator=_json_validator(allowed_evidence_ids),
                deadline_seconds=45.0,
            )
        except Exception:
            repository.mark_turn_outcome_unknown(
                scope,
                thread_uid,
                request_key,
                claim_token,
                failure_code="provider_call_exception_outcome_unknown",
            )
            return {
                "status": "blocked",
                "reason": "provider_outcome_unknown_manual_reconciliation_required",
                "provider": {**readiness(), "provider_called": True},
                "messages": [],
                "draft_actions": [],
                "idempotent_replay": False,
                "claim_state": "outcome_unknown",
                "claim_status": "descriptive_only",
                "retryable": False,
                "knowledge_bridge": {
                    "status": bridged.get("status"),
                    "mode": bridged.get("mode"),
                    "reason": bridged.get("reason"),
                },
            }
        if result.get("status") == "success" and isinstance(result.get("json"), dict):
            output = result["json"]
            assistant_content = str(output.get("answer") or "").strip()[:12_000]
            evidence_ids_used = [str(item) for item in output.get("evidence_ids") or []]
            assistant_status = "ready"
            provider_status = "ready"
            provider_reason = ""
        else:
            provider_status = "failed"
            provider_reason = str(result.get("failure_code") or result.get("reason") or "provider_failed")[:160]
        provider_metadata.update(
            {
                "provider": str(result.get("provider") or "")[:40],
                "model": str(result.get("model") or "")[:160],
                "latency_ms": int(result.get("latency_ms") or result.get("elapsed_ms") or 0),
                "cost_micro_usd": int(result.get("cost_micro_usd") or 0),
                "provider_attempts": int(result.get("provider_attempts") or 0),
                "confidence": (
                    result.get("json", {}).get("confidence")
                    if isinstance(result.get("json"), dict)
                    else None
                ),
            }
        )

    evidence_manifest = [
        {
            "evidence_id": item.get("evidence_id"),
            "kind": item.get("kind"),
        }
        for item in bridged.get("evidence") or []
        if isinstance(item, dict) and item.get("evidence_id")
    ][:32]
    turn = repository.create_degraded_turn(
        scope,
        thread_uid,
        content_text=content,
        context_refs=context_refs,
        requested_actions=requested_actions,
        client_request_id=request_key,
        provider_reason=provider_reason,
        assistant_content=assistant_content,
        assistant_provenance={
            "bridge": "advisor_owner_scope_v1",
            "evidence": evidence_manifest,
            "provider_evidence_ids": evidence_ids_used,
        },
        assistant_metadata={
            "bridge_status": bridged.get("status"),
            "bridge_mode": bridged.get("mode"),
            "bridge_reason": bridged.get("reason"),
            "navigation_actions": bridged.get("navigation_actions") or [],
            **provider_metadata,
        },
        assistant_status=assistant_status,
        provider_status=provider_status,
        provider_called=provider_called,
        memory_used=memory_used,
        claim_token=claim_token,
    )
    current_readiness = readiness()
    return {
        "status": "ok" if assistant_status == "ready" else "degraded",
        "reason": provider_reason,
        "provider": {**current_readiness, "provider_called": provider_called},
        "messages": turn["messages"],
        "draft_actions": turn["draft_actions"],
        "idempotent_replay": bool(turn.get("idempotent_replay")),
        "claim_state": "completed" if claim_token else "unclaimed",
        "claim_status": "descriptive_only",
        "knowledge_bridge": {
            "status": bridged.get("status"),
            "mode": bridged.get("mode"),
            "reason": bridged.get("reason"),
        },
    }
