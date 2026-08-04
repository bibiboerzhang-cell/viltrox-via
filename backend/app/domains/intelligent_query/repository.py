"""Small read-only SQL helpers shared by deterministic query handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import is_postgres_runtime
from app.domains.access import scope as access_scope
from app.domains.intelligent_query.contracts import NormalizedRequest, QueryScopeDenied


_KNOWN_TABLES = frozenset(
    {
        "vkpi_kol_pool",
        "vkpi_kol_pool_favorites",
        "vkpi_kol_pool_members",
        "vkpi_project_kol_assignments",
        "vkpi_kol_claims",
        "vkpi_kol_video_evidence",
        "vkpi_kol_llm_deep_analysis_results",
        "vkpi_product_aliases",
        "vkpi_projects",
        "vkpi_project_members",
        "vkpi_comments",
        "vkpi_reply_queue",
        "vkpi_bh_reviews",
        "vkpi_sentiment_results",
    }
)


def table_columns(conn: Any, table: str) -> set[str]:
    if table not in _KNOWN_TABLES:
        raise ValueError("table is not allowlisted")
    try:
        if is_postgres_runtime():
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=?",
                (table,),
            ).fetchall()
            return {str(dict(row).get("column_name") or "") for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {
            str(dict(row).get("name") or (row[1] if len(row) > 1 else ""))
            for row in rows
        }
    except Exception:
        return set()


def table_present(conn: Any, table: str) -> bool:
    return bool(table_columns(conn, table))


def as_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def text(value: Any, limit: int = 240) -> str:
    normalized = " ".join(str(value or "").replace("\x00", " ").split())
    return normalized[:limit]


def parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.fromisoformat(raw[:10])
            except ValueError:
                return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def freshness_status(value: Any, *, now: datetime, stale_after_days: int = 7) -> str:
    observed = parse_timestamp(value)
    if observed is None:
        return "unknown"
    return "fresh" if now.astimezone(timezone.utc) - observed <= timedelta(days=stale_after_days) else "stale"


def actual_scope_context(request: NormalizedRequest, staff: dict[str, Any] | None) -> dict[str, Any]:
    is_en = request.locale == "en-US"
    if not isinstance(staff, dict):
        raise QueryScopeDenied("staff scope is unavailable" if is_en else "员工权限范围不可用")
    # Staff identity must come from the authorized staff row.  ``user_id`` is
    # a different ID space and must never be reused as staff_id in query SQL.
    try:
        actor = int(staff.get("id") or staff.get("staff_id") or 0)
    except (TypeError, ValueError):
        actor = 0
    can_all = access_scope.can_view_all(staff)
    organization_status = str(staff.get("organization_scope_status") or "").strip().lower()
    if organization_status and organization_status != "resolved":
        raise QueryScopeDenied("organization scope is unresolved" if is_en else "组织权限范围尚未解析")
    if actor <= 0 and not can_all:
        raise QueryScopeDenied("authorized staff identity is unavailable" if is_en else "已授权员工身份不可用")
    requested = request.scope.requested_staff_id
    if requested and not can_all and requested != actor:
        raise QueryScopeDenied("requested staff scope is not allowed" if is_en else "无权查询指定员工范围")
    if request.scope.mode == "own" and not actor and not requested:
        raise QueryScopeDenied("own scope requires a staff identity" if is_en else "个人范围需要有效员工身份")
    if request.scope.mode == "team":
        # There is no verified organization/team key on all four source
        # tables yet.  Never trace team while silently returning global rows.
        raise QueryScopeDenied(
            "team scope is not implemented for Ask & Find v2"
            if is_en
            else "Ask & Find v2 尚未实现可核验的团队范围"
        )
    if request.scope.mode in {"auto", "all"} and not can_all:
        # KOL Pool is a shared read asset in the existing product.  Project
        # handlers and the user's KOL count use the established staff-visible
        # set.  A handler for a genuinely shared asset (weekly market voice)
        # must explicitly replace this trace with ``shared_global``.
        effective_mode = "own"
    else:
        effective_mode = request.scope.mode
    effective_staff = requested or (actor if effective_mode == "own" else None)
    return {
        "requested_mode": request.scope.mode,
        "applied_mode": effective_mode,
        "actor_staff_id": actor or None,
        "requested_staff_id": requested,
        "effective_staff_id": effective_staff,
        "can_view_all": bool(can_all),
        "role": access_scope.role_key(staff),
    }


def pool_predicates(
    conn: Any,
    request: NormalizedRequest,
    staff: dict[str, Any] | None,
    *,
    alias: str = "p",
) -> tuple[list[str], list[Any], list[dict[str, str]]]:
    """Return allowlisted KOL predicates and parameters; never interpolates input."""
    columns = table_columns(conn, "vkpi_kol_pool")
    prefix = f"{alias}."
    clauses: list[str] = []
    params: list[Any] = []
    missing: list[dict[str, str]] = []
    if "duplicate_of_id" in columns:
        clauses.append(f"{prefix}duplicate_of_id IS NULL")
    else:
        missing.append(
            {
                "field": "duplicate_of_id",
                "reason": (
                    "KOL canonical-duplicate column is unavailable"
                    if request.locale == "en-US"
                    else "KOL 主从去重字段不可用"
                ),
                "impact": (
                    "counts may include merged historical rows"
                    if request.locale == "en-US"
                    else "数量可能包含已归并的历史从记录"
                ),
            }
        )
    platform = text(request.filters.get("platform"), 80).lower()
    country = text(request.filters.get("country"), 80).lower()
    for requested_value, column, field in (
        (platform, "platform", "filters.platform"),
        (country, "country", "filters.country"),
    ):
        if not requested_value:
            continue
        if column in columns:
            clauses.append(f"LOWER(COALESCE({prefix}{column}, '')) = ?")
            params.append(requested_value)
            continue
        # A requested filter can never be silently ignored, because doing so
        # turns a narrow question into a broad data disclosure.  Fail closed
        # while keeping the source-status contract explicit.
        clauses.append("1=0")
        missing.append(
            {
                "field": field,
                "reason": (
                    f"KOL {column} filter column is unavailable"
                    if request.locale == "en-US"
                    else f"KOL {column} 筛选字段不可用"
                ),
                "impact": (
                    "the filtered result is intentionally unavailable"
                    if request.locale == "en-US"
                    else "筛选结果按安全策略返回不可用"
                ),
            }
        )

    scope_context = actual_scope_context(request, staff)
    effective_staff = scope_context.get("effective_staff_id")
    should_scope_own = scope_context.get("applied_mode") == "own" or request.scope.requested_staff_id is not None
    if should_scope_own:
        if not effective_staff:
            raise QueryScopeDenied(
                "own KOL scope requires a staff identity"
                if request.locale == "en-US"
                else "个人 KOL 范围需要有效员工身份"
            )
        branches: list[str] = []
        if table_present(conn, "vkpi_kol_pool_favorites"):
            branches.append(
                f"{prefix}id IN (SELECT kol_pool_id FROM vkpi_kol_pool_favorites WHERE staff_id=?)"
            )
            params.append(int(effective_staff))
        if table_present(conn, "vkpi_kol_pool_members"):
            branches.append(
                f"{prefix}id IN (SELECT kol_pool_id FROM vkpi_kol_pool_members WHERE staff_id=?)"
            )
            params.append(int(effective_staff))
        # Keep Ask's "my KOL" count aligned with the existing dashboard and
        # top-search visibility contract: favorites + KOLs in projects the
        # actor owns/created/is a member of + explicit pool shares + active
        # claims.  Every staff value is still a bound parameter.
        assignment_columns = table_columns(conn, "vkpi_project_kol_assignments")
        if (
            {"project_id", "kol_pool_id"}.issubset(assignment_columns)
            and table_present(conn, "vkpi_projects")
        ):
            project_columns = table_columns(conn, "vkpi_projects")
            project_scope: list[str] = []
            project_params: list[Any] = []
            if "assigned_staff_id" in project_columns:
                project_scope.append("pr.assigned_staff_id=?")
                project_params.append(int(effective_staff))
            if "created_by_staff_id" in project_columns:
                project_scope.append("pr.created_by_staff_id=?")
                project_params.append(int(effective_staff))
            member_columns = table_columns(conn, "vkpi_project_members")
            if (
                {"project_id", "staff_id"}.issubset(member_columns)
                and "restricted" in project_columns
            ):
                project_scope.append(
                    "(COALESCE(pr.restricted, FALSE)=FALSE AND "
                    "pr.id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id=?))"
                )
                project_params.append(int(effective_staff))
            if project_scope:
                branches.append(
                    f"{prefix}id IN (SELECT a.kol_pool_id FROM vkpi_project_kol_assignments a "
                    "JOIN vkpi_projects pr ON pr.id=a.project_id WHERE "
                    + "(" + " OR ".join(project_scope) + "))"
                )
                params.extend(project_params)
        claim_columns = table_columns(conn, "vkpi_kol_claims")
        if (
            "linked_main_kol_id" in columns
            and {"kol_id", "staff_id", "status"}.issubset(claim_columns)
        ):
            branches.append(
                f"{prefix}id IN (SELECT p2.id FROM vkpi_kol_pool p2 "
                "JOIN vkpi_kol_claims c ON c.kol_id=p2.linked_main_kol_id "
                "WHERE c.staff_id=? AND c.status='active')"
            )
            params.append(int(effective_staff))
        if branches:
            clauses.append("(" + " OR ".join(branches) + ")")
        else:
            clauses.append("1=0")
            missing.append(
                {
                    "field": "my_kol_membership",
                    "reason": (
                        "favorites and sharing tables are unavailable"
                        if request.locale == "en-US"
                        else "收藏与共享关系表不可用"
                    ),
                    "impact": (
                        "own-scope KOL result is intentionally empty"
                        if request.locale == "en-US"
                        else "个人范围 KOL 结果按安全策略返回空"
                    ),
                }
            )
    return clauses, params, missing


def where_sql(clauses: list[str]) -> str:
    return " WHERE " + " AND ".join(clauses) if clauses else ""
