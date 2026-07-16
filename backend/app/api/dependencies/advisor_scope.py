"""FastAPI dependencies for private Marketing Advisor data."""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_user_required
from app.core.permissions import check_tab_permission, staff_context_for_user
from app.domains.advisor.scope import AdvisorScope, AdvisorScopeError, advisor_scope_from_staff


def _resolve(user: dict, *, level: str) -> AdvisorScope:
    staff = staff_context_for_user(user)
    if not check_tab_permission(staff, "vkpi", level):
        raise HTTPException(status_code=403, detail=f"No permission for vkpi:{level}")
    try:
        return advisor_scope_from_staff(staff)
    except AdvisorScopeError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "advisor_scope_unresolved", "reason": exc.reason},
        ) from exc


async def require_advisor_read_scope(
    user=Depends(get_user_required),
) -> AdvisorScope:
    return _resolve(user, level="read")


async def require_advisor_write_scope(
    user=Depends(get_user_required),
) -> AdvisorScope:
    return _resolve(user, level="write")
