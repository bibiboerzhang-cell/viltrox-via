"""V-KPI 公司库存(Company Inventory)domain service — CRUD + 调动审计账本。

迁移 135(vkpi_inventory,sku UNIQUE)+ 136(vkpi_inventory_movements,审计流水)。
库存项跨 Event 共享(样品 / 量产 / 配件 / 设备),前端以 sku 为资源键做 PATCH/DELETE。
每一次 mutation(create/update/delete/调量)都在同一事务里向 vkpi_inventory_movements
落一条不可篡改流水:谁(moved_by_staff_id)/动作(action)/数量变化(delta_qty)/
变化后(new_qty)/事由(reason)/关联活动(event_id)/上下文(metadata_json)。

DB 走 get_conn(? 占位)应用路径,镜像 events/service.py。员工身份用 scope.actor_staff_id。
绝不碰 viltrox_fit_score / rule_v0;与 KOL Pool 物理隔离。
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.platform.db.schema_audit import ensure_vkpi_audit_schema


class InventoryVerificationConflict(ValueError):
    """The operator attempted to verify or revoke a stale inventory row."""


INVENTORY_VERIFICATION_SOURCE_TYPES = frozenset(
    {
        "physical_count_sheet",
        "warehouse_confirmation",
        "wms_export",
        "erp_export",
        "shopify_inventory_snapshot",
    }
)
INVENTORY_PROVIDER_SOURCE_TYPES = frozenset(
    {"wms_export", "erp_export", "shopify_inventory_snapshot"}
)
INVENTORY_SOURCE_CLOCK_SKEW = timedelta(minutes=5)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_REF_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "credential",
    "signature",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{int(_now().timestamp() * 1000)}"


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    try:
        sid = scope.actor_staff_id(staff)
        return sid or None
    except Exception:
        return (staff or {}).get("staff_id") or (staff or {}).get("id")


def _item_row(r: Any) -> dict[str, Any]:
    return dict(r) if r is not None else None


def _movement_row(r: Any) -> dict[str, Any]:
    row = dict(r)
    row["metadata_json"] = _loads(row.get("metadata_json"), {})
    return row


def _ensure_inventory_verification_schema() -> None:
    """Keep SQLite development databases additive; Postgres uses migration 263."""

    if is_postgres_runtime():
        return
    conn = get_conn()
    cursor = conn.execute("PRAGMA table_info(vkpi_inventory)")
    # Lightweight unit fakes do not emulate schema introspection.  Real
    # SQLite cursors always expose fetchall; leaving a fake untouched preserves
    # the production fail-closed behavior without coupling CRUD tests to DDL.
    if not hasattr(cursor, "fetchall"):
        return
    rows = cursor.fetchall()
    if not rows:
        return
    existing = {str(dict(row).get("name") or "") for row in rows}
    columns = {
        "quantity_status": "TEXT NOT NULL DEFAULT 'unverified'",
        "quantity_source": "TEXT NOT NULL DEFAULT 'unknown'",
        "quantity_verified_at": "TEXT",
        "quantity_source_ref": "TEXT",
        "quantity_source_observed_at": "TEXT",
        "quantity_evidence_sha256": "TEXT",
        "quantity_verified_by_staff_id": "INTEGER",
        "quantity_verified_organization_id": "INTEGER",
        "row_version": "INTEGER NOT NULL DEFAULT 1",
    }
    changed = False
    for column, ddl in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE vkpi_inventory ADD COLUMN {column} {ddl}")
            changed = True
    if changed:
        conn.commit()


def _verified_source_observed_at(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("source_observed_at is required")
    iso_value = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        observed = datetime.fromisoformat(iso_value)
    except ValueError as exc:
        raise ValueError("source_observed_at must be a valid ISO-8601 timestamp") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("source_observed_at timezone is required")
    observed_utc = observed.astimezone(timezone.utc)
    if observed_utc > datetime.now(timezone.utc) + INVENTORY_SOURCE_CLOCK_SKEW:
        raise ValueError("source_observed_at cannot be in the future")
    return observed_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_int(value: Any, field: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _verification_cas(body: dict[str, Any]) -> tuple[str, int, int, str]:
    expected_id = str(body.get("expected_id") or "").strip()
    expected_qty = int(body.get("expected_qty")) if body.get("expected_qty") is not None else -1
    expected_row_version = _positive_int(body.get("expected_row_version"), "expected_row_version")
    expected_updated_at = str(body.get("expected_updated_at") or "").strip()
    if not expected_id or expected_qty < 0 or not expected_updated_at:
        raise ValueError(
            "expected_id, expected_qty, expected_row_version and expected_updated_at are required"
        )
    return expected_id, expected_qty, expected_row_version, expected_updated_at


def _safe_source_ref(value: Any) -> str:
    """Accept a useful receipt reference while rejecting credential-bearing URLs."""

    raw = str(value or "").strip()
    if not raw or len(raw) > 500:
        raise ValueError("source_ref is required and must be at most 500 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError("source_ref cannot contain control characters")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ValueError("source_ref URL is invalid") from exc
    if parsed.scheme or parsed.netloc:
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_ref URL must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("source_ref URL cannot contain userinfo credentials")
        key_values = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        key_values.extend(urllib.parse.parse_qsl(parsed.fragment, keep_blank_values=True))
        unsafe_keys = []
        for key, _item in key_values:
            normalized = str(key or "").strip().lower().replace("-", "_")
            if any(part in normalized for part in _SENSITIVE_REF_KEY_PARTS):
                unsafe_keys.append(normalized)
        fragment_lower = urllib.parse.unquote(parsed.fragment or "").lower()
        if unsafe_keys or any(f"{part}=" in fragment_lower for part in _SENSITIVE_REF_KEY_PARTS):
            raise ValueError("source_ref URL cannot contain credential query or fragment keys")
    return raw


def _log_movement(
    conn: Any,
    *,
    inventory_sku: str,
    action: str,
    staff: dict[str, Any] | None,
    delta_qty: int | None = None,
    new_qty: int | None = None,
    reason: str = "",
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """向审计账本落一条流水(与调用方共用同一事务,由调用方 commit)。"""
    mid = _gen_id("mov")
    conn.execute(
        """
        INSERT INTO vkpi_inventory_movements
          (id, inventory_sku, event_id, action, delta_qty, new_qty, reason,
           moved_by_staff_id, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?::jsonb, NOW())
        """,
        (
            mid,
            str(inventory_sku),
            str(event_id) if event_id not in (None, "") else None,
            str(action or "adjust"),
            int(delta_qty) if delta_qty is not None else None,
            int(new_qty) if new_qty is not None else None,
            str(reason or ""),
            _staff_id(staff),
            _dumps(metadata or {}),
        ),
    )


# ── Inventory CRUD ──────────────────────────────────────────────────────────
def list_inventory() -> dict[str, Any]:
    """列全部库存项(跨 Event 共享,不按员工 scope 过滤——公司公共库存表)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_inventory ORDER BY is_sample DESC, category ASC, created_at ASC"
    ).fetchall()
    return {"items": [_item_row(r) for r in rows]}


