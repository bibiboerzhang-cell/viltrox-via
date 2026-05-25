"""KOL/product matching read surfaces backed by Memory v0."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.memory.common import (
    _kol_feature_summary,
    _load_json,
    _memory_candidate_score,
    _memory_entity_by_uid,
    _public_entity,
    _public_fact,
    _public_link,
    _public_product_link,
    _row_to_dict,
    _safe_limit,
    ensure_memory_schema,
)

def _matched_product_rows(query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = get_conn()
    like = f"%{query.lower()}%"
    products_by_id: dict[int, dict[str, Any]] = {}
    raw_products = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT p.*, NULL AS family_uid, NULL AS family_display_name,
                   (
                     SELECT COUNT(*)
                     FROM vkpi_memory_links l
                     WHERE l.target_entity_id=p.id
                       AND l.link_type='worked_on_product'
                   ) AS link_count
            FROM vkpi_memory_entities p
            WHERE p.entity_type='product'
              AND (lower(p.display_name) LIKE ? OR lower(p.identity_key) LIKE ?)
            ORDER BY link_count DESC, p.updated_at DESC, p.id DESC
            LIMIT 30
            """,
            (like, like),
        ).fetchall()
    ]
    for product in raw_products:
        products_by_id[int(product["id"])] = product

    family_rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT f.*,
                   COUNT(DISTINCT nl.source_entity_id) AS member_count,
                   COUNT(w.id) AS cooperation_count
            FROM vkpi_memory_entities f
            LEFT JOIN vkpi_memory_links nl
              ON nl.target_entity_id=f.id
             AND nl.link_type='normalized_to_product_family'
            LEFT JOIN vkpi_memory_links w
              ON w.target_entity_id=nl.source_entity_id
             AND w.link_type='worked_on_product'
            WHERE f.entity_type='product_family'
              AND (lower(f.display_name) LIKE ? OR lower(f.identity_key) LIKE ?)
            GROUP BY f.id
            ORDER BY cooperation_count DESC, member_count DESC, f.display_name
            LIMIT 20
            """,
            (like, like),
        ).fetchall()
    ]
    for family in family_rows:
        for row in conn.execute(
            """
            SELECT p.*, f.entity_uid AS family_uid, f.display_name AS family_display_name,
                   (
                     SELECT COUNT(*)
                     FROM vkpi_memory_links w
                     WHERE w.target_entity_id=p.id
                       AND w.link_type='worked_on_product'
                   ) AS link_count
            FROM vkpi_memory_links nl
            JOIN vkpi_memory_entities p ON p.id=nl.source_entity_id
            JOIN vkpi_memory_entities f ON f.id=nl.target_entity_id
            WHERE nl.target_entity_id=?
              AND nl.link_type='normalized_to_product_family'
            ORDER BY link_count DESC, p.display_name
            LIMIT 80
            """,
            (int(family["id"]),),
        ).fetchall():
            product = _row_to_dict(row)
            products_by_id.setdefault(int(product["id"]), product)

    product_rows = sorted(
        products_by_id.values(),
        key=lambda row: (int(row.get("link_count") or 0), _text(row.get("display_name"))),
        reverse=True,
    )
    return product_rows, family_rows


def product_kol_candidates(*, product_query: str, limit: int = 50) -> dict[str, Any]:
    """Return explainable KOL memory evidence for a product query.

    This is not a recommendation engine. It exposes historical product links and
    risk/review signals so P4 can consume a deterministic feature surface.
    """

    ensure_memory_schema()
    query = _text(product_query)
    if not query:
        raise ValueError("product_query is required")
    safe_limit = _safe_limit(limit, default=50, max_limit=200)
    conn = get_conn()
    product_rows, family_rows = _matched_product_rows(query)

    candidates_by_id: dict[int, dict[str, Any]] = {}
    for product in product_rows:
        product_id = int(product["id"])
        kol_rows = [
            _row_to_dict(row)
            for row in conn.execute(
                """
                SELECT k.*,
                       (
                         SELECT COUNT(*)
                         FROM vkpi_memory_links l2
                         WHERE l2.source_entity_id=k.id
                           AND l2.target_entity_id=?
                           AND l2.link_type='worked_on_product'
                       ) AS cooperation_count,
                       (
                         SELECT MAX(l3.observed_at)
                         FROM vkpi_memory_links l3
                         WHERE l3.source_entity_id=k.id
                           AND l3.target_entity_id=?
                           AND l3.link_type='worked_on_product'
                       ) AS last_observed_at
                FROM vkpi_memory_entities k
                WHERE k.entity_type='kol'
                  AND k.id IN (
                    SELECT DISTINCT source_entity_id
                    FROM vkpi_memory_links
                    WHERE target_entity_id=?
                      AND link_type='worked_on_product'
                  )
                ORDER BY cooperation_count DESC, k.updated_at DESC, k.id DESC
                LIMIT 300
                """,
                (product_id, product_id, product_id),
            ).fetchall()
        ]
        public_product = _public_entity(product)
        for kol in kol_rows:
            kol_id = int(kol["id"])
            item = candidates_by_id.setdefault(
                kol_id,
                {
                    "entity": _public_entity(kol),
                    "matched_products": [],
                    "matched_product_count": 0,
                    "matched_cooperation_count": 0,
                    "last_observed_at": kol.get("last_observed_at"),
                },
            )
            item["matched_products"].append(
                {
                    "entity_uid": public_product["entity_uid"],
                    "display_name": public_product["display_name"],
                    "identity_key": public_product["identity_key"],
                    "link_count": int(product.get("link_count") or 0),
                    "cooperation_count": int(kol.get("cooperation_count") or 0),
                    "family_uid": product.get("family_uid") or "",
                    "family_display_name": product.get("family_display_name") or "",
                }
            )
            item["matched_product_count"] = len(item["matched_products"])
            item["matched_cooperation_count"] += int(kol.get("cooperation_count") or 0)
            if kol.get("last_observed_at"):
                item["last_observed_at"] = kol.get("last_observed_at")

    items: list[dict[str, Any]] = []
    for kol_id, item in candidates_by_id.items():
        features = _kol_feature_summary(kol_id)
        score = _memory_candidate_score(
            cooperation_count=int(item["matched_cooperation_count"]),
            matched_product_count=int(item["matched_product_count"]),
            risk_flag_count=int(features["risk_flag_count"]),
            evidence_count=int(features["evidence_count"]),
            review_state=str(features["review_state"]),
            sync_status=str(features["sync_status"]),
            contact_status=str(features["contact_status"]),
        )
        items.append(
            {
                **item,
                "features": features,
                "memory_score": score["score"],
                "score_breakdown": score["breakdown"],
                "reasons": score["reasons"],
                "warnings": score["warnings"],
            }
        )

    items.sort(
        key=lambda item: (
            int(item["memory_score"]),
            int(item["matched_cooperation_count"]),
            int(item["matched_product_count"]),
        ),
        reverse=True,
    )
    return {
        "product_query": query,
        "matched_products": [_public_entity(row) | {"link_count": int(row.get("link_count") or 0)} for row in product_rows],
        "matched_families": [
            _public_entity(row)
            | {
                "member_count": int(row.get("member_count") or 0),
                "cooperation_count": int(row.get("cooperation_count") or 0),
            }
            for row in family_rows
        ],
        "items": items[:safe_limit],
        "total": len(items),
    }


def kol_product_memory(entity_uid: str, *, limit: int = 50) -> dict[str, Any]:
    ensure_memory_schema()
    entity = _memory_entity_by_uid(entity_uid)
    if not entity:
        raise LookupError("memory entity not found")
    if entity.get("entity_type") != "kol":
        raise ValueError("entity is not a KOL memory entity")
    safe_limit = _safe_limit(limit, default=50, max_limit=300)
    conn = get_conn()
    links = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT l.*, t.entity_uid AS target_uid, t.entity_type AS target_type,
                   t.identity_key AS target_identity_key,
                   t.display_name AS target_display_name,
                   t.identity_json AS target_identity_json,
                   t.metadata_json AS target_metadata_json
            FROM vkpi_memory_links l
            JOIN vkpi_memory_entities t ON t.id=l.target_entity_id
            WHERE l.source_entity_id=?
              AND l.link_type='worked_on_product'
            ORDER BY l.observed_at DESC, l.id DESC
            LIMIT ?
            """,
            (int(entity["id"]), safe_limit),
        ).fetchall()
    ]
    facts = _kol_feature_summary(int(entity["id"]))
    cooperation_facts = [
        _public_fact(_row_to_dict(row))
        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_memory_facts
            WHERE entity_id=?
              AND fact_type='cooperation'
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (int(entity["id"]), safe_limit),
        ).fetchall()
    ]
    return {
        "entity": _public_entity(entity),
        "features": facts,
        "product_links": [_public_product_link(row) for row in links],
        "cooperation_facts": cooperation_facts,
    }


