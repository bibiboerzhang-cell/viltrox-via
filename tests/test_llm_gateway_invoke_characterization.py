"""Offline behavior locks for splitting the text LLM invoke orchestrator."""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.platform import llm_gateway_invoke as target
from scripts.vkpi_engineering_health_collect import collect_complexity


@dataclass(frozen=True)
class FakeBinding:
    provider: str
    model_id: str

    @property
    def binding(self) -> str:
        return f"{self.provider}/{self.model_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "binding": self.binding,
        }

    def matches_response_model(self, actual_model: str) -> bool:
        return actual_model == self.model_id


class FakeLogger:
    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        self.events = events

    def warning(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log", "warning", message, deepcopy(kwargs.get("extra"))))

    def error(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log", "error", message, deepcopy(kwargs.get("extra"))))


@dataclass
class InvokeHarness:
    candidates: list[tuple[str, str, bool]] = field(
        default_factory=lambda: [("openai", "gpt-exact", True)]
    )
    provider_results: dict[str, Any] = field(
        default_factory=lambda: {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": "gpt-exact",
                "text": "ok",
                "input_tokens": 100,
                "output_tokens": 20,
                "latency_ms": 12,
            }
        }
    )
    binding_blockers: dict[str, str] = field(default_factory=dict)
    configured: set[str] = field(default_factory=lambda: {"openai"})
    budget_allowed: dict[str, bool] = field(default_factory=dict)
    deferred_result: dict[str, Any] | None = None
    audit_error_statuses: set[str] = field(default_factory=set)
    reserved_audit_error_statuses: set[str] = field(default_factory=set)
    reservation_error: BaseException | None = None
    settlement: dict[str, Any] = field(
        default_factory=lambda: {"settled": True, "reason": ""}
    )
    events: list[tuple[Any, ...]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            target._result_cache,
            "build_cache_plan",
            self.build_cache_plan,
        )
        monkeypatch.setattr(target, "cache_model_label", self.cache_model_label)
        monkeypatch.setattr(target, "serve_cached_result", self.serve_cached_result)
        monkeypatch.setattr(target, "store_cached_result", self.store_cached_result)
        monkeypatch.setattr(target, "deferred_or_none", self.deferred_or_none)

    def namespace(self) -> dict[str, Any]:
        return {
            "_rule_fallback": self.rule_fallback,
            "record_call": self.record_call,
            "_cost_scope_for_purpose": lambda purpose, tag: tag or f"scope:{purpose}",
            "_budget_guard": lambda: SimpleNamespace(check_budget=self.scope_budget),
            "logger": FakeLogger(self.events),
            "_monthly_budget_cents": lambda: 100_00,
            "_budget_remaining_cents": lambda: 90_00,
            "_ordered_model_candidates": self.ordered_candidates,
            "_resolve_gateway_binding": self.resolve_binding,
            "_binding_call_blocker": self.binding_blocker,
            "_is_provider_configured": lambda provider: provider in self.configured,
            "_PROVIDER_CALLERS": {
                provider: self.provider_caller(provider)
                for provider in self.provider_results
            },
            "_estimated_cost_usd": self.estimated_cost,
            "_budget_allows_provider": self.budget_allows_provider,
            "_record_budget_blocked_attempt": self.record_budget_blocked,
            "_llm_budget_reservations": lambda: self,
            "_acquire_strict_fleet_breaker": self.acquire_breaker,
            "_complete_strict_fleet_breaker": self.complete_breaker,
            "_abandon_strict_fleet_breaker": self.abandon_breaker,
            "SINGLE_CALL_BUDGET_SCOPE": "single-call",
            "_mark_reserved_attempt_unknown": self.mark_unknown,
            "_record_reserved_provider_attempt": self.record_reserved,
            "_safe_int": self.safe_int,
            "_estimate_cost_micro_usd": self.estimate_micro,
            "_micro_usd_to_cents": lambda value: int(value) // 10_000,
            "_normalise_runtime_error": lambda value: dict(value),
        }

    def scope_budget(self, scope: str, value: int, **kwargs: Any) -> bool:
        self.events.append(("scope_budget", scope, value, deepcopy(kwargs)))
        return True

    def ordered_candidates(self, *args: Any) -> list[tuple[str, str, bool]]:
        self.events.append(("ordered_candidates", deepcopy(args)))
        return list(self.candidates)

    def resolve_binding(self, provider: str, model: str) -> FakeBinding:
        self.events.append(("binding", provider, model))
        return FakeBinding(provider, model)

    def binding_blocker(self, binding: FakeBinding, **kwargs: Any) -> str:
        self.events.append(("binding_gate", binding.binding, deepcopy(kwargs)))
        return self.binding_blockers.get(binding.binding, "")

    def provider_caller(self, provider: str):
        def call(prompt: str, tokens: int, **kwargs: Any) -> Any:
            self.events.append(("provider", provider, prompt, tokens, deepcopy(kwargs)))
            result = self.provider_results[provider]
            if isinstance(result, BaseException):
                raise result
            return deepcopy(result)

        return call

    def estimated_cost(self, provider: str, **kwargs: Any) -> float:
        self.events.append(("estimate", provider, kwargs["max_output_tokens"]))
        return 0.0123

    def budget_allows_provider(self, provider: str, **kwargs: Any):
        self.events.append(("provider_budget", provider, deepcopy(kwargs)))
        allowed = self.budget_allowed.get(provider, True)
        return allowed, [{"provider": provider, "allowed": allowed}]

    def record_budget_blocked(self, provider: str, **kwargs: Any) -> None:
        self.events.append(("budget_blocked_record", provider, kwargs["purpose"]))

    def reserve_llm_budget(self, **kwargs: Any) -> SimpleNamespace:
        self.events.append(("reserve", kwargs["provider"], kwargs["model"]))
        if self.reservation_error is not None:
            raise self.reservation_error
        return SimpleNamespace(reservation_key=f"res-{kwargs['provider']}")

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))

    def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
        self.events.append(("settle", key, actual))
        return dict(self.settlement)

    def release_llm_reservation(self, key: str) -> None:
        self.events.append(("release", key))

    def acquire_breaker(self, **kwargs: Any) -> str:
        permit = f"permit-{kwargs['provider']}"
        self.events.append(("breaker_acquire", kwargs["provider"], permit))
        return permit

    def complete_breaker(self, permit: str, outcome: Any) -> None:
        status = outcome.get("status") if isinstance(outcome, dict) else type(outcome).__name__
        self.events.append(("breaker_complete", permit, status))

    def abandon_breaker(self, permit: str) -> None:
        self.events.append(("breaker_abandon", permit))

    def mark_unknown(self, key: str) -> None:
        self.events.append(("unknown", key))

    def record_reserved(self, **kwargs: Any) -> None:
        self.events.append(("reserved_record", kwargs["provider"], kwargs["status"]))
        if kwargs["status"] in self.reserved_audit_error_statuses:
            raise RuntimeError("sensitive reserved ledger failure")

    def record_call(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("record", kwargs["provider"], kwargs["status"]))
        self.records.append(deepcopy(kwargs))
        if kwargs["status"] in self.audit_error_statuses:
            raise RuntimeError("sensitive ledger failure")
        return {"call": {"call_uid": f"call-{len(self.records)}"}}

    def rule_fallback(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("fallback", kwargs["reason"]))
        return {
            "text": "",
            "provider": "rule_v0",
            "model": "rule_v0",
            "purpose": kwargs.get("purpose", ""),
            "status": "fallback_to_rule",
            "fallback_used": True,
            "reason": kwargs["reason"],
            "errors": deepcopy(kwargs.get("errors") or []),
            "failure_code": kwargs["reason"],
        }

    def cache_model_label(self, candidates: Any) -> str:
        self.events.append(("cache_label", deepcopy(candidates)))
        return "candidate-chain"

    def build_cache_plan(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.events.append(("cache_plan", args[0], kwargs["max_output_tokens"]))
        return {"key": "cache-key"}

    def serve_cached_result(self, **kwargs: Any) -> None:
        self.events.append(("cache_lookup", kwargs["purpose"]))
        return None

    def store_cached_result(self, plan: Any, result: dict[str, Any], audit: Any) -> None:
        self.events.append(("cache_store", plan["key"], result["status"], bool(audit)))

    def deferred_or_none(self, **kwargs: Any) -> dict[str, Any] | None:
        self.events.append(("deferred", kwargs["purpose"], len(kwargs["errors"])))
        return deepcopy(self.deferred_result)

    @staticmethod
    def safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def estimate_micro(self, provider: str, input_tokens: int, output_tokens: int, **_kwargs: Any) -> int:
        self.events.append(("actual_cost", provider, input_tokens, output_tokens))
        return input_tokens * 10 + output_tokens * 20


def _invoke(harness: InvokeHarness, **kwargs: Any) -> dict[str, Any]:
    defaults = {
        "purpose": "characterization",
        "preferred_provider": "openai",
        "model_override": "gpt-exact",
        "model_fallbacks": (),
        "skip_budget_check": True,
        "namespace": harness.namespace(),
    }
    defaults.update(kwargs)
    return target.invoke_impl("hello", **defaults)


def test_invoke_atomic_success_freezes_order_cost_and_return_contract(monkeypatch) -> None:
    harness = InvokeHarness()
    harness.install(monkeypatch)

    result = _invoke(harness, max_output_tokens=1, enforce_atomic_reservation=True)

    assert result.pop("deadline_seconds") == 90.0
    assert result.pop("provider_attempts") == 1
    assert result.pop("max_provider_attempts") == 2
    assert result.pop("elapsed_ms") >= 0
    assert result == {
        "status": "success",
        "provider": "openai",
        "model": "gpt-exact",
        "text": "ok",
        "input_tokens": 100,
        "output_tokens": 20,
        "latency_ms": 12,
        "fallback_used": False,
        "purpose": "characterization",
        "cost_micro_usd": 1400,
        "cost_cents": 0,
        "resolved_model_binding": {
            "provider": "openai",
            "model_id": "gpt-exact",
            "binding": "openai/gpt-exact",
        },
        "budget_reservation_key": "res-openai",
    }
    assert [event[0] for event in harness.events] == [
        "scope_budget",
        "ordered_candidates",
        "cache_label",
        "cache_plan",
        "cache_lookup",
        "binding",
        "binding_gate",
        "estimate",
        "provider_budget",
        "reserve",
        "breaker_acquire",
        "started",
        "provider",
        "breaker_complete",
        "actual_cost",
        "record",
        "settle",
        "cache_store",
    ]
    assert next(event for event in harness.events if event[0] == "provider")[3] == 16
    assert harness.records[0]["update_budget_scopes"] is False
    assert harness.records[0]["force_cost_ledger"] is True


def test_invoke_uncertain_provider_exception_stops_paid_candidate_chain(monkeypatch) -> None:
    harness = InvokeHarness(
        candidates=[
            ("openai", "gpt-exact", True),
            ("google", "gemini-exact", True),
        ],
        configured={"openai", "google"},
        provider_results={
            "openai": TimeoutError("sensitive upstream timeout detail"),
            "google": ["not", "a", "mapping"],
        },
    )
    harness.install(monkeypatch)

    result = _invoke(harness)

    assert result["provider"] == "rule_v0"
    assert result["fallback_reason"] == "provider_outcome_unknown"
    assert result["errors"] == [
        {
            "provider": "openai",
            "model": "gpt-exact",
            "status": "provider_exception",
            "error": "TimeoutError: sensitive upstream timeout detail",
        },
    ]
    assert [event[1] for event in harness.events if event[0] == "provider"] == [
        "openai",
    ]
    assert [event[2] for event in harness.events if event[0] == "record"] == [
        "provider_outcome_unknown"
    ]


def test_invoke_budget_block_can_defer_without_provider_io(monkeypatch) -> None:
    deferred = {
        "status": "deferred",
        "provider": "openai",
        "text": "",
        "deferred": True,
    }
    harness = InvokeHarness(
        budget_allowed={"openai": False},
        deferred_result=deferred,
    )
    harness.install(monkeypatch)

    result = _invoke(harness)

    assert {name: result[name] for name in deferred} == deferred
    assert result["provider_attempts"] == 0
    assert not any(event[0] == "provider" for event in harness.events)
    assert ("budget_blocked_record", "openai", "characterization") in harness.events
    assert harness.events[-1] == ("deferred", "characterization", 1)


def test_invoke_reserved_audit_failure_hides_secret_and_stops_chain(monkeypatch) -> None:
    harness = InvokeHarness(
        candidates=[
            ("openai", "gpt-exact", True),
            ("google", "gemini-exact", True),
        ],
        configured={"openai", "google"},
        provider_results={
            "openai": RuntimeError("sensitive provider payload"),
            "google": {
                "status": "success",
                "provider": "google",
                "model": "gemini-exact",
                "text": "must not run",
            },
        },
        reserved_audit_error_statuses={"provider_exception"},
    )
    harness.install(monkeypatch)

    result = _invoke(harness, enforce_atomic_reservation=True)

    assert result["reason"] == "audit_ledger_unavailable"
    assert result["budget_reservation_key"] == "res-openai"
    assert [event[1] for event in harness.events if event[0] == "provider"] == ["openai"]
    assert ("unknown", "res-openai") in harness.events
    rendered = repr((result, harness.events))
    assert "sensitive provider payload" not in rendered
    assert "sensitive reserved ledger failure" not in rendered


def test_invoke_empty_prompt_keeps_early_fallback_and_ledger_order(monkeypatch) -> None:
    harness = InvokeHarness()
    harness.install(monkeypatch)

    result = target.invoke_impl(
        "  ",
        purpose="empty",
        max_output_tokens=0,
        namespace=harness.namespace(),
    )

    assert result["reason"] == "empty_prompt"
    assert harness.events == [
        ("fallback", "empty_prompt"),
        ("record", "rule_v0", "empty_prompt"),
    ]
    assert harness.records[0]["prompt"] == "  "


def test_invoke_refactor_family_complexity_size_and_dependency_are_bounded() -> None:
    modules = [target]
    for name in (
        "_invoke_runtime",
        "_invoke_attempts",
        "_invoke_types",
    ):
        module = getattr(target, name, None)
        assert module is not None
        modules.append(module)
    rows = []
    for module in modules:
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) < 800
        rows.extend(collect_complexity({str(path): ast.parse(source)}))
    public = next(
        row
        for row in rows
        if row.path == str(Path(target.__file__)) and row.qualified_name == "invoke_impl"
    )
    assert public.cc <= 10
    assert max(row.cc for row in rows) <= 30
    assert "llm_gateway_invoke import" not in Path(
        target._invoke_attempts.__file__
    ).read_text(encoding="utf-8")
