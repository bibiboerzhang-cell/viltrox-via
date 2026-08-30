"""Public facade for the complete submission audit pipeline.

The phase implementation lives in bounded leaf modules.  Dependencies remain
lazy-loaded here so importing workers does not initialize provider clients.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.core.logging import get_logger
from app.db.connection import db_connection_scope
from app.utils.urls import detect_platform, valid_url


logger = get_logger(__name__)


@dataclass
class AuditContext:
    """External scoring hooks supplied by the audit worker."""

    compute_weighted_fn: Any = None
    update_benchmark_fn: Any = None
    get_vertical_fn: Any = None
    apply_learned_weights_fn: Any = None


async def perform_full_audit(job: Any, ctx: AuditContext = None) -> Dict[str, Any]:
    """Run the full scrape, analysis, detection, scoring and profile pipeline."""
    from app.services.ai.analyzers.claude_text import (
        analyze_text_content,
        check_content_similarity as _check_content_similarity,
    )
    from app.services.ai.analyzers.claude_vision import (
        analyze_url_content_smart,
        analyze_video_with_claude,
    )
    from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
    from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption
    from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
    from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE
    from app.services.ai.runtime_guards import guarded_provider_call
    from app.services.audit.pipeline_contract import AuditDependencies
    from app.services.audit.pipeline_execution import execute_full_audit
    from app.services.audit.similarity import (
        analyze_comments_for_spam,
        classify_product,
        detect_gear_mentions,
        detect_viltrox,
    )
    from app.services.scoring.benchmark import update_genre_benchmark
    from app.services.scoring.campaign import (
        compute_campaign_score,
        compute_creator_score,
        compute_ratios as _compute_ratios,
    )
    from app.services.scoring.creator import update_creator_profile
    from app.services.scoring.risk import compute_risk
    from app.services.scraping.platform_router import scrape_url

    # Keep the historical import-time dependency contract even though these
    # helpers are not directly invoked by the pipeline.
    del _check_content_similarity, _compute_ratios

    dependencies = AuditDependencies(
        db_connection_scope=db_connection_scope,
        logger=logger,
        valid_url=valid_url,
        detect_platform=detect_platform,
        scrape_url=scrape_url,
        analyze_video_with_claude=analyze_video_with_claude,
        analyze_url_content_smart=analyze_url_content_smart,
        analyze_text_content=analyze_text_content,
        gpt_prefilter_caption=gpt_prefilter_caption,
        analyze_youtube_with_gemini=analyze_youtube_with_gemini,
        gemini_available=GEMINI_AVAILABLE,
        anthropic_available=ANTHROPIC_AVAILABLE,
        guarded_provider_call=guarded_provider_call,
        classify_product=classify_product,
        detect_gear_mentions=detect_gear_mentions,
        detect_viltrox=detect_viltrox,
        analyze_comments_for_spam=analyze_comments_for_spam,
        compute_risk=compute_risk,
        compute_campaign_score=compute_campaign_score,
        compute_creator_score=compute_creator_score,
        update_creator_profile=update_creator_profile,
        update_genre_benchmark=update_genre_benchmark,
    )
    return await execute_full_audit(job, ctx, dependencies)
