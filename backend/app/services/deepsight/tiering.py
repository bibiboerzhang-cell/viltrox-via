from __future__ import annotations

from app.schemas.deepsight import DeepSightRequest


def decide_tier(request: DeepSightRequest) -> str:
    if request.scope == "official_matrix" and request.model_mode == "fast" and not request.refresh:
        return "tier0"
    if request.model_mode == "triad" or request.scan_accounts:
        return "tier2"
    return "tier1"


def ttl_for_tier(tier: str) -> int:
    if tier == "tier0":
        return 60 * 10
    if tier == "tier1":
        return 60 * 5
    return 60 * 2
