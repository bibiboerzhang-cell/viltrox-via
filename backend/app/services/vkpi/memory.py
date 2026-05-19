"""V-KPI Memory v0 service.

Memory v0 stores explainable facts from committed operational data. It is not a
vector store and does not train models; P4+ can read these facts directly.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _text
from app.services.vkpi.legacy_import_staging import ensure_legacy_staging_schema, json_dumps


def ensure_memory_schema() -> None:
    ensure_legacy_staging_schema()


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    return hashlib.sha1(_text(value).lower().encode("utf-8")).hexdigest()[:20]


def _entity_uid(entity_type: str, identity_key: str) -> str:
    return f"mem_{entity_type}_{_slug(identity_key)}"


def _fact_uid(entity_uid: str, fact_type: str, fact_key: str, source_ref: str) -> str:
    return "mem_fact_" + _slug(f"{entity_uid}:{fact_type}:{fact_key}:{source_ref}")


def _link_uid(source_uid: str, target_uid: str, link_type: str, source_ref: str) -> str:
    return "mem_link_" + _slug(f"{source_uid}:{target_uid}:{link_type}:{source_ref}")


def _snapshot_uid(scope: str, source_ref: str) -> str:
    return "mem_snapshot_" + _slug(f"{scope}:{source_ref}:{_utcnow()}")


def _fetch_batch(batch_uid: str) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM vkpi_legacy_import_batches WHERE batch_uid=?", (batch_uid,)).fetchone()
    if not row:
        raise ValueError(f"batch not found: {batch_uid}")
    return _row_to_dict(row)


def _upsert_entity(
    *,
    entity_type: str,
    identity_key: str,
    display_name: str,
    source_table: str,
    source_id: str,
    status: str = "active",
    confidence_score: float = 1.0,
    identity: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uid = _entity_uid(entity_type, identity_key)
    now = _utcnow()
    row = get_conn().execute(
        """
        INSERT INTO vkpi_memory_entities (
          entity_uid, entity_type, identity_key, display_name, source_table,
          source_id, status, confidence_score, identity_json, metadata_json,
          first_seen_at, last_seen_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, identity_key) DO UPDATE SET
          display_name=COALESCE(NULLIF(excluded.display_name, ''), vkpi_memory_entities.display_name),
          source_table=excluded.source_table,
          source_id=excluded.source_id,
          status=excluded.status,
          confidence_score=excluded.confidence_score,
          identity_json=excluded.identity_json,
          metadata_json=excluded.metadata_json,
          last_seen_at=excluded.last_seen_at,
          updated_at=excluded.updated_at
        RETURNING *
        """,
        (
            uid,
            entity_type,
            identity_key,
            display_name,
            source_table,
            source_id,
            status,
            float(confidence_score),
            json_dumps(identity or {}),
            json_dumps(metadata or {}),
            now,
            now,
            now,
            now,
        ),
    ).fetchone()
    return _row_to_dict(row)


def _upsert_fact(
    *,
    entity_id: int,
    entity_uid: str,
    fact_type: str,
    fact_key: str,
    value: str,
    source_ref: str,
    source_table: str,
    source_id: str,
    confidence_score: float = 1.0,
    fact: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uid = _fact_uid(entity_uid, fact_type, fact_key, source_ref)
    now = _utcnow()
    row = get_conn().execute(
        """
        INSERT INTO vkpi_memory_facts (
          fact_uid, entity_id, fact_type, fact_key, fact_value_text,
          confidence_score, source_ref, source_table, source_id, fact_json,
          source_json, metadata_json, observed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_id, fact_type, fact_key, source_ref) DO UPDATE SET
          fact_value_text=excluded.fact_value_text,
          confidence_score=excluded.confidence_score,
          fact_json=excluded.fact_json,
          source_json=excluded.source_json,
          metadata_json=excluded.metadata_json,
          observed_at=excluded.observed_at,
          updated_at=excluded.updated_at
        RETURNING *
        """,
        (
            uid,
            int(entity_id),
            fact_type,
            fact_key,
            value,
            float(confidence_score),
            source_ref,
            source_table,
            source_id,
            json_dumps(fact or {}),
            json_dumps(source or {}),
            json_dumps(metadata or {}),
            now,
            now,
            now,
        ),
    ).fetchone()
    return _row_to_dict(row)


def _upsert_link(
    *,
    source_entity_id: int,
    source_entity_uid: str,
    target_entity_id: int,
    target_entity_uid: str,
    link_type: str,
    source_ref: str,
    weight: float = 1.0,
    confidence_score: float = 1.0,
    source: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uid = _link_uid(source_entity_uid, target_entity_uid, link_type, source_ref)
    now = _utcnow()
    row = get_conn().execute(
        """
        INSERT INTO vkpi_memory_links (
          link_uid, source_entity_id, target_entity_id, link_type, weight,
          confidence_score, source_ref, source_json, metadata_json, observed_at,
          created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_entity_id, target_entity_id, link_type, source_ref) DO UPDATE SET
          weight=excluded.weight,
          confidence_score=excluded.confidence_score,
          source_json=excluded.source_json,
          metadata_json=excluded.metadata_json,
          observed_at=excluded.observed_at,
          updated_at=excluded.updated_at
        RETURNING *
        """,
        (
            uid,
            int(source_entity_id),
            int(target_entity_id),
            link_type,
            float(weight),
            float(confidence_score),
            source_ref,
            json_dumps(source or {}),
            json_dumps(metadata or {}),
            now,
            now,
            now,
        ),
    ).fetchone()
    return _row_to_dict(row)


def _product_key(value: str) -> str:
    return _text(value).lower()


def _product_entity(product_name: str, *, source_table: str, source_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
    name = _text(product_name)
    if not name:
        return None
    return _upsert_entity(
        entity_type="product",
        identity_key=_product_key(name),
        display_name=name,
        source_table=source_table,
        source_id=source_id,
        identity={"product_name": name},
        metadata=metadata or {},
    )


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


def summary(*, source_ref: str = "") -> dict[str, Any]:
    ensure_memory_schema()
    conn = get_conn()
    params: list[Any] = []
    source_clause = ""
    if source_ref:
        source_clause = " WHERE source_ref LIKE ?"
        params.append(f"{source_ref}%")
    entity_rows = conn.execute(
        "SELECT entity_type, COUNT(*) AS n FROM vkpi_memory_entities GROUP BY entity_type ORDER BY entity_type"
    ).fetchall()
    fact_rows = conn.execute(
        f"SELECT fact_type, COUNT(*) AS n FROM vkpi_memory_facts{source_clause} GROUP BY fact_type ORDER BY fact_type",
        params,
    ).fetchall()
    link_total = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_memory_links{source_clause}",
        params,
    ).fetchone()["n"]
    snapshot_total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_memory_snapshots").fetchone()["n"]
    return {
        "source_ref": source_ref,
        "entities": {row["entity_type"]: int(row["n"]) for row in entity_rows},
        "facts": {row["fact_type"]: int(row["n"]) for row in fact_rows},
        "links": int(link_total),
        "snapshots": int(snapshot_total),
    }


def list_entities(*, entity_type: str = "", query: str = "", limit: int = 100) -> dict[str, Any]:
    ensure_memory_schema()
    where: list[str] = []
    params: list[Any] = []
    if _text(entity_type):
        where.append("entity_type=?")
        params.append(_text(entity_type))
    if _text(query):
        where.append("(lower(display_name) LIKE ? OR lower(identity_key) LIKE ?)")
        like = f"%{_text(query).lower()}%"
        params.extend([like, like])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            f"""
            SELECT *
            FROM vkpi_memory_entities
            {clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, max(1, min(500, int(limit or 100)))),
        ).fetchall()
    ]
    return {"items": [_public_entity(row) for row in rows]}


