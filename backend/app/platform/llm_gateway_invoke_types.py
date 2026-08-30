"""Dependency-free state objects for text LLM invocation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class InvocationHooks:
    result_cache: Any
    cache_model_label: Any
    serve_cached_result: Any
    store_cached_result: Any
    deferred_or_none: Any


@dataclass
class InvocationContext:
    prompt: str
    purpose: str
    max_output_tokens: int
    preferred_provider: str | None
    model_override: str | None
    model_fallbacks: Iterable[tuple[str, str]] | None
    require_runtime_verified: bool
    skip_budget_check: bool
    require_configured_budget: bool
    cost_tag: str | None
    triggered_by: Any
    metadata: dict[str, Any] | None
    staff: dict[str, Any] | None
    enforce_atomic_reservation: bool
    namespace: dict[str, Any]
    deps: dict[str, Any]
    hooks: InvocationHooks
    safe_prompt: str = ""
    cost_scope: str = ""
    budget_warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[tuple[str, str, bool]] = field(default_factory=list)
    cache_plan: Any = None


@dataclass
class CandidateAttempt:
    index: int
    provider: str
    model_id: str
    explicit_model: bool
    binding: Any
    caller: Any
    estimated_cost: float
    budget_checks: Any
    reservation_key: str = ""
    breaker_permit: Any = None
