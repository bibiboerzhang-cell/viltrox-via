"""Transactional phases for the legacy-batch Memory v0 builder."""
from __future__ import annotations

from typing import Any


def _upsert_entity(ops: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    try:
        operation = ops["_upsert_entity"]
    except KeyError:
        raise NameError("name '_upsert_entity' is not defined") from None
    return operation(**kwargs)


def build_kol_entities(
    active_refs: list[dict[str, Any]],
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    entity_map: dict[int, dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    for row in active_refs:
        pool_raw = ops["_load_json"](row.get("pool_raw_platform_data") or "{}", {})
        identity_key = (
            f"{ops['_text'](row.get('pool_platform')).lower()}:"
            f"{ops['_text'](row.get('pool_handle')).lower()}"
        )
        entity = _upsert_entity(
            ops,
            entity_type="kol",
            identity_key=identity_key,
            display_name=ops["_text"](
                row.get("pool_display_name") or row.get("display_name") or row.get("pool_handle")
            ),
            source_table="vkpi_kol_pool",
            source_id=str(row.get("pool_id")),
            status=ops["_text"](row.get("pool_sync_status")) or "imported",
            confidence_score=float(row.get("confidence_score") or 1.0),
            identity={
                "platform": row.get("pool_platform"),
                "handle": row.get("pool_handle"),
                "profile_url": row.get("pool_profile_url"),
                "country": row.get("pool_country"),
                "source_type": row.get("pool_source_type"),
                "source_ref": row.get("pool_source_ref"),
            },
            metadata={
                "batch_uid": batch_uid,
                "legacy_entity_uid": row.get("entity_uid"),
                "weak_label": row.get("weak_label"),
                "review_state": pool_raw.get("review_state") if isinstance(pool_raw, dict) else "",
            },
        )
        entity_map[int(row["legacy_entity_id"])] = entity
        counters["kol_entities"] += 1
        _build_kol_facts(row, entity, pool_raw, batch_uid, source_ref_prefix, counters, ops)


def _build_kol_facts(
    row: dict[str, Any],
    entity: dict[str, Any],
    pool_raw: Any,
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    entity_uid = entity["entity_uid"]
    entity_id = int(entity["id"])
    base_source_ref = f"{source_ref_prefix}:entity:{row.get('entity_uid')}"
    for fact_type, fact_key, value in (
        ("sync_status", "current", row.get("pool_sync_status")),
        ("weak_label", "p2c", row.get("weak_label")),
        ("review_state", "p2d", pool_raw.get("review_state") if isinstance(pool_raw, dict) else ""),
        ("contact_status", "legacy", row.get("contact_status")),
        ("country", "profile", row.get("pool_country")),
    ):
        if ops["_text"](value):
            ops["_upsert_fact"](
                entity_id=entity_id,
                entity_uid=entity_uid,
                fact_type=fact_type,
                fact_key=fact_key,
                value=ops["_text"](value),
                source_ref=base_source_ref,
                source_table="vkpi_legacy_kol_entities",
                source_id=str(row["legacy_entity_id"]),
                fact={"value": ops["_text"](value)},
                source={"batch_uid": batch_uid, "entity_uid": row.get("entity_uid")},
            )
            counters["facts"] += 1
    evidence = ops["_load_json"](row.get("evidence_json") or "{}", {})
    for count_key in ("kol_profile_rows", "cooperation_rows", "risk_rows", "evidence_count"):
        ops["_upsert_fact"](
            entity_id=entity_id,
            entity_uid=entity_uid,
            fact_type="evidence_count",
            fact_key=count_key,
            value=str(int(row.get(count_key) or 0)),
            source_ref=base_source_ref,
            source_table="vkpi_legacy_kol_entities",
            source_id=str(row["legacy_entity_id"]),
            fact={"count": int(row.get(count_key) or 0)},
            source={"evidence": evidence},
        )
        counters["facts"] += 1


def build_cooperations(
    conn: Any,
    import_batch_id: int,
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    entity_map: dict[int, dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    rows = [
        ops["_row_to_dict"](row)
        for row in conn.execute(
            """
            SELECT ref.entity_id AS legacy_entity_id, ref.staging_id, c.source_sheet,
                   c.source_row, c.product, c.project, c.status, c.cooperation_date,
                   c.cost_amount, c.cost_currency, c.content_link, c.result
            FROM vkpi_legacy_kol_entity_refs ref
            JOIN vkpi_legacy_cooperations_staging c ON c.id=ref.staging_id
            WHERE ref.import_batch_id=? AND ref.pipeline='cooperations'
            ORDER BY ref.entity_id, c.id
            """,
            (import_batch_id,),
        ).fetchall()
    ]
    for row in rows:
        kol_entity = entity_map.get(int(row["legacy_entity_id"]))
        if not kol_entity:
            continue
        product = ops["_product_entity"](
            row.get("product") or row.get("project") or "",
            source_table="vkpi_legacy_cooperations_staging",
            source_id=str(row["staging_id"]),
            metadata={"source_sheet": row.get("source_sheet"), "source_row": row.get("source_row")},
        )
        if not product:
            continue
        counters["product_entities"] += 1
        source_ref = f"{source_ref_prefix}:cooperation:{row['staging_id']}"
        ops["_upsert_link"](
            source_entity_id=int(kol_entity["id"]),
            source_entity_uid=kol_entity["entity_uid"],
            target_entity_id=int(product["id"]),
            target_entity_uid=product["entity_uid"],
            link_type="worked_on_product",
            source_ref=source_ref,
            weight=1.0,
            confidence_score=0.9,
            source={"source_sheet": row.get("source_sheet"), "source_row": row.get("source_row")},
            metadata={"status": row.get("status"), "project": row.get("project"), "content_link": row.get("content_link")},
        )
        counters["links"] += 1
        ops["_upsert_fact"](
            entity_id=int(kol_entity["id"]),
            entity_uid=kol_entity["entity_uid"],
            fact_type="cooperation",
            fact_key=str(row["staging_id"]),
            value=ops["_text"](row.get("status") or "legacy_cooperation"),
            source_ref=source_ref,
            source_table="vkpi_legacy_cooperations_staging",
            source_id=str(row["staging_id"]),
            fact=ops["_row_to_dict"](row),
            source={"batch_uid": batch_uid},
        )
        counters["facts"] += 1


def build_risks(
    conn: Any,
    import_batch_id: int,
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    entity_map: dict[int, dict[str, Any]],
    ops: dict[str, Any],
) -> None:
    rows = [
        ops["_row_to_dict"](row)
        for row in conn.execute(
            """
            SELECT ref.entity_id AS legacy_entity_id, ref.staging_id, r.source_sheet,
                   r.source_row, r.risk_type, r.risk_reason, r.severity,
                   r.evidence, r.status
            FROM vkpi_legacy_kol_entity_refs ref
            JOIN vkpi_legacy_risk_watchlist_staging r ON r.id=ref.staging_id
            WHERE ref.import_batch_id=? AND ref.pipeline='risk_watchlist'
            ORDER BY ref.entity_id, r.id
            """,
            (import_batch_id,),
        ).fetchall()
    ]
    for row in rows:
        kol_entity = entity_map.get(int(row["legacy_entity_id"]))
        if not kol_entity:
            continue
        source_ref = f"{source_ref_prefix}:risk:{row['staging_id']}"
        ops["_upsert_fact"](
            entity_id=int(kol_entity["id"]),
            entity_uid=kol_entity["entity_uid"],
            fact_type="risk_flag",
            fact_key=ops["_text"](row.get("risk_type") or row.get("severity") or row["staging_id"]),
            value=ops["_text"](row.get("severity") or "risk"),
            source_ref=source_ref,
            source_table="vkpi_legacy_risk_watchlist_staging",
            source_id=str(row["staging_id"]),
            confidence_score=0.9,
            fact=ops["_row_to_dict"](row),
            source={"batch_uid": batch_uid},
        )
        counters["facts"] += 1
        counters["risk_facts"] += 1


def build_launches(
    conn: Any,
    import_batch_id: int,
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT id, product_name, product_sku, launch_name, launch_date,
               target_region, status, source_sheet, source_row
        FROM vkpi_legacy_launch_plans_staging
        WHERE import_batch_id=?
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        product = ops["_product_entity"](
            item.get("product_name") or item.get("launch_name") or item.get("product_sku") or "",
            source_table="vkpi_legacy_launch_plans_staging",
            source_id=str(item["id"]),
            metadata={"source_sheet": item.get("source_sheet"), "source_row": item.get("source_row")},
        )
        if not product:
            continue
        source_ref = f"{source_ref_prefix}:launch:{item['id']}"
        ops["_upsert_fact"](
            entity_id=int(product["id"]),
            entity_uid=product["entity_uid"],
            fact_type="launch_plan",
            fact_key=str(item["id"]),
            value=ops["_text"](item.get("status") or "planned"),
            source_ref=source_ref,
            source_table="vkpi_legacy_launch_plans_staging",
            source_id=str(item["id"]),
            fact=item,
            source={"batch_uid": batch_uid},
        )
        counters["facts"] += 1
        counters["launch_facts"] += 1


def build_product_costs(
    conn: Any,
    import_batch_id: int,
    batch_uid: str,
    source_ref_prefix: str,
    counters: Any,
    ops: dict[str, Any],
) -> None:
    rows = conn.execute(
        """
        SELECT id, sku, product_name, cost, currency, region,
               effective_date, source_sheet, source_row
        FROM vkpi_legacy_product_costs_staging
        WHERE import_batch_id=?
        """,
        (import_batch_id,),
    ).fetchall()
    for row in rows:
        item = ops["_row_to_dict"](row)
        product = ops["_product_entity"](
            item.get("product_name") or item.get("sku") or "",
            source_table="vkpi_legacy_product_costs_staging",
            source_id=str(item["id"]),
            metadata={
                "sku": item.get("sku"),
                "source_sheet": item.get("source_sheet"),
                "source_row": item.get("source_row"),
            },
        )
        if not product:
            continue
        source_ref = f"{source_ref_prefix}:product_cost:{item['id']}"
        ops["_upsert_fact"](
            entity_id=int(product["id"]),
            entity_uid=product["entity_uid"],
            fact_type="product_cost",
            fact_key=ops["_text"](item.get("region") or item.get("sku") or item["id"]),
            value=str(item.get("cost") or ""),
            source_ref=source_ref,
            source_table="vkpi_legacy_product_costs_staging",
            source_id=str(item["id"]),
            fact=item,
            source={"batch_uid": batch_uid},
        )
        counters["facts"] += 1
        counters["cost_facts"] += 1


def write_snapshot(
    conn: Any,
    batch_uid: str,
    import_batch_id: int,
    source_ref_prefix: str,
    counters: Any,
    ops: dict[str, Any],
) -> str:
    snapshot_uid = ops["_snapshot_uid"]("legacy_batch_memory", batch_uid)
    conn.execute(
        """
        INSERT INTO vkpi_memory_snapshots (
          snapshot_uid, scope, source_ref, status, entity_count, fact_count,
          link_count, feedback_count, summary_json, metadata_json
        ) VALUES (?, 'legacy_batch_memory', ?, 'completed', ?, ?, ?, 0, ?, ?)
        """,
        (
            snapshot_uid,
            source_ref_prefix,
            int(counters["kol_entities"] + counters["product_entities"]),
            int(counters["facts"]),
            int(counters["links"]),
            ops["json_dumps"](dict(counters)),
            ops["json_dumps"]({"batch_uid": batch_uid, "import_batch_id": import_batch_id}),
        ),
    )
    return snapshot_uid


def build_all(
    conn: Any,
    active_refs: list[dict[str, Any]],
    batch_uid: str,
    import_batch_id: int,
    source_ref_prefix: str,
    counters: Any,
    entity_map: dict[int, dict[str, Any]],
    ops: dict[str, Any],
) -> str:
    build_kol_entities(active_refs, batch_uid, source_ref_prefix, counters, entity_map, ops)
    build_cooperations(conn, import_batch_id, batch_uid, source_ref_prefix, counters, entity_map, ops)
    build_risks(conn, import_batch_id, batch_uid, source_ref_prefix, counters, entity_map, ops)
    build_launches(conn, import_batch_id, batch_uid, source_ref_prefix, counters, ops)
    build_product_costs(conn, import_batch_id, batch_uid, source_ref_prefix, counters, ops)
    return write_snapshot(conn, batch_uid, import_batch_id, source_ref_prefix, counters, ops)