def entity_facts(entity_uid: str, *, limit: int = 200) -> dict[str, Any]:
    ensure_memory_schema()
    entity = get_conn().execute("SELECT * FROM vkpi_memory_entities WHERE entity_uid=?", (entity_uid,)).fetchone()
    if not entity:
        raise LookupError("memory entity not found")
    entity_row = _row_to_dict(entity)
    facts = [
        _public_fact(_row_to_dict(row))
        for row in get_conn().execute(
            """
            SELECT *
            FROM vkpi_memory_facts
            WHERE entity_id=?
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (int(entity_row["id"]), max(1, min(500, int(limit or 200)))),
        ).fetchall()
    ]
    links = [
        _row_to_dict(row)
        for row in get_conn().execute(
            """
            SELECT l.*, t.entity_uid AS target_uid, t.entity_type AS target_type,
                   t.display_name AS target_display_name
            FROM vkpi_memory_links l
            JOIN vkpi_memory_entities t ON t.id=l.target_entity_id
            WHERE l.source_entity_id=?
            ORDER BY l.observed_at DESC, l.id DESC
            LIMIT ?
            """,
            (int(entity_row["id"]), max(1, min(500, int(limit or 200)))),
        ).fetchall()
    ]
    return {"entity": _public_entity(entity_row), "facts": facts, "links": [_public_link(row) for row in links]}


def record_feedback(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_memory_schema()
    entity_uid = _text(body.get("entity_uid"))
    entity_id = None
    if entity_uid:
        row = get_conn().execute("SELECT id FROM vkpi_memory_entities WHERE entity_uid=?", (entity_uid,)).fetchone()
        if row:
            entity_id = int(row["id"])
    feedback_type = _text(body.get("feedback_type")) or "note"
    uid = "mem_feedback_" + _slug(f"{entity_uid}:{feedback_type}:{_utcnow()}:{body}")
    staff_id = None
    if staff:
        staff_id = staff.get("id") or staff.get("staff_id")
    row = get_conn().execute(
        """
        INSERT INTO vkpi_memory_feedback (
          feedback_uid, entity_id, feedback_type, rating, status,
          created_by_staff_id, feedback_json, metadata_json
        ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
        RETURNING *
        """,
        (
            uid,
            entity_id,
            feedback_type,
            int(body["rating"]) if str(body.get("rating") or "").strip().lstrip("-").isdigit() else None,
            int(staff_id) if staff_id else None,
            json_dumps(body),
            json_dumps({"staff": staff or {}}),
        ),
    ).fetchone()
    get_conn().commit()
    return {"item": _public_feedback(_row_to_dict(row))}


def _public_entity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "entity_uid": row["entity_uid"],
        "entity_type": row["entity_type"],
        "identity_key": row["identity_key"],
        "display_name": row.get("display_name") or "",
        "status": row.get("status") or "",
        "confidence_score": float(row.get("confidence_score") or 0),
        "identity": _load_json(row.get("identity_json") or "{}", {}),
        "metadata": _load_json(row.get("metadata_json") or "{}", {}),
        "updated_at": row.get("updated_at"),
    }


def _public_fact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_uid": row["fact_uid"],
        "fact_type": row["fact_type"],
        "fact_key": row.get("fact_key") or "",
        "value": row.get("fact_value_text") or "",
        "confidence_score": float(row.get("confidence_score") or 0),
        "source_ref": row.get("source_ref") or "",
        "fact": _load_json(row.get("fact_json") or "{}", {}),
        "source": _load_json(row.get("source_json") or "{}", {}),
        "metadata": _load_json(row.get("metadata_json") or "{}", {}),
        "observed_at": row.get("observed_at"),
    }


def _public_link(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_uid": row["link_uid"],
        "link_type": row["link_type"],
        "target_uid": row.get("target_uid") or "",
        "target_type": row.get("target_type") or "",
        "target_display_name": row.get("target_display_name") or "",
        "weight": float(row.get("weight") or 0),
        "confidence_score": float(row.get("confidence_score") or 0),
        "source_ref": row.get("source_ref") or "",
        "metadata": _load_json(row.get("metadata_json") or "{}", {}),
    }


def _public_feedback(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "feedback_uid": row["feedback_uid"],
        "entity_id": row.get("entity_id"),
        "feedback_type": row.get("feedback_type") or "",
        "rating": row.get("rating"),
        "status": row.get("status") or "",
        "feedback": _load_json(row.get("feedback_json") or "{}", {}),
        "created_at": row.get("created_at"),
    }
