"""Strict production entrypoints for reviewed LLM call sites (facade).

Text generation uses the canonical gateway (``generate_text`` / ``generate_json``
live here).  Provider SDK adapters that the text gateway cannot express
(multimodal message blocks / image parts) live in per-provider siblings and are
re-exported from this module so every ``from app.platform.llm_production import X``
and every ``monkeypatch.setattr(llm_production, "X", ...)`` keeps working:

- :mod:`llm_production_anthropic` — ``generate_anthropic_messages``
- :mod:`llm_production_google`    — ``generate_google_content``
- :mod:`llm_production_openai`    — ``generate_openai_responses``

Each adapter applies the same exact-model task binding, dual-signed readiness,
conservative media-cost reservation, fleet breaker, ledger and settlement
contract without changing caller payloads.  Business code must import the
facade only (tests/test_llm_boundary_inventory_ratchet.py treats the facade
and its ``llm_production_*`` siblings as the one reviewed provider boundary).

monkeypatch 约定:provider 子模块通过 ``llm_production_common.expected_task_binding``
回到本门面读 ``current_task_model_binding``,所以打在门面上的
``current_task_model_binding`` 补丁对全部 provider 路径生效;``llm_gateway`` 是同一
模块对象,打在 ``llm_production.llm_gateway`` 上的补丁天然共享。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from app.core.model_registry import current_task_model_binding
from app.platform import llm_gateway
from app.platform.llm_production_common import (
    ProductionLlmUnavailable,
    progress_metadata as _progress_metadata,
    sdk_failure as _sdk_failure,
)
from app.platform.llm_production_anthropic import generate_anthropic_messages
from app.platform.llm_production_google import (
    GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
    GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    generate_google_content,
)
from app.platform.llm_production_openai import (
    OPENAI_RESPONSES_MAX_OUTPUT_TOKENS_HARD_CAP,
    generate_openai_responses,
    openai_response_text,
)


def generate_text(
    prompt: str,
    *,
    provider: str,
    model: str,
    purpose: str,
    max_output_tokens: int = 800,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate text through the exact-model, reservation-backed boundary.

    The authoritative empty fallback chain prevents a requested Anthropic model
    from silently falling into an unrelated OpenAI/Google global default.
    """

    provider_key = str(provider or "").strip().lower()
    provider_key = {"gemini": "google", "claude": "anthropic"}.get(
        provider_key, provider_key
    )
    exact_model = str(model or "").strip()
    if not provider_key or not exact_model:
        raise ValueError("provider and exact model are required")
    progress_metadata = _progress_metadata(
        purpose,
        metadata,
        phase="provider_generation",
    )
    result = llm_gateway.invoke(
        str(prompt or ""),
        purpose=str(purpose or ""),
        max_output_tokens=max_output_tokens,
        preferred_provider=provider_key,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        require_configured_budget=False,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata={
            **progress_metadata,
            "entrypoint": "llm_production_text_v1",
            "execution_class": "production",
            "request_content_recorded": False,
        },
        staff=staff,
        enforce_atomic_reservation=True,
    )
    if (
        str(result.get("status") or "") != "success"
        or str(result.get("provider") or "").strip().lower() != provider_key
        or not str(result.get("text") or "").strip()
    ):
        raise ProductionLlmUnavailable(result)
    return result


def generate_json(
    prompt: str,
    *,
    provider: str,
    model: str,
    purpose: str,
    max_output_tokens: int = 800,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    required_keys: Iterable[str] | None = None,
    validator: Callable[[Any], Any] | None = None,
    deadline_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
    require_configured_budget: bool = True,
) -> dict[str, Any]:
    """Generate one exact-model JSON result through the atomic boundary.

    Unlike :func:`generate_text`, this returns the gateway's bounded fallback
    object when the provider is blocked or its JSON contract fails.  Existing
    business callers already use that object to distinguish an honest degraded
    result from a validated model result; raising would erase that distinction.
    """

    provider_key = str(provider or "").strip().lower()
    provider_key = {"gemini": "google", "claude": "anthropic"}.get(
        provider_key, provider_key
    )
    exact_model = str(model or "").strip()
    if not provider_key or not exact_model:
        raise ValueError("provider and exact model are required")
    progress_metadata = _progress_metadata(
        purpose,
        metadata,
        phase="structured_generation",
    )
    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    actual_binding = f"{provider_key}/{exact_model}"
    if task_binding:
        expected_binding = current_task_model_binding().get(task_binding, "")
        if expected_binding != actual_binding:
            raise _sdk_failure(
                "task_binding_model_mismatch",
                provider=provider_key,
                model=exact_model,
                purpose=str(purpose or "").strip(),
                details={
                    "task_binding": task_binding,
                    "expected_binding": expected_binding,
                    "actual_binding": actual_binding,
                },
            )
    return llm_gateway.invoke_json(
        str(prompt or ""),
        purpose=str(purpose or ""),
        max_output_tokens=max_output_tokens,
        preferred_provider=provider_key,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        require_configured_budget=bool(require_configured_budget),
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata={
            **progress_metadata,
            "entrypoint": "llm_production_json_v1",
            "execution_class": "production",
            "request_content_recorded": False,
        },
        staff=staff,
        required_keys=required_keys,
        validator=validator,
        deadline_seconds=deadline_seconds,
        max_provider_attempts=1,
        enforce_atomic_reservation=True,
    )


__all__ = [
    "GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP",
    "GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP",
    "OPENAI_RESPONSES_MAX_OUTPUT_TOKENS_HARD_CAP",
    "ProductionLlmUnavailable",
    "current_task_model_binding",
    "generate_anthropic_messages",
    "generate_google_content",
    "generate_json",
    "generate_openai_responses",
    "generate_text",
    "llm_gateway",
    "openai_response_text",
]
