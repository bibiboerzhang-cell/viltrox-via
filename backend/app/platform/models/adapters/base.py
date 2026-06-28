"""Adapter base — uniform interface over heterogeneous model backends.

An adapter normalises one backend (a provider family or a self-hosted server)
to a single ``generate`` contract so the router / callers stay model-agnostic.
These are example stubs: the *real* transport currently lives in
``app.platform.llm_gateway`` and the router delegates there. Adapters exist so
future backends (a raw Qwen / vLLM HTTP endpoint that the gateway does not yet
speak) can be plugged in without changing call sites.

Contract: ``generate(prompt, max_output_tokens, **kwargs) -> AdapterResult``.
Stubs here intentionally do NOT make network calls; they return a structured
``not_implemented`` result so importing the package is side-effect free and
tests stay offline.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AdapterResult:
    """Normalised adapter output (mirrors the shape llm_gateway.invoke returns)."""

    text: str
    provider: str
    model: str
    status: str = "ok"
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class ModelAdapter(abc.ABC):
    """Uniform adapter interface. One instance per logical backend."""

    #: gateway_provider name this adapter maps to (bridge to llm_gateway).
    gateway_provider: str = ""
    #: human key, e.g. "openai", "qwen".
    name: str = ""

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int = 800,
        **kwargs: Any,
    ) -> AdapterResult:
        """Produce a completion for ``prompt`` using ``model``."""
        raise NotImplementedError

    def supports(self, model_id: str) -> bool:
        """Whether this adapter can serve a given concrete model id.

        Default: serve anything (subclasses may narrow). Kept permissive so the
        registry's ``model_id`` strings route without per-adapter allow-lists.
        """
        return True
