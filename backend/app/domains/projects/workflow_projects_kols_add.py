"""Behavior-preserving helpers for attaching creator-pool rows to a project.

The public workflow module owns dependency composition.  This module only
coordinates the supplied project-domain callbacks and the caller's database
transaction; it deliberately has no imports from KOL or recommendation
domains.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.shared.project_creator_lifecycle_ports import RecommendationFeedbackSink


@dataclass(frozen=True)
class AddProjectKolsRuntime:
    """Project-owned dependencies kept injectable at the public module seam."""

    ensure_schema: Callable[[], None]
    assert_project_access: Callable[..., Any]
    get_conn: Callable[[], Any]
    require_project: Callable[[Any, int], dict[str, Any]]
    to_int: Callable[[Any], int]
    to_json: Callable[[Any], str]
    staff_id: Callable[[dict[str, Any] | None], int]
    utcnow: Callable[[], Any]
    can_view_all: Callable[[dict[str, Any] | None], bool]
    locked_occupancy: Callable[[Any, list[int] | set[int]], dict[int, dict[str, Any]]]
    staff_names: Callable[[Any, list[Any]], dict[int, str]]
    record_agent_signals: Callable[[list[int], int, dict[str, Any] | None], None]
    record_feedback: Callable[..., None]
    log_audit: Callable[..., Any]
    logger: Any


@dataclass(frozen=True)
class AssignmentWriteResult:
    inserted_pool_ids: list[int]
    skipped_existing: int

    @property
    def inserted(self) -> int:
        return len(self.inserted_pool_ids)


def _requested_pool_ids(body: dict[str, Any], to_int: Callable[[Any], int]) -> list[int]:
    values = (
        body.get("kol_pool_ids")
        or body.get("kolPoolIds")
        or body.get("kol_ids")
        or body.get("kolIds")
        or []
    )
    if not isinstance(values, list):
        raise ValueError("kol_pool_ids must be a list")

    pool_ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        pool_id = to_int(value)
        if pool_id and pool_id not in seen:
            seen.add(pool_id)
            pool_ids.append(pool_id)
    if not pool_ids:
        raise ValueError("kol_pool_ids required")
    return pool_ids


def _existing_pool_ids(conn: Any, pool_ids: list[int]) -> set[int]:
    placeholders = ",".join("?" for _ in pool_ids)
    rows = conn.execute(
        f"SELECT id FROM vkpi_kol_pool WHERE id IN ({placeholders})",
        pool_ids,
    ).fetchall()
    return {int(row["id"]) for row in rows}


def _claim_owner_by_pool(
    conn: Any,
    existing_pool_ids: set[int],
    to_int: Callable[[Any], int],
) -> dict[int, int]:
    if not existing_pool_ids:
        return {}
    ordered_ids = sorted(existing_pool_ids)
    placeholders = ",".join("?" for _ in ordered_ids)
    rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, c.staff_id AS staff_id
        FROM vkpi_kol_pool p
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        WHERE p.id IN ({placeholders})
        ORDER BY c.id ASC
        """,
        ordered_ids,
    ).fetchall()
    owners: dict[int, int] = {}
    for row in rows:
        data = dict(row)
        pool_id = to_int(data.get("kol_pool_id"))
        owner_id = to_int(data.get("staff_id"))
        if pool_id and owner_id and pool_id not in owners:
            owners[pool_id] = owner_id
    return owners


def _blocked_occupancy(
    occupancy: dict[int, dict[str, Any]],
    actor_staff_id: int,
    to_int: Callable[[Any], int],
) -> dict[int, dict[str, Any]]:
    return {
        pool_id: item
        for pool_id, item in occupancy.items()
        if to_int(item.get("staff_id"))
        and to_int(item.get("staff_id")) != actor_staff_id
    }