def fit_features(entity_uid: str, *, product_query: str = "") -> dict[str, Any]:
    ensure_memory_schema()
    entity = _memory_entity_by_uid(entity_uid)
    if not entity:
        raise LookupError("memory entity not found")
    if entity.get("entity_type") != "kol":
        raise ValueError("entity is not a KOL memory entity")

    query = _text(product_query)
    product_match_count = 0
    product_cooperation_count = 0
    if query:
        product_rows, _family_rows = _matched_product_rows(query)
        product_ids = [int(row["id"]) for row in product_rows]
        if product_ids:
            placeholders = ",".join("?" for _ in product_ids)
            row = get_conn().execute(
                f"""
                SELECT COUNT(DISTINCT target_entity_id) AS product_count,
                       COUNT(id) AS cooperation_count
                FROM vkpi_memory_links
                WHERE source_entity_id=?
                  AND link_type='worked_on_product'
                  AND target_entity_id IN ({placeholders})
                """,
                (int(entity["id"]), *product_ids),
            ).fetchone()
            if row:
                product_match_count = int(row["product_count"] or 0)
                product_cooperation_count = int(row["cooperation_count"] or 0)

    features = _kol_feature_summary(int(entity["id"]))
    score = _memory_candidate_score(
        cooperation_count=product_cooperation_count,
        matched_product_count=product_match_count,
        risk_flag_count=int(features["risk_flag_count"]),
        evidence_count=int(features["evidence_count"]),
        review_state=str(features["review_state"]),
        sync_status=str(features["sync_status"]),
        contact_status=str(features["contact_status"]),
    )
    return {
        "entity": _public_entity(entity),
        "product_query": query,
        "features": {
            **features,
            "matched_product_count": product_match_count,
            "matched_product_cooperation_count": product_cooperation_count,
        },
        "memory_score": score["score"],
        "score_breakdown": score["breakdown"],
        "reasons": score["reasons"],
        "warnings": score["warnings"],
    }


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


