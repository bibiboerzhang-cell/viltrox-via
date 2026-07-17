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
    except (TypeError, ValueError):
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
    gateway.record_call(
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

    ``validator`` must return a truthy value (or ``(truthy, detail)``) to accept
    the decoded value. Provider attempts are recorded even when transport,
    parsing, or validation fails. Exact models and provider defaults share the
    same fail-closed runtime gate as :func:`llm_gateway.invoke`. An exact request
    without explicit ``model_fallbacks`` never falls into global defaults.

    ``require_runtime_verified`` remains accepted for call compatibility, but
    False cannot disable the production runtime-verification boundary.
    ``enforce_atomic_reservation`` is the explicit non-production migration
    boundary. Production forces it on centrally: one committed budget
    reservation is created and marked started immediately before every provider
    attempt, then settled or left ``unknown`` conservatively.
    """

    gateway = _gateway_module()
    enforce_atomic_reservation = gateway._strict_atomic_reservation_enabled(
        enforce_atomic_reservation
    )
    started = gateway.time.monotonic()
    resolved_deadline = gateway._resolve_deadline_seconds(deadline_seconds)
    deadline_at = started + resolved_deadline
    max_output_tokens = max(16, int(max_output_tokens or 0))
    safe_prompt = str(prompt or "")
    contract_keys = gateway._normalise_required_keys(required_keys)
    attempt_limit = None if max_provider_attempts is None else max(1, int(max_provider_attempts))

    if not safe_prompt.strip():
        fallback = gateway._rule_fallback(safe_prompt, purpose=purpose, reason="empty_prompt")
        fallback.update({"json": None, "deadline_seconds": resolved_deadline, "elapsed_ms": 0})
        gateway.record_call(
            provider="rule_v0",
            model="rule_v0",
            purpose=purpose,
            prompt=safe_prompt,
            status="empty_prompt",
            fallback_used=True,
            cost_tag=cost_tag,
            triggered_by=triggered_by,
            metadata={**(metadata or {}), "reason": fallback["reason"], "json_contract": True},
            staff=staff,
        )
        return fallback

    cost_scope = gateway._cost_scope_for_purpose(purpose, cost_tag)
    budget_warnings: list[dict[str, Any]] = []
    if cost_scope:
        try:
            if not gateway._budget_guard().check_budget(cost_scope, 0, require_configured=True):
                budget_warnings.append(
                    {"stage": "scope_preflight", "reason": "ai_budget_hard_stop", "cost_tag": cost_scope}
                )
                gateway.logger.warning(
                    "vkpi.llm_gateway.ai_budget_hard_stop_record_only",
                    extra={"cost_tag": cost_scope, "purpose": purpose},
                )
        except Exception:
            gateway.logger.warning("vkpi.llm_gateway.ai_budget_check_failed", exc_info=True)

    if not skip_budget_check:
        monthly_budget = gateway._monthly_budget_cents()
        remaining = gateway._budget_remaining_cents()
        if monthly_budget <= 0 or remaining <= 0:
            reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
            budget_warnings.append(
                {
                    "stage": "monthly_preflight",
                    "reason": reason,
                    "monthly_budget_cents": monthly_budget,
                    "remaining_cents": remaining,
                }
            )
            gateway.logger.warning(
                "vkpi.llm_gateway.monthly_budget_record_only",
                extra={"reason": reason, "purpose": purpose, "remaining_cents": remaining},
            )

    errors: list[dict[str, Any]] = []
    deadline_hit = False
    provider_attempts = 0
    candidates = gateway._ordered_model_candidates(
        preferred_provider,
        model_override,
        model_fallbacks,
    )
    for candidate_index, (provider, model_id, explicit_model) in enumerate(candidates):
        if gateway.time.monotonic() >= deadline_at:
            errors.append(
                {
                    "provider": provider or "unknown",
                    "model": model_id,
                    "status": "deadline_exceeded",
                    "error": "gateway deadline exceeded",
                }
            )
            deadline_hit = True
            break
        binding = gateway._resolve_gateway_binding(provider, model_id)
        binding_blocker = gateway._binding_call_blocker(
            binding,
            explicit_model=explicit_model,
            require_runtime_verified=require_runtime_verified,
        )
        if binding_blocker:
            errors.append(
                {
                    "provider": provider or "unknown",
                    "model": model_id,
                    "binding": binding.binding,
                    "status": "model_binding_blocked",
                    "error": binding_blocker,
                }
            )
            continue
        if not gateway._is_provider_configured(provider):
            errors.append({"provider": provider, "model": model_id, "status": "not_configured"})
            continue
        caller = gateway._PROVIDER_CALLERS.get(provider)
        if caller is None:
            errors.append({"provider": provider, "model": model_id, "status": "not_implemented"})
            continue

        estimated_cost = gateway._estimated_cost_usd(
            provider,
            prompt=safe_prompt,
            max_output_tokens=max_output_tokens,
            binding=binding,
        )
        try:
            provider_allowed, budget_checks = gateway._budget_allows_provider(
                provider,
                cost_scope=cost_scope,
                estimated_cost_usd=estimated_cost,
                require_configured=require_configured_budget,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed before a billable request
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "budget_check_failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:260]}",
                }
            )
            continue
        if not provider_allowed:
            budget_warnings.append(
                {
                    "stage": "provider_preflight",
                    "provider": provider,
                    "model": model_id,
                    "reason": "budget_blocked",
                    "estimated_cost_usd": estimated_cost,
                    "budget_checks": budget_checks,
                }
            )
            gateway._record_budget_blocked_attempt(
                provider,
                binding=binding,
                purpose=purpose,
                prompt=safe_prompt,
                cost_scope=cost_scope,
                estimated_cost_usd=estimated_cost,
                budget_checks=budget_checks,
                triggered_by=triggered_by,
                metadata=metadata,
                staff=staff,
            )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "budget_blocked",
                    "error": "budget_blocked",
                }
            )
            continue
        if gateway.time.monotonic() >= deadline_at:
            errors.append(
                {"provider": provider, "status": "deadline_exceeded", "error": "gateway deadline exceeded"}
            )
            deadline_hit = True
            break
        if attempt_limit is not None and provider_attempts >= attempt_limit:
            errors.append(
                {
                    "provider": "gateway",
                    "status": "provider_attempt_limit",
                    "error": f"provider attempt limit reached ({attempt_limit})",
                }
            )
            break

        reservation_key = ""
        breaker_permit = None
        try:
            if enforce_atomic_reservation:
                reservation = gateway._llm_budget_reservations().reserve_llm_budget(
                    provider=provider,
                    model=binding.model_id,
                    purpose=purpose,
                    prompt=safe_prompt,
                    estimated_cost_usd=estimated_cost,
                    cost_scope=cost_scope,
                    metadata=metadata,
                    staff=staff,
                    triggered_by=triggered_by,
                )
                reservation_key = str(reservation.reservation_key or "")
            breaker_permit = gateway._acquire_strict_fleet_breaker(
                provider=provider,
                model=binding.model_id,
                enforce_atomic_reservation=enforce_atomic_reservation,
            )
            if enforce_atomic_reservation:
                gateway._llm_budget_reservations().mark_llm_provider_started(
                    reservation_key
                )
        except Exception as exc:  # noqa: BLE001 - fail closed before network
            if breaker_permit is not None:
                try:
                    gateway._abandon_strict_fleet_breaker(breaker_permit)
                except Exception:
                    gateway.logger.error(
                        "vkpi.llm_gateway.json_fleet_breaker_abandon_failed",
                        extra={"provider": provider, "model": binding.model_id},
                        exc_info=True,
                    )
            if reservation_key:
                try:
                    gateway._llm_budget_reservations().release_llm_reservation(
                        reservation_key
                    )
                except Exception:
                    gateway.logger.error(
                        "vkpi.llm_gateway.json_reservation_release_failed",
                        extra={"reservation_key": reservation_key},
                        exc_info=True,
                    )
            reason = str(getattr(exc, "reason", "") or type(exc).__name__)
            blocked_scope = str(getattr(exc, "scope", "") or "")
            failure_reason = str(getattr(exc, "reason", "") or "")
            blocked_status = (
                failure_reason
                if failure_reason.startswith("fleet_breaker_")
                else "budget_blocked"
            )
            gateway.record_call(
                provider=provider,
                model=binding.model_id,
                purpose=purpose,
                prompt=safe_prompt,
                status=blocked_status,
                fallback_used=True,
                cost_tag=cost_scope or gateway.SINGLE_CALL_BUDGET_SCOPE,
                triggered_by=triggered_by,
                metadata={
                    **(metadata or {}),
                    "json_contract": True,
                    "reservation_key": reservation_key,
                    "reservation_reason": reason,
                    "reservation_scope": blocked_scope,
                    "fleet_breaker_blocked": blocked_status.startswith("fleet_breaker_"),
                    "estimated_cost_usd": estimated_cost,
                    "resolved_model_binding": binding.to_dict(),
                    "request_content_recorded": False,
                },
                staff=staff,
                update_budget_scopes=not enforce_atomic_reservation,
                force_cost_ledger=enforce_atomic_reservation,
            )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": blocked_status,
                    "error": reason,
                    "scope": blocked_scope,
                }
            )
            continue

        try:
            provider_attempts += 1
            if explicit_model:
                raw_result = caller(
                    safe_prompt,
                    max_output_tokens,
                    model_override=binding.model_id,
                )
            else:
                raw_result = caller(safe_prompt, max_output_tokens)
            result = (
                dict(raw_result)
                if isinstance(raw_result, dict)
                else {
                    "status": "failed",
                    "provider": provider,
                    "error": "provider returned a non-object result",
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolate provider failures for fallback
            if reservation_key:
                gateway._mark_reserved_attempt_unknown(reservation_key)
            result = {
                "status": "provider_exception",
                "provider": provider,
                "error": (
                    type(exc).__name__
                    if reservation_key
                    else f"{type(exc).__name__}: {str(exc)[:300]}"
                ),
            }

        try:
            gateway._complete_strict_fleet_breaker(breaker_permit, result)
        except Exception as breaker_exc:  # noqa: BLE001 - no fallback provider after state loss
            if reservation_key:
                gateway._mark_reserved_attempt_unknown(reservation_key)
            gateway.logger.error(
                "vkpi.llm_gateway.json_fleet_breaker_completion_failed",
                extra={
                    "provider": provider,
                    "model": binding.model_id,
                    "breaker_error_type": type(breaker_exc).__name__,
                },
            )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "fleet_breaker_store_unavailable_after_provider",
                    "error": type(breaker_exc).__name__,
                }
            )
            fallback = gateway._rule_fallback(
                safe_prompt,
                purpose=purpose,
                reason="fleet_breaker_store_unavailable_after_provider",
                errors=errors,
            )
            fallback.update(
                {
                    "json": None,
                    "deadline_seconds": resolved_deadline,
                    "elapsed_ms": max(
                        0, int((gateway.time.monotonic() - started) * 1000)
                    ),
                    "provider_attempts": provider_attempts,
                    "budget_reservation_key": reservation_key or None,
                }
            )
            return fallback

        completed_after_deadline = gateway.time.monotonic() >= deadline_at
        provider_status = str(result.get("status") or "failed")
        response_text = str(result.get("text") or "")
        attempt_status = provider_status
        attempt_error = str(result.get("error") or "")[:300]
        parsed_value: Any = None

        result_in_tokens = gateway._safe_int(result.get("input_tokens"))
        result_out_tokens = gateway._safe_int(result.get("output_tokens"))
        result_micro_raw = result.get("cost_micro_usd")
        result_micro_for_settlement = (
            gateway._safe_int(result_micro_raw)
            if result_micro_raw is not None
            else gateway._estimate_cost_micro_usd(
                provider,
                result_in_tokens,
                result_out_tokens,
                binding=binding,
            )
        )
        if (
            reservation_key
            and provider_status != "success"
            and provider_status != "provider_exception"
        ):
            gateway._mark_reserved_attempt_unknown(reservation_key)

        if provider_status == "success":
            actual_model = str(result.get("model") or "").strip()
            if explicit_model and not binding.matches_response_model(actual_model):
                attempt_status = "model_mismatch"
                attempt_error = "provider response model did not match exact request"
            elif not response_text.strip():
                attempt_status = "empty_response"
                attempt_error = "provider returned an empty response"
            else:
                try:
                    parsed_value = gateway._extract_json_value(response_text)
                except ValueError as exc:
                    attempt_status = "parse_failure"
                    attempt_error = str(exc)[:300]
                else:
                    validation_error = gateway._validate_json_contract(
                        parsed_value,
                        required_keys=contract_keys,
                        validator=validator,
                    )
                    if validation_error:
                        attempt_status = "validation_failure"
                        attempt_error = validation_error
                    elif completed_after_deadline:
                        attempt_status = "deadline_exceeded"
                        attempt_error = "provider completed after gateway deadline"
                    else:
                        attempt_status = "success"
                        attempt_error = ""

        try:
            result_micro = gateway._record_json_provider_attempt(
                provider,
                result,
                binding=binding,
                explicit_model=explicit_model,
                candidate_index=candidate_index,
                status=attempt_status,
                purpose=purpose,
                prompt=safe_prompt,
                cost_scope=cost_scope,
                triggered_by=triggered_by,
                metadata=metadata,
                staff=staff,
                attempt_errors=list(errors),
                budget_checks=budget_checks,
                budget_warnings=budget_warnings,
                estimated_cost_usd=estimated_cost,
                deadline_seconds=resolved_deadline,
                reservation_key=reservation_key,
                error=attempt_error,
            )
        except Exception as exc:  # noqa: BLE001 - strict calls fail closed on audit loss
            if not reservation_key:
                raise
            # Provider network I/O already happened.  Keep the reservation open
            # for reconciliation and never settle a billable attempt that has no
            # durable vkpi_llm_calls audit row.
            gateway._mark_reserved_attempt_unknown(reservation_key)
            gateway.logger.error(
                "vkpi.llm_gateway.json_provider_audit_failed",
                extra={
                    "provider": provider,
                    "purpose": purpose,
                    "reservation_key": reservation_key,
                    "audit_error_type": type(exc).__name__,
                },
            )
            if attempt_status != "success":
                errors.append(
                    {
                        "provider": provider,
                        "model": model_id,
                        "status": attempt_status,
                        "error": attempt_error,
                    }
                )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "audit_ledger_unavailable",
                    "error": type(exc).__name__,
                }
            )
            fallback = gateway._rule_fallback(
                safe_prompt,
                purpose=purpose,
                reason="audit_ledger_unavailable",
                errors=errors,
            )
            fallback.update(
                {
                    "json": None,
                    "deadline_seconds": resolved_deadline,
                    "elapsed_ms": max(
                        0, int((gateway.time.monotonic() - started) * 1000)
                    ),
                    "provider_attempts": provider_attempts,
                    "budget_reservation_key": reservation_key,
                }
            )
            return fallback

        # The call row is the durable audit anchor for the reservation.  Settle
        # only after JSON parsing/validation chose the final attempt status and
        # that exact provider result was committed to the ledger.
        if reservation_key and provider_status == "success":
            try:
                settlement = gateway._llm_budget_reservations().settle_llm_reservation(
                    reservation_key,
                    float(result_micro_for_settlement) / 1_000_000,
                )
                if not bool(settlement.get("settled")):
                    raise RuntimeError(
                        str(settlement.get("reason") or "reservation_not_settled")
                    )
            except Exception as exc:  # noqa: BLE001 - cost happened; keep open
                gateway._mark_reserved_attempt_unknown(reservation_key)
                gateway.logger.error(
                    "vkpi.llm_gateway.json_reservation_settlement_failed",
                    extra={
                        "provider": provider,
                        "purpose": purpose,
                        "reservation_key": reservation_key,
                        "settlement_error_type": type(exc).__name__,
                    },
                )
                errors.append(
                    {
                        "provider": provider,
                        "model": model_id,
                        "status": "reservation_settlement_failed",
                        "error": "reservation_settlement_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                fallback = gateway._rule_fallback(
                    safe_prompt,
                    purpose=purpose,
                    reason="reservation_settlement_failed",
                    errors=errors,
                )
                fallback.update(
                    {
                        "json": None,
                        "deadline_seconds": resolved_deadline,
                        "elapsed_ms": max(
                            0, int((gateway.time.monotonic() - started) * 1000)
                        ),
                        "provider_attempts": provider_attempts,
                        "budget_reservation_key": reservation_key,
                    }
                )
                return fallback

        if attempt_status == "success":
            result.update(
                {
                    "json": parsed_value,
                    "fallback_used": bool(errors),
                    "purpose": purpose,
                    "cost_micro_usd": result_micro,
                    "errors": [
                        gateway._normalise_runtime_error(item) for item in errors
                    ],
                    "deadline_seconds": resolved_deadline,
                    "elapsed_ms": max(0, int((gateway.time.monotonic() - started) * 1000)),
                    "provider_attempts": provider_attempts,
                    "resolved_model_binding": binding.to_dict(),
                    "budget_reservation_key": reservation_key or None,
                }
            )
            return result

        errors.append({"provider": provider, "status": attempt_status, "error": attempt_error})
        if completed_after_deadline:
            if attempt_status != "deadline_exceeded":
                errors.append(
                    {
                        "provider": "gateway",
                        "status": "deadline_exceeded",
                        "error": "gateway deadline exceeded",
                    }
                )
            deadline_hit = True
            break

    fallback_reason = "deadline_exceeded" if deadline_hit else "all_providers_failed"
    fallback = gateway._rule_fallback(safe_prompt, purpose=purpose, reason=fallback_reason, errors=errors)
    fallback.update(
        {
            "json": None,
            "deadline_seconds": resolved_deadline,
            "elapsed_ms": max(0, int((gateway.time.monotonic() - started) * 1000)),
            "provider_attempts": provider_attempts,
        }
    )
    gateway.record_call(
        provider="rule_v0",
        model="rule_v0",
        purpose=purpose,
        prompt=safe_prompt,
        status=fallback_reason,
        fallback_used=True,
        cost_tag=cost_scope,
        triggered_by=triggered_by,
        metadata={
            **(metadata or {}),
            "errors": errors,
            "json_contract": True,
            "deadline_seconds": resolved_deadline,
            # 如实口径:空的 fallback 链(绑定钉死)= 没有会被尝试的模型级后备胎。
            "model_level_fallback": bool(model_fallbacks),
            "runtime_verification_hard_gate": True,
        },
        staff=staff,
    )
    return fallback