def _get_by_sku(conn: Any, sku: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM vkpi_inventory WHERE sku = ?", (str(sku),)).fetchone()
    return _item_row(row) if row else None


def create_item(body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """新建库存项 + 落一条 action='add' 流水(delta=new_qty=初始数量)。"""
    _ensure_inventory_verification_schema()
    conn = get_conn()
    sku = str((body or {}).get("sku") or "").strip()
    if not sku:
        raise ValueError("sku required")
    if _get_by_sku(conn, sku):
        raise ValueError(f"sku already exists: {sku}")
    iid = str((body or {}).get("id") or _gen_id("s"))
    qty = int((body or {}).get("qty") or 0)
    # A typed quantity is a reference entry, not warehouse-source proof.  The
    # request body cannot promote itself to manual/source confirmed; a future
    # WMS/ERP/provider adapter must own that explicit verification boundary.
    conn.execute(
        """
        INSERT INTO vkpi_inventory
          (id, sku, name, category, qty, location, note, is_sample, created_by_staff_id,
           quantity_status, quantity_source, quantity_verified_at, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ? THEN NOW() ELSE NULL END, NOW(), NOW())
        """,
        (
            iid,
            sku,
            str((body or {}).get("name") or ""),
            str((body or {}).get("category") or "lens"),
            qty,
            str((body or {}).get("location") or ""),
            str((body or {}).get("note") or ""),
            bool((body or {}).get("is_sample") or False),
            _staff_id(staff),
            "unverified",
            "manual_reference" if "qty" in (body or {}) else "manual_placeholder",
            False,
        ),
    )
    _log_movement(
        conn,
        inventory_sku=sku,
        action="add",
        staff=staff,
        delta_qty=qty,
        new_qty=qty,
        reason=str((body or {}).get("reason") or "新建库存项"),
        event_id=(body or {}).get("event_id"),
        metadata={"name": str((body or {}).get("name") or ""), "category": str((body or {}).get("category") or "lens")},
    )
    conn.commit()
    return {"item": _get_by_sku(conn, sku)}


_UPDATABLE = {"name": str, "category": str, "location": str, "note": str}


def update_item(sku: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """改库存项。qty 变化 → action='adjust' 流水(记 delta/new_qty);
    仅改字段(name/location/...)→ action='edit' 流水(记改了哪些字段)。"""
    _ensure_inventory_verification_schema()
    conn = get_conn()
    body = body or {}
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise ValueError(f"inventory not found: {sku}")

    sets: list[str] = []
    vals: list[Any] = []
    changed_fields: list[str] = []
    for key, caster in _UPDATABLE.items():
        if key in body:
            sets.append(f"{key} = ?")
            vals.append(caster(body[key]) if body[key] is not None else "")
            changed_fields.append(key)
    if "is_sample" in body:
        sets.append("is_sample = ?")
        vals.append(bool(body["is_sample"]))
        changed_fields.append("is_sample")

    qty_changed = "qty" in body
    old_qty = int(current.get("qty") or 0)
    new_qty = old_qty
    if qty_changed:
        new_qty = max(0, int(body.get("qty") or 0))
        sets.append("qty = ?")
        vals.append(new_qty)
        sets.extend(
            [
                "quantity_status = ?",
                "quantity_source = ?",
                "quantity_verified_at = NULL",
                "quantity_source_ref = NULL",
                "quantity_source_observed_at = NULL",
                "quantity_evidence_sha256 = NULL",
                "quantity_verified_by_staff_id = NULL",
                "quantity_verified_organization_id = NULL",
            ]
        )
        vals.extend(["unverified", "manual_adjustment_reference"])

    if not sets:
        return {"item": current}

    sets.extend(["row_version = row_version + 1", "updated_at = NOW()"])
    vals.append(str(sku))
    conn.execute(f"UPDATE vkpi_inventory SET {', '.join(sets)} WHERE sku = ?", tuple(vals))

    if qty_changed:
        delta = new_qty - old_qty
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="adjust",
            staff=staff,
            delta_qty=delta,
            new_qty=new_qty,
            reason=str(body.get("reason") or ("增加库存" if delta > 0 else "减少库存" if delta < 0 else "调整库存")),
            event_id=body.get("event_id"),
            metadata={"old_qty": old_qty, "fields": changed_fields},
        )
    elif changed_fields:
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="edit",
            staff=staff,
            delta_qty=None,
            new_qty=new_qty,
            reason=str(body.get("reason") or f"修改字段: {', '.join(changed_fields)}"),
            event_id=body.get("event_id"),
            metadata={"fields": changed_fields},
        )
    conn.commit()
    return {"item": _get_by_sku(conn, str(sku))}


def verify_quantity(
    sku: str,
    body: dict[str, Any],
    *,
    authorization_evidence: dict[str, Any],
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Verify an existing, unchanged quantity against an immutable source receipt.

    This route never accepts a new quantity.  ``expected_qty`` is only a CAS
    assertion against the value already visible to the operator.
    """

    _ensure_inventory_verification_schema()
    ensure_vkpi_audit_schema()
    conn = get_conn()
    organization_id = scope.assert_legacy_default_organization(
        staff, conn, feature="inventory verification"
    )
    actor = _staff_id(staff)
    if not actor:
        raise ValueError("authenticated staff actor required")
    source_type = str(body.get("source_type") or "").strip().lower()
    source_ref = _safe_source_ref(body.get("source_ref"))
    evidence_sha256 = str(body.get("evidence_sha256") or "").strip().lower()
    if source_type not in INVENTORY_VERIFICATION_SOURCE_TYPES:
        raise ValueError("unsupported inventory source_type")
    if not _SHA256_RE.fullmatch(evidence_sha256):
        raise ValueError("evidence_sha256 must be a lowercase SHA-256 hex digest")
    source_observed_at = _verified_source_observed_at(body.get("source_observed_at"))
    expected_id, expected_qty, expected_row_version, expected_updated_at = _verification_cas(body)
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise LookupError(f"inventory not found: {sku}")
    now = _now()
    quantity_status = (
        "source_confirmed"
        if source_type in INVENTORY_PROVIDER_SOURCE_TYPES
        else "manual_confirmed"
    )
    try:
        fresh = conn.execute(
            """
            UPDATE vkpi_inventory
            SET quantity_status=?, quantity_source=?, quantity_source_ref=?,
                quantity_source_observed_at=?, quantity_evidence_sha256=?,
                quantity_verified_by_staff_id=?, quantity_verified_organization_id=?,
                quantity_verified_at=?, row_version=row_version + 1, updated_at=?
            WHERE id=? AND sku=? AND qty=? AND row_version=? AND updated_at=?
              AND quantity_status='unverified'
            RETURNING *
            """,
            (
                quantity_status,
                source_type,
                source_ref,
                source_observed_at,
                evidence_sha256,
                actor,
                organization_id,
                now,
                now,
                expected_id,
                str(sku),
                expected_qty,
                expected_row_version,
                expected_updated_at,
            ),
        ).fetchone()
        if not fresh:
            raise InventoryVerificationConflict(
                "inventory quantity changed after it was loaded; refresh before verifying"
            )
        fresh_dict = dict(fresh)
        audit_metadata = {
            "organization_id": organization_id,
            "quantity_status": quantity_status,
            "source_type": source_type,
            "source_ref": source_ref,
            "source_observed_at": source_observed_at,
            "evidence_sha256": evidence_sha256,
            "authorization_ref": authorization_evidence.get("authorization_ref"),
            "expected_qty": expected_qty,
            "expected_row_version": expected_row_version,
            "verified_row_version": fresh_dict.get("row_version"),
        }
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="verify",
            staff=staff,
            delta_qty=0,
            new_qty=expected_qty,
            reason=str(authorization_evidence.get("reason") or "inventory quantity verified"),
            metadata=audit_metadata,
        )
        audit.log_business_event(
            staff_id=actor,
            action_type="inventory_quantity_verify",
            target_type="inventory",
            target_id=str(sku),
            detail=str(authorization_evidence.get("reason") or "inventory quantity verified"),
            metadata=audit_metadata,
            conn=conn,
            commit=False,
            ensure_schema=False,
        )
        conn.commit()
        return {"item": fresh_dict, "verified": True, "quantity_changed": False}
    except Exception:
        conn.rollback()
        raise


def revoke_quantity_verification(
    sku: str,
    body: dict[str, Any],
    *,
    authorization_evidence: dict[str, Any],
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    """Invalidate a stale/manual receipt without changing the stored quantity."""

    _ensure_inventory_verification_schema()
    ensure_vkpi_audit_schema()
    conn = get_conn()
    organization_id = scope.assert_legacy_default_organization(
        staff, conn, feature="inventory verification revocation"
    )
    actor = _staff_id(staff)
    if not actor:
        raise ValueError("authenticated staff actor required")
    expected_id, expected_qty, expected_row_version, expected_updated_at = _verification_cas(body)
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise LookupError(f"inventory not found: {sku}")
    now = _now()
    try:
        fresh = conn.execute(
            """
            UPDATE vkpi_inventory
            SET quantity_status='unverified', quantity_source='verification_revoked',
                quantity_source_ref=NULL, quantity_source_observed_at=NULL,
                quantity_evidence_sha256=NULL, quantity_verified_by_staff_id=NULL,
                quantity_verified_organization_id=NULL, quantity_verified_at=NULL,
                row_version=row_version + 1, updated_at=?
            WHERE id=? AND sku=? AND qty=? AND row_version=? AND updated_at=?
              AND quantity_status IN ('manual_confirmed','source_confirmed')
            RETURNING *
            """,
            (
                now,
                expected_id,
                str(sku),
                expected_qty,
                expected_row_version,
                expected_updated_at,
            ),
        ).fetchone()
        if not fresh:
            raise InventoryVerificationConflict(
                "inventory verification changed after it was loaded; refresh before revoking"
            )
        fresh_dict = dict(fresh)
        audit_metadata = {
            "organization_id": organization_id,
            "previous_source_type": current.get("quantity_source"),
            "previous_evidence_sha256": current.get("quantity_evidence_sha256"),
            "authorization_ref": authorization_evidence.get("authorization_ref"),
            "expected_qty": expected_qty,
            "expected_row_version": expected_row_version,
            "revoked_row_version": fresh_dict.get("row_version"),
        }
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="verification_revoke",
            staff=staff,
            delta_qty=0,
            new_qty=expected_qty,
            reason=str(authorization_evidence.get("reason") or "inventory verification revoked"),
            metadata=audit_metadata,
        )
        audit.log_business_event(
            staff_id=actor,
            action_type="inventory_quantity_verification_revoke",
            target_type="inventory",
            target_id=str(sku),
            detail=str(authorization_evidence.get("reason") or "inventory verification revoked"),
            metadata=audit_metadata,
            conn=conn,
            commit=False,
            ensure_schema=False,
        )
        conn.commit()
        return {"item": fresh_dict, "verified": False, "quantity_changed": False}
    except Exception:
        conn.rollback()
        raise


def delete_item(sku: str, body: dict[str, Any] | None, staff: dict[str, Any] | None) -> dict[str, Any]:
    """删库存项 + 落一条 action='delete' 流水(delta = -剩余数量)。
    流水先于删除写入(不带库存外键),库存项删了历史仍可追溯。"""
    conn = get_conn()
    body = body or {}
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise ValueError(f"inventory not found: {sku}")
    old_qty = int(current.get("qty") or 0)
    _log_movement(
        conn,
        inventory_sku=str(sku),
        action="delete",
        staff=staff,
        delta_qty=-old_qty,
        new_qty=0,
        reason=str(body.get("reason") or "删除库存项"),
        event_id=body.get("event_id"),
        metadata={"name": current.get("name"), "category": current.get("category"), "qty_at_delete": old_qty},
    )
    conn.execute("DELETE FROM vkpi_inventory WHERE sku = ?", (str(sku),))
    conn.commit()
    return {"ok": True, "sku": str(sku)}


# ── Groups 组团(逻辑分组,不扣库存)─────────────────────────────────────────
def _group_row(conn: Any, g: dict[str, Any]) -> dict[str, Any]:
    """补上组内 items(JOIN 库存表拿 name/category/总库存,诚实显示是否充足)。"""
    rows = conn.execute(
        """
        SELECT gi.id, gi.inventory_sku, gi.qty_in_group, gi.note,
               i.name AS item_name, i.category AS item_category, i.qty AS total_available
        FROM vkpi_inventory_group_items gi
        LEFT JOIN vkpi_inventory i ON i.sku = gi.inventory_sku
        WHERE gi.group_id = ?
        ORDER BY gi.added_at ASC
        """,
        (str(g["id"]),),
    ).fetchall()
    items = [dict(r) for r in rows]
    g = dict(g)
    g["items"] = items
    g["item_count"] = len(items)
    g["total_units"] = sum(int(it.get("qty_in_group") or 0) for it in items)
    return g


def list_groups(event_id: str | None = None) -> dict[str, Any]:
    """列全部组(或某 event 的组),每组带成员清单。"""
    conn = get_conn()
    if event_id:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_groups WHERE event_id = ? ORDER BY created_at DESC",
            (str(event_id),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vkpi_inventory_groups ORDER BY created_at DESC").fetchall()
    return {"groups": [_group_row(conn, dict(r)) for r in rows]}


def create_group(body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """新建组(可带初始成员 items=[{sku, qty, note}])。"""
    conn = get_conn()
    body = body or {}
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("group name required")
    gid = _gen_id("grp")
    conn.execute(
        """
        INSERT INTO vkpi_inventory_groups
          (id, name, note, location, event_id, created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?, NOW(), NOW())
        """,
        (
            gid,
            name,
            str(body.get("note") or ""),
            str(body.get("location") or ""),
            str(body.get("event_id")) if body.get("event_id") else None,
            _staff_id(staff),
        ),
    )
    for i, it in enumerate(body.get("items") or []):
        sku = str((it or {}).get("sku") or "").strip()
        if not sku:
            continue
        conn.execute(
            "INSERT INTO vkpi_inventory_group_items (id, group_id, inventory_sku, qty_in_group, note, added_at) "
            "VALUES (?,?,?,?,?, NOW())",
            (f"{_gen_id('gi')}_{i}", gid, sku, max(1, int((it or {}).get("qty") or 1)), str((it or {}).get("note") or "")),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (gid,)).fetchone()
    return {"group": _group_row(conn, dict(row))}


def update_group(group_id: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """改组名/备注/位置/关联 event。"""
    conn = get_conn()
    body = body or {}
    sets, vals = [], []
    for key in ("name", "note", "location", "event_id"):
        if key in body:
            sets.append(f"{key} = ?")
            vals.append(str(body[key]) if body[key] not in (None, "") else None)
    if not sets:
        row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
        if not row:
            raise ValueError(f"group not found: {group_id}")
        return {"group": _group_row(conn, dict(row))}
    sets.append("updated_at = NOW()")
    vals.append(str(group_id))
    conn.execute(f"UPDATE vkpi_inventory_groups SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        raise ValueError(f"group not found: {group_id}")
    return {"group": _group_row(conn, dict(row))}


def delete_group(group_id: str, body: dict[str, Any] | None, staff: dict[str, Any] | None) -> dict[str, Any]:
    """删组(成员行 ON DELETE CASCADE 一并删;不动各项独立库存)。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        raise ValueError(f"group not found: {group_id}")
    conn.execute("DELETE FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),))
    conn.commit()
    return {"ok": True, "group_id": str(group_id)}


def add_to_group(group_id: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """往组里加一个 SKU × 数量(逻辑加入,不扣库存)。"""
    conn = get_conn()
    body = body or {}
    sku = str(body.get("sku") or "").strip()
    if not sku:
        raise ValueError("sku required")
    g = conn.execute("SELECT id FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not g:
        raise ValueError(f"group not found: {group_id}")
    conn.execute(
        "INSERT INTO vkpi_inventory_group_items (id, group_id, inventory_sku, qty_in_group, note, added_at) "
        "VALUES (?,?,?,?,?, NOW())",
        (_gen_id("gi"), str(group_id), sku, max(1, int(body.get("qty") or 1)), str(body.get("note") or "")),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"group": _group_row(conn, dict(row))}


def remove_from_group(group_id: str, item_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """从组里移除一个成员行(不动各项独立库存)。"""
    conn = get_conn()
    conn.execute(
        "DELETE FROM vkpi_inventory_group_items WHERE id = ? AND group_id = ?",
        (str(item_id), str(group_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        return {"ok": True, "group_id": str(group_id)}
    return {"group": _group_row(conn, dict(row))}


def find_sku_groups(sku: str) -> dict[str, Any]:
    """查某 SKU 在哪些组里(用于库存表行显示「所在分组」)。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT g.id AS group_id, g.name AS group_name, g.location, gi.qty_in_group
        FROM vkpi_inventory_group_items gi
        JOIN vkpi_inventory_groups g ON g.id = gi.group_id
        WHERE gi.inventory_sku = ?
        ORDER BY g.created_at DESC
        """,
        (str(sku),),
    ).fetchall()
    return {"groups": [dict(r) for r in rows]}


# ── 从产品库批量补缺 SKU(qty=0,幂等,只增不改)──────────────────────────────
_CAT_MAP = {
    "lens": "lens", "lenses": "lens",
    "lighting": "equipment", "flash": "equipment", "monitor": "equipment", "equipment": "equipment",
    "accessories": "accessory", "accessory": "accessory", "battery": "accessory",
    "uv filter": "accessory", "filter": "accessory", "macro extension tube": "accessory",
}


def import_missing_from_catalog(staff: dict[str, Any] | None) -> dict[str, Any]:
    """把 vkpi_products 里有、vkpi_inventory 里没有的 SKU 批量补进库存(qty=0,location 待填)。
    幂等:已存在的 sku ON CONFLICT DO NOTHING;绝不覆盖已有项的 qty/字段。"""
    _ensure_inventory_verification_schema()
    conn = get_conn()
    # 产品库列名兼容(category_main / category)
    cols = {c[0] for c in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'vkpi_products'"
    ).fetchall() if isinstance(c, (tuple, list))} or {
        dict(r)["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'vkpi_products'"
        ).fetchall()
    }
    cat_col = "category_main" if "category_main" in cols else ("category" if "category" in cols else None)
    name_col = (
        "model_name"
        if "model_name" in cols
        else "marketing_name"
        if "marketing_name" in cols
        else "name"
        if "name" in cols
        else "product_name"
        if "product_name" in cols
        else "sku"
    )
    sel = f"SELECT p.sku AS sku, p.{name_col} AS name" + (f", p.{cat_col} AS cat" if cat_col else ", '' AS cat")
    sel += " FROM vkpi_products p LEFT JOIN vkpi_inventory i ON i.sku = p.sku WHERE i.sku IS NULL AND p.sku IS NOT NULL AND p.sku <> ''"
    rows = conn.execute(sel).fetchall()
    # created_by_staff_id 有 FK→staff:校验存在,否则置 NULL(批量导入容忍无效/系统身份)。
    sid = _staff_id(staff)
    if sid is not None and not conn.execute("SELECT 1 FROM staff WHERE id = ?", (sid,)).fetchone():
        sid = None
    added = 0
    for i, r in enumerate(rows):
        d = dict(r)
        sku = str(d.get("sku") or "").strip()
        # viltrox.com 的多数 Shopify 变体没有官方 SKU。目录同步会使用
        # WEB-VAR-<variant_id> 稳定标识，但它不是仓库 SKU，禁止自动建库存行。
        if not sku or sku.upper().startswith("WEB-VAR-"):
            continue
        cat = _CAT_MAP.get(str(d.get("cat") or "").strip().lower(), "accessory")
        conn.execute(
            """
            INSERT INTO vkpi_inventory
              (id, sku, name, category, qty, location, note, is_sample, created_by_staff_id,
               quantity_status, quantity_source, quantity_verified_at, created_at, updated_at)
            VALUES (?,?,?,?,0,'','从产品库导入·待填库存量',FALSE,?,
                    'unverified','catalog_reference',NULL, NOW(), NOW())
            ON CONFLICT (sku) DO NOTHING
            """,
            (f"{_gen_id('s')}_{i}", sku, str(d.get("name") or sku), cat, sid),
        )
        added += 1
    conn.commit()
    return {"ok": True, "imported": added}


# ── Movements ledger ────────────────────────────────────────────────────────
def list_movements(sku: str | None = None, limit: int = 100) -> dict[str, Any]:
    """读审计流水。sku 给定则只看该 SKU,否则全量(均按时间倒序)。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 100), 500))
    if sku:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_movements WHERE inventory_sku = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (str(sku), safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_movements ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    return {"items": [_movement_row(r) for r in rows]}
