"""JSON-contract invocation support for :mod:`app.platform.llm_gateway`.

The canonical gateway module re-exports these names. Runtime dependencies are
resolved through that module so existing monkeypatch paths such as
``app.platform.llm_gateway._PROVIDER_CALLERS`` keep controlling the call path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from app.platform import llm_gateway_result_cache as _result_cache
from app.platform.llm_gateway_call_hooks import (
    cache_model_label,
    deferred_or_none,
    serve_cached_result,
    store_cached_result,
)
from app.platform.llm_gateway_json_attempt_runtime import (
    preflight_candidate,
    run_candidate,
)
from app.platform.llm_gateway_json_runtime import invoke_json_runtime


DEFAULT_DEADLINE_SECONDS = 90.0
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def _gateway_module() -> Any:
    from app.platform import llm_gateway

    return llm_gateway


def _resolve_deadline_seconds(deadline_seconds: float | None) -> float:
    gateway = _gateway_module()
    if deadline_seconds is None:
        deadline_seconds = (
            gateway.os.getenv("VKPI_LLM_GATEWAY_DEADLINE_SECONDS")
            or gateway.os.getenv("LLM_GATEWAY_DEADLINE_SECONDS")
            or gateway.DEFAULT_DEADLINE_SECONDS
        )
    try:
        return max(0.0, float(deadline_seconds))
    except (TypeError, ValueError):
        return gateway.DEFAULT_DEADLINE_SECONDS


def _extract_json_value(text: str) -> Any:
    """Decode direct, fenced, or prose-wrapped JSON without repairing invalid JSON."""

    gateway = _gateway_module()
    raw = str(text or "")
    if not raw.strip():
        raise ValueError("empty response")

    candidates = [match.group(1) for match in gateway._JSON_FENCE_RE.finditer(raw)]
    candidates.append(raw)
    last_error = "no JSON object or array found"
    for candidate in candidates:
        stripped = candidate.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except (TypeError, ValueError) as exc:
            last_error = str(exc)

        # Balanced top-level spans handle prose around JSON without accepting a
        # valid nested object from inside an otherwise malformed outer payload.
        for container in gateway._json_container_candidates(candidate):
            try:
                return json.loads(container)
            except (TypeError, ValueError) as exc:
                last_error = str(exc)
    raise ValueError(last_error[:300])


def _json_container_candidates(text: str) -> Iterable[str]:
    start: int | None = None
    stack: list[str] = []
    in_string = False
    escaped = False
    matching = {"}": "{", "]": "["}

    for index, char in enumerate(text):
        if start is None:
            if char in "[{":
                start = index
                stack = [char]
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or stack[-1] != matching[char]:
                yield text[start : index + 1]
                start = None
                stack = []
            else:
                stack.pop()
                if not stack:
                    yield text[start : index + 1]
                    start = None

    if start is not None:
        yield text[start:]


def _normalise_required_keys(required_keys: Iterable[str] | None) -> tuple[str, ...]:
    if required_keys is None:
        return ()
    if isinstance(required_keys, str):
        required_keys = (required_keys,)
    return tuple(dict.fromkeys(str(key) for key in required_keys if str(key)))


def _validate_json_contract(
    value: Any,
    *,
    required_keys: tuple[str, ...],
    validator: Callable[[Any], Any] | None,
) -> str:
    if required_keys:
        if not isinstance(value, dict):
            return "required_keys require a top-level JSON object"
        missing = [key for key in required_keys if key not in value]
        if missing:
            return f"missing required keys: {', '.join(missing)}"
    if validator is None:
        return ""
    try:
        verdict = validator(value)
    except Exception as exc:  # noqa: BLE001 - caller validators are contract boundaries
        return f"validator raised {type(exc).__name__}: {str(exc)[:200]}"
    if isinstance(verdict, tuple):
        accepted = bool(verdict[0]) if verdict else False
        detail = str(verdict[1]) if len(verdict) > 1 else ""
    else:
        accepted = bool(verdict)
        detail = ""
    if accepted:
        return ""
    return detail[:300] or "validator rejected JSON"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _record_json_provider_attempt(
    provider: str,
    result: dict[str, Any],
    *,
    binding: Any,
    explicit_model: bool,
    candidate_index: int,
    status: str,
    purpose: str,
    prompt: str,
    cost_scope: str,
    triggered_by: Any,
    metadata: dict[str, Any] | None,
    staff: dict[str, Any] | None,
    attempt_errors: list[dict[str, Any]],
    budget_checks: list[dict[str, Any]],
    budget_warnings: list[dict[str, Any]],
    estimated_cost_usd: float,
    deadline_seconds: float,
    reservation_key: str = "",
    error: str = "",
    audit_sink: dict[str, Any] | None = None,
) -> int:
    gateway = _gateway_module()
    input_tokens = gateway._safe_int(result.get("input_tokens"))
    output_tokens = gateway._safe_int(result.get("output_tokens"))
    result_micro = result.get("cost_micro_usd")
    cost_micro_usd = (
        gateway._safe_int(result_micro)
        if result_micro is not None
        else gateway._estimate_cost_micro_usd(
            provider,
            input_tokens,
            output_tokens,
            binding=binding,
        )
    )
    actual_model = str(result.get("model") or "").strip()
    audit = gateway.record_call(
        provider=provider,
        model=actual_model or binding.model_id,
        purpose=purpose,
        prompt=prompt,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_cents=gateway._safe_int(result.get("cost_cents")),
        cost_micro_usd=cost_micro_usd,
        status=status,
        fallback_used=status != "success" or bool(attempt_errors),
        cost_tag=cost_scope,
        triggered_by=triggered_by,
        metadata={
            **(metadata or {}),
            "latency_ms": result.get("latency_ms"),
            "attempt_error": error,
            "attempt_errors": attempt_errors,
            "budget_checks": budget_checks,
            "budget_warnings": budget_warnings,
            "budget_gate": (
                "atomic_reservation" if reservation_key else "provider_hard_stop"
            ),
            "reservation_key": reservation_key,
            "estimated_cost_usd": estimated_cost_usd,
            "deadline_seconds": deadline_seconds,
            "requested_model": binding.model_id if explicit_model else "",
            "actual_model": actual_model or binding.model_id,
            "resolved_model_binding": binding.to_dict(),
            "model_fallback_index": candidate_index,
        },
        staff=staff,
        update_budget_scopes=not bool(reservation_key),
        force_cost_ledger=bool(reservation_key),
    )
    if audit_sink is not None and isinstance(audit, dict):
        audit_sink.update(audit)
    return cost_micro_usd


def invoke_json(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
    require_runtime_verified: bool = True,
    skip_budget_check: bool = False,
    require_configured_budget: bool = False,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    required_keys: Iterable[str] | None = None,
    validator: Callable[[Any], Any] | None = None,
    deadline_seconds: float | None = None,
    max_provider_attempts: int | None = None,
    enforce_atomic_reservation: bool = False,
) -> dict[str, Any]:
    """Invoke providers until a response satisfies the requested JSON contract.

    Provider ordering, runtime verification, budget reservation, attempt audit,
    JSON decoding/validation, cache and fail-closed fallback are orchestrated by
    a leaf runtime while this stable facade preserves every caller seam.
    """

    return invoke_json_runtime(
        _gateway_module(),
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        model_override=model_override,
        model_fallbacks=model_fallbacks,
        require_runtime_verified=require_runtime_verified,
        skip_budget_check=skip_budget_check,
        require_configured_budget=require_configured_budget,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
        required_keys=required_keys,
        validator=validator,
        deadline_seconds=deadline_seconds,
        max_provider_attempts=max_provider_attempts,
        enforce_atomic_reservation=enforce_atomic_reservation,
        preflight_candidate=preflight_candidate,
        run_candidate=run_candidate,
        build_cache_plan=_result_cache.build_cache_plan,
        cache_model_label=cache_model_label,
        serve_cached_result=serve_cached_result,
        deferred_or_none=deferred_or_none,
        store_cached_result=store_cached_result,
    )
