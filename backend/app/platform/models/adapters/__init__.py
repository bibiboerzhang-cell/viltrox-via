"""Adapter registry — uniform interface + example stubs (openai / qwen).

Real provider transport stays in ``app.platform.llm_gateway``; these adapters
are the formal seam for future backends the gateway does not yet speak. The
stubs are offline (no network) and return ``not_implemented`` so the package
imports cleanly in tests.
"""

from __future__ import annotations

from typing import Any, Optional

from .base import AdapterResult, ModelAdapter


class OpenAIAdapter(ModelAdapter):
    """Example stub for the OpenAI / OpenAI-compatible family (incl. Qwen hosts).

    Real calls are delegated through llm_gateway today; this stub documents the
    contract and lets future direct-transport work drop in without API churn.
    """

    gateway_provider = "openai"
    name = "openai"

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int = 800,
        **kwargs: Any,
    ) -> AdapterResult:
        return AdapterResult(
            text="",
            provider=self.gateway_provider,
            model=model,
            status="not_implemented",
            raw={"note": "stub: delegate to llm_gateway.invoke(preferred_provider='openai')"},
        )


class QwenAdapter(ModelAdapter):
    """Example stub for a hosted/self-hosted Qwen backend (OpenAI-compatible).

    Separated from OpenAIAdapter so a dedicated Qwen endpoint (different base URL
    / auth) can be wired later without touching the OpenAI path.
    """

    gateway_provider = "openai"  # OpenAI-compatible transport reuse
    name = "qwen"

    def supports(self, model_id: str) -> bool:
        return "qwen" in str(model_id or "").lower()

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int = 800,
        **kwargs: Any,
    ) -> AdapterResult:
        return AdapterResult(
            text="",
            provider=self.gateway_provider,
            model=model,
            status="not_implemented",
            raw={"note": "stub: wire dedicated Qwen endpoint here when available"},
        )


# Name -> adapter instance. Extend as real backends are added.
_ADAPTERS: dict[str, ModelAdapter] = {
    OpenAIAdapter.name: OpenAIAdapter(),
    QwenAdapter.name: QwenAdapter(),
}


def get_adapter(name: str) -> Optional[ModelAdapter]:
    """Lookup an adapter by name (e.g. 'openai', 'qwen')."""
    return _ADAPTERS.get(str(name or "").strip())


def list_adapters() -> list[ModelAdapter]:
    """All registered adapters."""
    return list(_ADAPTERS.values())


__all__ = [
    "AdapterResult",
    "ModelAdapter",
    "OpenAIAdapter",
    "QwenAdapter",
    "get_adapter",
    "list_adapters",
]
