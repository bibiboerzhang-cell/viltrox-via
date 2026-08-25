"""Management-only, aggregate KOL search health route."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.manager_guard import require_manager_tab
from app.domains.access import scope as access_scope
from app.domains.kol import search_sessions_team_status


router = APIRouter(tags=["vkpi-kol-pool"])


@router.get("/kol-search-sessions/team-status")
def get_kol_search_team_status(
    limit: int = Query(
        default=search_sessions_team_status.MAX_TEAM_STATUS_BATCH_SIZE,
        ge=1,
        le=search_sessions_team_status.MAX_TEAM_STATUS_BATCH_SIZE,
        description="Compatible batch-size hint; the server scans to the bounded population fence.",
    ),
    staff=Depends(require_manager_tab("vkpi", "read")),
) -> dict:
    """Return PII-free progress counts across current employee searches."""

    try:
        return search_sessions_team_status.build_team_search_status(
            staff=staff,
            limit=limit,
        )
    except access_scope.ScopeDenied as exc:
        raise HTTPException(
            status_code=403,
            detail="KOL search team status organization scope unavailable",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "kol_search_team_status_unavailable",
                "retryable": True,
            },
        ) from exc


__all__ = ["router"]
