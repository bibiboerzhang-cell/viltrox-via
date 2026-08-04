"""Deterministic evidence queries for the Ask & Find v2 command surface.

The package is intentionally read-only.  It turns a bounded natural-language
request into one of a small set of parameterised SQL queries and returns the
same evidence contract for every intent.  It never invokes an LLM, provider,
worker or write-side service.
"""

from app.domains.intelligent_query.service import (
    QueryScopeDenied,
    QueryValidationError,
    execute_query,
)

__all__ = ["QueryScopeDenied", "QueryValidationError", "execute_query"]
