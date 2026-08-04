"""Owner-scoped request helpers for KOL search-session routes."""
from __future__ import annotations

from fastapi import HTTPException

import app.domains.kol.search_sessions as kol_search_sessions
from app.api.routers.vkpi_kol_pool_helpers import _int_or_none


def _owned_search_session_or_http(session_id: int, staff: dict) -> dict:
    """Resolve one current-user session; cross-user IDs deliberately look absent."""
    try:
        return kol_search_sessions.get_session(
            int(session_id),
            staff=staff,
            scope_to_staff=True,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "search_session_unavailable",
                "operation": "search_session",
                "retryable": True,
            },
        ) from exc


def _approved_session_kol_ids(session: dict, raw_ids: object) -> list[int]:
    """Body may narrow an approved set, never expand it to arbitrary pool IDs."""
    approved: list[int] = []
    approved_set: set[int] = set()
    for value in session.get("approved_kol_ids") or []:
        parsed = _int_or_none(value)
        if parsed and parsed not in approved_set:
            approved_set.add(parsed)
            approved.append(parsed)
    if not isinstance(raw_ids, list) or not raw_ids:
        return approved
    requested: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        parsed = _int_or_none(value)
        if parsed and parsed not in seen:
            seen.add(parsed)
            requested.append(parsed)
    outside = [kol_pool_id for kol_pool_id in requested if kol_pool_id not in approved_set]
    if outside:
        raise HTTPException(
            status_code=400,
            detail="kol_pool_ids must be a subset of approved session candidates: "
            + ",".join(str(kol_pool_id) for kol_pool_id in outside),
        )
    return requested


__all__ = ["_approved_session_kol_ids", "_owned_search_session_or_http"]
