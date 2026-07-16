"""V-KPI reconciliation queue and append-only attribution adjustments."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import attribution
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _money_cents(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(round(float(str(value).replace("$", "").replace(",", "")) * 100))
    except (TypeError, ValueError):
        return _int(value)


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _row(table: str, row_id: int) -> dict[str, Any]:
    r = get_conn().execute(f"SELECT * FROM {table} WHERE id=?", (int(row_id),)).fetchone()
    if not r:
        raise LookupError(f"{table} row not found")
    return dict(r)


def enqueue_unmatched(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    ensure_vkpi_reconciliation_schema()
    source = str(body.get("source_platform") or body.get("source") or "shopify").strip().lower()
    if source not in {"shopify", "amazon", "manual", "custom"}:
        source = "custom"
    source_ref = str(body.get("source_ref") or body.get("order_id") or f"unmatched:{secrets.token_hex(6)}").strip()
    revenue_cents = _money_cents(body.get("revenue_cents", body.get("revenue_usd", body.get("revenue", 0))))
    priority = 10 if revenue_cents >= 100000 else 5 if revenue_cents >= 30000 else 0
    conn = get_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO vkpi_reconciliation_queue
            (source_platform, source_ref, order_id, revenue_cents, currency, occurred_at,
             customer_country, product_sku, discount_codes_json, utm_json, landing_referrer,
             raw_payload_json, status, priority, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source,
            source_ref,
            str(body.get("order_id") or ""),
            revenue_cents,
            str(body.get("currency") or "USD"),
            str(body.get("occurred_at") or _utcnow()),
            str(body.get("customer_country") or ""),
            str(body.get("product_sku") or ""),
            _json(body.get("discount_codes") or body.get("discount_codes_json") or []),
            _json(body.get("utm") or body.get("utm_json") or {}),
            str(body.get("landing_referrer") or body.get("landing_site") or ""),
            _json(body.get("raw_payload") or body.get("evidence") or body),
            "pending",
            priority,
            _utcnow(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_reconciliation_queue WHERE source_platform=? AND source_ref=?", (source, source_ref)).fetchone()
    return {"queue_item": dict(row) if row else {}, "status": "queued"}


def list_queue(status: str = "pending", limit: int = 100, assigned_to_staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    where: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        where.append("q.status=?")
        params.append(status)
    if assigned_to_staff_id:
        where.append("q.assigned_to_staff_id=?")
        params.append(int(assigned_to_staff_id))
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT q.*, s.id AS staff_id, u.name AS assigned_staff_name, u.email AS assigned_staff_email
        FROM vkpi_reconciliation_queue q
        LEFT JOIN staff s ON s.id = q.assigned_to_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        {clause}
        ORDER BY q.priority DESC, q.created_at ASC, q.id ASC
        LIMIT ?
        """,
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


def stats() -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM vkpi_reconciliation_stats").fetchone()
        if row:
            return {
                **dict(row),
                "business_truth_status": "reference_diagnostic",
                "counts_toward_verified_gmv": False,
            }
    except Exception as exc:
        logger.warning("vkpi reconciliation stats view failed, using grouped fallback: %s", exc)
    statuses = {"pending": 0, "in_review": 0, "resolved": 0, "rejected": 0, "pending_gmv_cents": 0}
    for row in conn.execute("SELECT status, COUNT(*) AS n, COALESCE(SUM(revenue_cents),0) AS revenue FROM vkpi_reconciliation_queue GROUP BY status").fetchall():
        key = str(row["status"] or "")
        statuses[f"{key}_count"] = int(row["n"] or 0)
        if key == "pending":
            statuses["pending_gmv_cents"] = int(row["revenue"] or 0)
    return {
        **statuses,
        "business_truth_status": "reference_diagnostic",
        "counts_toward_verified_gmv": False,
    }


def get_item(queue_id: int) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    item = _row("vkpi_reconciliation_queue", int(queue_id))
    history = get_conn().execute("SELECT * FROM vkpi_attribution_adjustments WHERE metadata_json LIKE ? ORDER BY adjusted_at DESC", (f'%"queue_id": {int(queue_id)}%',)).fetchall()
    return {"item": item, "history": [dict(row) for row in history]}


