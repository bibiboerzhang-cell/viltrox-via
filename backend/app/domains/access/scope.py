"""V-KPI data-scope enforcement.

Frontend staff_id/view_as_staff_id is only a hint. Backend read/write paths
must reduce it to the actor's allowed scope before hitting business tables.
"""
from __future__ import annotations

from typing import Any

from app.core.permissions import normalize_permissions
from app.db.connection import get_conn
from app.domains.platform import tenancy


MANAGER_ROLES = {"admin", "owner", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}
FINANCE_ROLES = {"finance", "accounting"}


class ScopeDenied(PermissionError):
    """Raised when a staff actor attempts to view or mutate out-of-scope data."""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def actor_staff_id(staff: dict[str, Any] | None) -> int:
    if not staff:
        return 0
    return _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id"))


def role_key(staff: dict[str, Any] | None) -> str:
    return str((staff or {}).get("role") or "").strip().lower()


def is_owner(staff: dict[str, Any] | None) -> bool:
    return bool(staff) and _int((staff or {}).get("is_owner")) == 1


def can_view_all(staff: dict[str, Any] | None, *, domain: str = "general") -> bool:
    role = role_key(staff)
    if is_owner(staff):
        return True
    if role == "admin":
        permissions = normalize_permissions(
            (staff or {}).get("permissions_json") or (staff or {}).get("permissions"),
            role,
            owner=False,
        )
        return str(permissions.get("vkpi") or "none").lower() == "admin"
    if role in MANAGER_ROLES:
        return True
    if domain in {"cost", "finance", "export"} and role in FINANCE_ROLES:
        return True
    return False


def scope_context(
    staff: dict[str, Any] | None,
    requested_staff_id: int | None = None,
    *,
    domain: str = "general",
) -> dict[str, Any]:
    """Return the actual backend scope used for a list/read request.

    The frontend may request another staff_id, but non-manager actors are
    always reduced to their own staff id. Returning this context makes that
    reduction visible to the UI and prevents "view all" assumptions from
    leaking into new pages.
    """
    actor = actor_staff_id(staff)
    requested = _int(requested_staff_id)
    can_all = can_view_all(staff, domain=domain)
    effective = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not staff:
        mode = "anonymous"
    elif can_all and requested:
        mode = "requested_staff"
    elif can_all:
        mode = "all"
    else:
        mode = "own"
    return {
        "actor_staff_id": actor or None,
        "requested_staff_id": requested or None,
        "effective_staff_id": effective or None,
        "can_view_all": bool(can_all),
        "scope_mode": mode,
        "role": role_key(staff),
        "is_owner": is_owner(staff),
        "domain": domain,
    }


def effective_staff_id(staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, domain: str = "general") -> int | None:
    requested = _int(requested_staff_id)
    if can_view_all(staff, domain=domain):
        return requested or None
    actor = actor_staff_id(staff)
    return actor or None


def staff_filter(column_sql: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, domain: str = "general") -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not scoped_staff_id:
        return "", []
    return f"{column_sql} = ?", [scoped_staff_id]


