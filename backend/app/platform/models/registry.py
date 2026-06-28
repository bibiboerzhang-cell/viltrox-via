"""Model registry — capability / cost / context / latency / locality metadata.

In-memory dict config for now (no DB, no migration). Each entry is a logical
model the router may select. ``gateway_provider`` is the bridge back to the
existing ``llm_gateway`` provider names (openai / google / anthropic / rule_v0)
so the router can delegate the real transport without rewriting any provider.

Scores are normalised 0.0 - 1.0:
  - ``quality``  higher = stronger reasoning / output fidelity
  - ``speed``    higher = lower latency (faster)
Costs are integer cents per million tokens (mirrors llm_gateway units).
``is_local`` flags self-hosted models (no external network / no per-token spend).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ModelSpec:
    """One selectable logical model + its routing metadata."""

    key: str  # stable internal id, e.g. "gpt", "gemini", "claude", "qwen", "local_vllm"
    display_name: str
    gateway_provider: str  # bridges to llm_gateway PROVIDER_ORDER name
    model_id: str  # concrete model string passed to the provider transport
    quality: float  # 0..1 — higher is stronger
    speed: float  # 0..1 — higher is faster (lower latency)
    input_cents_per_million: int
    output_cents_per_million: int
    context_tokens: int
    typical_latency_ms: int
    is_local: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def cost_per_million_blended(self, *, input_weight: float = 0.7) -> float:
        """Blended per-million cost estimate (cents). Input-heavy by default."""
        iw = max(0.0, min(1.0, input_weight))
        return iw * float(self.input_cents_per_million) + (1.0 - iw) * float(
            self.output_cents_per_million
        )


# Stable model id constants (avoid magic strings at call sites).
GPT = "gpt"
GEMINI = "gemini"
CLAUDE = "claude"
QWEN = "qwen"
LOCAL_VLLM = "local_vllm"


# In-memory registry. Cost / context numbers mirror llm_gateway.PROVIDER_CONFIG
# where they overlap; quality/speed/latency are routing heuristics.
_REGISTRY: dict[str, ModelSpec] = {
    GPT: ModelSpec(
        key=GPT,
        display_name="OpenAI GPT",
        gateway_provider="openai",
        model_id="gpt-5.4-mini",
        quality=0.86,
        speed=0.62,
        input_cents_per_million=25,
        output_cents_per_million=200,
        context_tokens=128_000,
        typical_latency_ms=2600,
        is_local=False,
        tags=("general", "reasoning", "tools"),
    ),
    GEMINI: ModelSpec(
        key=GEMINI,
        display_name="Google Gemini Flash",
        gateway_provider="google",
        model_id="gemini-flash-latest",
        quality=0.74,
        speed=0.90,
        input_cents_per_million=7,
        output_cents_per_million=30,
        context_tokens=1_000_000,
        typical_latency_ms=1200,
        is_local=False,
        tags=("general", "cheap", "long-context", "fast"),
    ),
    CLAUDE: ModelSpec(
        key=CLAUDE,
        display_name="Anthropic Claude",
        gateway_provider="anthropic",
        model_id="claude-latest",
        quality=0.92,
        speed=0.58,
        input_cents_per_million=25,
        output_cents_per_million=125,
        context_tokens=200_000,
        typical_latency_ms=3000,
        is_local=False,
        tags=("general", "reasoning", "long-context", "writing"),
    ),
    QWEN: ModelSpec(
        key=QWEN,
        display_name="Qwen (hosted)",
        gateway_provider="openai",  # OpenAI-compatible endpoint; transport reuse
        model_id="qwen2.5-72b-instruct",
        quality=0.70,
        speed=0.72,
        input_cents_per_million=4,
        output_cents_per_million=12,
        context_tokens=131_000,
        typical_latency_ms=1800,
        is_local=False,
        tags=("general", "cheap", "multilingual"),
    ),
    LOCAL_VLLM: ModelSpec(
        key=LOCAL_VLLM,
        display_name="Local vLLM",
        gateway_provider="rule_v0",  # no external spend; gateway treats as local/free
        model_id="local-vllm",
        quality=0.55,
        speed=0.80,
        input_cents_per_million=0,
        output_cents_per_million=0,
        context_tokens=32_000,
        typical_latency_ms=900,
        is_local=True,
        tags=("local", "free", "private"),
    ),
}


def list_models() -> list[ModelSpec]:
    """All registered models (stable insertion order)."""
    return list(_REGISTRY.values())


def get_model(key: str) -> Optional[ModelSpec]:
    """Lookup a model by its stable key, or None."""
    return _REGISTRY.get(str(key or "").strip())


def models_for_provider(gateway_provider: str) -> list[ModelSpec]:
    """All models that delegate to a given llm_gateway provider name."""
    target = str(gateway_provider or "").strip()
    return [m for m in _REGISTRY.values() if m.gateway_provider == target]


def local_models() -> list[ModelSpec]:
    """Self-hosted / zero-cost models."""
    return [m for m in _REGISTRY.values() if m.is_local]
