"""Recommendation and product-analysis domain.

Public convenience exports are resolved lazily.  Eagerly importing the launch
matcher here made importing an independent submodule (for example the feature
store) depend on the KOL product-fit graph, which in turn imports the launch
matcher and can observe it only partially initialized.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "BUDGET_SCOPE",
    "FORBIDDEN_WRITE_FLAGS",
    "build_new_launch_match_preview",
    "build_project_next_action_preview",
    "format_preview_summary",
    "format_project_next_action_summary",
    "render_markdown",
]


_LAZY_EXPORTS = {
    "BUDGET_SCOPE": ("app.domains.recommendations.new_launch_match", "BUDGET_SCOPE"),
    "FORBIDDEN_WRITE_FLAGS": ("app.domains.recommendations.new_launch_match", "FORBIDDEN_WRITE_FLAGS"),
    "build_new_launch_match_preview": (
        "app.domains.recommendations.new_launch_match",
        "build_new_launch_match_preview",
    ),
    "build_project_next_action_preview": (
        "app.domains.recommendations.project_next_action",
        "build_project_next_action_preview",
    ),
    "format_preview_summary": (
        "app.domains.recommendations.new_launch_match_format",
        "format_preview_summary",
    ),
    "format_project_next_action_summary": (
        "app.domains.recommendations.project_next_action_format",
        "format_preview_summary",
    ),
    "render_markdown": ("app.domains.recommendations.new_launch_match_format", "render_markdown"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
