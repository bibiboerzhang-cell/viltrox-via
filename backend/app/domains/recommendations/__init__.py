"""Recommendation and product-analysis domain."""

from app.domains.recommendations.new_launch_match import (
    BUDGET_SCOPE,
    FORBIDDEN_WRITE_FLAGS,
    build_new_launch_match_preview,
)
from app.domains.recommendations.new_launch_match_format import format_preview_summary, render_markdown
from app.domains.recommendations.project_next_action import build_project_next_action_preview
from app.domains.recommendations.project_next_action_format import (
    format_preview_summary as format_project_next_action_summary,
)

__all__ = [
    "BUDGET_SCOPE",
    "FORBIDDEN_WRITE_FLAGS",
    "build_new_launch_match_preview",
    "build_project_next_action_preview",
    "format_preview_summary",
    "format_project_next_action_summary",
    "render_markdown",
]
