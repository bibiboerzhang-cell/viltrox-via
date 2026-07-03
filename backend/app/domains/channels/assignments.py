"""C7 矩阵二期 A3:官方账号分配管理(哪个成员负责哪个官号)。

数据落 vkpi_channel_assignments(迁移 209,additive 可回滚):
  channel_id → vkpi_employee_channels.id;staff_id → staff.id;
  (channel_id, role) 唯一,role 默认 owner=主负责人,预留 backup。
读:全量分配 + 成员下拉选项(owner/管理层可写,成员只读展示;can_manage 由路由层判定传入)。
写:管理层 set/clear(staff_id 传 0/None=清除),幂等先删后插;vkpi_channel_audit 尽力留痕。
红线:绝不写 viltrox_fit_score、不碰 rule_v0 评分、不动 KOL 归属判定;全程 ? 占位零字面拼值。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.channels.common import _int, _is_official_channel_row, _text, _utcnow
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)

DEFAULT_ROLE = "owner"
ALLOWED_ROLES = {"owner", "backup"}
_SCHEMA_READY = False


def _ensure_assignments_schema() -> None:
    """建表兜底:PG 正路走迁移 209;老进程未跑迁移或本地 sqlite 时按同构 DDL 补建(幂等)。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn = get_conn()
    try:
        if is_postgres_runtime():
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vkpi_channel_assignments (
                    id BIGSERIAL PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    assigned_at TEXT,
                    assigned_by_staff_id INTEGER,
                    UNIQUE (channel_id, role)
                )
                """
            )
        else:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vkpi_channel_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    assigned_at TEXT,
                    assigned_by_staff_id INTEGER,
                    UNIQUE (channel_id, role)
                );
                """
            )
        conn.commit()
        _SCHEMA_READY = True
    except Exception as exc:  # noqa: BLE001 — 并发建表等环境差异不拦路;缺表由读写路径诚实降级
        logger.warning("ensure vkpi_channel_assignments schema failed: %s", exc)


def _is_active_flag(value: Any) -> bool:
    """staff.active 兼容读:BOOLEAN 读回可能是 int 1/0 或字符串(compat 陷阱),统一容错判真。"""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return True
    return str(value).strip().lower() not in {"0", "false", "f", "no"}


