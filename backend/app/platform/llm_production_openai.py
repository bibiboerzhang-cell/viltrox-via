"""OpenAI Responses-API (text + image) strict adapter (new, 2026-08-23).

The canonical text gateway already drives OpenAI for plain prompts.  Keyframe
judgment needs ``input_image`` parts, which the text gateway cannot carry, so
this narrow SDK adapter applies the same exact-model task binding, readiness
gate, atomic budget reservation, fleet breaker, ledger and settlement contract
as the Anthropic/Gemini siblings.  The caller keeps its own client (the
proxy-aware ``services.ai.clients.openai_client``), retry and JSON parsing.

调用方 / 测试只认门面 ``app.platform.llm_production``;本模块不得被业务代码直接
import。任务绑定经门面解析(llm_production_common.expected_task_binding)。
请求形状与网关传输层一致:gpt-5 系不传 temperature/top_p;reasoning.effort 只按
精确 id 表注入(gpt-5.5 保持 provider 默认)。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from app.platform import llm_gateway
from app.platform.llm_gateway_providers import _openai_reasoning_effort
from app.platform.llm_production_common import (
    expected_task_binding as _expected_task_binding,
    progress_metadata as _progress_metadata,
    sdk_failure as _sdk_failure,
)

_ENTRYPOINT = "llm_production_openai_responses_v1"
# 预留估算:detail=high 的图按 OpenAI 瓦片上限保守估(1 张 ≈ 1600 token);
# 文本按 4 字符/token。估高不估低——预留只是上限,结算按真实 usage。
OPENAI_IMAGE_TOKENS = 1600
_TEXT_DIVISOR = 4.0
OPENAI_RESPONSES_MAX_OUTPUT_TOKENS_HARD_CAP = 8192


def openai_input_fingerprint(input_items: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            input_items,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"openai_responses_sha256:{digest}"


def openai_input_token_estimate(input_items: list[dict[str, Any]]) -> int:
    total = 256
    for item in input_items:
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, str):
            total += max(1, int(len(content) / _TEXT_DIVISOR))
            continue
        if not isinstance(content, list):
            raise ValueError("unsupported_openai_input_content")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("unsupported_openai_content_part")
            part_type = str(part.get("type") or "").strip().lower()
            if part_type in {"input_text", "text"}:
                total += max(1, int(len(str(part.get("text") or "")) / _TEXT_DIVISOR))
            elif part_type == "input_image":
                total += OPENAI_IMAGE_TOKENS
            else:
                raise ValueError(f"unsupported_openai_part_type:{part_type or 'missing'}")
    return max(1, total)


def openai_responses_create_kwargs(
    model: str, max_output_tokens: int, input_items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build ``responses.create`` kwargs: exact model, cap, caller input, effort policy."""

    kwargs: dict[str, Any] = {
        "model": str(model or "").strip(),
        "input": input_items,
        "max_output_tokens": int(max_output_tokens),
    }
    effort = _openai_reasoning_effort(str(model or ""))
    if effort:
        kwargs["reasoning"] = {"effort": effort}
    return kwargs


def openai_response_text(response: Any) -> str:
    """Return the joined output text (``output_text`` first, then walk ``output``)."""

    text = str(getattr(response, "output_text", "") or "").strip()
    if text:
        return text
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if str(getattr(part, "type", "") or "") in {"output_text", "text"}:
                parts.append(str(getattr(part, "text", "") or ""))
    return "".join(parts).strip()


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        raw_in = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
        raw_out = usage.get("output_tokens") or usage.get("completion_tokens") or 0
    else:
        raw_in = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
        raw_out = getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
    try:
        return max(0, int(raw_in or 0)), max(0, int(raw_out or 0))
    except (TypeError, ValueError):
        return 0, 0


def _record(**kwargs: Any) -> None:
    llm_gateway.record_call(**kwargs)