def _forced_conflict_rows(
    blocked: dict[int, dict[str, Any]],
    owner_names: dict[int, str],
    to_int: Callable[[Any], int],
) -> list[dict[str, Any]]:
    return [
        {
            "kol_pool_id": pool_id,
            "occupied_by_staff_id": to_int(item.get("staff_id")),
            "occupied_by_name": owner_names.get(to_int(item.get("staff_id")), ""),
            "claim_source": str(item.get("source") or ""),
            "claim_id": item.get("claim_id"),
            "occupied_project_id": item.get("project_id"),
        }
        for pool_id, item in sorted(blocked.items())
    ]


def _raise_blocked_error(
    conn: Any,
    blocked: dict[int, dict[str, Any]],
    owner_names: dict[int, str],
    to_int: Callable[[Any], int],
) -> None:
    blocked_ids = sorted(blocked)
    placeholders = ",".join("?" for _ in blocked_ids)
    rows = conn.execute(
        f"SELECT id, COALESCE(display_name, handle, '') AS label FROM vkpi_kol_pool WHERE id IN ({placeholders})",
        blocked_ids,
    ).fetchall()
    labels = {
        to_int(dict(row).get("id")): str(dict(row).get("label") or "").strip()
        for row in rows
    }
    parts: list[str] = []
    for pool_id, item in sorted(blocked.items()):
        kol_label = labels.get(pool_id) or f"KOL #{pool_id}"
        owner_id = to_int(item.get("staff_id"))
        owner_label = owner_names.get(owner_id) or f"员工#{owner_id}"
        parts.append(f"「{kol_label}」已被 {owner_label} 跟进")
    raise ValueError(
        "以下 KOL 已被他人认领/跟进,未写入:"
        + ";".join(parts)
        + "。如确需加入,请管理员携 force=true 重试。"
    )


def _resolve_claim_conflicts(
    conn: Any,
    existing_pool_ids: set[int],
    actor_staff_id: int,
    body: dict[str, Any],
    staff: dict[str, Any] | None,
    runtime: AddProjectKolsRuntime,
) -> list[dict[str, Any]]:
    occupancy = runtime.locked_occupancy(conn, existing_pool_ids)
    blocked = _blocked_occupancy(occupancy, actor_staff_id, runtime.to_int)
    if not blocked:
        return []
    owner_names = runtime.staff_names(
        conn,
        [item["staff_id"] for item in blocked.values()],
    )
    if bool(body.get("force")) and runtime.can_view_all(staff):
        return _forced_conflict_rows(blocked, owner_names, runtime.to_int)
    _raise_blocked_error(conn, blocked, owner_names, runtime.to_int)


def _insert_assignment(
    conn: Any,
    *,
    project_id: int,
    kol_pool_id: int,
    assigned_staff_id: int,
    actor_staff_id: int,
    now: Any,
    to_json: Callable[[Any], str],
) -> bool:
    row = conn.execute(
        """
        INSERT INTO vkpi_project_kol_assignments (
            project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
            source, source_ref, metadata_json, created_at, updated_at
        ) VALUES (?, ?, 'discovered', 'active', ?, 'manual', ?, ?, ?, ?)
        ON CONFLICT (project_id, kol_pool_id) DO NOTHING
        RETURNING id
        """,
        (
            project_id,
            kol_pool_id,
            assigned_staff_id or None,
            f"ui:add_kol:{project_id}",
            to_json(
                {
                    "source": "project_detail_add_kol",
                    "actor_staff_id": actor_staff_id,
                }
            ),
            now,
            now,
        ),
    ).fetchone()
    return bool(row)


