"""Creation transaction helpers for the project workflow facade."""
from __future__ import annotations

from typing import Any, Callable, Collection


def prepare_project(
    body: dict[str, Any],
    staff: dict[str, Any] | None,
    *,
    normalize_stage: Callable[[str], str],
    project_stages: Collection[str],
    staff_id: Callable[[dict[str, Any] | None], int],
    to_int: Callable[..., int],
    can_view_all: Callable[[dict[str, Any] | None], bool],
    utcnow: Callable[[], str],
    token_hex: Callable[[int], str],
    normalize_products: Callable[[dict[str, Any]], list[dict[str, str]]],
) -> dict[str, Any]:
    name = str(body.get("project_name") or body.get("name") or "").strip()
    if not name:
        raise ValueError("project_name required")
    stage = normalize_stage(str(body.get("stage") or "discovery"))
    if stage not in project_stages:
        raise ValueError("unsupported stage")
    actor_staff_id = staff_id(staff)
    assigned_staff_id = to_int(body.get("assigned_staff_id"), actor_staff_id)
    if not can_view_all(staff):
        assigned_staff_id = actor_staff_id
    now = utcnow()
    project_uid = str(
        body.get("project_uid") or f"VKPI-{token_hex(5).upper()}"
    ).strip()
    products = normalize_products(body)
    primary_product = products[0] if products else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if products:
        metadata = {
            **metadata,
            "products": products,
            "product_skus": [item["product_sku"] for item in products],
        }
    return {
        "name": name,
        "stage": stage,
        "actor_staff_id": actor_staff_id,
        "assigned_staff_id": assigned_staff_id,
        "now": now,
        "project_uid": project_uid,
        "products": products,
        "primary_product": primary_product,
        "metadata": metadata,
    }


def persist_project(
    conn: Any,
    body: dict[str, Any],
    prepared: dict[str, Any],
    *,
    terminal_stages: Collection[str],
    to_int: Callable[..., int],
    json_dump: Callable[[Any], str],
) -> int:
    stage = prepared["stage"]
    now = prepared["now"]
    actor_staff_id = prepared["actor_staff_id"]
    assigned_staff_id = prepared["assigned_staff_id"]
    primary_product = prepared["primary_product"]
    conn.execute(
        """
        INSERT INTO vkpi_projects (
            project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
            product_sku, product_name, platform, marketplace, stage, stage_status,
            priority, source_type, shopify_discount_code, shopify_link, amazon_asin,
            amazon_attribution_link, amazon_associates_link, sample_status, tracking_number,
            target_post_date, due_at, started_at, last_activity_at, metadata_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            prepared["project_uid"],
            prepared["name"],
            to_int(body.get("kol_id")) or None,
            assigned_staff_id or None,
            actor_staff_id or None,
            str(primary_product.get("product_sku") or body.get("product_sku") or ""),
            str(primary_product.get("product_name") or body.get("product_name") or ""),
            str(body.get("platform") or ""),
            str(body.get("marketplace") or ""),
            stage,
            "closed" if stage in terminal_stages else str(body.get("stage_status") or "active"),
            str(body.get("priority") or "normal"),
            str(body.get("source_type") or "manual"),
            str(body.get("shopify_discount_code") or ""),
            str(body.get("shopify_link") or ""),
            str(body.get("amazon_asin") or ""),
            str(body.get("amazon_attribution_link") or ""),
            str(body.get("amazon_associates_link") or ""),
            str(body.get("sample_status") or "not_required"),
            str(body.get("tracking_number") or ""),
            body.get("target_post_date"),
            body.get("due_at"),
            now,
            now,
            json_dump(prepared["metadata"]),
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM vkpi_projects WHERE project_uid=?",
        (prepared["project_uid"],),
    ).fetchone()
    project_id = int(row["id"]) if row else 0
    if project_id:
        _record_creation_rows(
            conn,
            body,
            project_id,
            stage=stage,
            actor_staff_id=actor_staff_id,
            assigned_staff_id=assigned_staff_id,
            now=now,
            to_int=to_int,
            json_dump=json_dump,
        )
    conn.commit()
    return project_id


def _record_creation_rows(
    conn: Any,
    body: dict[str, Any],
    project_id: int,
    *,
    stage: str,
    actor_staff_id: int,
    assigned_staff_id: int,
    now: str,
    to_int: Callable[..., int],
    json_dump: Callable[[Any], str],
) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_project_stage_events
            (project_id, from_stage, to_stage, event_type, actor_staff_id, note, effective_at, metadata_json, created_at)
        VALUES (?, '', ?, 'created', ?, ?, ?, '{}', ?)
        """,
        (
            project_id,
            stage,
            actor_staff_id or None,
            str(body.get("note") or ""),
            now,
            now,
        ),
    )
    if not to_int(body.get("kol_id")) or not assigned_staff_id:
        return
    existing_claim = conn.execute(
        "SELECT id FROM vkpi_kol_claims WHERE kol_id=? AND status='active' LIMIT 1",
        (to_int(body.get("kol_id")),),
    ).fetchone()
    if existing_claim:
        return
    conn.execute(
        """
        INSERT INTO vkpi_kol_claims (
            kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at,
            metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            to_int(body.get("kol_id")),
            assigned_staff_id,
            project_id,
            "active",
            now,
            now,
            json_dump({"source": "project_create"}),
            now,
            now,
        ),
    )


__all__ = ["persist_project", "prepare_project"]
