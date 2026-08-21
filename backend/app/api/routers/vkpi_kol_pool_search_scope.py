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


def _reused_video_session_lineage(
    session: dict | None,
    result: dict,
    *,
    body: dict,
    staff: dict,
    default_source: str,
    kol_pool_id: int,
    evidence_id: int,
) -> tuple[dict, int]:
    """Persist session + exact URL item before a signed final_v1 child is queued.

    Paid video analysis requires durable actor/session/item lineage even when
    the direct URL endpoint did not ask for history.  The session is created
    only after local evidence proved the exact target; the item must exist
    before the queue call, and exactly one ``url_video`` item may match.
    Raises ``RuntimeError`` (no enqueue) when that lineage cannot be recorded.
    """
    if not session:
        session = kol_search_sessions.ensure_session_for_result(
            session_id=None,
            create=True,
            query_text=str(body.get("url") or ""),
            query_type="url_video",
            source=str(body.get("source") or default_source),
            input_payload={key: value for key, value in body.items() if key != "api_token"},
            staff=staff,
        )
    session_id = _int_or_none((session or {}).get("id"))
    if not session_id:
        raise RuntimeError("video_analysis_session_required")
    recorded = kol_search_sessions.attach_url_result(int(session_id), result)
    matches = [
        item
        for item in (recorded.get("items") or [])
        if _int_or_none(item.get("kol_pool_id")) == int(kol_pool_id)
        and _int_or_none(item.get("evidence_id")) == int(evidence_id)
        and str(item.get("item_type") or "").strip().lower() == "url_video"
    ]
    item_id = _int_or_none(matches[0].get("id")) if len(matches) == 1 else None
    if not item_id:
        raise RuntimeError("video_analysis_session_item_required")
    return dict(session or {}), int(item_id)


__all__ = [
    "_approved_session_kol_ids",
    "_owned_search_session_or_http",
    "_reused_video_session_lineage",
]
