"""Immutable input contract for the profile recall orchestration phases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecallRequest:
    recall_started: float
    query_text: str
    product_sku: str
    safe_candidate_limit: int
    requested_candidate_limit: int
    safe_server_candidate_limit_override: int | None
    server_candidate_limit_override_applied: bool
    safe_limit: int
    safe_creator_quota: int
    safe_reviewer_quota: int
    safe_vector_weight: float
    safe_type_weight: float
    ratio_policy: str
    mixed_policy: str
    dedupe: bool
    type_boost_enabled: bool
    exclude_chinese: bool
    product_focus: Any
    target_persona: str
    provider_free: bool
    normalized_filters: dict[str, Any]
    unsupported_filters: list[str]
    retrieval_filters: dict[str, Any]
    search_strategy: str
    normalized_bucket_policy: dict[str, Any]
    bucket_policy_adjusted: bool
    allow_backfill: bool
    operator_query_text: str
    required_product_evidence_terms: Any
    local_qualification_policy: dict[str, Any] | None
    smart_local_enabled: bool
    targeted_query_cell: dict[str, Any] | None
