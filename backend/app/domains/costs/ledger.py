"""V-KPI cost ledger helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit, business_truth
from app.domains.access import scope
from app.domains.costs.common import (
    TYPE_ALIASES,
    VALID_COST_TYPES,
    _amount_cents,
    _int,
    _json,
    _sku,
    normalize_cost_status,
    normalize_currency,
    utcnow,
    validate_amount_cents,
)
from app.domains.costs.product_catalog import ensure_product_catalog_schema, list_product_catalog
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_audit import ensure_vkpi_audit_schema
from app.shared.staff_identity import staff_id


class ProductCostVerificationConflict(ValueError):
    """The human verified a stale product-cost snapshot."""


PRODUCT_COST_VERIFICATION_SOURCE_TYPES = frozenset(
    {
        "supplier_invoice",
        "vendor_invoice",
        "finance_erp",
        "approved_quote",
        "warehouse_cost_sheet",
    }
)
PRODUCT_COST_SOURCE_CLOCK_SKEW = timedelta(minutes=5)


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
    if observed_utc > datetime.now(timezone.utc) + PRODUCT_COST_SOURCE_CLOCK_SKEW:
        raise ValueError("source_observed_at cannot be in the future")
    return observed_utc.isoformat(timespec="seconds").replace("+00:00", "Z")


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
    # PostgreSQL schema is migration-owned.  Runtime DDL here made ordinary
    # reads and writes incompatible with the read-only release-validation
    # checkout and could hide a missing migration in normal traffic.
    if is_postgres_runtime():
        return
    conn = get_conn()
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
            row_version INTEGER NOT NULL DEFAULT 1,
            verification_status TEXT NOT NULL DEFAULT 'reference_unverified',
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            source_observed_at TEXT,
            verified_by_staff_id INTEGER,
            verified_at TEXT,
            created_by_staff_id INTEGER,
            updated_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing = {str(row["name"]) for row in conn.execute("PRAGMA table_info(vkpi_product_cost_catalog)").fetchall()}
    for column, ddl in {
        "row_version": "INTEGER NOT NULL DEFAULT 1",
        "verification_status": "TEXT NOT NULL DEFAULT 'reference_unverified'",
        "source_type": "TEXT NOT NULL DEFAULT ''",
        "source_ref": "TEXT NOT NULL DEFAULT ''",
        "source_observed_at": "TEXT",
        "verified_by_staff_id": "INTEGER",
        "verified_at": "TEXT",
    }.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE vkpi_product_cost_catalog ADD COLUMN {column} {ddl}")
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
    # 兄弟对齐 add_cost:非负 + BIGINT 上界越界 → ValueError(→400),不撞 BIGINT 溢出 500。
    unit_cost_cents = validate_amount_cents(unit_cost_cents)
    # 币种过白名单归一(与成本台账同口径),非法 → ValueError(→400);空值回退 USD。
    currency = normalize_currency(body.get("currency")) or "USD"
    actor_staff_id = staff_id(staff)
    now = utcnow()
    active = 0 if str(body.get("active")).strip().lower() in {"0", "false", "no", "inactive"} else 1
    active_value: Any = bool(active) if is_postgres_runtime() else active
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_product_cost_catalog (
            product_sku, product_name, unit_cost_cents, currency, active, note,
            row_version,
            verification_status, source_type, source_ref, source_observed_at,
            verified_by_staff_id, verified_at,
            created_by_staff_id, updated_by_staff_id, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(product_sku) DO UPDATE SET
            product_name=excluded.product_name,
            unit_cost_cents=excluded.unit_cost_cents,
            currency=excluded.currency,
            active=excluded.active,
            note=excluded.note,
            row_version=vkpi_product_cost_catalog.row_version + 1,
            verification_status='reference_unverified',
            source_type=excluded.source_type,
            source_ref=excluded.source_ref,
            source_observed_at=excluded.source_observed_at,
            verified_by_staff_id=NULL,
            verified_at=NULL,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at
        """,
        (
            sku,
            str(body.get("product_name") or body.get("name") or ""),
            unit_cost_cents,
            currency,
            active_value,
            str(body.get("note") or ""),
            1,
            "reference_unverified",
            str(body.get("source_type") or "historical_reference"),
            str(body.get("source_ref") or ""),
            str(body.get("source_observed_at") or "") or None,
            None,
            None,
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
            "currency": product_cost.get("currency") or currency,
            "active": bool(active),
            "note": product_cost.get("note") or str(body.get("note") or ""),
            "verification_status": "reference_unverified",
            "source_type": product_cost.get("source_type") or "historical_reference",
            "source_ref": product_cost.get("source_ref") or "",
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
        WHERE upper(product_sku)=?
          AND active
          AND verification_status='verified'
          AND NULLIF(TRIM(source_type), '') IS NOT NULL
          AND NULLIF(TRIM(source_ref), '') IS NOT NULL
          AND source_observed_at IS NOT NULL
          AND verified_by_staff_id IS NOT NULL
          AND verified_at IS NOT NULL
        LIMIT 1
        """,
        (sku,),
    ).fetchone()
    return dict(row) if row else None


def verify_product_cost(
    product_sku: str,
    body: dict[str, Any],
    *,
    authorization_evidence: dict[str, Any],
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote a reference cost only after source + human evidence is present."""

    ensure_vkpi_schema()
    ensure_product_cost_schema()
    sku = _sku(product_sku)
    if not sku:
        raise ValueError("product_sku required")
    source_type = str(body.get("source_type") or "").strip().lower()
    source_ref = str(body.get("source_ref") or "").strip()
    if not source_type or not source_ref or not str(body.get("source_observed_at") or "").strip():
        raise ValueError("source_type, source_ref and source_observed_at are required")
    if source_type not in PRODUCT_COST_VERIFICATION_SOURCE_TYPES:
        raise ValueError("unsupported product cost source_type")
    source_observed_at = _verified_source_observed_at(body.get("source_observed_at"))
    expected_id = _int(body.get("expected_id"))
    expected_unit_cost_cents = _int(body.get("expected_unit_cost_cents"), -1)
    expected_currency = normalize_currency(body.get("expected_currency"))
    expected_row_version = _int(body.get("expected_row_version"))
    expected_updated_at = str(body.get("expected_updated_at") or "").strip()
    if (
        expected_id <= 0
        or expected_unit_cost_cents < 0
        or not expected_currency
        or expected_row_version <= 0
        or not expected_updated_at
    ):
        raise ValueError(
            "expected_id, expected_unit_cost_cents, expected_currency, "
            "expected_row_version and expected_updated_at are required"
        )
    actor = staff_id(staff)
    if not actor:
        raise ValueError("authenticated staff actor required")
    # Schema guards run before the business transaction. In particular the
    # SQLite audit guard may commit its DDL, so it must never run between the
    # cost CAS update and the audit insert.
    ensure_vkpi_audit_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_product_cost_catalog WHERE upper(product_sku)=?", (sku,)).fetchone()
    if not row:
        raise LookupError("product cost not found")
    now = utcnow()
    try:
        fresh = conn.execute(
            """
            UPDATE vkpi_product_cost_catalog
            SET verification_status='verified', source_type=?, source_ref=?, source_observed_at=?,
                verified_by_staff_id=?, verified_at=?, updated_by_staff_id=?, updated_at=?,
                row_version=row_version + 1
            WHERE id=?
              AND upper(product_sku)=?
              AND unit_cost_cents=?
              AND upper(currency)=?
              AND row_version=?
              AND updated_at=?
              AND verification_status<>'verified'
            RETURNING *
            """,
            (
                source_type,
                source_ref,
                source_observed_at,
                actor,
                now,
                actor,
                now,
                expected_id,
                sku,
                expected_unit_cost_cents,
                expected_currency,
                expected_row_version,
                expected_updated_at,
            ),
        ).fetchone()
        if not fresh:
            raise ProductCostVerificationConflict(
                "product cost changed after it was loaded; refresh and verify the current row"
            )
        fresh_dict = dict(fresh)
        audit.log_business_event(
            staff_id=actor,
            action_type="product_cost_verify",
            target_type="product_cost",
            target_id=sku,
            detail=str(authorization_evidence.get("reason") or "product cost verified"),
            metadata={
                "product_sku": sku,
                "verification_status": "verified",
                "source_type": source_type,
                "source_ref": source_ref,
                "source_observed_at": source_observed_at,
                "authorization_ref": authorization_evidence.get("authorization_ref"),
                "expected_id": expected_id,
                "expected_unit_cost_cents": expected_unit_cost_cents,
                "expected_currency": expected_currency,
                "expected_row_version": expected_row_version,
                "verified_row_version": fresh_dict.get("row_version"),
            },
            conn=conn,
            commit=False,
            ensure_schema=False,
        )
        conn.commit()
        return {"product_cost": fresh_dict, "verified": True}
    except Exception:
        conn.rollback()
        raise


def record_shipped_product_cost(project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Post lens/sample cost once a project reaches shipped.

    Employees do not enter this value. Management sets it in the product cost
    catalog, and shipping is the operational trigger that makes the lens a cost.
    """
    ensure_vkpi_schema()
    ensure_product_cost_schema()
    conn = get_conn()
    project_row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project_row:
        raise LookupError("project not found")
    project = dict(project_row)
    sku = _sku(project["product_sku"])
    if not sku:
        return {"status": "skipped", "reason": "missing_product_sku"}
    product_cost = _product_cost_for_sku(sku)
    if not product_cost:
        any_cost = conn.execute(
            "SELECT verification_status FROM vkpi_product_cost_catalog WHERE upper(product_sku)=? AND active LIMIT 1",
            (sku,),
        ).fetchone()
        reason = "product_cost_unverified" if any_cost else "product_cost_not_configured"
        return {"status": "skipped", "reason": reason, "product_sku": sku}
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
            status, incurred_at, source_ref, note, created_by_staff_id, metadata_json, created_at,
            approved_by_staff_id, approved_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            _json({
                "auto": True,
                "trigger": "shipped",
                "product_sku": sku,
                "product_cost_id": product_cost.get("id"),
                "verification_status": "verified",
                "source_type": product_cost.get("source_type"),
                "source_ref": product_cost.get("source_ref"),
                "source_observed_at": product_cost.get("source_observed_at"),
            }),
            now,
            product_cost.get("verified_by_staff_id") or actor_staff_id or None,
            product_cost.get("verified_at") or now,
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
            "verification_status": "verified",
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
    # 写前硬校验(路由映 400):金额非负 + 上界防 BIGINT 溢出 500;币种/状态白名单防任意值混入。
    amount_cents = validate_amount_cents(_amount_cents(body))
    currency = normalize_currency(body.get("currency"))
    requested_status = normalize_cost_status(body.get("status"))
    # Human-entered costs are reviewable evidence, not actual spend.  Only the
    # manager approval route (or verified product-cost automation above) can
    # create an approved actual row.
    status = "pending" if requested_status in {None, "actual"} else requested_status
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
                SET amount_cents=?, currency=?, status=?, incurred_at=?, note=?, metadata_json=?,
                    approved_by_staff_id=NULL, approved_at=NULL, updated_at=?
                WHERE id=?
                """,
                (
                    amount_cents,
                    currency or old.get("currency") or "USD",
                    status,
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
        f"""
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
            status,
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
               CASE WHEN {business_truth.approved_actual_cost_sql('c')}
                    THEN 1 ELSE 0 END AS is_approved_actual,
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
    costs = [dict(row) for row in rows]
    for row in costs:
        row["business_truth_status"] = (
            "approved_actual"
            if int(row.get("is_approved_actual") or 0) == 1
            else "reference_only"
        )
    return {"costs": costs}


def get_cost(cost_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    _ensure_cost_ledger_columns()
    conn = get_conn()
    row = conn.execute(
        f"""
        SELECT c.*,
               CASE WHEN {business_truth.approved_actual_cost_sql('c')}
                    THEN 1 ELSE 0 END AS is_approved_actual,
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
    cost["business_truth_status"] = (
        "approved_actual"
        if int(cost.get("is_approved_actual") or 0) == 1
        else "reference_only"
    )
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
    truth_material_changed = False
    if "cost_type" in body:
        cost_type = TYPE_ALIASES.get(str(body.get("cost_type") or "").strip().lower(), str(body.get("cost_type") or "").strip().lower())
        if cost_type not in VALID_COST_TYPES:
            raise ValueError("unsupported cost_type")
        updates.append("cost_type=?")
        params.append(cost_type)
        truth_material_changed = True
    if "amount_cents" in body or "amount_usd" in body or "amount" in body:
        # 兄弟对齐 add_cost:非负 + BIGINT 上界越界 → ValueError(→400),不脏落库/溢出 500。
        amount_cents = validate_amount_cents(_amount_cents(body))
        updates.append("amount_cents=?")
        params.append(amount_cents)
        truth_material_changed = True
    if "currency" in body:
        # 兄弟对齐 add_cost:币种过白名单归一;非法 → ValueError(→400),空值跳过(保留原币不置空)。
        currency = normalize_currency(body.get("currency"))
        if currency is not None:
            updates.append("currency=?")
            params.append(currency)
            truth_material_changed = True
    for key in ("incurred_at", "source_ref", "note"):
        if key in body:
            updates.append(f"{key}=?")
            params.append(str(body.get(key) or ""))
            if key != "note":
                truth_material_changed = True
    if not updates:
        return {"cost": old, "updated": False}
    if truth_material_changed:
        updates.extend(["status='pending'", "approved_by_staff_id=NULL", "approved_at=NULL"])
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
            "authorization_evidence": body.get("_authorization_evidence") or {},
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
            "authorization_evidence": (body or {}).get("_authorization_evidence") or {},
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
            "authorization_evidence": (body or {}).get("_authorization_evidence") or {},
        },
    )
    return {"cost": fresh, "voided": True}


def summarize_project(project_id: int) -> dict[str, Any]:
    ensure_vkpi_schema()
    # 汇总只计管理员已批准或可验证自动过账的实际成本。
    # 按 currency 分组:异币种绝不直加(无汇率),单币种给标量 total_cost_cents,
    # 多币种置 None 并以 totals_by_currency 明细呈现(mixed_currency=True 提示调用方)。
    rows = get_conn().execute(
        """
        SELECT cost_type, currency, COALESCE(SUM(amount_cents), 0) AS amount_cents
        FROM vkpi_cost_ledger
        WHERE project_id=? AND status='actual' AND approved_at IS NOT NULL
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