def _record_touch_best_effort(
    conn: Any,
    *,
    project_id: int,
    kol_pool_id: int,
    assigned_staff_id: int,
    actor_staff_id: int,
    now: Any,
    logger: Any,
) -> None:
    conn.execute("SAVEPOINT vkpi_project_touch")
    try:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool_touches
                (kol_pool_id, staff_id, channel, project_id, note, touched_at, created_at)
            VALUES (?, ?, 'project_assignment', ?, ?, ?, ?)
            ON CONFLICT (kol_pool_id, channel, project_id) DO UPDATE SET
                staff_id=excluded.staff_id,
                touched_at=excluded.touched_at
            """,
            (
                kol_pool_id,
                assigned_staff_id or actor_staff_id or None,
                project_id,
                "added to project",
                now,
                now,
            ),
        )
        conn.execute("RELEASE SAVEPOINT vkpi_project_touch")
    except Exception:
        try:
            conn.execute("ROLLBACK TO SAVEPOINT vkpi_project_touch")
            conn.execute("RELEASE SAVEPOINT vkpi_project_touch")
        except Exception:
            logger.exception("kol_pool touch savepoint recovery failed")
            raise
        logger.warning(
            "kol_pool touch log skipped for pool_id=%s project_id=%s",
            kol_pool_id,
            project_id,
            exc_info=True,
        )


def _write_assignments(
    conn: Any,
    *,
    project_id: int,
    pool_ids: list[int],
    existing_pool_ids: set[int],
    claim_owners: dict[int, int],
    fallback_staff_id: int,
    actor_staff_id: int,
    now: Any,
    runtime: AddProjectKolsRuntime,
) -> AssignmentWriteResult:
    inserted_pool_ids: list[int] = []
    skipped_existing = 0
    for pool_id in pool_ids:
        if pool_id not in existing_pool_ids:
            continue
        assigned_staff_id = claim_owners.get(pool_id) or fallback_staff_id
        inserted = _insert_assignment(
            conn,
            project_id=project_id,
            kol_pool_id=pool_id,
            assigned_staff_id=assigned_staff_id,
            actor_staff_id=actor_staff_id,
            now=now,
            to_json=runtime.to_json,
        )
        if not inserted:
            skipped_existing += 1
            continue
        inserted_pool_ids.append(pool_id)
        _record_touch_best_effort(
            conn,
            project_id=project_id,
            kol_pool_id=pool_id,
            assigned_staff_id=assigned_staff_id,
            actor_staff_id=actor_staff_id,
            now=now,
            logger=runtime.logger,
        )
    return AssignmentWriteResult(inserted_pool_ids, skipped_existing)


def _favorite_attached_pool_ids(
    conn: Any,
    attached_pool_ids: list[int],
    actor_staff_id: int,
) -> None:
    if not actor_staff_id or not attached_pool_ids:
        return
    for pool_id in attached_pool_ids:
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id, note)
            VALUES (?, ?, ?)
            ON CONFLICT (kol_pool_id, staff_id) DO NOTHING
            """,
            (pool_id, actor_staff_id, "项目添加自动收藏"),
        )


def _record_feedback_effects(
    feedback_sink: RecommendationFeedbackSink | None,
    *,
    project_id: int,
    inserted_pool_ids: list[int],
    attached_pool_ids: list[int],
    actor_staff_id: int,
    staff: dict[str, Any] | None,
    runtime: AddProjectKolsRuntime,
) -> None:
    for pool_id in inserted_pool_ids:
        runtime.record_feedback(
            feedback_sink,
            pool_id,
            "touch",
            staff=staff,
            payload={"project_id": project_id, "channel": "project_assignment"},
            source="project_assignment",
        )
    if not actor_staff_id:
        return
    for pool_id in attached_pool_ids:
        runtime.record_feedback(
            feedback_sink,
            pool_id,
            "favorite",
            staff=staff,
            payload={"project_id": project_id},
            source="project_auto_favorite",
        )


