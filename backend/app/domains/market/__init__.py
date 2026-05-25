"""Market signal domain facade."""

from app.domains.market.signal_classifier import (
    VILTROX_TERMS,
    build_market_signal_classification,
    render_classification_markdown,
    write_classification_report,
)
from app.domains.market.signal_review_package import (
    build_external_signal_review_package,
    build_external_signal_review_package_from_files,
    build_market_signal_review_package,
    build_market_signal_review_package_from_file,
    competitor_signal_rows_from_review_package,
)
from app.domains.market.signal_review_reports import (
    render_competitor_signal_write_markdown,
    render_external_signal_review_package_markdown,
    render_review_package_markdown,
    write_competitor_signal_write_report,
    write_external_signal_review_package_report,
    write_review_package_report,
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
from app.domains.market.signal_write_package import (
    TARGET_TABLES,
    build_external_market_signal_write_package,
    build_market_signal_write_package,
    build_market_signal_write_package_from_file,
)

__all__ = [
    "KEYWORD_GROUPS",
    "KEYWORDS",
    "TIER1_GROUPS",
    "TIER2_GROUPS",
    "TARGET_TABLES",
    "VILTROX_TERMS",
    "build_external_signal_review_package",
    "build_external_signal_review_package_from_files",
    "build_external_market_signal_write_package",
    "build_market_signal_classification",
    "build_market_signal_review_package",
    "build_market_signal_review_package_from_file",
    "build_market_signal_write_package",
    "build_market_signal_write_package_from_file",
    "competitor_signal_rows_from_review_package",
    "dedupe_keywords",
    "keyword_groups",
    "keyword_hits",
    "render_competitor_signal_write_markdown",
    "render_external_signal_review_package_markdown",
    "render_classification_markdown",
    "render_review_package_markdown",
    "summarize_keyword_groups",
    "write_competitor_signal_write_report",
    "write_external_signal_review_package_report",
    "write_classification_report",
    "write_review_package_report",
]