def generate_openai_responses(
    *,
    client: Any,
    input_items: list[dict[str, Any]],
    model: str,
    purpose: str,
    max_output_tokens: int,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Execute one exact OpenAI Responses (text+image) attempt under strict gates.

    ``input_items`` is passed to ``client.responses.create`` unchanged.  This
    boundary owns authorization, conservative media-cost reservation, progress,
    response-model/usage verification and settlement; callers keep their retry
    and parsing semantics.  A task binding is mandatory (no inferred purpose).
    """

    provider = "openai"
    exact_model = str(model or "").strip()
    exact_purpose = str(purpose or "").strip()
    if not exact_model or not isinstance(input_items, list) or not input_items:
        raise ValueError("model and non-empty input_items are required")
    try:
        output_limit = min(
            OPENAI_RESPONSES_MAX_OUTPUT_TOKENS_HARD_CAP, max(1, int(max_output_tokens))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("valid max_output_tokens is required") from exc
    progress_metadata = _progress_metadata(
        exact_purpose, metadata, phase="multimodal_generation"
    )
    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    expected_binding = _expected_task_binding(task_binding)
    actual_binding = f"{provider}/{exact_model}"
    if not task_binding or expected_binding != actual_binding:
        raise _sdk_failure(
            "task_binding_model_mismatch",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            details={
                "task_binding": task_binding,
                "expected_binding": expected_binding,
                "actual_binding": actual_binding,
            },
        )

    request_identity = openai_input_fingerprint(input_items)
    base_metadata = {
        **progress_metadata,
        "entrypoint": _ENTRYPOINT,
        "request_content_recorded": False,
    }
    preflight = llm_gateway.budget_preflight(
        request_identity,
        purpose=exact_purpose,
        max_output_tokens=output_limit,
        preferred_provider=provider,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        cost_tag=cost_tag,
        require_configured=False,
    )
    candidates = preflight.get("providers") if isinstance(preflight.get("providers"), list) else []
    candidate = next(
        (item for item in candidates if str(item.get("binding") or "") == actual_binding),
        {},
    )
    if not bool(candidate.get("provider_calls_allowed")):
        reason = str(
            candidate.get("binding_gate_reason")
            or preflight.get("provider_gate_detail")
            or preflight.get("provider_gate_reason")
            or "provider_calls_blocked"
        )
        _record(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            status="provider_blocked",
            fallback_used=True,
            metadata={**base_metadata, "provider_gate_reason": reason},
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
        )
        raise _sdk_failure(reason, provider=provider, model=exact_model, purpose=exact_purpose)

    binding = llm_gateway._resolve_gateway_binding(provider, exact_model)
    input_estimate = openai_input_token_estimate(input_items)
    estimated_micro = llm_gateway._estimate_cost_micro_usd(
        provider, input_estimate, output_limit, binding=binding
    )
    estimated_cost = max(0.000001, float(estimated_micro) / 1_000_000)
    cost_scope = llm_gateway._cost_scope_for_purpose(exact_purpose, cost_tag)
    reservation_key = ""
    breaker_session = None
    try:
        reservation = llm_gateway._llm_budget_reservations().reserve_llm_budget(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            estimated_cost_usd=estimated_cost,
            cost_scope=cost_scope,
            metadata={
                **base_metadata,
                "estimated_input_tokens": input_estimate,
                "max_output_tokens": output_limit,
            },
            staff=staff,
            triggered_by=triggered_by,
        )
        reservation_key = str(reservation.reservation_key or "")
        breaker_session = llm_gateway._acquire_strict_fleet_breaker(
            provider=provider, model=exact_model, enforce_atomic_reservation=True
        )
        llm_gateway._llm_budget_reservations().mark_llm_provider_started(reservation_key)
    except Exception as exc:
        if breaker_session is not None:
            try:
                llm_gateway._abandon_strict_fleet_breaker(breaker_session)
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.openai_breaker_abandon_failed_before_provider",
                    extra={"provider": provider, "model": exact_model},
                    exc_info=True,
                )
        if reservation_key:
            try:
                llm_gateway._llm_budget_reservations().release_llm_reservation(reservation_key)
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.openai_reservation_release_failed_before_provider",
                    extra={"reservation_key": reservation_key},
                    exc_info=True,
                )
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        _record(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            status="budget_blocked",
            fallback_used=True,
            cost_tag=cost_scope,
            metadata={
                **base_metadata,
                "reservation_reason": reason,
                "estimated_cost_usd": estimated_cost,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
            force_cost_ledger=True,
        )
        raise _sdk_failure(reason, provider=provider, model=exact_model, purpose=exact_purpose) from exc

    started = time.monotonic()
    try:
        response = client.responses.create(
            **openai_responses_create_kwargs(exact_model, output_limit, input_items)
        )
    except Exception as provider_exc:
        breaker_completion_error: Exception | None = None
        try:
            llm_gateway._complete_strict_fleet_breaker(breaker_session, provider_exc)
        except Exception as exc:  # shared health state is a hard boundary
            breaker_completion_error = exc
        try:
            _record(
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
                prompt=request_identity,
                status="provider_exception",
                fallback_used=False,
                metadata={
                    **base_metadata,
                    "reservation_key": reservation_key,
                    "reservation_estimated_cost_usd": estimated_cost,
                    "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                },
                triggered_by=triggered_by,
                staff=staff,
                update_budget_scopes=False,
            )
        except Exception:
            llm_gateway.logger.error(
                "vkpi.llm_production.openai_exception_audit_failed",
                extra={"provider": provider, "model": exact_model, "reservation_key": reservation_key},
                exc_info=True,
            )
        finally:
            llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        if breaker_completion_error is not None:
            raise _sdk_failure(
                "fleet_breaker_store_unavailable_after_provider",
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
            ) from breaker_completion_error
        raise

    input_tokens, output_tokens = _usage_tokens(response)
    response_model = str(getattr(response, "model", "") or "").strip()
    status = "success"
    if not binding.matches_response_model(response_model):
        status = "model_mismatch"
    elif input_tokens <= 0 or output_tokens <= 0:
        status = "usage_missing"
    try:
        llm_gateway._complete_strict_fleet_breaker(breaker_session, {"status": status})
    except Exception as exc:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise _sdk_failure(
            "fleet_breaker_store_unavailable_after_provider",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc
    actual_micro = (
        llm_gateway._estimate_cost_micro_usd(provider, input_tokens, output_tokens, binding=binding)
        if status == "success"
        else 0
    )
    try:
        _record(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=actual_micro,
            status=status,
            fallback_used=False,
            cost_tag=cost_scope,
            metadata={
                **base_metadata,
                "reservation_key": reservation_key,
                "reservation_estimated_cost_usd": estimated_cost,
                "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                "response_model": response_model,
                "max_output_tokens": output_limit,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
            force_cost_ledger=True,
        )
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise
    if status != "success":
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise _sdk_failure(status, provider=provider, model=exact_model, purpose=exact_purpose)
    try:
        settlement = llm_gateway._llm_budget_reservations().settle_llm_reservation(
            reservation_key, float(actual_micro) / 1_000_000
        )
        if not bool(settlement.get("settled")):
            raise RuntimeError(str(settlement.get("reason") or "not_settled"))
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise
    return response


__all__ = [
    "OPENAI_IMAGE_TOKENS",
    "OPENAI_RESPONSES_MAX_OUTPUT_TOKENS_HARD_CAP",
    "generate_openai_responses",
    "openai_input_fingerprint",
    "openai_input_token_estimate",
    "openai_response_text",
    "openai_responses_create_kwargs",
]
