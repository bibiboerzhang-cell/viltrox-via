"""P3: single additive MY KOL aggregate read-endpoint.

GET /api/admin/vkpi/my-kol/aggregate?staff_id=&window_days=

One read-only call that returns a staff member's full MY KOL payload (staff row,
official channel matrix, pool favorites, projects, claims, kpi summary). Additive:
the frontend MyKolPage keeps its current calls until it adopts this later.

RBAC: require_tab("vkpi","read"). Employees (non can_view_all) only ever get
their own aggregate; managers may pass ?staff_id= to view a specific member,
defaulting to themselves.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.domains.access import scope
from app.domains.kol import my_kol_aggregate

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-my-kol"])


@router.get("/my-kol/aggregate")
def my_kol_aggregate_endpoint(
    staff_id: int | None = Query(default=None, ge=1),
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return the single MY KOL aggregate bundle for the scoped staff member."""
    # 修(2026-06-16):owner/manager(can_view_all=True)自查(未显式传 staff_id)时
    # effective_staff_id 返回 None('全部'语义)→ 旧逻辑 403,导致 owner 收藏写进库却永远读不到。
    # 自查场景回落到本人 actor id(显式传 staff_id 仍走 manager 跨看)。
    target = scope.effective_staff_id(staff, staff_id) or scope.actor_staff_id(staff)
    if not target:
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    try:
        return my_kol_aggregate.build_my_kol_aggregate(
            get_conn(),
            int(target),
            window_days=int(window_days),
            actor=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
