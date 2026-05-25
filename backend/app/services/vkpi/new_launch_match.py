"""Compatibility shim for the recommendations domain new-launch matcher."""
from __future__ import annotations

from app.domains.recommendations import new_launch_match as _impl
from app.domains.recommendations import new_launch_match_helpers as _helpers
from app.domains.recommendations.new_launch_match_format import format_preview_summary, render_markdown

for _name in getattr(_helpers, "__all__", []):
    globals()[_name] = getattr(_helpers, _name)

for _name in (
    "BUDGET_SCOPE",
    "FORBIDDEN_WRITE_FLAGS",
    "REASON_BUDGET_SCOPE",
    "SCENARIO",
    "build_new_launch_match_preview",
    "_attach_reason",
    "_deterministic_reason",
    "_json",
    "_json_write",
    "_last_by_uid",
    "_markdown_write",
    "_market_detail",
    "_parse_reason_text",
    "_persist_preview_run",
    "_reason_prompt",
):
    globals()[_name] = getattr(_impl, _name)

del _name
__all__ = [name for name in globals() if not name.startswith("__") and name not in {"_impl", "_helpers"}]
