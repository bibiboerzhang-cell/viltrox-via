"""Message, content, terms, and shipment write operations for V-KPI workflow."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.vkpi import audit, scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.domains.projects.workflow_common import _amount_cents, _int, _json, staff_id, utcnow

def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)

def add_project_message(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    project = conn.execute("SELECT kol_id, assigned_staff_id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    message_body = str(body.get("body") or body.get("message") or body.get("snippet") or "").strip()
    conn.execute(
        """
        INSERT INTO vkpi_messages (
            project_id, kol_id, staff_id, source, direction, sender, receiver,
            body, snippet, evidence_url, follow_up_due_at, captured_at, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            staff_id(staff) or _int(project["assigned_staff_id"]) or None,
            str(body.get("source") or "manual"),
            str(body.get("direction") or "outbound"),
            str(body.get("sender") or ""),
            str(body.get("receiver") or ""),
            message_body,
            str(body.get("snippet") or message_body[:240]),
            str(body.get("evidence_url") or ""),
            body.get("follow_up_due_at"),
            str(body.get("captured_at") or now),
            _json(body.get("metadata")),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_messages WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff) or _int(project["assigned_staff_id"]),
            action_type="message_capture",
            target_type="message",
            target_id=item.get("id", ""),
            detail=str(item.get("body") or item.get("snippet") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None},
        )
    return item