def _staff_options() -> list[dict[str, Any]]:
    """分配下拉的成员选项(仅 active),名字沿用 users 展示口径。"""
    try:
        rows = get_conn().execute(
            """
            SELECT s.id,
                   COALESCE(u.name, u.email, 'Staff ' || s.id) AS name,
                   COALESCE(u.email, '') AS email,
                   COALESCE(s.role, '') AS role,
                   COALESCE(s.active, 1) AS active
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            ORDER BY s.id ASC
            """,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 缺表等环境差异按空选项降级
        logger.warning("channel assignment staff options unavailable: %s", exc)
        return []
    options: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if not _is_active_flag(row.get("active")):
            continue
        options.append(
            {
                "id": _int(row.get("id")),
                "name": _text(row.get("name"), f"Staff {_int(row.get('id'))}"),
                "email": _text(row.get("email")),
                "role": _text(row.get("role")),
            }
        )
    return options


def list_assignments(*, staff: dict[str, Any] | None = None, can_manage: bool = False) -> dict[str, Any]:
    """读端:全量分配(带成员名/账号名)+ 成员选项 + 观看者身份。

    缺表/迁移未跑时诚实降级 available=False + 空数据,不拖垮矩阵页主体。
    """
    me_staff_id = resolve_staff_id(staff) or None
    try:
        _ensure_assignments_schema()
        rows = get_conn().execute(
            """
            SELECT a.id, a.channel_id, a.staff_id, a.role, a.assigned_at, a.assigned_by_staff_id,
                   COALESCE(u.name, u.email, 'Staff ' || a.staff_id) AS staff_name,
                   COALESCE(u.email, '') AS staff_email,
                   COALESCE(c.platform, '') AS platform,
                   COALESCE(c.account_handle, '') AS handle,
                   COALESCE(c.account_display_name, c.account_handle, '') AS display_name,
                   COALESCE(c.metadata_json, '{}') AS metadata_json
            FROM vkpi_channel_assignments a
            LEFT JOIN staff st ON st.id = a.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            LEFT JOIN vkpi_employee_channels c ON c.id = a.channel_id AND c.deleted_at IS NULL
            ORDER BY a.channel_id ASC, a.role ASC, a.id ASC
            """,
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — 缺表等环境差异诚实降级空数据
        logger.warning("channel assignments unavailable: %s", exc)
        return {
            "available": False,
            "can_manage": bool(can_manage),
            "me_staff_id": me_staff_id,
            "assignments": [],
            "staff_options": [],
        }
    assignments: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        assignments.append(
            {
                "id": _int(row.get("id")),
                "channel_id": _int(row.get("channel_id")),
                "staff_id": _int(row.get("staff_id")),
                "role": _text(row.get("role"), DEFAULT_ROLE),
                "assigned_at": _text(row.get("assigned_at")),
                "assigned_by_staff_id": _int(row.get("assigned_by_staff_id")) or None,
                "staff_name": _text(row.get("staff_name")),
                "staff_email": _text(row.get("staff_email")),
                "platform": _text(row.get("platform")),
                "handle": _text(row.get("handle")),
                "display_name": _text(row.get("display_name"), row.get("handle")),
                # 官号判定沿用 channels 域统一口径(metadata_json 标记),前端可据此过滤
                "official": _is_official_channel_row(row),
            }
        )
    return {
        "available": True,
        "can_manage": bool(can_manage),
        "me_staff_id": me_staff_id,
        "assignments": assignments,
        "staff_options": _staff_options(),
    }


def _audit(channel_id: int, actor_staff_id: int, action: str, detail: str) -> None:
    """vkpi_channel_audit 尽力留痕;审计失败不影响主流程。"""
    try:
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel_id, actor_staff_id or None, action, detail, _utcnow()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("channel assignment audit skipped: %s", exc)


def set_assignment(channel_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """写端:指派/改派/清除某官号的负责成员(路由层已限 owner/管理层)。

    body.staff_id 传 0/None = 清除该 role 的分配;role 默认 owner。
    幂等实现:同 (channel_id, role) 先删后插,天然满足唯一约束。
    """
    _ensure_assignments_schema()
    conn = get_conn()
    channel_id = _int(channel_id)
    row = conn.execute(
        """
        SELECT id, platform, account_handle, account_display_name, metadata_json
        FROM vkpi_employee_channels
        WHERE id = ? AND deleted_at IS NULL
        """,
        (channel_id,),
    ).fetchone()
    if not row:
        raise LookupError(f"channel {channel_id} not found")
    channel = dict(row)
    payload = body or {}
    role = (_text(payload.get("role"), DEFAULT_ROLE)).lower()
    if role not in ALLOWED_ROLES:
        raise ValueError(f"role must be one of {sorted(ALLOWED_ROLES)}")
    actor_id = resolve_staff_id(staff) or 0
    target_staff_id = _int(payload.get("staff_id"))
    if not target_staff_id:
        conn.execute(
            "DELETE FROM vkpi_channel_assignments WHERE channel_id = ? AND role = ?",
            (channel_id, role),
        )
        conn.commit()
        _audit(channel_id, actor_id, "assignment_cleared", f"role={role} handle={_text(channel.get('account_handle'))}")
        return {"ok": True, "channel_id": channel_id, "role": role, "staff_id": None, "cleared": True}
    staff_row = conn.execute("SELECT id FROM staff WHERE id = ?", (target_staff_id,)).fetchone()
    if not staff_row:
        raise ValueError(f"staff {target_staff_id} not found")
    assigned_at = _utcnow()
    conn.execute(
        "DELETE FROM vkpi_channel_assignments WHERE channel_id = ? AND role = ?",
        (channel_id, role),
    )
    conn.execute(
        """
        INSERT INTO vkpi_channel_assignments (channel_id, staff_id, role, assigned_at, assigned_by_staff_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (channel_id, target_staff_id, role, assigned_at, actor_id or None),
    )
    conn.commit()
    _audit(
        channel_id,
        actor_id,
        "assignment_set",
        f"role={role} staff_id={target_staff_id} handle={_text(channel.get('account_handle'))}",
    )
    return {
        "ok": True,
        "channel_id": channel_id,
        "role": role,
        "staff_id": target_staff_id,
        "assigned_at": assigned_at,
        "cleared": False,
    }
