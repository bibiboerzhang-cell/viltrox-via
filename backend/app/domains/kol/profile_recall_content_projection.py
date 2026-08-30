"""Public, non-text projection for targeted cached-content evidence."""
from __future__ import annotations

from typing import Any


_STATUS_FIELDS = (
    "status",
    "pending",
    "pending_counts_toward_target",
    "source_types",
    "evidence_fields",
    "evidence_record_count",
    "content_text_returned",
    "provider_calls",
    "llm_calls",
    "claim_status",
)


def public_content_evidence_status(value: Any) -> dict[str, Any] | None:
    """Return bounded status metadata; cached prose never crosses this seam."""

    if not isinstance(value, dict):
        return None
    return {key: value.get(key) for key in _STATUS_FIELDS}


__all__ = ["public_content_evidence_status"]
