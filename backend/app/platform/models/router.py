"""Model router — pick a model for a skill from thresholds, with a fallback chain.

Selection is a pure, deterministic function of the registry + a ``RouteRequest``
(quality/cost/speed thresholds + weights). The real LLM call is delegated to the
existing ``app.platform.llm_gateway.invoke`` via each model's ``gateway_provider``
— this layer NEVER re-implements provider transport.

Algorithm:
  1. Hard-filter the registry by quality / speed / context floors and cost ceiling.
  2. Score survivors = w_q*quality + w_c*cost + w_s*speed (weights normalised).
  3. Sort by score desc, then quality desc, then cheaper, then stable key —
     fully deterministic. The top is the primary; the rest form the fallback
     chain (best-effort alternatives in the same ranked order).
  4. If nothing passes the hard filters, fall back to the cheapest/most-local
     model so the caller still gets a usable decision (router never raises on
     "no candidate"; it degrades).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import cost_policy, quality_policy, registry
from .registry import ModelSpec


@dataclass(frozen=True)
class RouteRequest:
    """A skill's routing constraints + axis weights.

    Thresholds are hard floors/ceilings; weights bias the soft ranking among
    candidates that pass. ``allow_local`` / ``deny_models`` / ``prefer_models``
    give skills coarse control without touching the registry.
    """

    skill: str = ""
    min_quality: Optional[float] = None
    max_cost_cents_per_million: Optional[float] = None
    min_speed: Optional[float] = None
    min_context_tokens: Optional[int] = None
    quality_weight: float = 0.5
    cost_weight: float = 0.3
    speed_weight: float = 0.2
    allow_local: bool = True
    prefer_models: tuple[str, ...] = field(default_factory=tuple)
    deny_models: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RouteDecision:
    """The router's choice: primary model + ranked fallback chain + scores."""

    skill: str
    primary: ModelSpec
    fallback_chain: tuple[ModelSpec, ...]
    scores: dict[str, float]
    degraded: bool  # True when no candidate met the hard filters

    @property
    def provider_chain(self) -> list[str]:
        """gateway_provider names in order (primary first) for delegation."""
        chain = [self.primary, *self.fallback_chain]
        seen: set[str] = set()
        out: list[str] = []
        for m in chain:
            if m.gateway_provider not in seen:
                seen.add(m.gateway_provider)
                out.append(m.gateway_provider)
        return out


def _normalised_weights(req: RouteRequest) -> tuple[float, float, float]:
    q = max(0.0, float(req.quality_weight))
    c = max(0.0, float(req.cost_weight))
    s = max(0.0, float(req.speed_weight))
    total = q + c + s
    if total <= 0:
        return (1.0, 0.0, 0.0)
    return (q / total, c / total, s / total)


def _passes_hard_filters(model: ModelSpec, req: RouteRequest) -> bool:
    if model.key in req.deny_models:
        return False
    if model.is_local and not req.allow_local:
        return False
    if not quality_policy.meets_quality(model, req.min_quality):
        return False
    if not quality_policy.meets_speed(model, req.min_speed):
        return False
    if not quality_policy.meets_context(model, req.min_context_tokens):
        return False
    if not cost_policy.within_cost_ceiling(model, req.max_cost_cents_per_million):
        return False
    return True


def _score(model: ModelSpec, req: RouteRequest) -> float:
    wq, wc, ws = _normalised_weights(req)
    score = (
        wq * quality_policy.quality_score(model)
        + wc * cost_policy.cost_score(model, max_cents_per_million=req.max_cost_cents_per_million)
        + ws * quality_policy.speed_score(model)
    )
    # Soft preference bump (does not override hard filters, only ranking).
    if model.key in req.prefer_models:
        score += 1.0
    return score


def _rank(models: list[ModelSpec], req: RouteRequest) -> list[ModelSpec]:
    # Deterministic: score desc, quality desc, blended-cost asc, key asc.
    return sorted(
        models,
        key=lambda m: (
            -_score(m, req),
            -m.quality,
            cost_policy.blended_cost_cents(m),
            m.key,
        ),
    )


def route(req: RouteRequest) -> RouteDecision:
    """Pick a primary model + fallback chain for the request (pure, no I/O)."""
    all_models = registry.list_models()
    candidates = [m for m in all_models if _passes_hard_filters(m, req)]
    degraded = not candidates
    if degraded:
        # Nothing met the floors — degrade to the cheapest non-denied model so
        # the caller still gets a decision. Prefer local/free when allowed.
        pool = [m for m in all_models if m.key not in req.deny_models]
        if req.allow_local is False:
            pool = [m for m in pool if not m.is_local]
        if not pool:
            pool = list(all_models)
        candidates = sorted(
            pool,
            key=lambda m: (cost_policy.blended_cost_cents(m), -m.quality, m.key),
        )

    ranked = _rank(candidates, req) if not degraded else candidates
    primary = ranked[0]
    fallback = tuple(ranked[1:])
    scores = {m.key: round(_score(m, req), 6) for m in ranked}
    return RouteDecision(
        skill=req.skill,
        primary=primary,
        fallback_chain=fallback,
        scores=scores,
        degraded=degraded,
    )


def route_and_invoke(
    prompt: str,
    req: RouteRequest,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    **gateway_kwargs: Any,
) -> dict[str, Any]:
    """Route, then delegate the real call to llm_gateway.invoke.

    The router chooses the model; transport / budget / ledger all stay in the
    existing gateway. We pass the chosen model's ``gateway_provider`` as
    ``preferred_provider`` so the gateway uses its own configured-provider and
    fallback machinery. We do NOT re-implement any provider here.

    Import of llm_gateway is lazy so importing this package never drags in the
    gateway's heavy deps (DB / schema) at module load.
    """
    from app.platform import llm_gateway  # lazy: keep router import-light

    decision = route(req)
    result = llm_gateway.invoke(
        prompt,
        purpose=purpose or req.skill,
        max_output_tokens=max_output_tokens,
        preferred_provider=decision.primary.gateway_provider,
        **gateway_kwargs,
    )
    # Annotate with the routing decision for observability (non-breaking add).
    if isinstance(result, dict):
        result.setdefault("router", {})
        result["router"] = {
            "skill": decision.skill,
            "model_key": decision.primary.key,
            "model_id": decision.primary.model_id,
            "gateway_provider": decision.primary.gateway_provider,
            "fallback_chain": [m.key for m in decision.fallback_chain],
            "degraded": decision.degraded,
        }
    return result
