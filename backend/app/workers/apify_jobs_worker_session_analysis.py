"""Compatibility facade for domain-owned final-v1 session projections."""
from app.domains.kol.search_session_job_analysis import (
    score_entry as _score_entry,
    search_session_analysis_summary_from_result as _search_session_analysis_summary_from_result,
)

__all__ = ["_score_entry", "_search_session_analysis_summary_from_result"]