def assign(queue_id: int, assignee_staff_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    actor = _actor(staff)
    conn = get_conn()
    item = _row("vkpi_reconciliation_queue", int(queue_id))
    if item["status"] not in {"pending", "in_review"}:
        raise ValueError("queue item is not assignable")
    conn.execute(
        "UPDATE vkpi_reconciliation_queue SET status='in_review', assigned_to_staff_id=?, assigned_at=? WHERE id=?",
        (int(assignee_staff_id), _utcnow(), int(queue_id)),
    )
    conn.commit()
    _log_adjustment(
        attribution_id=_int(item.get("resolved_attribution_id")), adjustment_type="assign", field_changed="assigned_to_staff_id",
        old_value=str(item.get("assigned_to_staff_id") or ""), new_value=str(assignee_staff_id), evidence_text="reconciliation assignment",
        staff_id=actor, metadata={"queue_id": int(queue_id)},
    )
    return get_item(queue_id)


def _log_adjustment(*, attribution_id: int, adjustment_type: str, field_changed: str, old_value: str, new_value: str, evidence_text: str, staff_id: int | None, metadata: dict[str, Any] | None = None, ip: str = "") -> int:
    ensure_vkpi_reconciliation_schema()
    if not attribution_id:
        attribution_id = _int((get_conn().execute("SELECT id FROM vkpi_sales_attributions ORDER BY id DESC LIMIT 1").fetchone() or {}).get("id"))
    if not attribution_id:
        return 0
    get_conn().execute(
        """
        INSERT INTO vkpi_attribution_adjustments
            (attribution_id, adjustment_type, field_changed, old_value, new_value,
             evidence_text, evidence_files_json, adjusted_by_staff_id, adjusted_at, ip, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (int(attribution_id), adjustment_type, field_changed, old_value, new_value, evidence_text, "[]", int(staff_id) if staff_id else None, _utcnow(), ip, _json(metadata or {})),
    )
    get_conn().commit()
    row = get_conn().execute("SELECT id FROM vkpi_attribution_adjustments ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def resolve(queue_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    item = _row("vkpi_reconciliation_queue", int(queue_id))
    if item["status"] not in {"pending", "in_review"}:
        raise ValueError("queue item already closed")
    evidence_text = str(body.get("evidence_text") or body.get("evidence") or "").strip()
    if not evidence_text:
        raise ValueError("evidence_text required")
    actor = _actor(staff)
    source_ref = f"{item['source_platform']}:{item['source_ref']}:resolved:{queue_id}:{secrets.token_hex(3)}"
    attr = attribution.create_attribution(
        {
            "source_platform": item["source_platform"],
            "source_ref": source_ref,
            "project_id": _int(body.get("project_id")),
            "link_id": _int(body.get("link_id")) or None,
            "kol_id": _int(body.get("kol_id")) or None,
            "staff_id": _int(body.get("staff_id")) or actor or None,
            "product_sku": body.get("product_sku") or item.get("product_sku") or "",
            "order_id": item.get("order_id") or None,
            "revenue_cents": _int(body.get("revenue_cents"), _int(item.get("revenue_cents"))),
            "currency": item.get("currency") or "USD",
            "confidence": str(body.get("confidence") or "manual"),
            "occurred_at": item.get("occurred_at") or _utcnow(),
            "evidence": {
                "source": "manual_reconciliation",
                "queue_id": int(queue_id),
                "evidence_text": evidence_text,
                "original_source_ref": item.get("source_ref"),
                "authorization_evidence": body.get("_authorization_evidence") or {},
                "payload": body,
            },
        },
        staff=staff,
        ingest_class="human_verified",
    ).get("attribution") or {}
    attr_id = _int(attr.get("id"))
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_reconciliation_queue
        SET status='resolved', resolved_attribution_id=?, resolved_at=?, resolved_by_staff_id=?
        WHERE id=?
        """,
        (attr_id or None, _utcnow(), actor or None, int(queue_id)),
    )
    conn.commit()
    _log_adjustment(attribution_id=attr_id, adjustment_type="manual_resolve", field_changed="status", old_value=str(item.get("status")), new_value="resolved", evidence_text=evidence_text, staff_id=actor, metadata={"queue_id": int(queue_id), "created_attribution_id": attr_id})
    return {"queue_item": _row("vkpi_reconciliation_queue", int(queue_id)), "attribution": attr}


def reject(queue_id: int, reason: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    item = _row("vkpi_reconciliation_queue", int(queue_id))
    actor = _actor(staff)
    get_conn().execute(
        "UPDATE vkpi_reconciliation_queue SET status='rejected', rejection_reason=?, resolved_at=?, resolved_by_staff_id=? WHERE id=?",
        (str(reason or ""), _utcnow(), actor or None, int(queue_id)),
    )
    get_conn().commit()
    _log_adjustment(attribution_id=_int(item.get("resolved_attribution_id")), adjustment_type="reject", field_changed="status", old_value=str(item.get("status")), new_value="rejected", evidence_text=str(reason or "rejected"), staff_id=actor, metadata={"queue_id": int(queue_id)})
    return get_item(queue_id)


def _base_attribution(attribution_id: int) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_sales_attributions WHERE id=?", (int(attribution_id),)).fetchone()
    if not row:
        raise LookupError("attribution not found")
    return dict(row)


def adjust_attribution(attribution_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _base_attribution(int(attribution_id))
    actor = _actor(staff)
    evidence_text = str(body.get("evidence_text") or body.get("evidence") or "manual adjustment").strip()
    new_revenue = body.get("revenue_cents")
    if new_revenue is None and "revenue_usd" in body:
        new_revenue = _money_cents(body.get("revenue_usd"))
    delta = _int(new_revenue, _int(base.get("revenue_cents"))) - _int(base.get("revenue_cents"))
    payload = {**base, "source_ref": f"{base['source_ref']}:adjust:{secrets.token_hex(4)}", "revenue_cents": delta, "confidence": "manual", "evidence": {"source": "attribution_adjustment", "base_attribution_id": int(attribution_id), "evidence_text": evidence_text, "delta_cents": delta, "authorization_evidence": body.get("_authorization_evidence") or {}}}
    attr = attribution.create_attribution(payload, staff=staff, ingest_class="human_verified").get("attribution") or {}
    adj_id = _log_adjustment(attribution_id=int(attribution_id), adjustment_type="amount_adjust", field_changed="revenue_cents", old_value=str(base.get("revenue_cents")), new_value=str(new_revenue), evidence_text=evidence_text, staff_id=actor, metadata={"adjustment_attribution_id": attr.get("id"), "delta_cents": delta})
    return {"adjustment_id": adj_id, "adjustment_attribution": attr, "base_attribution": base}


def void_attribution(attribution_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    base = _base_attribution(int(attribution_id))
    actor = _actor(staff)
    evidence_text = str((body or {}).get("evidence_text") or (body or {}).get("reason") or "void attribution")
    payload = {**base, "source_ref": f"{base['source_ref']}:void:{secrets.token_hex(4)}", "revenue_cents": -_int(base.get("revenue_cents")), "commission_cents": -_int(base.get("commission_cents")), "confidence": "void", "evidence": {"source": "void", "base_attribution_id": int(attribution_id), "evidence_text": evidence_text, "authorization_evidence": (body or {}).get("_authorization_evidence") or {}}}
    attr = attribution.create_attribution(payload, staff=staff, ingest_class="human_verified").get("attribution") or {}
    adj_id = _log_adjustment(attribution_id=int(attribution_id), adjustment_type="void", field_changed="revenue_cents", old_value=str(base.get("revenue_cents")), new_value="0", evidence_text=evidence_text, staff_id=actor, metadata={"offset_attribution_id": attr.get("id")})
    return {"adjustment_id": adj_id, "offset_attribution": attr}


def refund(
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    ingest_class: str = "manual_unverified",
) -> dict[str, Any]:
    amount_cents = -abs(_money_cents(body.get("refund_cents", body.get("refund_usd", body.get("amount_usd", 0)))))
    source_ref = str(body.get("source_ref") or body.get("refund_id") or f"refund:{secrets.token_hex(6)}")
    source_platform = str(body.get("source_platform") or "shopify")
    existing = get_conn().execute(
        "SELECT id, revenue_cents FROM vkpi_sales_attributions WHERE source_platform=? AND source_ref=?",
        (source_platform, source_ref),
    ).fetchone()
    old_revenue = _int((dict(existing) if existing else {}).get("revenue_cents"))
    attr = attribution.create_attribution(
        {
            "source_platform": source_platform,
            "source_ref": source_ref,
            "project_id": body.get("project_id"),
            "link_id": body.get("link_id"),
            "kol_id": body.get("kol_id"),
            "staff_id": body.get("staff_id"),
            "shopify_order_snapshot_id": body.get("shopify_order_snapshot_id"),
            "product_sku": body.get("product_sku") or "",
            "revenue_cents": amount_cents,
            "confidence": "refund",
            "occurred_at": body.get("occurred_at") or _utcnow(),
            "evidence": {"source": "refund", "payload": body},
        },
        staff=staff,
        ingest_class=ingest_class,
    ).get("attribution") or {}
    if not existing or old_revenue != amount_cents:
        _log_adjustment(
            attribution_id=_int(attr.get("id")),
            adjustment_type="refund",
            field_changed="revenue_cents",
            old_value=str(old_revenue if existing else 0),
            new_value=str(amount_cents),
            evidence_text=str(body.get("evidence_text") or "refund webhook"),
            staff_id=_actor(staff),
            metadata={
                "refund": True,
                "source_ref": source_ref,
                "base_attribution_id": body.get("base_attribution_id"),
                "shopify_order_snapshot_id": body.get("shopify_order_snapshot_id"),
            },
        )
    return {"attribution": attr, "idempotent": bool(existing and old_revenue == amount_cents)}


def attribution_history(attribution_id: int) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    base = _base_attribution(int(attribution_id))
    rows = get_conn().execute("SELECT * FROM vkpi_attribution_adjustments WHERE attribution_id=? ORDER BY adjusted_at DESC, id DESC", (int(attribution_id),)).fetchall()
    return {"attribution": base, "adjustments": [dict(row) for row in rows]}


def sync_logs(limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_reconciliation_schema()
    rows = get_conn().execute("SELECT * FROM vkpi_shopify_sync_runs ORDER BY started_at DESC, id DESC LIMIT ?", (max(1, min(500, int(limit or 100))),)).fetchall()
    return {"sync_runs": [dict(row) for row in rows]}
