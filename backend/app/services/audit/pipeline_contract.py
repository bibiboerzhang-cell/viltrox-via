"""Internal data contracts shared by the bounded audit pipeline phases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class AuditDependencies:
    """Runtime dependencies loaded by the public pipeline facade."""

    db_connection_scope: Any
    logger: Any
    valid_url: Any
    detect_platform: Any
    scrape_url: Any
    analyze_video_with_claude: Any
    analyze_url_content_smart: Any
    analyze_text_content: Any
    gpt_prefilter_caption: Any
    analyze_youtube_with_gemini: Any
    gemini_available: bool
    anthropic_available: bool
    guarded_provider_call: Any
    classify_product: Any
    detect_gear_mentions: Any
    detect_viltrox: Any
    analyze_comments_for_spam: Any
    compute_risk: Any
    compute_campaign_score: Any
    compute_creator_score: Any
    update_creator_profile: Any
    update_genre_benchmark: Any


@dataclass
class CollectedSource:
    scraped: Dict[str, Any]
    title: str
    caption: str
    raw_text: str
    metrics: Dict[str, Any]
    metrics_available: Dict[str, Any]


@dataclass
class AnalysisOutcome:
    video_analysis_result: Dict[str, Any] | None
    prefilter_result: Dict[str, Any] | None
    text_analysis_result: Dict[str, Any] | None
    has_upload: bool
    uploaded_analysis_path: str


@dataclass
class DetectionOutcome:
    product_match: Dict[str, Any]
    gear_mentions: Any
    brand: Dict[str, Any]
    hints: Dict[str, Any]


@dataclass
class ScoringOutcome:
    content_types: list[Any]
    creator_score: Any
    campaign: Dict[str, Any]
    final_score: Any
    detection_status: str
    recommendation: str
    overall_score: Any
    risk: Dict[str, Any]
    comment_spam: Any
