"""Compatibility helpers for attachment status receipts."""
from typing import Any

from app.domains.kol.search_sessions_serde import _normalize_status, _text


def _session_status_after_profile_item(current_status: str, current_phase: str, item_status: str) -> str:
    if _text(current_status).lower() == "running" and _text(current_phase).lower() in {"base", "profile"}:
        return "running"
    return _normalize_status(item_status)


def _attached_result_count(result: dict[str, Any]) -> int:
    items = result.get("items")
    if isinstance(items, list) and items:
        return len(items)
    buckets = result.get("buckets") if isinstance(result.get("buckets"), dict) else {}
    bucket_count = sum(len(value) for value in buckets.values() if isinstance(value, list))
    return bucket_count if bucket_count else len(items or [])


def _persist_attached_status(session_id: int, recorded: dict[str, Any], *, status: str,
                             result_state: str) -> dict[str, Any]:
    from app.domains.kol.search_sessions import update_session_result_summary

    normalized = _normalize_status(status)
    if _text(recorded.get("status")).lower() == normalized:
        return recorded
    updated = update_session_result_summary(
        int(session_id), status=normalized, summary_patch={"result_state": result_state},
    )
    recorded["status"] = updated.get("status") or normalized
    if isinstance(recorded.get("result_summary"), dict):
        recorded["result_summary"]["result_state"] = result_state
    return recorded