def project_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> tuple[str, list[Any]]:
    """PV-4 裁决(2026-06-14,用户决策):员工只看自己负责/创建的项目;管理层(can_view_all)全可见。

    推翻 PV-3(员工看全部非受限)——用户明确要"员工只看自己做的项目,管理员看全部"。
    非 admin:(assigned_staff_id=自己 OR created_by_staff_id=自己) AND 非 restricted。
    归属键由管理层在设置页分配/创建项目时落;未分配的项目对该员工不可见(诚实——尚未派活)。
    admin 全可见(含 restricted);requested_staff_id 仅在 admin 下作显式按人筛选(查询语义)。

    项目共享接线(2026-06-14,ADDITIVE):在 own-only 基础上「加宽」——非 admin 员工
    还能看到「被显式共享给自己」(vkpi_project_members.staff_id=自己)的项目。这只是
    OR 进一条新分支,绝不去掉 own-only 既有限制,也绝不扩到未共享的项目。共享放行同样
    gate 在「非 restricted」上(与 own 子句对齐):restricted 项目仍只认 assigned/creator/
    can_view_all,共享 viewer/editor 看不到 restricted 项目(先遮后开铁则不破)。
    无登录人/admin 两条分支原样保留。子查询 staff_id 用参数,alias 是常量,注入安全。

    公司公共项目接线(2026-06-14,ADDITIVE,迁移 133 is_public):再 OR 进
    「COALESCE(is_public,FALSE)=TRUE」——public 项目对所有成员可见(无需逐个共享/派活)。
    同样 gate 在「非 restricted」上:restricted 项目永不因 is_public 而可见(先遮后开)。
    is_public 不引入新参数(纯列比较),actor 三参数不变。只「加宽」读;写权仍由
    assert_project_access 严格 gate,public 不放开写。
    """
    prefix = f"{alias}." if alias else ""
    if can_view_all(staff):
        if requested_staff_id:
            return f"({prefix}assigned_staff_id = ? OR {prefix}created_by_staff_id = ?)", [int(requested_staff_id), int(requested_staff_id)]
        return "", []
    actor = actor_staff_id(staff)
    if not actor:
        # 取不到登录人 → 保守只给非受限(不暴露 restricted),避免空登录态异常
        return f"(COALESCE({prefix}restricted, FALSE) = FALSE)", []
    return (
        f"(COALESCE({prefix}restricted, FALSE) = FALSE AND "
        f"({prefix}assigned_staff_id = ? OR {prefix}created_by_staff_id = ? OR "
        f"{prefix}id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id = ?) OR "
        f"COALESCE({prefix}is_public, FALSE) = TRUE))",
        [int(actor), int(actor), int(actor)],
    )


def link_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id)
    if not scoped_staff_id:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"({prefix}staff_id = ? OR {prefix}created_by_staff_id = ?)", [scoped_staff_id, scoped_staff_id]


def row_staff_filter(alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None, *, column: str = "staff_id", domain: str = "general") -> tuple[str, list[Any]]:
    scoped_staff_id = effective_staff_id(staff, requested_staff_id, domain=domain)
    if not scoped_staff_id:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"{prefix}{column} = ?", [scoped_staff_id]


