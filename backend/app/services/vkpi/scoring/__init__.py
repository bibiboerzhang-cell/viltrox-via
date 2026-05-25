"""Backwards-compatible scoring shim."""
from app.domains.scoring import ScoringRegistry, ScoringResult
from app.domains.scoring import rule_v0  # noqa: F401

__all__ = ["ScoringRegistry", "ScoringResult"]
