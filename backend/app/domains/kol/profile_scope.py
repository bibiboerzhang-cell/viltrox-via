"""KOL profile scope query helpers."""
from __future__ import annotations

from typing import Any

from app.domains.kol.payload_utils import _int
from app.services.vkpi import scope


def project_staff_filter(staff: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if scope.can_view_all(staff):
        return "", []
    actor = scope.actor_staff_id(staff)
    return " AND (p.assigned_staff_id=? OR p.created_by_staff_id=?)", [actor, actor]


def project_scope_clause(*, is_manager: bool, project_ids: list[int], column: str) -> tuple[str, list[Any]]:
    if is_manager:
        return "", []
    if not project_ids:
        return " AND 1=0", []
    placeholders = ",".join("?" for _ in project_ids)
    return f" AND {column} IN ({placeholders})", list(project_ids)


def link_scope_clause(*, is_manager: bool, actor: int, project_ids: list[int]) -> tuple[str, list[Any]]:
    if is_manager:
        return "", []
    link_scope_sql = " AND (l.staff_id=? OR l.created_by_staff_id=?"
    link_scope_params: list[Any] = [actor, actor]
    if project_ids:
        placeholders = ",".join("?" for _ in project_ids)
        link_scope_sql += f" OR l.project_id IN ({placeholders})"
        link_scope_params.extend(project_ids)
    link_scope_sql += ")"
    return link_scope_sql, link_scope_params


def audit_where_parts(
    *,
    kol_id: int,
    project_ids: list[int],
    link_ids: list[int],
    sales: list[dict[str, Any]],
    costs: list[dict[str, Any]],
) -> tuple[str, list[Any]]:
    audit_clauses = ["(target_type='kol' AND target_id=?)"]
    audit_params: list[Any] = [str(kol_id)]
    if project_ids:
        placeholders = ",".join("?" for _ in project_ids)
        audit_clauses.append(f"(target_type='project' AND target_id IN ({placeholders}))")
        audit_params.extend(str(item) for item in project_ids)
    if link_ids:
        placeholders = ",".join("?" for _ in link_ids)
        audit_clauses.append(f"(target_type='link' AND target_id IN ({placeholders}))")
        audit_params.extend(str(item) for item in link_ids)
    sales_ids = [_int(item.get("id")) for item in sales if _int(item.get("id"))]
    if sales_ids:
        placeholders = ",".join("?" for _ in sales_ids)
        audit_clauses.append(f"(target_type='attribution' AND target_id IN ({placeholders}))")
        audit_params.extend(str(item) for item in sales_ids)
    cost_ids = [_int(item.get("id")) for item in costs if _int(item.get("id"))]
    if cost_ids:
        placeholders = ",".join("?" for _ in cost_ids)
        audit_clauses.append(f"(target_type='cost' AND target_id IN ({placeholders}))")
        audit_params.extend(str(item) for item in cost_ids)
    return " OR ".join(audit_clauses), audit_params
