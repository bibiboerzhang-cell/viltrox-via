"""Pure outcome rules for Apify batch execution summaries."""
from __future__ import annotations

from typing import Any


def target_execution_outcome(
    *,
    executed: bool,
    provider_status: str,
    sync_status: str,
    matched: bool,
    result: dict[str, Any],
) -> tuple[str, bool, str]:
    if not executed:
        return "planned", False, "not_executed"
    if provider_status == "ok" and matched:
        return "matched", False, sync_status or "synced"
    if provider_status == "ok":
        return "unmatched", True, "dataset_item_not_mapped"
    if provider_status == "not_configured":
        return "not_configured", True, "apify_not_configured"
    reason = str(
        result.get("error") or sync_status or provider_status or "batch_error"
    )[:500]
    return "error", True, reason
