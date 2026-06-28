"""Quality / speed policy — pure helpers to gate and score models.

Mirror of cost_policy on the quality and latency axes. The router hard-filters
candidates below a skill's minimum quality (and minimum speed), then scores the
survivors so stronger / faster models rank higher.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .registry import ModelSpec


def meets_quality(model: "ModelSpec", min_quality: float | None) -> bool:
    """True if the model's quality meets the floor (None = no floor)."""
    if min_quality is None:
        return True
    return model.quality >= float(min_quality)


def meets_speed(model: "ModelSpec", min_speed: float | None) -> bool:
    """True if the model's speed meets the floor (None = no floor)."""
    if min_speed is None:
        return True
    return model.speed >= float(min_speed)


def meets_context(model: "ModelSpec", min_context_tokens: int | None) -> bool:
    """True if the model's context window is large enough (None = no requirement)."""
    if min_context_tokens is None:
        return True
    return model.context_tokens >= int(min_context_tokens)


def quality_score(model: "ModelSpec") -> float:
    """Normalised 0..1 quality desirability (already 0..1 in registry)."""
    return max(0.0, min(1.0, float(model.quality)))


def speed_score(model: "ModelSpec") -> float:
    """Normalised 0..1 speed desirability (already 0..1 in registry)."""
    return max(0.0, min(1.0, float(model.speed)))
