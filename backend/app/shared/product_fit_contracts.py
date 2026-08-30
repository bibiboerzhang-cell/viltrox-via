"""Public contracts shared by deterministic product-fit use cases.

This module is intentionally type-only: it has no database, provider, domain,
logging, or runtime-registry imports.  The mapping-shaped DTOs preserve the
existing JSON payload contract while the protocols make read and inference
effects explicit at composition boundaries.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, TypedDict


class Evidence(TypedDict, total=False):
    type: str
    polarity: str
    severity: str
    detail: str
    score_component: str
    source_table: str
    source_id: str
    source_ref: str
    source_sheet: str
    source_row: str
    confidence_score: float


class ProductFamily(TypedDict, total=False):
    id: int
    entity_uid: str
    display_name: str
    identity_key: str
    metadata_json: str


class MarketSignal(TypedDict, total=False):
    id: int
    entity_id: int
    fact_type: str
    fact_value_text: str
    fact_json: str
    observed_at: str


class FitScoreBreakdown(TypedDict, total=False):
    historical_fit: float
    adjacent_product_fit: float
    dimensions11_product_fit: float
    cooperation_depth: float
    market_activity: float
    contact_readiness: float
    region_relevance: float
    data_quality: float
    base: float
    penalty_factors: Mapping[str, float]
    penalty_factor: float
    final: float


class ProductFitCandidate(TypedDict, total=False):
    rank: int
    percentile_rank: float
    product_family_uid: str
    product_family_name: str
    product_member_count: int
    score: float
    score_breakdown: FitScoreBreakdown
    evidence_pro: list[Evidence]
    evidence_con: list[Evidence]
    links: Mapping[str, str]
    recommendation_reason: Mapping[str, Any]


class PreviewExecutionPolicy(TypedDict):
    mode: str
    provider_calls_allowed: bool
    provider_calls_planned: int
    provider_call_scope: str
    deterministic_ranking: bool
    business_actions_executed: bool


class ReasonResult(TypedDict, total=False):
    mode: str
    provider: str
    model: str
    requested_binding: str
    status: str
    fallback_reason: str
    short_reason: str
    pitch_angle: str
    caution_note: str


class ProductFitRepository(Protocol):
    """Read-only repository required by KOL product-fit scoring."""

    def list_kol_entities(self) -> list[dict[str, Any]]: ...

    def pools_by_source_ref(self) -> dict[str, dict[str, Any]]: ...

    def legacy_entities_by_uid(self) -> dict[str, dict[str, Any]]: ...

    def facts_by_kol(self) -> dict[int, list[dict[str, Any]]]: ...

    def worked_links_by_kol(self) -> dict[int, list[dict[str, Any]]]: ...

    def product_family_maps(
        self,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]: ...

    def target_market_signals(self, family_id: int) -> list[dict[str, Any]]: ...

    def candidate_families(self) -> list[dict[str, Any]]: ...

    def official_family_links(self) -> dict[int, list[dict[str, Any]]]: ...

    def dimensions11_fit(self, kol_pool_id: int) -> dict[str, dict[str, Any]]: ...


class RecommendationReasonPort(Protocol):
    """Bounded optional inference port; ranking never depends on its result."""

    def generate_reason(
        self,
        candidate: Mapping[str, Any],
        *,
        binding: Mapping[str, Any],
        token_limit: int,
        budget_scope: str,
    ) -> ReasonResult: ...


def copy_reason_result(value: Mapping[str, Any]) -> ReasonResult:
    """Return the public mapping DTO without retaining adapter-owned state."""

    return ReasonResult(**dict(value))


__all__ = [
    "Evidence",
    "FitScoreBreakdown",
    "MarketSignal",
    "PreviewExecutionPolicy",
    "ProductFamily",
    "ProductFitCandidate",
    "ProductFitRepository",
    "ReasonResult",
    "RecommendationReasonPort",
    "copy_reason_result",
]
