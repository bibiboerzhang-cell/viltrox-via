#!/usr/bin/env python3
"""Bounded exact-model Stage-1 canary for the active V-KPI task bindings.

The command is deliberately dry-run by default.  A live run is local-only and
requires an environment acknowledgement bound to the complete execution-plan
hash.  It is an unsigned connectivity observation, never production-readiness
evidence, and it does not modify the production readiness gate.

Only a fixed, non-sensitive canary prompt is sent.  Neither the prompt nor the
provider response is printed or persisted by this command; the report contains
only exact model identities, status, measured latency and a response digest.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts.ops.vkpi_stage1_model_canary_reporting import (  # noqa: E402
    AUTHORIZATION_ENV,
    CANARY_EXPECTED_RESPONSE,
    CANARY_VERSION,
    base_report as _base_report,
    result_row as _result_row,
    sha256_text as _sha256_text,
)
from app.core.config import IS_PRODUCTION  # noqa: E402
from app.core.model_registry import current_task_model_binding  # noqa: E402
from app.platform import llm_budget_reservations, llm_gateway  # noqa: E402
from app.platform.models.runtime import (  # noqa: E402
    ResolvedModelBinding,
    resolve_model_binding,
    split_binding,
)


# 2026-08-22 模型升级刀:TASK_MODEL_BINDING 去重后恰好 6 个精确绑定
# (anthropic/claude-sonnet-5、anthropic/claude-opus-5、google/gemini-3.6-flash、
# google/gemini-2.5-pro、openai/gpt-5.6-luna、openai/gpt-5.5)。A 车道改绑定表时这里
# 必须同步(tests/test_vkpi_stage1_model_canary.py 与 test_vkpi_model_evidence_plan_contract
# 的 unique_binding_count 同源);A 未合入前本不变量为红属预期。
EXPECTED_UNIQUE_BINDINGS = 6
MAX_CALLS_HARD_LIMIT = 8
MAX_OUTPUT_TOKENS_HARD_LIMIT = 128
MAX_PER_CALL_TIMEOUT_SECONDS = 30
MAX_TOTAL_TIMEOUT_SECONDS = 240
MAX_COST_USD_HARD_LIMIT = Decimal("0.10")
DEFAULT_MAX_CALLS = EXPECTED_UNIQUE_BINDINGS
DEFAULT_MAX_OUTPUT_TOKENS = 32
DEFAULT_PER_CALL_TIMEOUT_SECONDS = 20
DEFAULT_TOTAL_TIMEOUT_SECONDS = 180
DEFAULT_MAX_COST_USD = Decimal("0.01")
CANARY_COST_SCOPE = "cron:vkpi_stage1_model_canary"
CANARY_PURPOSE = "vkpi_stage1_model_canary"
_SINGLE_CALL_SCOPE = "single_call"
CANARY_PROMPT = (
    "V-KPI exact-model connectivity canary. Reply with exactly "
    "VKPI_STAGE1_CANARY_OK and nothing else."
)
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_SAFE_PROVIDER_STATUSES = frozenset(
    {
        "empty_response",
        "failed",
        "invalid_response",
        "invoker_exception",
        "not_configured",
        "provider_429",
        "provider_5xx",
        "provider_exception",
        "provider_http_error",
        "timeout",
        "transport_error",
    }
)


class CanarySafetyError(ValueError):
    """Raised before provider I/O when the requested plan is not bounded."""


@dataclass(frozen=True, slots=True)
class BindingPlan:
    binding: str
    provider: str
    model: str
    tasks: tuple[str, ...]
    estimated_cost_usd: Decimal
    resolved: ResolvedModelBinding


@dataclass(frozen=True, slots=True)
class CanaryPlan:
    bindings: tuple[BindingPlan, ...]
    selected: tuple[BindingPlan, ...]
    max_calls: int
    max_output_tokens: int
    per_call_timeout_seconds: int
    total_timeout_seconds: int
    max_cost_usd: Decimal
    estimated_cost_usd: Decimal
    manifest_sha256: str

    @property
    def authorization_value(self) -> str:
        return f"approve:{self.manifest_sha256}"


LiveInvoker = Callable[[str, str, int, int], Mapping[str, Any]]
ProviderConfigured = Callable[[str], bool]
BudgetChecker = Callable[[BindingPlan], bool]
LedgerRecorder = Callable[..., Any]
Clock = Callable[[], float]


class ReservationManager(Protocol):
    def reserve_llm_budget(self, **kwargs: Any) -> Any: ...

    def mark_llm_provider_started(self, reservation_key: str) -> None: ...

    def mark_llm_provider_unknown(self, reservation_key: str) -> bool: ...

    def release_llm_reservation(self, reservation_key: str) -> bool: ...

    def settle_llm_reservation(
        self,
        reservation_key: str,
        actual_cost_usd: Decimal | float | str,
    ) -> Mapping[str, Any]: ...


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decimal(value: Decimal | float | str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CanarySafetyError("max_cost_usd_invalid") from exc
    if not result.is_finite():
        raise CanarySafetyError("max_cost_usd_invalid")
    return result


def _validate_limits(
    *,
    max_calls: int,
    max_output_tokens: int,
    per_call_timeout_seconds: int,
    total_timeout_seconds: int,
    max_cost_usd: Decimal,
) -> None:
    if isinstance(max_calls, bool) or not 1 <= int(max_calls) <= MAX_CALLS_HARD_LIMIT:
        raise CanarySafetyError("max_calls_must_be_between_1_and_8")
    if (
        isinstance(max_output_tokens, bool)
        or not 16 <= int(max_output_tokens) <= MAX_OUTPUT_TOKENS_HARD_LIMIT
    ):
        raise CanarySafetyError("max_output_tokens_must_be_between_16_and_128")
    if (
        isinstance(per_call_timeout_seconds, bool)
        or not 1
        <= int(per_call_timeout_seconds)
        <= MAX_PER_CALL_TIMEOUT_SECONDS
    ):
        raise CanarySafetyError("per_call_timeout_seconds_must_be_between_1_and_30")
    if (
        isinstance(total_timeout_seconds, bool)
        or not int(per_call_timeout_seconds)
        <= int(total_timeout_seconds)
        <= MAX_TOTAL_TIMEOUT_SECONDS
    ):
        raise CanarySafetyError("total_timeout_seconds_must_be_between_per_call_and_240")
    if max_cost_usd <= 0 or max_cost_usd > MAX_COST_USD_HARD_LIMIT:
        raise CanarySafetyError("max_cost_usd_must_be_above_0_and_at_most_0.10")


def _binding_inventory() -> tuple[tuple[str, tuple[str, ...]], ...]:
    tasks_by_binding: dict[str, list[str]] = defaultdict(list)
    for task, binding in sorted(current_task_model_binding().items()):
        tasks_by_binding[str(binding)].append(str(task))
    inventory = tuple(
        (binding, tuple(sorted(tasks)))
        for binding, tasks in sorted(tasks_by_binding.items())
    )
    if len(inventory) != EXPECTED_UNIQUE_BINDINGS:
        raise CanarySafetyError(
            f"expected_exactly_{EXPECTED_UNIQUE_BINDINGS}_unique_task_bindings"
        )
    return inventory


def build_plan(
    *,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    per_call_timeout_seconds: int = DEFAULT_PER_CALL_TIMEOUT_SECONDS,
    total_timeout_seconds: int = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    max_cost_usd: Decimal | float | str = DEFAULT_MAX_COST_USD,
    only_bindings: tuple[str, ...] | None = None,
) -> CanaryPlan:
    """Build a deterministic zero-I/O plan for the current six bindings."""

    cost_ceiling = _decimal(max_cost_usd)
    _validate_limits(
        max_calls=max_calls,
        max_output_tokens=max_output_tokens,
        per_call_timeout_seconds=per_call_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
        max_cost_usd=cost_ceiling,
    )
    rows: list[BindingPlan] = []
    for binding, tasks in _binding_inventory():
        provider, model = split_binding(binding)
        resolved = resolve_model_binding(provider, model, runtime_availability={})
        blocker = resolved.blocker(
            require_registered=True,
            require_runtime_verified=False,
            require_pricing=True,
        )
        if blocker:
            raise CanarySafetyError(f"{binding}:{blocker}")
        estimate = Decimal(
            str(
                llm_gateway._estimated_cost_usd(
                    provider,
                    prompt=CANARY_PROMPT,
                    max_output_tokens=int(max_output_tokens),
                    binding=resolved,
                )
            )
        )
        if estimate <= 0:
            raise CanarySafetyError(f"{binding}:estimated_cost_unavailable")
        rows.append(
            BindingPlan(
                binding=binding,
                provider=provider,
                model=model,
                tasks=tasks,
                estimated_cost_usd=estimate,
                resolved=resolved,
            )
        )

    requested_bindings = tuple(str(value or "").strip() for value in (only_bindings or ()))
    if requested_bindings:
        if any(not value for value in requested_bindings):
            raise CanarySafetyError("selected_binding_must_not_be_empty")
        if len(set(requested_bindings)) != len(requested_bindings):
            raise CanarySafetyError("selected_bindings_must_be_unique")
        known = {row.binding for row in rows}
        unknown = [value for value in requested_bindings if value not in known]
        if unknown:
            raise CanarySafetyError("selected_binding_not_in_task_inventory")
        if len(requested_bindings) > int(max_calls):
            raise CanarySafetyError("selected_binding_count_exceeds_max_calls")
        requested = set(requested_bindings)
        selected = tuple(row for row in rows if row.binding in requested)
    else:
        selected = tuple(rows[: int(max_calls)])
    estimated_total = sum(
        (row.estimated_cost_usd for row in selected), start=Decimal("0")
    )
    manifest = {
        "version": CANARY_VERSION,
        "bindings": [row.binding for row in selected],
        "task_binding_inventory_sha256": _sha256_bytes(
            _canonical_json(
                [
                    {"binding": row.binding, "tasks": list(row.tasks)}
                    for row in rows
                ]
            )
        ),
        "prompt_sha256": _sha256_text(CANARY_PROMPT),
        "max_calls": int(max_calls),
        "max_output_tokens": int(max_output_tokens),
        "per_call_timeout_seconds": int(per_call_timeout_seconds),
        "total_timeout_seconds": int(total_timeout_seconds),
        "max_cost_usd": format(cost_ceiling, "f"),
        "estimated_cost_usd": format(estimated_total, "f"),
        "claim_status": "descriptive_only",
        "attestation_status": "unsigned_not_readiness_evidence",
    }
    return CanaryPlan(
        bindings=tuple(rows),
        selected=selected,
        max_calls=int(max_calls),
        max_output_tokens=int(max_output_tokens),
        per_call_timeout_seconds=int(per_call_timeout_seconds),
        total_timeout_seconds=int(total_timeout_seconds),
        max_cost_usd=cost_ceiling,
        estimated_cost_usd=estimated_total,
        manifest_sha256=_sha256_bytes(_canonical_json(manifest)),
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_budget_checker(row: BindingPlan) -> bool:
    """Apply the existing monthly/single-call/provider budget gates read-only."""

    if llm_gateway._monthly_budget_cents() <= 0:
        return False
    if llm_gateway._budget_remaining_cents() <= 0:
        return False
    allowed, _checks = llm_gateway._budget_allows_provider(
        row.provider,
        cost_scope=CANARY_COST_SCOPE,
        estimated_cost_usd=float(row.estimated_cost_usd),
        require_configured=False,
    )
    return bool(allowed)


@contextmanager
def _bounded_provider_timeout(provider: str, timeout_seconds: int) -> Iterator[None]:
    """Temporarily narrow the gateway adapter timeout in this CLI process."""

    config = llm_gateway.PROVIDER_CONFIG.get(provider)
    if not isinstance(config, dict):
        raise CanarySafetyError("provider_transport_missing")
    previous = config.get("timeout")
    try:
        previous_number = int(previous or MAX_PER_CALL_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        previous_number = MAX_PER_CALL_TIMEOUT_SECONDS
    config["timeout"] = min(previous_number, int(timeout_seconds))
    try:
        yield
    finally:
        config["timeout"] = previous


def _default_live_invoker(
    binding: str,
    prompt: str,
    max_output_tokens: int,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    provider, model = split_binding(binding)
    caller = llm_gateway._PROVIDER_CALLERS.get(provider)
    if caller is None:
        return {"status": "failed", "provider": provider}
    with _bounded_provider_timeout(provider, timeout_seconds):
        return caller(
            prompt,
            int(max_output_tokens),
            model_override=model,
        )


def _safe_provider_result(
    row: BindingPlan,
    raw: Mapping[str, Any] | None,
    *,
    latency_ms: int,
) -> dict[str, Any]:
    payload = raw if isinstance(raw, Mapping) else {}
    raw_status = str(payload.get("status") or "failed").strip().lower()
    text = str(payload.get("text") or "")
    candidate_model = str(payload.get("model") or "").strip()
    response_model = (
        candidate_model if _SAFE_MODEL_RE.fullmatch(candidate_model) else ""
    )
    response_sha256 = _sha256_text(text)

    if raw_status == "success":
        if not text.strip():
            status = "empty_response"
        elif not response_model:
            status = "response_model_unreported"
        elif not row.resolved.matches_response_model(response_model):
            status = "model_mismatch"
        elif text.strip() != CANARY_EXPECTED_RESPONSE:
            status = "invalid_response"
        else:
            status = "success"
    else:
        status = raw_status if raw_status in _SAFE_PROVIDER_STATUSES else "failed"
    return _result_row(
        row,
        status=status,
        response_model=response_model,
        latency_ms=latency_ms,
        response_sha256=response_sha256,
    )


def _result_with_status(result: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        "binding": str(result.get("binding") or ""),
        "requested_model": str(result.get("requested_model") or ""),
        "response_model": str(result.get("response_model") or ""),
        "status": status,
        "latency_ms": max(0, int(result.get("latency_ms") or 0)),
        "response_sha256": str(result.get("response_sha256") or ""),
        "claim_status": "descriptive_only",
    }


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _actual_cost_micro_usd(
    row: BindingPlan,
    raw: Mapping[str, Any],
) -> int | None:
    reported = _nonnegative_int(raw.get("cost_micro_usd"))
    if reported is not None:
        return reported
    input_tokens = _nonnegative_int(raw.get("input_tokens"))
    output_tokens = _nonnegative_int(raw.get("output_tokens"))
    if input_tokens is None or output_tokens is None:
        return None
    return int(
        llm_gateway._estimate_cost_micro_usd(
            row.provider,
            input_tokens,
            output_tokens,
            binding=row.resolved,
        )
    )


def _record_secret_free_ledger(
    recorder: LedgerRecorder,
    *,
    row: BindingPlan,
    raw: Mapping[str, Any],
    safe_result: Mapping[str, Any],
    reservation_key: str,
    plan_sha256: str,
    actual_cost_micro_usd: int | None,
) -> dict[str, Any]:
    input_tokens = _nonnegative_int(raw.get("input_tokens")) or 0
    output_tokens = _nonnegative_int(raw.get("output_tokens")) or 0
    response_model = str(safe_result.get("response_model") or "").strip()
    recorded = recorder(
        provider=row.provider,
        model=response_model or row.model,
        purpose=CANARY_PURPOSE,
        # The fixed request is represented only by its digest in metadata.  An
        # empty prompt prevents any alternate recorder from persisting content.
        prompt="",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micro_usd=int(actual_cost_micro_usd or 0),
        status=str(safe_result.get("status") or "failed"),
        fallback_used=False,
        cost_tag=CANARY_COST_SCOPE,
        metadata={
            "entrypoint": CANARY_VERSION,
            "execution_class": "stage1_canary",
            "claim_status": "descriptive_only",
            "production_authorized": False,
            "response_contract_sha256": _sha256_text(CANARY_EXPECTED_RESPONSE),
            "request_content_recorded": False,
            "response_content_recorded": False,
            "request_sha256": _sha256_text(CANARY_PROMPT),
            "response_sha256": str(safe_result.get("response_sha256") or ""),
            "requested_binding": row.binding,
            "plan_sha256": plan_sha256,
            "reservation_key": reservation_key,
            "latency_ms": max(0, int(safe_result.get("latency_ms") or 0)),
        },
        update_budget_scopes=False,
        force_cost_ledger=True,
    )
    call = recorded.get("call") if isinstance(recorded, Mapping) else None
    if not isinstance(call, Mapping) or not str(call.get("call_uid") or "").strip():
        raise RuntimeError("usage_ledger_write_unconfirmed")
    cost_ledger = (
        recorded.get("cost_ledger") if isinstance(recorded, Mapping) else None
    )
    if not isinstance(cost_ledger, Mapping):
        raise RuntimeError("cost_ledger_write_unconfirmed")
    expected_micro = int(actual_cost_micro_usd or 0)
    call_micro = _nonnegative_int(call.get("cost_micro_usd"))
    mirror_micro = _nonnegative_int(cost_ledger.get("cost_micro_usd"))
    ledger_id = _nonnegative_int(cost_ledger.get("ledger_id"))
    if call_micro != expected_micro:
        raise RuntimeError("usage_ledger_amount_mismatch")
    if mirror_micro != expected_micro:
        raise RuntimeError("cost_ledger_amount_mismatch")
    if ledger_id is None or ledger_id <= 0:
        raise RuntimeError("cost_ledger_id_missing")
    return {
        "call_uid": str(call.get("call_uid") or ""),
        "call_cost_micro_usd": call_micro,
        "cost_ledger_id": ledger_id,
        "mirror_cost_micro_usd": mirror_micro,
    }


def _verify_four_ledger_accounting(
    *,
    expected_micro_usd: int,
    expected_scopes: tuple[str, ...],
    ledger_receipt: Mapping[str, Any],
    settlement_receipt: Mapping[str, Any],
) -> None:
    expected = int(expected_micro_usd)
    if _nonnegative_int(ledger_receipt.get("call_cost_micro_usd")) != expected:
        raise RuntimeError("usage_ledger_amount_mismatch")
    if _nonnegative_int(ledger_receipt.get("mirror_cost_micro_usd")) != expected:
        raise RuntimeError("cost_ledger_amount_mismatch")
    if not bool(settlement_receipt.get("readback_verified")):
        raise RuntimeError("settlement_readback_unverified")
    if (
        _nonnegative_int(settlement_receipt.get("actual_cost_micro_usd"))
        != expected
    ):
        raise RuntimeError("reservation_ledger_amount_mismatch")
    raw_scopes = settlement_receipt.get("scopes_updated")
    raw_deltas = settlement_receipt.get("scope_deltas_micro_usd")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise RuntimeError("budget_scope_receipt_missing")
    if not isinstance(raw_deltas, Mapping):
        raise RuntimeError("budget_scope_delta_receipt_missing")
    scopes = [str(scope or "") for scope in raw_scopes]
    if any(not scope for scope in scopes) or len(scopes) != len(set(scopes)):
        raise RuntimeError("budget_scope_receipt_invalid")
    if _SINGLE_CALL_SCOPE in scopes or _SINGLE_CALL_SCOPE in raw_deltas:
        raise RuntimeError("single_call_scope_must_not_accumulate")
    if set(scopes) != set(expected_scopes):
        raise RuntimeError("budget_scope_set_mismatch")
    if set(scopes) != {str(scope) for scope in raw_deltas}:
        raise RuntimeError("budget_scope_delta_receipt_mismatch")
    if any(_nonnegative_int(raw_deltas.get(scope)) != expected for scope in scopes):
        raise RuntimeError("budget_scope_delta_amount_mismatch")


def _mark_unknown_best_effort(
    reservations: ReservationManager,
    reservation_key: str,
) -> bool:
    try:
        return bool(reservations.mark_llm_provider_unknown(reservation_key))
    except Exception:
        # provider_started is itself an open state; failure to transition it to
        # unknown must never release the reservation or allow another call.
        return False


def _blocked_live_report(
    report: dict[str, Any],
    plan: CanaryPlan,
    *,
    status: str,
) -> dict[str, Any]:
    selected = {row.binding for row in plan.selected}
    report["results"] = [
        _result_row(
            row,
            status=(status if row.binding in selected else "not_selected"),
        )
        for row in plan.bindings
    ]
    report["all_selected_bindings_succeeded"] = False
    return report


def run_canary(
    *,
    live: bool = False,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    per_call_timeout_seconds: int = DEFAULT_PER_CALL_TIMEOUT_SECONDS,
    total_timeout_seconds: int = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    max_cost_usd: Decimal | float | str = DEFAULT_MAX_COST_USD,
    only_bindings: tuple[str, ...] | None = None,
    environment: Mapping[str, str] | None = None,
    live_invoker: LiveInvoker | None = None,
    provider_configured: ProviderConfigured | None = None,
    budget_checker: BudgetChecker | None = None,
    reservation_manager: ReservationManager | None = None,
    ledger_recorder: LedgerRecorder | None = None,
    is_production: bool | None = None,
    monotonic: Clock = time.monotonic,
) -> dict[str, Any]:
    """Execute one bounded plan; dependency injection keeps tests zero-I/O."""

    plan = build_plan(
        max_calls=max_calls,
        max_output_tokens=max_output_tokens,
        per_call_timeout_seconds=per_call_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
        max_cost_usd=max_cost_usd,
        only_bindings=only_bindings,
    )
    report = _base_report(plan, live=live)
    if plan.estimated_cost_usd > plan.max_cost_usd:
        return _blocked_live_report(report, plan, status="cost_plan_blocked")
    if not live:
        return report

    env = environment if environment is not None else os.environ
    if bool(IS_PRODUCTION if is_production is None else is_production):
        return _blocked_live_report(report, plan, status="production_forbidden")
    if _truthy(env.get("VKPI_LLM_GATEWAY_FORCE_OFFLINE")):
        return _blocked_live_report(report, plan, status="force_offline")
    supplied_authorization = str(env.get(AUTHORIZATION_ENV) or "")
    if not hmac.compare_digest(supplied_authorization, plan.authorization_value):
        return _blocked_live_report(report, plan, status="authorization_blocked")
    report["authorization"]["authorized"] = True

    configured = provider_configured or llm_gateway._is_provider_configured
    check_budget = budget_checker or _default_budget_checker
    try:
        if not all(configured(row.provider) for row in plan.selected):
            return _blocked_live_report(report, plan, status="not_configured")
        if not all(check_budget(row) for row in plan.selected):
            return _blocked_live_report(report, plan, status="budget_blocked")
    except Exception:  # fail closed without serialising exception content
        return _blocked_live_report(report, plan, status="preflight_failed")

    invoke = live_invoker or _default_live_invoker
    reservations = reservation_manager or llm_budget_reservations
    record_ledger = ledger_recorder or llm_gateway.record_call
    deadline = monotonic() + plan.total_timeout_seconds
    result_by_binding: dict[str, dict[str, Any]] = {}
    calls = 0
    stop_after_boundary_failure = False
    observed_cost_usd = Decimal("0")
    for row in plan.selected:
        if stop_after_boundary_failure:
            result_by_binding[row.binding] = _result_row(
                row, status="not_attempted_after_fail_closed"
            )
            continue
        remaining = deadline - monotonic()
        if remaining < 1:
            result_by_binding[row.binding] = _result_row(
                row, status="total_timeout"
            )
            stop_after_boundary_failure = True
            continue
        call_timeout = min(
            plan.per_call_timeout_seconds,
            max(1, int(math.floor(remaining))),
        )

        reservation_key = ""
        reservation_scopes: tuple[str, ...] = ()
        try:
            reservation = reservations.reserve_llm_budget(
                provider=row.provider,
                model=row.model,
                purpose=CANARY_PURPOSE,
                prompt=CANARY_PROMPT,
                estimated_cost_usd=float(row.estimated_cost_usd),
                cost_scope=CANARY_COST_SCOPE,
                metadata={
                    "execution_class": "stage1_canary",
                    "claim_status": "descriptive_only",
                    "phase": "exact_model_canary",
                    "attempt_index": calls + 1,
                    "attempt_total": len(plan.selected),
                    "target_label": row.binding,
                },
            )
            reservation_key = str(
                getattr(reservation, "reservation_key", "") or ""
            ).strip()
            reservation_scopes = tuple(
                str(scope or "").strip()
                for scope in (
                    getattr(reservation, "cumulative_scopes", ()) or ()
                )
                if str(scope or "").strip()
            )
            if not reservation_key or not reservation_scopes:
                raise RuntimeError("reservation_receipt_incomplete")
        except Exception:
            result_by_binding[row.binding] = _result_row(
                row, status="reservation_failed"
            )
            stop_after_boundary_failure = True
            continue

        try:
            reservations.mark_llm_provider_started(reservation_key)
        except Exception:
            # No provider I/O occurred, so release is safe.  A release failure
            # leaves the reservation open and still stops the run.
            try:
                reservations.release_llm_reservation(reservation_key)
            except Exception:
                pass
            result_by_binding[row.binding] = _result_row(
                row, status="reservation_start_failed"
            )
            stop_after_boundary_failure = True
            continue

        started = monotonic()
        calls += 1
        invoker_raised = False
        try:
            raw = invoke(
                row.binding,
                CANARY_PROMPT,
                plan.max_output_tokens,
                call_timeout,
            )
        except Exception:  # do not expose exception values or provider secrets
            raw = {"status": "invoker_exception"}
            invoker_raised = True
        latency_ms = max(0, int((monotonic() - started) * 1000))
        if latency_ms > call_timeout * 1000:
            bounded_raw = dict(raw) if isinstance(raw, Mapping) else {}
            bounded_raw["status"] = "timeout"
            raw = bounded_raw
        raw_payload = raw if isinstance(raw, Mapping) else {}
        safe_result = _safe_provider_result(
            row,
            raw_payload,
            latency_ms=latency_ms,
        )
        actual_cost_micro_usd = _actual_cost_micro_usd(row, raw_payload)

        try:
            ledger_receipt = _record_secret_free_ledger(
                record_ledger,
                row=row,
                raw=raw_payload,
                safe_result=safe_result,
                reservation_key=reservation_key,
                plan_sha256=plan.manifest_sha256,
                actual_cost_micro_usd=actual_cost_micro_usd,
            )
        except Exception:
            _mark_unknown_best_effort(reservations, reservation_key)
            result_by_binding[row.binding] = _result_with_status(
                safe_result, "ledger_failed"
            )
            stop_after_boundary_failure = True
            continue

        provider_status = str(raw_payload.get("status") or "failed").lower()
        if invoker_raised or provider_status != "success":
            unknown_marked = _mark_unknown_best_effort(
                reservations, reservation_key
            )
            result_by_binding[row.binding] = (
                safe_result
                if unknown_marked
                else _result_with_status(
                    safe_result, "reservation_unknown_mark_failed"
                )
            )
            stop_after_boundary_failure = True
            continue

        if actual_cost_micro_usd is None:
            _mark_unknown_best_effort(reservations, reservation_key)
            result_by_binding[row.binding] = _result_with_status(
                safe_result, "cost_accounting_failed"
            )
            stop_after_boundary_failure = True
            continue

        try:
            settlement = reservations.settle_llm_reservation(
                reservation_key,
                Decimal(actual_cost_micro_usd) / Decimal(1_000_000),
            )
            if not bool(settlement.get("settled")):
                raise RuntimeError("reservation_not_settled")
        except Exception:
            _mark_unknown_best_effort(reservations, reservation_key)
            result_by_binding[row.binding] = _result_with_status(
                safe_result, "settlement_failed"
            )
            stop_after_boundary_failure = True
            continue

        try:
            _verify_four_ledger_accounting(
                expected_micro_usd=actual_cost_micro_usd,
                expected_scopes=reservation_scopes,
                ledger_receipt=ledger_receipt,
                settlement_receipt=settlement,
            )
        except Exception:
            result_by_binding[row.binding] = _result_with_status(
                safe_result, "accounting_mismatch"
            )
            stop_after_boundary_failure = True
            continue

        observed_cost_usd += Decimal(actual_cost_micro_usd) / Decimal(1_000_000)
        report["accounting"]["verified_calls"] += 1
        report["accounting"]["observed_cost_micro_usd"] += int(
            actual_cost_micro_usd
        )
        if observed_cost_usd > plan.max_cost_usd:
            result_by_binding[row.binding] = _result_with_status(
                safe_result, "cost_ceiling_exceeded"
            )
            stop_after_boundary_failure = True
            continue
        result_by_binding[row.binding] = safe_result

    selected = {row.binding for row in plan.selected}
    report["results"] = [
        result_by_binding.get(row.binding)
        or _result_row(
            row,
            status=(
                "not_attempted_after_fail_closed"
                if row.binding in selected
                else "not_selected"
            ),
        )
        for row in plan.bindings
    ]
    report["provider_calls_performed"] = calls
    selected_results = [
        result_by_binding.get(row.binding) for row in plan.selected
    ]
    report["all_selected_bindings_succeeded"] = bool(selected_results) and all(
        isinstance(item, dict) and item.get("status") == "success"
        for item in selected_results
    )
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run by default. Live mode requires a plan-bound environment "
            "authorization and remains descriptive-only."
        )
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    parser.add_argument(
        "--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS
    )
    parser.add_argument(
        "--per-call-timeout-seconds",
        type=int,
        default=DEFAULT_PER_CALL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--total-timeout-seconds",
        type=int,
        default=DEFAULT_TOTAL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--max-cost-usd", type=Decimal, default=DEFAULT_MAX_COST_USD
    )
    parser.add_argument(
        "--binding",
        action="append",
        default=[],
        help=(
            "Probe only this exact task binding; repeat for multiple bindings. "
            "Unknown or duplicate bindings fail before provider I/O."
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run_canary(
            live=args.live,
            max_calls=args.max_calls,
            max_output_tokens=args.max_output_tokens,
            per_call_timeout_seconds=args.per_call_timeout_seconds,
            total_timeout_seconds=args.total_timeout_seconds,
            max_cost_usd=args.max_cost_usd,
            only_bindings=tuple(args.binding),
        )
    except CanarySafetyError as exc:
        report = {
            "version": CANARY_VERSION,
            "mode": "live" if args.live else "dry_run",
            "claim_status": "descriptive_only",
            "attestation_status": "unsigned_not_readiness_evidence",
            "production_authorized": False,
            "provider_calls_performed": 0,
            "status": str(exc),
            "results": [],
        }
        exit_code = 2
    else:
        if not args.live:
            exit_code = 0
        elif report.get("all_selected_bindings_succeeded") is True:
            exit_code = 0
        else:
            exit_code = 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