def assert_project_access(project_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if can_view_all(staff):
        return
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("project scope denied")
    row = get_conn().execute(
        """
        SELECT assigned_staff_id, created_by_staff_id, COALESCE(restricted, FALSE) AS restricted,
               COALESCE(is_public, FALSE) AS is_public
        FROM vkpi_projects
        WHERE id=?
        """,
        (int(project_id),),
    ).fetchone()
    if not row:
        return
    item = dict(row)
    if actor in {_int(item.get("assigned_staff_id")), _int(item.get("created_by_staff_id"))}:
        # own(assigned/creator)= 完全权限(读写),原样保留,不收紧。
        return
    # 项目共享接线(2026-06-14,ADDITIVE):actor 若是该项目的显式共享成员,按 role 区分:
    #   viewer → 只读(write=False 放行,write=True 拒);editor → 读写均放行。
    # 这是新增放行路径,不去掉下面 own/admin/非 restricted 的既有任一放行。
    member = get_conn().execute(
        "SELECT role FROM vkpi_project_members WHERE project_id=? AND staff_id=?",
        (int(project_id), actor),
    ).fetchone()
    if member is not None:
        role = str(dict(member).get("role") or "viewer").strip().lower()
        if not write:
            return
        if role == "editor":
            return
        raise ScopeDenied("project scope denied")
    # 公司公共项目接线(2026-06-14,ADDITIVE,迁移 133 is_public):
    #   public(is_public=TRUE)且非 restricted 的项目 → 任何 actor 可「读」(read 放行)。
    #   写仍严格 gate:public 不放开写,只有 own/editor-member/admin 能写(下面尾部不放行
    #   非成员的写,且这里仅在 not write 时放行)。restricted 项目永不因 public 而可见
    #   (与 own/member 子句对齐,先遮后开铁则不破)。
    if not write and bool(item.get("is_public")) and not bool(item.get("restricted")):
        return
    # T2 写侧收紧(2026-06-14,推翻 PV-3 写放行):非 restricted 项目此前「读写均放行」
    # ——任何持 vkpi:write 的员工都能改任意非受限项目,等于公共写。写只认 own/creator/
    # editor-member/admin(上方已先行放行,走到这里的写一定是无授权路径)。
    #
    # 读兜底收紧(defect②,2026-07-08,IDOR 修复):非 restricted 项目此前「读一律放行」
    # ——员工按 ID 直读别人独占项目的详情+子资源(快递单号/收款等暴露),list 侧
    # project_filter 已 own-only 遮蔽却在详情兜底放行,等于绕过。现读兜底仅对「真无归属
    # 历史行」(assigned_staff_id 与 created_by_staff_id 双 NULL)放行——这批存量占多数,
    # 只开它们的读不会让看板/详情全盘 403;已归属别人的非 restricted 项目详情读复用
    # project_filter 的 own/assigned/creator/member/public 口径(上方已逐条放行,走到这里
    # 说明 actor 都不符合)→ 拒。restricted 项目读写均只认 assigned/creator/全可见角色
    # (先遮后开铁则不破)。
    if (
        not write
        and not bool(item.get("restricted"))
        and not _int(item.get("assigned_staff_id"))
        and not _int(item.get("created_by_staff_id"))
    ):
        return
    raise ScopeDenied("project scope denied")


def assert_project_access_many(
    project_ids: list[int] | tuple[int, ...] | set[int],
    staff: dict[str, Any] | None,
    *,
    write: bool = False,
) -> None:
    """Batch-equivalent project gate for bounded multi-target read models.

    The scalar gate is intentionally kept for ordinary detail routes.  Batch
    analysis polling may cover up to 200 evidence rows, however, and calling
    the scalar gate per distinct project recreates a permission N+1 (up to two
    reads per project).  This helper mirrors the scalar decisions with one
    bounded query; missing project ids keep the scalar helper's downstream-404
    behaviour.
    """
    normalized: list[int] = []
    for raw_id in project_ids:
        project_id = _int(raw_id)
        if project_id > 0 and project_id not in normalized:
            normalized.append(project_id)
        if len(normalized) >= 200:
            break
    if not normalized or can_view_all(staff):
        return
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("project scope denied")
    placeholders = ",".join("?" for _ in normalized)
    rows = get_conn().execute(
        f"""
        SELECT p.id, p.assigned_staff_id, p.created_by_staff_id,
               COALESCE(p.restricted, FALSE) AS restricted,
               COALESCE(p.is_public, FALSE) AS is_public,
               pm.role AS member_role
        FROM vkpi_projects p
        LEFT JOIN vkpi_project_members pm
          ON pm.project_id=p.id AND pm.staff_id=?
        WHERE p.id IN ({placeholders})
        """,
        (actor, *normalized),
    ).fetchall()
    for row in rows:
        item = dict(row)
        if actor in {
            _int(item.get("assigned_staff_id")),
            _int(item.get("created_by_staff_id")),
        }:
            continue
        member_role = str(item.get("member_role") or "").strip().lower()
        if member_role:
            if not write or member_role == "editor":
                continue
            raise ScopeDenied("project scope denied")
        if not write and bool(item.get("is_public")) and not bool(item.get("restricted")):
            continue
        if (
            not write
            and not bool(item.get("restricted"))
            and not _int(item.get("assigned_staff_id"))
            and not _int(item.get("created_by_staff_id"))
        ):
            continue
        raise ScopeDenied("project scope denied")


def _team_ids_for_event(row: dict[str, Any]) -> set[int]:
    """把 vkpi_events.team_ids(jsonb 数组,psycopg 可能回字符串或 list)解析成 int 集合。"""
    raw = row.get("team_ids")
    if raw in (None, ""):
        return set()
    if isinstance(raw, (list, tuple)):
        items = raw
    else:
        try:
            import json as _json

            items = _json.loads(raw)
        except Exception:
            return set()
    out: set[int] = set()
    for v in items if isinstance(items, (list, tuple)) else []:
        iv = _int(v)
        if iv:
            out.add(iv)
    return out


def event_organization_id(
    staff: dict[str, Any] | None,
    conn: Any | None = None,
) -> int:
    """Resolve the trusted Event workspace without swallowing DB failures.

    An explicit organization attached by the authenticated staff dependency is
    authoritative.  Otherwise resolve the actor's membership using this
    operation's connection.  Legacy staff with no membership remain in org 1,
    but an unavailable or ambiguous membership lookup fails closed.
    """
    explicit = _int((staff or {}).get("organization_id"))
    if explicit > 0:
        return explicit
    actor = actor_staff_id(staff)
    if actor <= 0:
        raise ScopeDenied("event organization context unavailable")
    db = conn or get_conn()
    try:
        rows = db.execute(
            "SELECT organization_id FROM organization_members WHERE staff_id=? ORDER BY id",
            (actor,),
        ).fetchall()
    except Exception as exc:
        raise ScopeDenied("event organization context unavailable") from exc
    memberships = {_int(dict(row).get("organization_id")) for row in rows}
    memberships.discard(0)
    if len(memberships) > 1:
        raise ScopeDenied("event organization context is ambiguous")
    if memberships:
        return next(iter(memberships))
    # A missing membership is a supported legacy-org-1 shape only for a real
    # staff row.  Without this existence check an arbitrary/stale actor id
    # could be collapsed into the default workspace merely because both the
    # membership query and the staff identity were empty.
    try:
        staff_row = db.execute("SELECT 1 FROM staff WHERE id=?", (actor,)).fetchone()
    except Exception as exc:
        raise ScopeDenied("event organization context unavailable") from exc
    if staff_row is None:
        raise ScopeDenied("event organization context unavailable")
    return int(tenancy.default_organization_id())


def assert_legacy_default_organization(
    staff: dict[str, Any] | None,
    conn: Any | None = None,
    *,
    feature: str = "legacy resource",
) -> int:
    """Fail closed when an unscoped legacy table is used outside org 1.

    Reports and exports predate ``organization_id``.  Until an additive
    migration and backfill exist, they may safely serve only the default
    workspace.  Membership lookup errors and ambiguous memberships are already
    denied by :func:`event_organization_id`; this wrapper adds the capability
    gate without pretending that staff ownership is tenant isolation.
    """
    organization_id = event_organization_id(staff, conn)
    if organization_id != int(tenancy.default_organization_id()):
        raise ScopeDenied(f"{feature} organization scope unavailable")
    return organization_id


def event_has_organization_scope(conn: Any | None = None) -> bool:
    """Return whether migration 244's ``vkpi_events.organization_id`` exists.

    ``PRAGMA table_info`` is intentionally used through the compatibility DB
    adapter: SQLite handles it natively and Postgres translates it to a
    read-only ``information_schema`` query.  Detection errors fail closed for
    every workspace; ``False`` exclusively means a successful pre-244 probe.
    """
    db = conn or get_conn()
    try:
        rows = db.execute("PRAGMA table_info(vkpi_events)").fetchall()
    except Exception as exc:
        raise ScopeDenied("event organization schema unavailable") from exc
    for row in rows:
        try:
            name = dict(row).get("name")
        except Exception:
            try:
                name = row[1]
            except Exception:
                name = None
        if str(name or "").strip().lower() == "organization_id":
            return True
    return False


def event_organization_context(
    staff: dict[str, Any] | None,
    conn: Any | None = None,
) -> tuple[int, bool]:
    """Return ``(organization_id, schema_is_scoped)`` for Event operations.

    Before migration 244 only the default workspace may use the legacy table.
    A non-default actor never collapses into org 1 merely because deployment is
    between code and schema rollout; callers must deny or return an empty set.
    """
    organization_id = event_organization_id(staff, conn)
    scoped = event_has_organization_scope(conn)
    if not scoped and organization_id != int(tenancy.default_organization_id()):
        raise ScopeDenied("event organization scope unavailable")
    return organization_id, scoped


def staff_belongs_to_event_organization(conn: Any, staff_id: Any, organization_id: int) -> bool:
    """Validate a team/share target without breaking legacy org-1 staff rows.

    Staff with an explicit organization membership must match.  Historical
    staff without any membership remain valid only in the default workspace;
    a non-default workspace always requires an explicit membership.
    """
    target = _int(staff_id)
    if target <= 0:
        return False
    try:
        rows = conn.execute(
            "SELECT organization_id FROM organization_members WHERE staff_id=? ORDER BY id",
            (target,),
        ).fetchall()
    except Exception:
        return False
    memberships = {_int(dict(row).get("organization_id")) for row in rows}
    memberships.discard(0)
    if memberships:
        return int(organization_id) in memberships
    # Preserve the legacy default-workspace fallback only for an existing
    # staff identity.  This also turns a nonexistent share/team target into a
    # clean scope rejection instead of a later FK/500 failure.
    try:
        staff_row = conn.execute("SELECT 1 FROM staff WHERE id=?", (target,)).fetchone()
    except Exception:
        return False
    return (
        staff_row is not None
        and int(organization_id) == int(tenancy.default_organization_id())
    )


def assert_event_access(event_id: str, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    """活动写权限收口(2026-06-14,镜像 assert_project_access)。

    放行裁决(越上越宽):
    - can_view_all(admin/owner/manager)→ 当前 organization 内完全读写;不得跨 workspace。
    - owner_id == actor → 完全读写(活动所有者)。
    - team_ids 含 actor → editor 级:读写均放行。裁决(可追认):Events 是显式「多人协作」
      模块(团队成员本就在跑这个活动),故团队成员=编辑者,可写;与项目侧 assigned/creator
      全权对齐。若未来要更严,可把团队降为只读 + 仅 owner/editor-member 可写。
    - vkpi_event_members 共享成员 → 按 role:viewer 只读(write=True 拒);editor 读写。
    - 其余(含「有 vkpi:write 但与本活动无关」的员工)→ 拒。这正是收紧点:不再是任何
      vkpi:write 都能改任意活动,只有 owner/team/editor-member/admin 能写。
    - 活动行不存在 → 放行(交由下游 CRUD 自然空操作,避免对 404 泄露存在性;镜像项目侧)。

    红线:只读 vkpi_events / vkpi_event_members;绝不碰 viltrox_fit_score / rule_v0。
    """
    conn = get_conn()
    organization_id, organization_scoped = event_organization_context(staff, conn)
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("event scope denied")
    if organization_scoped:
        row = conn.execute(
            "SELECT owner_id, team_ids, COALESCE(is_public, FALSE) AS is_public "
            "FROM vkpi_events WHERE id=? AND organization_id=?",
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT owner_id, team_ids, COALESCE(is_public, FALSE) AS is_public FROM vkpi_events WHERE id=?",
            (str(event_id),),
        ).fetchone()
    if not row:
        # With migration 244 present, an unqualified id may belong to another
        # workspace.  Treat both cross-workspace and absent rows as denied so a
        # manager/public shortcut can never turn that ambiguity into access.
        if organization_scoped:
            raise ScopeDenied("event scope denied")
        return
    item = dict(row)
    # Global roles are global only inside the actor's current organization.
    # The organization-qualified lookup above deliberately happens before this
    # shortcut so manager/admin cannot read or mutate another workspace by id.
    if can_view_all(staff):
        return
    if actor == _int(item.get("owner_id")):
        # owner = 完全读写,原样放行。
        return
    if actor in _team_ids_for_event(item):
        # team 成员 = editor:读写均放行(显式协作模块)。
        return
    # 活动共享接线(2026-06-14,ADDITIVE):actor 若是显式共享成员,按 role 区分:
    #   viewer → 只读(write=False 放行,write=True 拒);editor → 读写均放行。
    member = conn.execute(
        "SELECT role FROM vkpi_event_members WHERE event_id=? AND staff_id=?",
        (str(event_id), actor),
    ).fetchone()
    if member is not None:
        role = str(dict(member).get("role") or "viewer").strip().lower()
        if not write:
            return
        if role == "editor":
            return
        raise ScopeDenied("event scope denied")
    # 公司公共活动接线(2026-06-14,ADDITIVE,迁移 133 is_public):
    #   public(is_public=TRUE)的活动 → 任何 actor 可「读」(read 放行)。vkpi_events 无
    #   restricted 列,故 public-read 仅 gate 在 is_public 上。写仍严格 gate:public 不放开写,
    #   只有 owner/team/editor-member/admin 能写(仅在 not write 时放行)。
    if not write and bool(item.get("is_public")):
        return
    raise ScopeDenied("event scope denied")


def is_project_member(project_id: int, staff: dict[str, Any] | None) -> bool:
    """True 当 actor 是该项目的 assigned 或 creator(批D 收款遮蔽豁免判定用)。"""
    actor = actor_staff_id(staff)
    if not actor:
        return False
    row = get_conn().execute(
        "SELECT assigned_staff_id, created_by_staff_id FROM vkpi_projects WHERE id=?",
        (int(project_id),),
    ).fetchone()
    if not row:
        return False
    item = dict(row)
    return actor in {_int(item.get("assigned_staff_id")), _int(item.get("created_by_staff_id"))}


_ANALYSIS_TARGET_PROJECT_TABLES = {
    "contract": "vkpi_project_contracts",
    "video": "vkpi_kol_video_evidence",
}


def resolve_analysis_target_project(target_type: str, target_id: str | int) -> int | None:
    """批D 权限收口(2026-06-12):把分析缓存读目标映射回所属项目。

    project → 自身;contract/video → 反查行上的 project_id;kol_pool 等无项目
    维度的目标、或反查不到(历史 NULL 归属)→ 返回 None,调用方维持 tab 级权限,
    不额外收紧(诚实降级,不假装有归属)。
    """
    kind = str(target_type or "").strip().lower()
    try:
        row_id = int(str(target_id).strip())
    except (TypeError, ValueError):
        return None
    if kind == "project":
        return row_id
    table = _ANALYSIS_TARGET_PROJECT_TABLES.get(kind)
    if not table:
        return None
    row = get_conn().execute(
        f"SELECT project_id FROM {table} WHERE id=?",  # noqa: S608 - table 来自白名单映射
        (row_id,),
    ).fetchone()
    if not row:
        return None
    project_id = _int(dict(row).get("project_id"))
    return project_id or None


def resolve_analysis_target_projects(
    target_type: str,
    target_ids: list[str | int] | tuple[str | int, ...],
) -> set[int]:
    """Batch variant used by bounded analysis status polling.

    It returns only concrete project owners.  Historical video/contract rows
    with NULL project ownership retain the existing tab-level fallback.
    """
    kind = str(target_type or "").strip().lower()
    row_ids: list[int] = []
    for target_id in target_ids:
        try:
            value = int(str(target_id).strip())
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in row_ids:
            row_ids.append(value)
        if len(row_ids) >= 200:
            break
    if kind == "project":
        return set(row_ids)
    table = _ANALYSIS_TARGET_PROJECT_TABLES.get(kind)
    if not table or not row_ids:
        return set()
    placeholders = ",".join("?" for _ in row_ids)
    rows = get_conn().execute(
        f"SELECT DISTINCT project_id FROM {table} "  # noqa: S608 - table 来自白名单映射
        f"WHERE id IN ({placeholders}) AND project_id IS NOT NULL",
        tuple(row_ids),
    ).fetchall()
    return {
        project_id
        for row in rows
        if (project_id := _int(dict(row).get("project_id")))
    }


def assert_link_access(link_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if can_view_all(staff):
        return
    actor = actor_staff_id(staff)
    if not actor:
        raise ScopeDenied("link scope denied")
    row = get_conn().execute(
        """
        SELECT l.staff_id, l.created_by_staff_id, p.assigned_staff_id, p.created_by_staff_id AS project_creator_id
        FROM vkpi_links l
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        WHERE l.id=?
        """,
        (int(link_id),),
    ).fetchone()
    if not row:
        return
    item = dict(row)
    allowed = {
        _int(item.get("staff_id")),
        _int(item.get("created_by_staff_id")),
        _int(item.get("assigned_staff_id")),
        _int(item.get("project_creator_id")),
    }
    if actor in allowed:
        return
    raise ScopeDenied("link scope denied")


def assert_staff_access(target_staff_id: int | None, staff: dict[str, Any] | None, *, domain: str = "general") -> None:
    target = _int(target_staff_id)
    if not target or can_view_all(staff, domain=domain):
        return
    if target != actor_staff_id(staff):
        raise ScopeDenied("staff scope denied")


# ── W4 三层账号语义(2026-06-14,纯 ADDITIVE) ───────────────────────────
# 形式化三层:公司账号(全局)/ 成员(自己负责·被分享·被分配/public)/ 无权限。
# 红线:完全建立在既有 can_view_all / actor_staff_id / project_filter 之上,
#   不引入新角色判定、不新写 SQL、不改任何既有函数签名或行为(W2/W3 仍 import 它们)。
def account_tier(staff: dict[str, Any] | None) -> str:
    """三层账号语义的单点判定(纯字典,不碰 DB)。

    返回值:
      'company' = 公司账号视角(全局可见)= 既有 can_view_all 集合
                  (owner / vkpi=admin / manager / lead);与 dashboard 的
                  'company'/'owned_matrix' scope 对齐(dashboard/metric_maturity.py:34)。
      'member'  = 成员视角(有身份的普通成员):可见集 = 自己负责 ∪ 被显式共享
                  ∪ 被分配 ∪ public(由 project_filter 形式化实现)。
      'none'    = 无身份(未登录 / 取不到 staff_id)。

    ⚠️ 完全建立在 can_view_all 之上,不引入新角色判定逻辑——避免与既有授权双源。
    """
    if not staff:
        return "none"
    if can_view_all(staff):
        return "company"
    if actor_staff_id(staff):
        return "member"
    return "none"


def member_visible_project_ids_sql(
    alias: str, staff: dict[str, Any] | None, requested_staff_id: int | None = None
) -> tuple[str, list[Any]]:
    """「成员可见项目集」的 WHERE 片段 helper —— 单一真源,复用 project_filter。

    返回 (sql_fragment, params),可被其它查询直接拼接。片段即 project_filter 已实现的
    own ∪ shared(vkpi_project_members) ∪ assigned ∪ public(非 restricted)三/四分支,
    company 视角下返回 ("", []) 表示全可见。不复制 SQL,避免「成员可见集」多源漂移。
    """
    return project_filter(alias, staff, requested_staff_id)


def member_shared_project_ids(staff: dict[str, Any] | None) -> list[int]:
    """纯读 vkpi_project_members,返回「被显式共享给当前成员」的 project_id 列表。

    供 producer / 路由直接拿 id 列表用(若不想拼 JOIN)。只读、'?' 占位、无身份返回 []。
    红线:只 SELECT 隔离的共享成员表,绝不碰 vkpi_projects 列 / viltrox_fit_score / rule_v0。
    """
    actor = actor_staff_id(staff)
    if not actor:
        return []
    rows = get_conn().execute(
        "SELECT project_id FROM vkpi_project_members WHERE staff_id = ?",
        (int(actor),),
    ).fetchall()
    out: list[int] = []
    for r in rows:
        pid = _int(dict(r).get("project_id"))
        if pid:
            out.append(pid)
    return out


def member_shared_kol_ids(staff: dict[str, Any] | None) -> list[int]:
    """纯读 vkpi_kol_pool_members(迁移 159),返回「被显式共享给当前成员」的 kol_pool_id 列表。

    member_shared_project_ids 的 KOL 镜像:供 My KOL 读取过滤把「共享进来的池 KOL」OR 进
    本人可见集(只读可见,绝不改归属/收藏/认领)。只读、'?' 占位、无身份返回 []。
    红线:只 SELECT 隔离的共享成员表(159),绝不碰 vkpi_kol_pool 列 / viltrox_fit_score / rule_v0。
    """
    actor = actor_staff_id(staff)
    if not actor:
        return []
    rows = get_conn().execute(
        "SELECT kol_pool_id FROM vkpi_kol_pool_members WHERE staff_id = ?",
        (int(actor),),
    ).fetchall()
    out: list[int] = []
    for r in rows:
        kid = _int(dict(r).get("kol_pool_id"))
        if kid:
            out.append(kid)
    return out
