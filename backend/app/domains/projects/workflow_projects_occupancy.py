"""Audit logging + staff-name + pool-occupancy helpers for V-KPI workflow projects.

Moved verbatim from workflow_projects.py (behavior-preserving extraction).
"""
from __future__ import annotations

import logging
from typing import Any

from app.domains.projects.workflow_common import _int, staff_id

logger = logging.getLogger(__name__)


def _log_project_audit(
    *,
    staff: dict[str, Any] | None,
    action_type: str,
    project_id: int,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Best-effort business audit for project lifecycle actions."""
    try:
        from app.domains import audit

        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type=action_type,
            target_type="project",
            target_id=int(project_id),
            detail=detail,
            metadata=metadata or {},
        )
    except Exception as exc:  # pragma: no cover - audit must not block workflow actions
        logger.warning("vkpi.workflow_project_audit_failed", extra={"action_type": action_type, "project_id": project_id, "error": str(exc)})


def _current_user_id(staff: dict[str, Any] | None) -> int:
    return int((staff or {}).get("user_id") or 0)


def _staff_display_names(conn, staff_ids: list[int] | set[int]) -> dict[int, str]:
    """staff.id -> 真人姓名(users.name),查无名时回退「员工#id」。"""
    ids = sorted({int(value) for value in staff_ids if _int(value)})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT st.id AS staff_id, u.name AS user_name
        FROM staff st
        LEFT JOIN users u ON u.id = st.user_id
        WHERE st.id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    names: dict[int, str] = {}
    for row in rows:
        data = dict(row)
        sid = _int(data.get("staff_id"))
        if sid:
            names[sid] = str(data.get("user_name") or "").strip() or f"员工#{sid}"
    for sid in ids:
        names.setdefault(sid, f"员工#{sid}")
    return names


def _pool_claim_occupancy(conn, kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    """乙案(项目维独占)事实独占源,选择器置灰与写入防绕过共用同一口径:

    1. active claim(vkpi_kol_claims,经 vkpi_kol_pool.linked_main_kol_id join,
       预研实测命中≈0 但为真实认领表,优先级最高);
    2. 在役 assignment 负责人(stage/stage_status 非终态、项目非 cancelled/deleted/
       restricted)——与 pool_favorites.list_favorites 的在役口径同源,另排除 closed。
    返回 {kol_pool_id: {staff_id, source('claim'|'assignment'), claim_id, project_id, stage}}。
    """
    ids = sorted({_int(value) for value in kol_pool_ids if _int(value)})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    occupancy: dict[int, dict[str, Any]] = {}
    assignment_rows = conn.execute(
        f"""
        SELECT a.kol_pool_id, a.assigned_staff_id, a.project_id, COALESCE(a.stage, '') AS stage
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_projects pr ON pr.id = a.project_id
        WHERE a.kol_pool_id IN ({placeholders})
          AND a.assigned_staff_id IS NOT NULL
          AND COALESCE(a.stage, '') NOT IN ('churned', 'cancelled', 'lost', 'closed')
          AND COALESCE(a.stage_status, '') NOT IN ('lost', 'cancelled', 'released', 'deleted')
          AND pr.stage <> 'cancelled'
          AND COALESCE(pr.stage_status, '') <> 'deleted'
          AND COALESCE(pr.restricted, FALSE) = FALSE
        ORDER BY a.updated_at DESC, a.id DESC
        """,
        ids,
    ).fetchall()
    for row in assignment_rows:
        data = dict(row)
        pool_id = _int(data.get("kol_pool_id"))
        owner_staff_id = _int(data.get("assigned_staff_id"))
        if not pool_id or not owner_staff_id or pool_id in occupancy:
            continue
        occupancy[pool_id] = {
            "staff_id": owner_staff_id,
            "source": "assignment",
            "claim_id": None,
            "project_id": _int(data.get("project_id")) or None,
            "stage": str(data.get("stage") or ""),
        }
    claim_rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, c.id AS claim_id, c.staff_id, c.project_id
        FROM vkpi_kol_pool p
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        WHERE p.id IN ({placeholders})
        ORDER BY c.id ASC
        """,
        ids,
    ).fetchall()
    for row in claim_rows:
        data = dict(row)
        pool_id = _int(data.get("kol_pool_id"))
        owner_staff_id = _int(data.get("staff_id"))
        if not pool_id or not owner_staff_id:
            continue
        occupancy[pool_id] = {
            "staff_id": owner_staff_id,
            "source": "claim",
            "claim_id": _int(data.get("claim_id")) or None,
            "project_id": _int(data.get("project_id")) or None,
            "stage": "",
        }
    return occupancy
