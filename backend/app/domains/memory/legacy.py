"""Legacy batch to Memory v0 build path."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.legacy_import.legacy_import_staging import json_dumps
from app.domains.memory import legacy_build
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
        snapshot_uid = legacy_build.build_all(
            conn,
            active_refs,
            batch_uid,
            import_batch_id,
            source_ref_prefix,
            counters,
            kol_memory_by_legacy_entity_id,
            globals(),
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
