"""Market signal domain facade."""

from app.domains.market.signal_classifier import (
    VILTROX_TERMS,
    build_market_signal_classification,
    render_classification_markdown,
    write_classification_report,
)
from app.domains.market.signal_taxonomy import (
    KEYWORD_GROUPS,
    KEYWORDS,
    TIER1_GROUPS,
    TIER2_GROUPS,
    dedupe_keywords,
    keyword_groups,
    keyword_hits,
    summarize_keyword_groups,
)

__all__ = [
    "KEYWORD_GROUPS",
    "KEYWORDS",
    "TIER1_GROUPS",
    "TIER2_GROUPS",
    "VILTROX_TERMS",
    "build_market_signal_classification",
    "dedupe_keywords",
    "keyword_groups",
    "keyword_hits",
    "render_classification_markdown",
    "summarize_keyword_groups",
    "write_classification_report",
]
