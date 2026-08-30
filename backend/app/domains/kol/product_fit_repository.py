"""SQL adapter for the read-only product-fit repository port."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from app.db.connection import get_conn, is_postgres_runtime
from app.shared.product_fit_contracts import ProductFitRepository
from app.shared.product_fit_policy import (
    as_row_dict,
    entity_payload,
    load_json,
    lower,
    normalize_product_fit_key,
    safe_float,
    safe_int,
    text,
)


_LEGACY_CONTACT_CAPABILITY_PREFIX_CHARS = 512


class SqlProductFitRepository(ProductFitRepository):
    """Production read adapter preserving the legacy SQL byte contract."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] = get_conn,
        postgres_runtime: Callable[[], bool] = is_postgres_runtime,
    ) -> None:
        self._connection_factory = connection_factory
        self._postgres_runtime = postgres_runtime

    def _conn(self) -> Any:
        return self._connection_factory()

    def list_kol_entities(self) -> list[dict[str, Any]]:
        rows = self._conn().execute(
        """
        SELECT id, entity_uid, display_name, status, identity_json, metadata_json
        FROM vkpi_memory_entities
        WHERE entity_type='kol'
          AND status IN ('active', 'imported', 'needs_human_review')
        ORDER BY id
        """
        ).fetchall()
        return [as_row_dict(row) for row in rows]

    def pools_by_source_ref(self) -> dict[str, dict[str, Any]]:
        if self._postgres_runtime():
            contact_prefix = (
                "LEFT(COALESCE(raw_platform_data, ''), "
                f"{_LEGACY_CONTACT_CAPABILITY_PREFIX_CHARS})"
            )
            raw_reach_projection = (
                "POSITION('\"low_reach\"' IN COALESCE(raw_platform_data, '')) > 0 "
                "AS low_reach_flagged, "
                f"(POSITION('\"contact_has_email\": true' IN {contact_prefix}) > 0 "
                f"OR POSITION('\"contact_has_email\":true' IN {contact_prefix}) > 0) "
                "AS contact_has_email, "
                f"(POSITION('\"contact_has_phone\": true' IN {contact_prefix}) > 0 "
                f"OR POSITION('\"contact_has_phone\":true' IN {contact_prefix}) > 0) "
                "AS contact_has_phone"
            )
        else:
            raw_reach_projection = "raw_platform_data"
        rows = self._conn().execute(
        f"""
        SELECT id, platform, handle, display_name, country, source_ref,
               sync_status, {raw_reach_projection},
               followers, avg_views, avg_comments, engagement_rate
        FROM vkpi_kol_pool
        WHERE source_type='legacy_excel_p2d'
        """
        ).fetchall()
        return {text(row["source_ref"]): as_row_dict(row) for row in rows}

    def legacy_entities_by_uid(self) -> dict[str, dict[str, Any]]:
        rows = self._conn().execute(
        """
        SELECT id, entity_uid, weak_label, resolution_decision
        FROM vkpi_legacy_kol_entities
        """
        ).fetchall()
        return {text(row["entity_uid"]): as_row_dict(row) for row in rows}

    def facts_by_kol(self) -> dict[int, list[dict[str, Any]]]:
        rows = self._conn().execute(
        """
        SELECT id, entity_id, fact_type, fact_value_text, confidence_score,
               source_ref, source_table, source_id, fact_json, observed_at
        FROM vkpi_memory_facts
        WHERE fact_type IN (
          'contact_status',
          'risk_flag',
          'sync_status',
          'weak_label',
          'country',
          'review_state',
          'evidence_count'
        )
        ORDER BY observed_at DESC, id DESC
        """
        ).fetchall()
        facts: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for raw in rows:
            row = as_row_dict(raw)
            facts[int(row["entity_id"])].append(row)
        return facts

    def worked_links_by_kol(self) -> dict[int, list[dict[str, Any]]]:
        rows = self._conn().execute(
        """
        SELECT l.id, l.link_uid, l.source_entity_id, l.target_entity_id,
               l.link_type, l.confidence_score, l.source_ref, l.source_json,
               p.entity_uid AS product_uid,
               p.display_name AS product_name,
               p.identity_key AS product_key
        FROM vkpi_memory_links l
        JOIN vkpi_memory_entities p ON p.id=l.target_entity_id
        WHERE l.link_type='worked_on_product'
        """
        ).fetchall()
        links: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for raw in rows:
            row = as_row_dict(raw)
            links[int(row["source_entity_id"])].append(row)
        return links

    def product_family_maps(
        self,
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
        rows = self._conn().execute(
        """
        SELECT p.id AS product_id,
               p.entity_uid AS product_uid,
               p.display_name AS product_name,
               p.identity_key AS product_key,
               p.metadata_json AS product_metadata_json,
               f.id AS family_id,
               f.entity_uid AS family_uid,
               f.display_name AS family_name,
               f.identity_key AS family_key,
               f.metadata_json AS family_metadata_json
        FROM vkpi_memory_links nl
        JOIN vkpi_memory_entities p ON p.id=nl.source_entity_id
        JOIN vkpi_memory_entities f ON f.id=nl.target_entity_id
        WHERE nl.link_type='normalized_to_product_family'
          AND p.entity_type='product'
          AND f.entity_type='product_family'
        """
        ).fetchall()
        product_to_family: dict[int, dict[str, Any]] = {}
        family_by_id: dict[int, dict[str, Any]] = {}
        for raw in rows:
            row = as_row_dict(raw)
            product_to_family[int(row["product_id"])] = row
            family_by_id[int(row["family_id"])] = {
                "id": int(row["family_id"]),
                "entity_uid": row["family_uid"],
                "display_name": row["family_name"],
                "identity_key": row["family_key"],
                "metadata_json": row["family_metadata_json"],
            }
        return product_to_family, family_by_id

    def target_market_signals(self, family_id: int) -> list[dict[str, Any]]:
        rows = self._conn().execute(
        """
        SELECT *
        FROM vkpi_memory_facts
        WHERE entity_id=?
          AND fact_type IN ('market_signal', 'launch_plan')
        ORDER BY observed_at DESC, id DESC
        """,
            (int(family_id),),
        ).fetchall()
        return [as_row_dict(row) for row in rows]

    def candidate_families(self) -> list[dict[str, Any]]:
        rows = self._conn().execute(
        """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='product_family'
          AND status IN ('active', 'imported')
        ORDER BY display_name, id
        """
        ).fetchall()
        return [as_row_dict(row) for row in rows]

    def official_family_links(self) -> dict[int, list[dict[str, Any]]]:
        rows = self._conn().execute(
        """
        SELECT *
        FROM vkpi_memory_links
        WHERE link_type='official_account_published_product'
        ORDER BY observed_at DESC, id DESC
        """
        ).fetchall()
        links: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for raw in rows:
            row = as_row_dict(raw)
            links[int(row.get("target_entity_id") or 0)].append(row)
        return links

    def dimensions11_fit(self, kol_pool_id: int) -> dict[str, dict[str, Any]]:
        if not int(kol_pool_id or 0):
            return {}
        row = self._conn().execute(
        """
        SELECT id, dimensions_11_json
        FROM vkpi_kol_profile_deep
        WHERE kol_pool_id=?
        LIMIT 1
        """,
            (int(kol_pool_id),),
        ).fetchone()
        if not row:
            return {}
        profile_id = safe_int(row["id"])
        payload = load_json(row["dimensions_11_json"] or "{}", {})
        if not isinstance(payload, dict):
            return {}
        block4 = (
            payload.get("block4_specialty")
            if isinstance(payload.get("block4_specialty"), dict)
            else {}
        )
        raw_fit = (
            block4.get("product_fit")
            if isinstance(block4.get("product_fit"), dict)
            else {}
        )
        raw_conf = (
            block4.get("product_fit_confidence")
            if isinstance(block4.get("product_fit_confidence"), dict)
            else {}
        )
        result: dict[str, dict[str, Any]] = {}
        for sku, score in raw_fit.items():
            sku_text = text(sku)
            normalized = normalize_product_fit_key(sku_text)
            if not sku_text or not normalized:
                continue
            numeric_score = max(0.0, min(100.0, safe_float(score, 0.0)))
            confidence = max(
                0.0,
                min(1.0, safe_float(raw_conf.get(sku_text), 0.0)),
            )
            if numeric_score <= 0 or confidence <= 0:
                continue
            result[sku_text] = {
                "sku": sku_text,
                "normalized": normalized,
                "score": numeric_score,
                "confidence": confidence,
                "profile_deep_id": profile_id,
                "method": text(payload.get("method")),
                "computed_at": text(payload.get("computed_at")),
            }
        return result


def resolve_kol(
    repository: ProductFitRepository,
    *,
    kol_entity_uid: str = "",
    kol_pool_id: int = 0,
    platform: str = "",
    handle: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve exactly one selector using a read-only repository."""

    selectors = [
        bool(text(kol_entity_uid)),
        bool(int(kol_pool_id or 0)),
        bool(text(platform) and text(handle)),
    ]
    if sum(1 for item in selectors if item) != 1:
        raise ValueError(
            "provide exactly one KOL selector: --kol-entity-uid, "
            "--kol-pool-id, or --platform + --handle"
        )
    kol_rows = repository.list_kol_entities()
    pool_map = repository.pools_by_source_ref()
    pools_by_id = {
        safe_int(row.get("id")): row
        for row in pool_map.values()
        if safe_int(row.get("id"))
    }
    kols_by_ref: dict[str, dict[str, Any]] = {}
    for row in kol_rows:
        source_ref = text(entity_payload(row, "identity_json").get("source_ref"))
        if source_ref:
            kols_by_ref[source_ref] = row

    if kol_entity_uid:
        for row in kol_rows:
            if text(row.get("entity_uid")) == text(kol_entity_uid):
                source_ref = text(
                    entity_payload(row, "identity_json").get("source_ref")
                )
                return row, pool_map.get(source_ref, {})
        raise ValueError(f"KOL memory entity not found: {kol_entity_uid}")
    if kol_pool_id:
        pool = pools_by_id.get(int(kol_pool_id))
        if not pool:
            raise ValueError(f"KOL pool row not found: {kol_pool_id}")
        kol = kols_by_ref.get(text(pool.get("source_ref")))
        if not kol:
            raise ValueError(f"KOL memory entity not found for pool id: {kol_pool_id}")
        return kol, pool

    platform_key = lower(platform)
    handle_key = lower(handle).lstrip("@")
    for pool in pool_map.values():
        if (
            lower(pool.get("platform")) == platform_key
            and lower(pool.get("handle")).lstrip("@") == handle_key
        ):
            kol = kols_by_ref.get(text(pool.get("source_ref")))
            if not kol:
                raise ValueError(f"KOL memory entity not found for {platform}:{handle}")
            return kol, pool
    raise ValueError(f"KOL pool row not found for {platform}:{handle}")


__all__ = ["SqlProductFitRepository", "resolve_kol"]
