"""Market signal domain facade."""

from app.domains.market.signal_classifier import (
    VILTROX_TERMS,
    build_market_signal_classification,
    render_classification_markdown,
    write_classification_report,
)
from app.domains.market.external_signal_reports import (
    render_external_daily_candidate_plan_markdown,
    render_external_signal_smoke_markdown,
    write_external_daily_candidate_plan,
    write_external_signal_smoke,
)
from app.domains.market.external_signal_smoke import (
    ALLOWED_HOSTS,
    DEFAULT_SOURCES,
    build_external_daily_candidate_plan,
    build_external_signal_smoke,
    build_external_signal_smoke_from_file,
    build_external_source_matrix,
    google_news_rss_url,
)
from app.domains.market.intelligence_cards import (
    build_market_intelligence_cards,
    build_market_intelligence_cards_from_files,
    latest_external_signal_smoke_report,
    latest_usable_market_llm_report,
    render_market_intelligence_cards_markdown,
    write_market_intelligence_cards,
)
from app.domains.market.intelligence_card_repository import latest_reviewed_market_run_id
from app.domains.market.intelligence_v0 import (
    COMPETITOR_SIGNAL_TABLE,
    MARKET_STORAGE_TABLES,
    build_market_intelligence_report,
)
from app.domains.market.intelligence_use_case import build_market_intelligence_v0
from app.domains.market.llm_quality import (
    evaluate_market_llm_output,
    evaluate_market_llm_report,
    evaluate_market_llm_report_file,
)
from app.domains.market.provider_preflight import (
    DEFAULT_PROMPT,
    PROVIDER_SOURCES,
    build_provider_preflight,
)
from app.domains.market.signal_commit import (
    build_competitor_signal_run_summary,
    build_competitor_signal_write_result,
    validate_competitor_signal_write_request,
)
from app.domains.market.source_design import (
    CANONICAL_CONTRACT,
    SOURCE_REGISTRY,
    TABLE_NAMES,
    build_market_source_design_report_from_tables,
    source_readiness,
)
from app.domains.market.source_design_use_case import build_market_source_design_report
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
from app.domains.market.signal_review_persistence import write_reviewed_competitor_signals
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
from app.domains.market.signal_ingest_use_case import write_market_signal_package

__all__ = [
    "ALLOWED_HOSTS",
    "CANONICAL_CONTRACT",
    "COMPETITOR_SIGNAL_TABLE",
    "DEFAULT_SOURCES",
    "DEFAULT_PROMPT",
    "KEYWORD_GROUPS",
    "KEYWORDS",
    "MARKET_STORAGE_TABLES",
    "PROVIDER_SOURCES",
    "SOURCE_REGISTRY",
    "TABLE_NAMES",
    "TIER1_GROUPS",
    "TIER2_GROUPS",
    "TARGET_TABLES",
    "VILTROX_TERMS",
    "build_external_daily_candidate_plan",
    "build_external_signal_smoke",
    "build_external_signal_smoke_from_file",
    "build_external_source_matrix",
    "build_provider_preflight",
    "build_external_signal_review_package",
    "build_external_signal_review_package_from_files",
    "build_external_market_signal_write_package",
    "build_competitor_signal_run_summary",
    "build_competitor_signal_write_result",
    "build_market_intelligence_cards",
    "build_market_intelligence_cards_from_files",
    "build_market_intelligence_report",
    "build_market_intelligence_v0",
    "build_market_signal_classification",
    "build_market_source_design_report_from_tables",
    "build_market_source_design_report",
    "build_market_signal_review_package",
    "build_market_signal_review_package_from_file",
    "build_market_signal_write_package",
    "build_market_signal_write_package_from_file",
    "competitor_signal_rows_from_review_package",
    "dedupe_keywords",
    "evaluate_market_llm_output",
    "evaluate_market_llm_report",
    "evaluate_market_llm_report_file",
    "google_news_rss_url",
    "keyword_groups",
    "keyword_hits",
    "latest_external_signal_smoke_report",
    "latest_reviewed_market_run_id",
    "latest_usable_market_llm_report",
    "render_competitor_signal_write_markdown",
    "render_external_daily_candidate_plan_markdown",
    "render_external_signal_smoke_markdown",
    "render_external_signal_review_package_markdown",
    "render_classification_markdown",
    "render_market_intelligence_cards_markdown",
    "render_review_package_markdown",
    "source_readiness",
    "summarize_keyword_groups",
    "write_competitor_signal_write_report",
    "write_external_daily_candidate_plan",
    "write_external_signal_smoke",
    "write_external_signal_review_package_report",
    "write_market_signal_package",
    "write_classification_report",
    "write_market_intelligence_cards",
    "write_review_package_report",
    "write_reviewed_competitor_signals",
    "validate_competitor_signal_write_request",
]
