"""Implementation of the public LLM gateway invoke facade.

The public symbol stays in :mod:`app.platform.llm_gateway`; this module keeps
the established signature and delegates to dependency-leaf runtimes. Gateway
dependencies are still supplied from the facade's live namespace so existing
monkeypatch and operator override contracts remain intact.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.platform import llm_gateway_result_cache as _result_cache
from app.platform import llm_gateway_invoke_attempts as _invoke_attempts
from app.platform import llm_gateway_invoke_runtime as _invoke_runtime
from app.platform import llm_gateway_invoke_types as _invoke_types
from app.platform.llm_gateway_call_hooks import (
    cache_model_label,
    deferred_or_none,
    serve_cached_result,
    store_cached_result,
)


def invoke_impl(
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
    enforce_atomic_reservation: bool = False,
    deadline_seconds: float | None = None,
    max_provider_attempts: int | None = None,
    namespace: dict[str, Any],
) -> dict[str, Any]:
    """Invoke an LLM while resolving gateway dependencies from live namespace."""

    hooks = _invoke_types.InvocationHooks(
        result_cache=_result_cache,
        cache_model_label=cache_model_label,
        serve_cached_result=serve_cached_result,
        store_cached_result=store_cached_result,
        deferred_or_none=deferred_or_none,
    )
    return _invoke_runtime.invoke_impl(
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
        enforce_atomic_reservation=enforce_atomic_reservation,
        deadline_seconds=deadline_seconds,
        max_provider_attempts=max_provider_attempts,
        namespace=namespace,
        hooks=hooks,
    )
