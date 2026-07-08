"""V-KPI cost ledger helpers."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.domains.costs.common import (
    MAX_AMOUNT_CENTS,
    TYPE_ALIASES,
    VALID_COST_TYPES,
    _amount_cents,
    _int,
    _json,
    _sku,
    normalize_cost_status,
    normalize_currency,
    utcnow,
)
from app.domains.costs.product_catalog import ensure_product_catalog_schema, list_product_catalog
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_audit import ensure_vkpi_audit_schema
from app.domains.projects.workflow import staff_id


def _ensure_cost_ledger_columns() -> None:
    """Add v3 cost lifecycle fields for existing SQLite dev databases."""
    if is_postgres_runtime():
        return
    conn = get_conn()
    existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(vkpi_cost_ledger)").fetchall()}
    columns = {
        "approved_by_staff_id": "INTEGER",
        "approved_at": "TEXT",
        "voided_by_staff_id": "INTEGER",
        "voided_at": "TEXT",
        "updated_at": "TEXT",
    }
    for column, ddl in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE vkpi_cost_ledger ADD COLUMN {column} {ddl}")
    conn.commit()


def ensure_product_cost_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_cost_catalog (
                id BIGSERIAL PRIMARY KEY,
                product_sku TEXT NOT NULL UNIQUE,
                product_name TEXT DEFAULT '',
                unit_cost_cents BIGINT NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                active BOOLEAN NOT NULL DEFAULT TRUE,
                note TEXT DEFAULT '',
                created_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
                updated_by_staff_id BIGINT REFERENCES staff(id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_cost_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_sku TEXT NOT NULL UNIQUE,
                product_name TEXT DEFAULT '',
                unit_cost_cents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'USD',
                active INTEGER NOT NULL DEFAULT 1,
                note TEXT DEFAULT '',
                created_by_staff_id INTEGER,
                updated_by_staff_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_product_cost_catalog_active
        ON vkpi_product_cost_catalog(active, product_sku)
        """
    )
    conn.commit()
    _ensure_cost_ledger_columns()


def upsert_product_cost(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_product_cost_schema()
    sku = _sku(body.get("product_sku") or body.get("sku"))
    if not sku:
        raise ValueError("product_sku required")
    unit_cost_cents = _amount_cents({"amount_usd": body.get("unit_cost_usd", body.get("amount_usd", body.get("unit_cost", 0)))})
    if "unit_cost_cents" in body:
        unit_cost_cents = _int(body.get("unit_cost_cents"))
    if unit_cost_cents < 0:
        raise ValueError("unit cost must be non-negative")
    actor_staff_id = staff_id(staff)
    now = utcnow()
    active = 0 if str(body.get("active")).strip().lower() in {"0", "false", "no", "inactive"} else 1
    active_value: Any = bool(active) if is_postgres_runtime() else active
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_product_cost_catalog (
            product_sku, product_name, unit_cost_cents, currency, active, note,
            created_by_staff_id, updated_by_staff_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_sku) DO UPDATE SET
            product_name=excluded.product_name,
            unit_cost_cents=excluded.unit_cost_cents,
            currency=excluded.currency,
            active=excluded.active,
            note=excluded.note,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at
        """,
        (
            sku,
            str(body.get("product_name") or body.get("name") or ""),
            unit_cost_cents,
            str(body.get("currency") or "USD"),
            active_value,
            str(body.get("note") or ""),
            actor_staff_id or None,
            actor_staff_id or None,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_product_cost_catalog WHERE product_sku=?", (sku,)).fetchone()
    product_cost = dict(row) if row else {}
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="product_cost_upsert",
        target_type="product_cost",
        target_id=sku,
        detail=f"{sku}:{unit_cost_cents}",
        metadata={
            "product_cost_id": product_cost.get("id"),
            "product_sku": sku,
            "unit_cost_cents": unit_cost_cents,
            "currency": product_cost.get("currency") or str(body.get("currency") or "USD"),
            "active": bool(active),
            "note": product_cost.get("note") or str(body.get("note") or ""),
        },
    )
    return {"product_cost": product_cost}


def list_product_costs(limit: int = 200, include_inactive: bool = False) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_product_cost_schema()
    clause = "" if include_inactive else "WHERE active"
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_product_cost_catalog
        {clause}
        ORDER BY updated_at DESC, product_sku ASC
        LIMIT ?
        """,
        (max(1, min(500, int(limit or 200))),),
    ).fetchall()
    return {"product_costs": [dict(row) for row in rows]}


