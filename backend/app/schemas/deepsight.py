from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ScanAccount(BaseModel):
    platform: str
    handle: str
    name: str | None = None


class DeepSightRequest(BaseModel):
    brand: str = "Viltrox"
    scope: Literal["official_matrix", "ugc_market", "all_visual_life"] = "official_matrix"
    days: int = Field(default=7, ge=1, le=180)
    previous_days: int | None = Field(default=None, ge=1, le=180)
    platforms: list[str] = Field(default_factory=list)
    include_competitors: bool = True
    include_comments: bool = True
    include_visual_life: bool = True
    refresh: bool = False
    model_mode: Literal["fast", "triad"] = "fast"
    scan_accounts: list[ScanAccount] = Field(default_factory=list)
    scan_data: dict[str, Any] | None = None
    max_posts_per_account: int = Field(default=60, ge=5, le=1000)
    concurrency: int = Field(default=4, ge=1, le=12)


class DeepSightAction(BaseModel):
    priority: Literal["urgent", "this_week", "ongoing"]
    owner: str
    text: str
    expected_impact: str


class DeepSightDiagnosis(BaseModel):
    one_liner: str
    overall_health: Literal["good", "warning", "critical"]
    platform_diagnosis: list[dict[str, Any]] = Field(default_factory=list)
    account_diagnosis: list[dict[str, Any]] = Field(default_factory=list)
    comment_insight: dict[str, Any] = Field(default_factory=dict)
    product_insight: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[DeepSightAction] = Field(default_factory=list)
    split_vote: bool = False
    council_views: dict[str, Any] = Field(default_factory=dict)
    emotional_layer: dict[str, Any] = Field(default_factory=dict)


class DeepSightResponse(BaseModel):
    status: Literal["success"] = "success"
    tier: Literal["tier0", "tier1", "tier2"]
    cache_hit: bool = False
    generated_at: str
    elapsed_sec: float
    evidence_pack: dict[str, Any]
    diagnosis: dict[str, Any]