def add_project_content(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    post_url = str(body.get("post_url") or body.get("url") or "").strip()
    if not post_url:
        raise ValueError("post_url required")
    conn = get_conn()
    project = conn.execute("SELECT kol_id, platform FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_content_posts (
            project_id, kol_id, link_id, platform, post_url, title, thumbnail_url,
            published_at, content_type, views, likes, comments, shares,
            rights_status, ad_usage_allowed, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id, post_url) DO UPDATE SET
            kol_id=excluded.kol_id,
            link_id=excluded.link_id,
            platform=excluded.platform,
            title=excluded.title,
            thumbnail_url=excluded.thumbnail_url,
            published_at=excluded.published_at,
            content_type=excluded.content_type,
            views=excluded.views,
            likes=excluded.likes,
            comments=excluded.comments,
            shares=excluded.shares,
            rights_status=excluded.rights_status,
            ad_usage_allowed=excluded.ad_usage_allowed,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            _int(body.get("link_id")) or None,
            str(body.get("platform") or project["platform"] or ""),
            post_url,
            str(body.get("title") or ""),
            str(body.get("thumbnail_url") or ""),
            body.get("published_at"),
            str(body.get("content_type") or "video"),
            _int(body.get("views")),
            _int(body.get("likes")),
            _int(body.get("comments")),
            _int(body.get("shares")),
            str(body.get("rights_status") or "unknown"),
            _db_bool(body.get("ad_usage_allowed")),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_content_posts WHERE project_id=? AND post_url=?", (int(project_id), post_url)).fetchone()
    item = dict(row) if row else {}
    if item:
        asset_url = str(body.get("asset_url") or body.get("thumbnail_url") or "").strip()
        if asset_url:
            existing_asset = conn.execute(
                "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                (int(item["id"]), asset_url),
            ).fetchone()
            if not existing_asset:
                conn.execute(
                    """
                    INSERT INTO vkpi_content_assets (
                        post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        int(item["id"]),
                        int(project_id),
                        asset_url,
                        str(body.get("asset_type") or body.get("content_type") or "content"),
                        str(body.get("usage_rights") or body.get("rights_status") or "unknown"),
                        _json({
                            "source": "project_detail_form",
                            "marker": (body.get("metadata") or {}).get("marker") if isinstance(body.get("metadata"), dict) else None,
                            "content_post_id": item.get("id"),
                        }),
                        now,
                    ),
                )
                conn.commit()
                asset_row = conn.execute(
                    "SELECT id FROM vkpi_content_assets WHERE post_id=? AND asset_url=? ORDER BY id DESC LIMIT 1",
                    (int(item["id"]), asset_url),
                ).fetchone()
                audit.log_business_event(
                    staff_id=staff_id(staff),
                    action_type="content_asset_add",
                    target_type="content_post",
                    target_id=item.get("id", ""),
                    detail=asset_url[:240],
                    metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "asset_id": asset_row["id"] if asset_row else None},
                )
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="content_capture",
            target_type="content_post",
            target_id=item.get("id", ""),
            detail=str(item.get("title") or item.get("post_url") or "")[:240],
            metadata={"project_id": int(project_id), "kol_id": _int(project["kol_id"]) or None, "post_url": post_url},
        )
    return item

def upsert_project_terms(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    if not conn.execute("SELECT id FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone():
        raise LookupError("project not found")
    now = utcnow()
    actor = staff_id(staff) or None
    shopify_link = str(body.get("shopify_url") or body.get("shopify_link") or "").strip()
    if shopify_link:
        conn.execute(
            "UPDATE vkpi_projects SET shopify_link=?, updated_at=? WHERE id=?",
            (shopify_link, now, int(project_id)),
        )
    conn.execute(
        """
        INSERT INTO vkpi_project_terms (
            project_id, cash_fee_cents, currency, sample_terms, deliverables_json,
            usage_rights, due_at, note, created_by_staff_id, updated_by_staff_id,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_id) DO UPDATE SET
            cash_fee_cents=excluded.cash_fee_cents,
            currency=excluded.currency,
            sample_terms=excluded.sample_terms,
            deliverables_json=excluded.deliverables_json,
            usage_rights=excluded.usage_rights,
            due_at=excluded.due_at,
            note=excluded.note,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at
        """,
        (
            int(project_id),
            _amount_cents(body.get("cash_fee_usd", body.get("cash_fee", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("sample_terms") or ""),
            json.dumps(body.get("deliverables") if isinstance(body.get("deliverables"), list) else [], ensure_ascii=False),
            str(body.get("usage_rights") or ""),
            body.get("due_at"),
            str(body.get("note") or ""),
            actor,
            actor,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_project_terms WHERE project_id=?", (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=actor,
            action_type="terms_upsert",
            target_type="project",
            target_id=int(project_id),
            detail=str(body.get("note") or shopify_link or item.get("sample_terms") or "")[:240],
            metadata={"project_id": int(project_id), "terms_id": item.get("id"), "shopify_link_updated": bool(shopify_link)},
        )
    return item

def add_project_shipment(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    project = conn.execute("SELECT kol_id, product_sku, product_name FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not project:
        raise LookupError("project not found")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_sample_assets (
            project_id, kol_id, product_sku, product_name, serial_number,
            sample_cost_cents, currency, return_required, status, shipped_at,
            received_at, note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            _int(project["kol_id"]) or None,
            str(body.get("product_sku") or project["product_sku"] or ""),
            str(body.get("product_name") or project["product_name"] or ""),
            str(body.get("serial_number") or ""),
            _amount_cents(body.get("sample_cost_usd", body.get("sample_cost", 0))),
            str(body.get("currency") or "USD"),
            _db_bool(body.get("return_required")),
            str(body.get("sample_status") or "shipped"),
            str(body.get("shipped_at") or now),
            body.get("received_at"),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    sample_id = conn.execute("SELECT id FROM vkpi_sample_assets WHERE project_id=? ORDER BY id DESC LIMIT 1", (int(project_id),)).fetchone()
    sample_asset_id = int(sample_id["id"]) if sample_id else None
    conn.execute(
        """
        INSERT INTO vkpi_shipments (
            project_id, sample_asset_id, carrier, tracking_number, status,
            shipping_cost_cents, currency, shipped_at, delivered_at, evidence_url,
            note, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            sample_asset_id,
            str(body.get("carrier") or ""),
            str(body.get("tracking_number") or ""),
            str(body.get("shipping_status") or "shipped"),
            _amount_cents(body.get("shipping_cost_usd", body.get("shipping_cost", 0))),
            str(body.get("currency") or "USD"),
            str(body.get("shipped_at") or now),
            body.get("delivered_at"),
            str(body.get("evidence_url") or ""),
            str(body.get("note") or ""),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT sh.*, sa.product_sku, sa.product_name, sa.serial_number, sa.sample_cost_cents
        FROM vkpi_shipments sh
        LEFT JOIN vkpi_sample_assets sa ON sa.id = sh.sample_asset_id
        WHERE sh.project_id=?
        ORDER BY sh.id DESC
        LIMIT 1
        """,
        (int(project_id),),
    ).fetchone()
    item = dict(row) if row else {}
    if item:
        audit.log_business_event(
            staff_id=staff_id(staff),
            action_type="shipment_add",
            target_type="shipment",
            target_id=item.get("id", ""),
            detail=str(item.get("tracking_number") or item.get("carrier") or "")[:240],
            metadata={"project_id": int(project_id), "sample_asset_id": item.get("sample_asset_id"), "product_sku": item.get("product_sku")},
        )
    return item
