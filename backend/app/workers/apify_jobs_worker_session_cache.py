"""Compatibility facade for the domain-owned ready-cache projection."""
from app.domains.kol.search_session_job_analysis import (
    search_session_analysis_summary_from_ready_cache,
)

__all__ = ["search_session_analysis_summary_from_ready_cache"]
