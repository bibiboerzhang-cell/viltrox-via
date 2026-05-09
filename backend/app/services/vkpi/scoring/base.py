"""Scoring strategy contract for V-KPI recommendations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ScoringResult:
    score: float
    breakdown: dict[str, Any] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    version: str = "rule_v0"


class ScoringStrategy(Protocol):
    version: str

    def score(self, features: dict[str, Any], brief: dict[str, Any] | None = None) -> ScoringResult:
        ...


class ScoringRegistry:
    _strategies: dict[str, ScoringStrategy] = {}

    @classmethod
    def register(cls, strategy: ScoringStrategy) -> None:
        cls._strategies[str(strategy.version)] = strategy

    @classmethod
    def get(cls, version: str = "rule_v0") -> ScoringStrategy:
        key = str(version or "rule_v0")
        if key not in cls._strategies:
            raise KeyError(f"scoring strategy not registered: {key}")
        return cls._strategies[key]

    @classmethod
    def versions(cls) -> list[str]:
        return sorted(cls._strategies)