def _log_add_audit(
    *,
    project_id: int,
    pool_ids: list[int],
    existing_pool_ids: set[int],
    result: AssignmentWriteResult,
    fallback_staff_id: int,
    claim_owners: dict[int, int],
    missing_pool_ids: list[int],
    forced_conflicts: list[dict[str, Any]],
    staff: dict[str, Any] | None,
    log_audit: Callable[..., Any],
) -> None:
    if not result.inserted:
        return
    forced_detail = (
        f" (admin force, {len(forced_conflicts)} 个越过他人占用)"
        if forced_conflicts
        else ""
    )
    log_audit(
        staff=staff,
        action_type="project_add_kols",
        project_id=project_id,
        detail=f"added {result.inserted} KOL assignments" + forced_detail,
        metadata={
            "kol_pool_ids": [pool_id for pool_id in pool_ids if pool_id in existing_pool_ids],
            "assigned_staff_id_fallback": fallback_staff_id or None,
            "claim_owner_by_pool": {str(key): value for key, value in claim_owners.items()},
            "missing_kol_pool_ids": missing_pool_ids,
            "skipped_existing": result.skipped_existing,
            "force": bool(forced_conflicts),
            "forced_claim_conflicts": forced_conflicts,
        },
    )


def execute_add_project_kols(
    project_id: int,
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    feedback_sink: RecommendationFeedbackSink | None,
    runtime: AddProjectKolsRuntime,
) -> dict[str, Any]:
    """Attach pool rows while retaining the legacy transaction boundaries."""
    runtime.ensure_schema()
    runtime.assert_project_access(project_id, staff, write=True)
    conn = runtime.get_conn()
    normalized_project_id = int(project_id)
    runtime.require_project(conn, normalized_project_id)

    pool_ids = _requested_pool_ids(body, runtime.to_int)
    existing_pool_ids = _existing_pool_ids(conn, pool_ids)
    missing_pool_ids = [pool_id for pool_id in pool_ids if pool_id not in existing_pool_ids]
    actor_staff_id = runtime.staff_id(staff)
    requested_staff_id = runtime.to_int(
        body.get("assigned_staff_id") or body.get("assignedStaffId")
    )
    fallback_staff_id = (
        requested_staff_id
        if runtime.can_view_all(staff) and requested_staff_id
        else actor_staff_id
    )
    claim_owners = _claim_owner_by_pool(conn, existing_pool_ids, runtime.to_int)
    forced_conflicts = _resolve_claim_conflicts(
        conn,
        existing_pool_ids,
        actor_staff_id,
        body,
        staff,
        runtime,
    )

    now = runtime.utcnow()
    write_result = _write_assignments(
        conn,
        project_id=normalized_project_id,
        pool_ids=pool_ids,
        existing_pool_ids=existing_pool_ids,
        claim_owners=claim_owners,
        fallback_staff_id=fallback_staff_id,
        actor_staff_id=actor_staff_id,
        now=now,
        runtime=runtime,
    )
    if write_result.inserted:
        conn.execute(
            "UPDATE vkpi_projects SET updated_at=?, last_activity_at=? WHERE id=?",
            (now, now, normalized_project_id),
        )
    attached_pool_ids = [pool_id for pool_id in pool_ids if pool_id in existing_pool_ids]
    _favorite_attached_pool_ids(conn, attached_pool_ids, actor_staff_id)
    conn.commit()

    runtime.record_agent_signals(
        write_result.inserted_pool_ids,
        normalized_project_id,
        staff,
    )
    _record_feedback_effects(
        feedback_sink,
        project_id=normalized_project_id,
        inserted_pool_ids=write_result.inserted_pool_ids,
        attached_pool_ids=attached_pool_ids,
        actor_staff_id=actor_staff_id,
        staff=staff,
        runtime=runtime,
    )
    _log_add_audit(
        project_id=normalized_project_id,
        pool_ids=pool_ids,
        existing_pool_ids=existing_pool_ids,
        result=write_result,
        fallback_staff_id=fallback_staff_id,
        claim_owners=claim_owners,
        missing_pool_ids=missing_pool_ids,
        forced_conflicts=forced_conflicts,
        staff=staff,
        log_audit=runtime.log_audit,
    )
    return {
        "project_id": normalized_project_id,
        "requested": len(pool_ids),
        "inserted": write_result.inserted,
        "skipped_existing": write_result.skipped_existing,
        "missing_kol_pool_ids": missing_pool_ids,
        "forced_claim_conflicts": forced_conflicts,
    }


__all__ = ["AddProjectKolsRuntime", "execute_add_project_kols"]
