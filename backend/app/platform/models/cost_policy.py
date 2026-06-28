"""Cost policy — pure helpers to gate / score models by cost.

No DB, no network. The router uses these to (a) hard-filter models that exceed a
skill's per-million cost ceiling and (b) score remaining candidates so cheaper
models rank higher. Budget *enforcement* against real spend stays in
``llm_gateway`` / ``budget_guard`` — this layer is selection heuristics only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .registry import ModelSpec


def blended_cost_cents(model: "ModelSpec", *, input_weight: float = 0.7) -> float:
    """Blended per-million-token cost in cents for ranking."""
    return model.cost_per_million_blended(input_weight=input_weight)


def within_cost_ceiling(model: "ModelSpec", max_cents_per_million: float | None) -> bool:
    """True if the model's blended cost is at/under the ceiling.

    ``None`` ceiling means "no cost limit" (always passes).
    """
    if max_cents_per_million is None:
        return True
    return blended_cost_cents(model) <= float(max_cents_per_million)


def cost_score(model: "ModelSpec", *, max_cents_per_million: float | None = None) -> float:
    """Normalised 0..1 cost desirability (1.0 = free / cheapest).

    Scaled against the ceiling when provided, else against a sane reference of
    300 cents/million so absolute cheapness still ranks. Local/free models get 1.0.
    """
    cost = blended_cost_cents(model)
    if cost <= 0:
        return 1.0
    reference = float(max_cents_per_million) if max_cents_per_million else 300.0
    if reference <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (cost / reference)))
