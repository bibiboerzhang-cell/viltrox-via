"""Transactional phases for building market memory from legacy staging."""
from __future__ import annotations

from typing import Any


def reset_scope(conn: Any, source_scope: str, counters: Any) -> None:
    counters["reset_facts"] = int(
        conn.execute(
            """
            DELETE FROM vkpi_memory_facts
            WHERE fact_type='market_signal'
              AND source_ref LIKE ?
            """,
            (f"{source_scope}:%",),
        ).rowcount
        or 0
    )
    counters["reset_links"] = int(
        conn.execute(
            """
            DELETE FROM vkpi_memory_links
            WHERE link_type='official_account_published_product'
              AND source_ref LIKE ?
            """,
            (f"{source_scope}:%",),
        ).rowcount
        or 0
    )


def build_launch_plans(
    conn: Any,
    import_batch_id: int,
    source_scope: str,
    batch_uid: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_launch_plans_staging
        WHERE import_batch_id=?
          AND review_status='ready'
        ORDER BY id
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        target = ops["_market_target_for_product"](
            item.get("product_name") or item.get("product_sku") or item.get("launch_name") or "",
            fallback_topic=item.get("launch_name") or item.get("source_sheet") or "launch_plan",
            topic_kind="launch_plan",
            source_table="vkpi_legacy_launch_plans_staging",
            source_id=str(item["id"]),
        )
        ops["_upsert_market_signal"](
            target=target,
            signal_type="launch_plan",
            source_ref=f"{source_scope}:launch:{item['id']}",
            source_table="vkpi_legacy_launch_plans_staging",
            source_id=str(item["id"]),
            signal_date=item.get("launch_date"),
            value=item.get("status") or "planned",
            confidence_score=0.9 if target["entity_type"] == "product_family" else 0.65,
            payload={
                "launch_name": item.get("launch_name"),
                "product_sku": item.get("product_sku"),
                "product_name": item.get("product_name"),
                "category_primary": item.get("category_primary"),
                "category_secondary": item.get("category_secondary"),
                "launch_date": item.get("launch_date"),
                "target_region": item.get("target_region"),
                "target_platforms": ops["_load_json"](item.get("target_platforms_json") or "[]", []),
                "campaign_owner": item.get("campaign_owner"),
                "official_material_ref": item.get("official_material_ref"),
                "kol_plan_ref": item.get("kol_plan_ref"),
                "status": item.get("status"),
                "notes": item.get("notes"),
                "source_sheet": item.get("source_sheet"),
                "source_row": item.get("source_row"),
            },
            batch_uid=batch_uid,
        )
        counters["launch_plan"] += 1


def build_official_content(
    conn: Any,
    import_batch_id: int,
    source_scope: str,
    batch_uid: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_official_content_staging
        WHERE import_batch_id=?
          AND review_status='ready'
        ORDER BY id
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        target = ops["_market_target_for_product"](
            item.get("product") or "",
            fallback_topic=item.get("title") or item.get("official_account") or "official_content",
            topic_kind="official_content",
            source_table="vkpi_legacy_official_content_staging",
            source_id=str(item["id"]),
        )
        account = ops["_official_account_entity"](
            item.get("normalized_platform") or item.get("platform"),
            item.get("official_account"),
        )
        source_ref = f"{source_scope}:official_content:{item['id']}"
        if account and target["entity_type"] == "product_family":
            ops["_upsert_link"](
                source_entity_id=int(account["id"]),
                source_entity_uid=account["entity_uid"],
                target_entity_id=int(target["id"]),
                target_entity_uid=target["entity_uid"],
                link_type="official_account_published_product",
                source_ref=source_ref,
                weight=1.0,
                confidence_score=0.85,
                source={"source_sheet": item.get("source_sheet"), "source_row": item.get("source_row")},
                metadata={"platform": item.get("normalized_platform") or item.get("platform"), "link": item.get("link")},
            )
            counters["official_links"] += 1
        ops["_upsert_market_signal"](
            target=target,
            signal_type="official_content",
            source_ref=source_ref,
            source_table="vkpi_legacy_official_content_staging",
            source_id=str(item["id"]),
            signal_date=item.get("publish_date"),
            value=item.get("status") or item.get("content_type") or "scheduled",
            confidence_score=0.9 if target["entity_type"] == "product_family" else 0.7,
            payload={
                "official_account": item.get("official_account"),
                "platform": item.get("normalized_platform") or item.get("platform"),
                "publish_date": item.get("publish_date"),
                "content_type": item.get("content_type"),
                "title": item.get("title"),
                "product": item.get("product"),
                "link": item.get("link"),
                "status": item.get("status"),
                "owner": item.get("owner"),
                "notes": item.get("notes"),
                "source_sheet": item.get("source_sheet"),
                "source_row": item.get("source_row"),
                "official_account_uid": account.get("entity_uid") if account else "",
            },
            batch_uid=batch_uid,
        )
        counters["official_content"] += 1


def build_official_materials(
    conn: Any,
    import_batch_id: int,
    source_scope: str,
    batch_uid: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_official_materials_staging
        WHERE import_batch_id=?
          AND review_status='ready'
        ORDER BY id
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        target = ops["_market_target_for_product"](
            item.get("product_name") or item.get("product_sku") or item.get("launch_ref") or "",
            fallback_topic=item.get("content_description") or item.get("launch_ref") or "official_material",
            topic_kind="official_material",
            source_table="vkpi_legacy_official_materials_staging",
            source_id=str(item["id"]),
        )
        ops["_upsert_market_signal"](
            target=target,
            signal_type="official_material",
            source_ref=f"{source_scope}:official_material:{item['id']}",
            source_table="vkpi_legacy_official_materials_staging",
            source_id=str(item["id"]),
            signal_date=item.get("product_publish_date") or item.get("target_delivery_date") or item.get("request_date"),
            value=item.get("publish_status") or item.get("production_status") or "material",
            confidence_score=0.85 if target["entity_type"] == "product_family" else 0.65,
            payload={
                "launch_ref": item.get("launch_ref"),
                "product_sku": item.get("product_sku"),
                "product_name": item.get("product_name"),
                "owner": item.get("owner"),
                "production_status": item.get("production_status"),
                "project": item.get("project"),
                "content_type": item.get("content_type"),
                "content_description": item.get("content_description"),
                "content_format": item.get("content_format"),
                "publish_status": item.get("publish_status"),
                "product_publish_date": item.get("product_publish_date"),
                "production_team": item.get("production_team"),
                "official_usage_ref": item.get("official_usage_ref"),
                "source_sheet": item.get("source_sheet"),
                "source_row": item.get("source_row"),
            },
            batch_uid=batch_uid,
        )
        counters["official_material"] += 1


def build_voc_alerts(
    conn: Any,
    import_batch_id: int,
    source_scope: str,
    batch_uid: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT *
        FROM vkpi_legacy_voc_alerts_staging
        WHERE import_batch_id=?
          AND review_status='ready'
        ORDER BY id
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        target = ops["_market_target_for_product"](
            item.get("product") or "",
            fallback_topic=item.get("issue_type") or item.get("content") or "voc_alert",
            topic_kind="voc_alert",
            source_table="vkpi_legacy_voc_alerts_staging",
            source_id=str(item["id"]),
            platform=item.get("normalized_platform") or item.get("platform"),
            sentiment=item.get("sentiment"),
        )
        ops["_upsert_market_signal"](
            target=target,
            signal_type="voc_alert",
            source_ref=f"{source_scope}:voc:{item['id']}",
            source_table="vkpi_legacy_voc_alerts_staging",
            source_id=str(item["id"]),
            signal_date=item.get("issue_date"),
            value=item.get("sentiment") or item.get("severity") or "voc",
            confidence_score=0.85 if target["entity_type"] == "product_family" else 0.65,
            payload={
                "platform": item.get("normalized_platform") or item.get("platform"),
                "product": item.get("product"),
                "issue_type": item.get("issue_type"),
                "sentiment": item.get("sentiment"),
                "content": item.get("content"),
                "link": item.get("link"),
                "evidence": item.get("evidence"),
                "issue_date": item.get("issue_date"),
                "severity": item.get("severity"),
                "status": item.get("status"),
                "owner": item.get("owner"),
                "notes": item.get("notes"),
                "source_sheet": item.get("source_sheet"),
                "source_row": item.get("source_row"),
            },
            batch_uid=batch_uid,
        )
        counters["voc_alert"] += 1


def build_all(
    conn: Any,
    import_batch_id: int,
    source_scope: str,
    batch_uid: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    reset_scope(conn, source_scope, counters)
    build_launch_plans(conn, import_batch_id, source_scope, batch_uid, counters, ops)
    build_official_content(conn, import_batch_id, source_scope, batch_uid, counters, ops)
    build_official_materials(conn, import_batch_id, source_scope, batch_uid, counters, ops)
    build_voc_alerts(conn, import_batch_id, source_scope, batch_uid, counters, ops)
