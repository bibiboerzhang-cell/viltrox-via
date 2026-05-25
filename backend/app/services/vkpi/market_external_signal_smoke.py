"""Compatibility facade for read-only external market signal smokes."""
from __future__ import annotations

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

__all__ = [
    "ALLOWED_HOSTS",
    "DEFAULT_SOURCES",
    "build_external_daily_candidate_plan",
    "build_external_signal_smoke",
    "build_external_signal_smoke_from_file",
    "build_external_source_matrix",
    "google_news_rss_url",
    "render_external_daily_candidate_plan_markdown",
    "render_external_signal_smoke_markdown",
    "write_external_daily_candidate_plan",
    "write_external_signal_smoke",
]
