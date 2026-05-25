"""Comment collection and intelligence domain exports."""
from __future__ import annotations

from app.domains.comments.channel import (
    COMMENT_COLLECT_PLATFORMS,
    MAX_CHANNEL_COMMENT_CAP,
    _comment_contract,
    _comment_external_post_id,
    _package_reddit_comments,
    cache_inline_channel_comments,
    channel_post_comments,
    collect_channel_post_comments,
)
from app.domains.comments.collector import (
    _record_run,
    _resolve_post,
    _standardize_comment,
    batch_collect_pending,
    collect_post_comments,
    ensure_vkpi_comments_schema,
    stats,
)
from app.domains.comments.intelligence import (
    _rule_sentiment,
    _rule_tags,
    _rule_v0_comment_summary,
    ensure_vkpi_comment_intelligence_schema,
    get_run,
    list_runs,
    overview,
    process_post,
    process_recent_posts,
    retry_run,
)
from app.domains.comments.intelligence_rules import rule_v0_comment_summary
