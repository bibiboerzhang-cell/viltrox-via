"""Compatibility shim for recommendations-domain project next-action previews."""
from __future__ import annotations

from app.domains.recommendations.project_next_action import (
    FORBIDDEN_WRITE_FLAGS,
    build_project_next_action_preview,
)
from app.domains.recommendations.project_next_action_format import format_preview_summary, render_markdown

__all__ = [
    "FORBIDDEN_WRITE_FLAGS",
    "build_project_next_action_preview",
    "format_preview_summary",
    "render_markdown",
]