def _product_cost_for_sku(product_sku: str) -> dict[str, Any] | None:
    ensure_product_cost_schema()
    sku = _sku(product_sku)
    if not sku:
        return None
    row = get_conn().execute(
        """
        SELECT *
        FROM vkpi_product_cost_catalog
        WHERE upper(product_sku)=? AND active
        LIMIT 1
        """,
        (sku,),
    ).fetchone()
    return dict(row) if row else None


def record_shipped_product_cost(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Post lens/sample cost once a project reaches shipped.

    Employees do not enter this value. Management sets it in the product cost
    catalog, and shipping is the operational trigger that makes the lens a cost.
    """
    ensure_vkpi_schema()
    ensure_product_cost_schema()
    conn = get_conn()
    project = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    sku = _sku(project["product_sku"])
    if not sku:
        return {"status": "skipped", "reason": "missing_product_sku"}
    product_cost = _product_cost_for_sku(sku)
    if not product_cost:
        return {"status": "skipped", "reason": "product_cost_not_configured", "product_sku": sku}
    source_ref = f"auto_product_cost:{int(project_id)}:{sku}"
    existing = conn.execute(
        """
        SELECT id
        FROM vkpi_cost_ledger
        WHERE project_id=? AND cost_type='product' AND source_ref=? AND status!='void'
        LIMIT 1
        """,
        (int(project_id), source_ref),
    ).fetchone()
    if existing:
        return {"status": "skipped", "reason": "already_recorded", "cost_id": int(existing["id"])}
    actor_staff_id = staff_id(staff)
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_cost_ledger (
            project_id, kol_id, staff_id, cost_type, amount_cents, currency,
            status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            project["kol_id"],
            project["assigned_staff_id"],
            "product",
            _int(product_cost.get("unit_cost_cents")),
            str(product_cost.get("currency") or "USD"),
            "actual",
            now,
            source_ref,
            f"发货自动计入镜头成本：{sku}",
            actor_staff_id or None,
            _json({"auto": True, "trigger": "shipped", "product_sku": sku, "product_cost_id": product_cost.get("id")}),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_cost_ledger WHERE source_ref=? ORDER BY id DESC LIMIT 1", (source_ref,)).fetchone()
    cost = dict(row) if row else {}
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="cost_add",
        target_type="cost",
        target_id=cost.get("id", ""),
        detail=f"auto_product_cost:{sku}:{_int(product_cost.get('unit_cost_cents'))}",
        metadata={
            "project_id": int(project_id),
            "project_uid": project.get("project_uid"),
            "kol_id": project["kol_id"],
            "staff_id": project["assigned_staff_id"],
            "cost_id": cost.get("id"),
            "cost_type": "product",
            "amount_cents": _int(product_cost.get("unit_cost_cents")),
            "source_ref": source_ref,
            "product_sku": sku,
            "product_cost_id": product_cost.get("id"),
            "auto": True,
            "trigger": "shipped",
        },
    )
    return {"status": "recorded", "cost": cost, "product_cost": product_cost}


def add_cost(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    project_id = _int(body.get("project_id"))
    cost_type = str(body.get("cost_type") or "other").strip().lower()
    cost_type = TYPE_ALIASES.get(cost_type, cost_type)
    if not project_id:
        raise ValueError("project_id required")
    scope.assert_project_access(project_id, staff, write=True)
    if cost_type not in VALID_COST_TYPES:
        raise ValueError("unsupported cost_type")
    amount_cents = _amount_cents(body)
    if amount_cents < 0:
        raise ValueError("amount must be non-negative")
    # 写前硬校验(路由映 400):金额上界防 BIGINT 溢出 500;币种/状态白名单防任意值混入。
    if amount_cents > MAX_AMOUNT_CENTS:
        raise ValueError("amount exceeds maximum allowed")
    currency = normalize_currency(body.get("currency"))
    status = normalize_cost_status(body.get("status"))
    conn = get_conn()
    project = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        raise LookupError("project not found")
    actor_staff_id = staff_id(staff)
    now = utcnow()
    source_ref = str(body.get("source_ref") or "").strip()
    # 批B #1(2026-06-12):物流/产品类成本带 source_ref 即幂等——路由层每次保存物流都调 add_cost,
    # 按 (project_id, cost_type, source_ref) 查重,命中未作废行则 UPDATE 替代 INSERT,防双记。
    if source_ref and cost_type in {"shipping", "product"}:
        existing = conn.execute(
            """
            SELECT *
            FROM vkpi_cost_ledger
            WHERE project_id=? AND cost_type=? AND source_ref=? AND status!='void'
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id, cost_type, source_ref),
        ).fetchone()
        if existing:
            old = dict(existing)
            old_metadata = old.get("metadata_json")
            metadata_value = (
                _json(body.get("metadata"))
                if body.get("metadata") is not None
                else (old_metadata if isinstance(old_metadata, str) else _json(old_metadata))
            )
            conn.execute(
                """
                UPDATE vkpi_cost_ledger
                SET amount_cents=?, currency=?, status=?, incurred_at=?, note=?, metadata_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    amount_cents,
                    currency or old.get("currency") or "USD",
                    status or old.get("status") or "actual",
                    str(body.get("incurred_at") or old.get("incurred_at") or now),
                    str(body.get("note") or old.get("note") or ""),
                    metadata_value,
                    now,
                    int(old["id"]),
                ),
            )
            conn.commit()
            fresh = dict(conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(old["id"]),)).fetchone())
            audit.log_business_event(
                staff_id=actor_staff_id,
                action_type="cost_edit",
                target_type="cost",
                target_id=int(old["id"]),
                detail=f"{cost_type}:{amount_cents} (source_ref idempotent update)",
                metadata={
                    "project_id": project_id,
                    "kol_id": fresh.get("kol_id"),
                    "staff_id": fresh.get("staff_id"),
                    "cost_id": int(old["id"]),
                    "cost_type": cost_type,
                    "amount_cents": amount_cents,
                    "old_amount_cents": _int(old.get("amount_cents")),
                    "source_ref": source_ref,
                    "idempotent_update": True,
                },
            )
            return {"cost": fresh, "idempotent_update": True}
    # P2:INSERT 取行一律 RETURNING——并发下 ORDER BY id DESC 会取到别人的行。
    row = conn.execute(
        """
        INSERT INTO vkpi_cost_ledger (
            project_id, kol_id, staff_id, cost_type, amount_cents, currency,
            status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        RETURNING *
        """,
        (
            project_id,
            project["kol_id"],
            project["assigned_staff_id"],
            cost_type,
            amount_cents,
            currency or "USD",
            status or "actual",
            str(body.get("incurred_at") or now),
            source_ref,
            str(body.get("note") or ""),
            actor_staff_id or None,
            _json(body.get("metadata")),
            now,
            now,
        ),
    ).fetchone()
    conn.commit()
    cost = dict(row) if row else {}
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="cost_add",
        target_type="cost",
        target_id=cost.get("id", ""),
        detail=f"{cost_type}:{amount_cents}",
        metadata={
            "project_id": project_id,
            "kol_id": project["kol_id"],
            "staff_id": project["assigned_staff_id"],
            "cost_id": cost.get("id"),
            "cost_type": cost_type,
            "amount_cents": amount_cents,
            "source_ref": cost.get("source_ref"),
            "note": cost.get("note"),
        },
    )
    return {"cost": cost}


def list_costs(project_id: int | None = None, staff_id: int | None = None, limit: int = 100, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        scope.assert_project_access(int(project_id), staff)
        where.append("c.project_id=?")
        params.append(int(project_id))
    scope_clause, scope_params = scope.row_staff_filter("c", staff, staff_id, domain="cost")
    if scope_clause:
        where.append(scope_clause)
        params.extend(scope_params)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = get_conn().execute(
        f"""
        SELECT c.*,
               p.project_name,
               p.product_sku,
               k.channel_name AS kol_name,
               u.name AS staff_name
        FROM vkpi_cost_ledger c
        LEFT JOIN vkpi_projects p ON p.id = c.project_id
        LEFT JOIN kols k ON k.id = c.kol_id
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        {clause}
        ORDER BY c.incurred_at DESC, c.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"costs": [dict(row) for row in rows]}


def get_cost(cost_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    conn = get_conn()
    row = conn.execute(
        """
        SELECT c.*,
               p.project_name,
               p.product_sku,
               p.platform AS project_platform,
               k.channel_name AS kol_name,
               k.channel_url AS kol_url,
               k.platform AS kol_platform,
               owner_user.name AS staff_name,
               owner_user.email AS staff_email,
               creator_user.name AS created_by_name,
               approver_user.name AS approved_by_name,
               voider_user.name AS voided_by_name
        FROM vkpi_cost_ledger c
        LEFT JOIN vkpi_projects p ON p.id = c.project_id
        LEFT JOIN kols k ON k.id = c.kol_id
        LEFT JOIN staff owner_staff ON owner_staff.id = c.staff_id
        LEFT JOIN users owner_user ON owner_user.id = owner_staff.user_id
        LEFT JOIN staff creator_staff ON creator_staff.id = c.created_by_staff_id
        LEFT JOIN users creator_user ON creator_user.id = creator_staff.user_id
        LEFT JOIN staff approver_staff ON approver_staff.id = c.approved_by_staff_id
        LEFT JOIN users approver_user ON approver_user.id = approver_staff.user_id
        LEFT JOIN staff voider_staff ON voider_staff.id = c.voided_by_staff_id
        LEFT JOIN users voider_user ON voider_user.id = voider_staff.user_id
        WHERE c.id=?
        """,
        (int(cost_id),),
    ).fetchone()
    if not row:
        raise LookupError("cost not found")
    cost = dict(row)
    project_id = _int(cost.get("project_id"))
    if project_id:
        scope.assert_project_access(project_id, staff)
    else:
        scope.assert_staff_access(_int(cost.get("staff_id")), staff, domain="cost")
    audit_events: list[dict[str, Any]] = []
    if scope.can_view_all(staff, domain="audit"):
        ensure_vkpi_audit_schema()
        audit_events = [
            dict(item)
            for item in conn.execute(
                """
                SELECT ba.*, s.name AS staff_name, u.email AS staff_email
                FROM vkpi_business_audit_logs ba
                LEFT JOIN staff st ON st.id = ba.staff_id
                LEFT JOIN users s ON s.id = st.user_id
                LEFT JOIN users u ON u.id = st.user_id
                WHERE (ba.target_type=? AND ba.target_id=?)
                   OR ba.metadata_json LIKE ?
                   OR ba.metadata_json LIKE ?
                ORDER BY ba.created_at DESC, ba.id DESC
                LIMIT 100
                """,
                ("cost", str(int(cost_id)), f'%"cost_id": {int(cost_id)}%', f'%"cost_id":"{int(cost_id)}"%'),
            ).fetchall()
        ]
    return {
        "cost": cost,
        "project": {
            "id": cost.get("project_id"),
            "project_name": cost.get("project_name"),
            "product_sku": cost.get("product_sku"),
            "platform": cost.get("project_platform"),
        },
        "kol": {
            "id": cost.get("kol_id"),
            "channel_name": cost.get("kol_name"),
            "channel_url": cost.get("kol_url"),
            "platform": cost.get("kol_platform"),
        },
        "owner": {
            "id": cost.get("staff_id"),
            "name": cost.get("staff_name"),
            "email": cost.get("staff_email"),
        },
        "audit_events": audit_events,
    }


def update_cost(cost_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone()
    if not row:
        raise LookupError("cost not found")
    old = dict(row)
    project_id = _int(old.get("project_id"))
    if project_id:
        scope.assert_project_access(project_id, staff, write=True)
    actor_staff_id = staff_id(staff)
    updates: list[str] = []
    params: list[Any] = []
    if "cost_type" in body:
        cost_type = TYPE_ALIASES.get(str(body.get("cost_type") or "").strip().lower(), str(body.get("cost_type") or "").strip().lower())
        if cost_type not in VALID_COST_TYPES:
            raise ValueError("unsupported cost_type")
        updates.append("cost_type=?")
        params.append(cost_type)
    if "amount_cents" in body or "amount_usd" in body or "amount" in body:
        amount_cents = _amount_cents(body)
        if amount_cents < 0:
            raise ValueError("amount must be non-negative")
        updates.append("amount_cents=?")
        params.append(amount_cents)
    for key in ("currency", "incurred_at", "source_ref", "note"):
        if key in body:
            updates.append(f"{key}=?")
            params.append(str(body.get(key) or ""))
    if not updates:
        return {"cost": old, "updated": False}
    updates.append("updated_at=?")
    params.append(utcnow())
    params.append(int(cost_id))
    conn.execute(f"UPDATE vkpi_cost_ledger SET {', '.join(updates)} WHERE id=?", tuple(params))
    conn.commit()
    fresh = dict(conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone())
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="cost_edit",
        target_type="cost",
        target_id=int(cost_id),
        detail="cost ledger edited",
        metadata={
            "cost_id": int(cost_id),
            "project_id": fresh.get("project_id"),
            "kol_id": fresh.get("kol_id"),
            "staff_id": fresh.get("staff_id"),
            "source_ref": fresh.get("source_ref"),
            "note": fresh.get("note"),
            "old": old,
            "new": fresh,
        },
    )
    return {"cost": fresh, "updated": True}


def approve_cost(cost_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone()
    if not row:
        raise LookupError("cost not found")
    old = dict(row)
    actor_staff_id = staff_id(staff)
    now = utcnow()
    conn.execute(
        """
        UPDATE vkpi_cost_ledger
        SET status='actual', approved_by_staff_id=?, approved_at=?, updated_at=?
        WHERE id=?
        """,
        (actor_staff_id or None, now, now, int(cost_id)),
    )
    conn.commit()
    fresh = dict(conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone())
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="cost_approve",
        target_type="cost",
        target_id=int(cost_id),
        detail=str((body or {}).get("note") or "cost approved"),
        metadata={
            "cost_id": int(cost_id),
            "old_status": old.get("status"),
            "new_status": fresh.get("status"),
            "project_id": fresh.get("project_id"),
            "kol_id": fresh.get("kol_id"),
            "staff_id": fresh.get("staff_id"),
            "source_ref": fresh.get("source_ref"),
            "note": fresh.get("note"),
        },
    )
    return {"cost": fresh, "approved": True}


def void_cost(cost_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone()
    if not row:
        raise LookupError("cost not found")
    old = dict(row)
    actor_staff_id = staff_id(staff)
    now = utcnow()
    reason = str((body or {}).get("reason") or (body or {}).get("note") or "voided")
    conn.execute(
        """
        UPDATE vkpi_cost_ledger
        SET status='void', voided_by_staff_id=?, voided_at=?, updated_at=?,
            note=CASE WHEN note='' THEN ? ELSE note || ' | void: ' || ? END
        WHERE id=?
        """,
        (actor_staff_id or None, now, now, reason, reason, int(cost_id)),
    )
    conn.commit()
    fresh = dict(conn.execute("SELECT * FROM vkpi_cost_ledger WHERE id=?", (int(cost_id),)).fetchone())
    audit.log_business_event(
        staff_id=actor_staff_id,
        action_type="cost_void",
        target_type="cost",
        target_id=int(cost_id),
        detail=reason,
        metadata={
            "cost_id": int(cost_id),
            "old_status": old.get("status"),
            "new_status": fresh.get("status"),
            "project_id": fresh.get("project_id"),
            "kol_id": fresh.get("kol_id"),
            "staff_id": fresh.get("staff_id"),
            "source_ref": fresh.get("source_ref"),
            "note": fresh.get("note"),
        },
    )
    return {"cost": fresh, "voided": True}


def summarize_project(project_id: int) -> dict[str, Any]:
    ensure_vkpi_schema()
    # 汇总只计已确认的实际成本(status='actual'):estimate/pending 不冒充实际支出。
    # 按 currency 分组:异币种绝不直加(无汇率),单币种给标量 total_cost_cents,
    # 多币种置 None 并以 totals_by_currency 明细呈现(mixed_currency=True 提示调用方)。
    rows = get_conn().execute(
        """
        SELECT cost_type, currency, COALESCE(SUM(amount_cents), 0) AS amount_cents
        FROM vkpi_cost_ledger
        WHERE project_id=? AND status='actual'
        GROUP BY cost_type, currency
        ORDER BY amount_cents DESC
        """,
        (int(project_id),),
    ).fetchall()
    by_type = [dict(row) for row in rows]
    totals_by_currency: dict[str, int] = {}
    for row in by_type:
        cur = str(row.get("currency") or "USD")
        totals_by_currency[cur] = totals_by_currency.get(cur, 0) + int(row.get("amount_cents") or 0)
    currencies = list(totals_by_currency.keys())
    mixed = len(currencies) > 1
    if not currencies:
        total: int | None = 0
        single_currency: str | None = None
    elif len(currencies) == 1:
        single_currency = currencies[0]
        total = totals_by_currency[single_currency]
    else:
        single_currency = None
        total = None
    return {
        "project_id": int(project_id),
        "total_cost_cents": total,
        "currency": single_currency,
        "mixed_currency": mixed,
        "totals_by_currency": totals_by_currency,
        "by_type": by_type,
    }
