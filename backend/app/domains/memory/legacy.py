"""Legacy batch to Memory v0 build path."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.legacy_import.legacy_import_staging import json_dumps
from app.domains.memory.common import (
    _fetch_batch,
    _load_json,
    _row_to_dict,
    _snapshot_uid,
    _upsert_fact,
    _upsert_link,
    ensure_memory_schema,
    summary,
)
from app.domains.memory.product import _product_entity

def build_memory_from_legacy_batch(batch_uid: str) -> dict[str, Any]:
    """Build Memory v0 facts from active P2D committed refs for a legacy batch."""

    ensure_memory_schema()
    conn = get_conn()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    source_ref_prefix = f"legacy_batch:{batch_uid}"
    counters: Counter[str] = Counter()
    kol_memory_by_legacy_entity_id: dict[int, dict[str, Any]] = {}

    active_refs = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT r.id AS committed_ref_id, r.staging_id AS legacy_entity_id,
                   r.target_id AS kol_pool_id, e.*, p.id AS pool_id,
                   p.platform AS pool_platform, p.handle AS pool_handle,
                   p.display_name AS pool_display_name, p.profile_url AS pool_profile_url,
                   p.country AS pool_country, p.sync_status AS pool_sync_status,
                   p.source_type AS pool_source_type, p.source_ref AS pool_source_ref,
                   p.raw_platform_data AS pool_raw_platform_data
            FROM vkpi_legacy_import_committed_refs r
            JOIN vkpi_legacy_kol_entities e ON e.id=r.staging_id
            JOIN vkpi_kol_pool p ON p.id=CAST(r.target_id AS BIGINT)
            WHERE r.import_batch_id=?
              AND r.pipeline='kol_entities'
              AND r.target_table='vkpi_kol_pool'
              AND r.rollback_status='not_rolled_back'
            ORDER BY e.id
            """,
            (import_batch_id,),
        ).fetchall()
    ]
    if not active_refs:
        raise RuntimeError("no active P2D committed refs found for memory build")

    try:
        for row in active_refs:
            pool_raw = _load_json(row.get("pool_raw_platform_data") or "{}", {})
            identity_key = f"{_text(row.get('pool_platform')).lower()}:{_text(row.get('pool_handle')).lower()}"
            entity = _upsert_entity(
                entity_type="kol",
                identity_key=identity_key,
                display_name=_text(row.get("pool_display_name") or row.get("display_name") or row.get("pool_handle")),
                source_table="vkpi_kol_pool",
                source_id=str(row.get("pool_id")),
                status=_text(row.get("pool_sync_status")) or "imported",
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
            kol_memory_by_legacy_entity_id[int(row["legacy_entity_id"])] = entity
            counters["kol_entities"] += 1
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
                if _text(value):
                    _upsert_fact(
                        entity_id=entity_id,
                        entity_uid=entity_uid,
                        fact_type=fact_type,
                        fact_key=fact_key,
                        value=_text(value),
                        source_ref=base_source_ref,
                        source_table="vkpi_legacy_kol_entities",
                        source_id=str(row["legacy_entity_id"]),
                        fact={"value": _text(value)},
                        source={"batch_uid": batch_uid, "entity_uid": row.get("entity_uid")},
                    )
                    counters["facts"] += 1
            evidence = _load_json(row.get("evidence_json") or "{}", {})
            for count_key in ("kol_profile_rows", "cooperation_rows", "risk_rows", "evidence_count"):
                _upsert_fact(
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

        # Cooperation rows become product experience links.
        cooperation_rows = [
            _row_to_dict(row)
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
        for row in cooperation_rows:
            kol_entity = kol_memory_by_legacy_entity_id.get(int(row["legacy_entity_id"]))
            if not kol_entity:
                continue
            product = _product_entity(
                row.get("product") or row.get("project") or "",
                source_table="vkpi_legacy_cooperations_staging",
                source_id=str(row["staging_id"]),
                metadata={"source_sheet": row.get("source_sheet"), "source_row": row.get("source_row")},
            )
            if not product:
                continue
            counters["product_entities"] += 1
            source_ref = f"{source_ref_prefix}:cooperation:{row['staging_id']}"
            _upsert_link(
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
            _upsert_fact(
                entity_id=int(kol_entity["id"]),
                entity_uid=kol_entity["entity_uid"],
                fact_type="cooperation",
                fact_key=str(row["staging_id"]),
                value=_text(row.get("status") or "legacy_cooperation"),
                source_ref=source_ref,
                source_table="vkpi_legacy_cooperations_staging",
                source_id=str(row["staging_id"]),
                fact=_row_to_dict(row),
                source={"batch_uid": batch_uid},
            )
            counters["facts"] += 1

        risk_rows = [
            _row_to_dict(row)
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
        for row in risk_rows:
            kol_entity = kol_memory_by_legacy_entity_id.get(int(row["legacy_entity_id"]))
            if not kol_entity:
                continue
            source_ref = f"{source_ref_prefix}:risk:{row['staging_id']}"
            _upsert_fact(
                entity_id=int(kol_entity["id"]),
                entity_uid=kol_entity["entity_uid"],
                fact_type="risk_flag",
                fact_key=_text(row.get("risk_type") or row.get("severity") or row["staging_id"]),
                value=_text(row.get("severity") or "risk"),
                source_ref=source_ref,
                source_table="vkpi_legacy_risk_watchlist_staging",
                source_id=str(row["staging_id"]),
                confidence_score=0.9,
                fact=_row_to_dict(row),
                source={"batch_uid": batch_uid},
            )
            counters["facts"] += 1
            counters["risk_facts"] += 1

        for row in conn.execute(
            """
            SELECT id, product_name, product_sku, launch_name, launch_date,
                   target_region, status, source_sheet, source_row
            FROM vkpi_legacy_launch_plans_staging
            WHERE import_batch_id=?
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            product = _product_entity(
                item.get("product_name") or item.get("launch_name") or item.get("product_sku") or "",
                source_table="vkpi_legacy_launch_plans_staging",
                source_id=str(item["id"]),
                metadata={"source_sheet": item.get("source_sheet"), "source_row": item.get("source_row")},
            )
            if not product:
                continue
            source_ref = f"{source_ref_prefix}:launch:{item['id']}"
            _upsert_fact(
                entity_id=int(product["id"]),
                entity_uid=product["entity_uid"],
                fact_type="launch_plan",
                fact_key=str(item["id"]),
                value=_text(item.get("status") or "planned"),
                source_ref=source_ref,
                source_table="vkpi_legacy_launch_plans_staging",
                source_id=str(item["id"]),
                fact=item,
                source={"batch_uid": batch_uid},
            )
            counters["facts"] += 1
            counters["launch_facts"] += 1

        for row in conn.execute(
            """
            SELECT id, sku, product_name, cost, currency, region,
                   effective_date, source_sheet, source_row
            FROM vkpi_legacy_product_costs_staging
            WHERE import_batch_id=?
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            product = _product_entity(
                item.get("product_name") or item.get("sku") or "",
                source_table="vkpi_legacy_product_costs_staging",
                source_id=str(item["id"]),
                metadata={"sku": item.get("sku"), "source_sheet": item.get("source_sheet"), "source_row": item.get("source_row")},
            )
            if not product:
                continue
            source_ref = f"{source_ref_prefix}:product_cost:{item['id']}"
            _upsert_fact(
                entity_id=int(product["id"]),
                entity_uid=product["entity_uid"],
                fact_type="product_cost",
                fact_key=_text(item.get("region") or item.get("sku") or item["id"]),
                value=str(item.get("cost") or ""),
                source_ref=source_ref,
                source_table="vkpi_legacy_product_costs_staging",
                source_id=str(item["id"]),
                fact=item,
                source={"batch_uid": batch_uid},
            )
            counters["facts"] += 1
            counters["cost_facts"] += 1

        snapshot_uid = _snapshot_uid("legacy_batch_memory", batch_uid)
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
                json_dumps(dict(counters)),
                json_dumps({"batch_uid": batch_uid, "import_batch_id": import_batch_id}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return summary(source_ref=source_ref_prefix) | {
        "batch_uid": batch_uid,
        "snapshot_uid": snapshot_uid,
        "build_counts": dict(counters),
    }


