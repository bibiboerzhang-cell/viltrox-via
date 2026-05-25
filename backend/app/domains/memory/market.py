"""Market signal Memory v0 build and read paths."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _text
from app.domains.memory.common import (
    _fetch_batch,
    _load_json,
    _public_market_signal,
    _row_to_dict,
    _safe_limit,
    _upsert_entity,
    _upsert_fact,
    _upsert_link,
    ensure_memory_schema,
)
from app.domains.memory.product import _normalize_product_family

def build_market_memory_from_legacy_batch(batch_uid: str) -> dict[str, Any]:
    """Build Market Memory v0 facts from launch, official content, materials, and VOC staging."""

    ensure_memory_schema()
    conn = get_conn()
    batch = _fetch_batch(batch_uid)
    import_batch_id = int(batch["id"])
    source_scope = f"market_memory:v0:batch:{batch_uid}"
    counters: Counter[str] = Counter()
    try:
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

        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_legacy_launch_plans_staging
            WHERE import_batch_id=?
              AND review_status='ready'
            ORDER BY id
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            target = _market_target_for_product(
                item.get("product_name") or item.get("product_sku") or item.get("launch_name") or "",
                fallback_topic=item.get("launch_name") or item.get("source_sheet") or "launch_plan",
                topic_kind="launch_plan",
                source_table="vkpi_legacy_launch_plans_staging",
                source_id=str(item["id"]),
            )
            _upsert_market_signal(
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
                    "target_platforms": _load_json(item.get("target_platforms_json") or "[]", []),
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

        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_legacy_official_content_staging
            WHERE import_batch_id=?
              AND review_status='ready'
            ORDER BY id
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            target = _market_target_for_product(
                item.get("product") or "",
                fallback_topic=item.get("title") or item.get("official_account") or "official_content",
                topic_kind="official_content",
                source_table="vkpi_legacy_official_content_staging",
                source_id=str(item["id"]),
            )
            account = _official_account_entity(item.get("normalized_platform") or item.get("platform"), item.get("official_account"))
            source_ref = f"{source_scope}:official_content:{item['id']}"
            if account and target["entity_type"] == "product_family":
                _upsert_link(
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
            _upsert_market_signal(
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

        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_legacy_official_materials_staging
            WHERE import_batch_id=?
              AND review_status='ready'
            ORDER BY id
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            target = _market_target_for_product(
                item.get("product_name") or item.get("product_sku") or item.get("launch_ref") or "",
                fallback_topic=item.get("content_description") or item.get("launch_ref") or "official_material",
                topic_kind="official_material",
                source_table="vkpi_legacy_official_materials_staging",
                source_id=str(item["id"]),
            )
            _upsert_market_signal(
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

        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_legacy_voc_alerts_staging
            WHERE import_batch_id=?
              AND review_status='ready'
            ORDER BY id
            """,
            (import_batch_id,),
        ).fetchall():
            item = _row_to_dict(row)
            target = _market_target_for_product(
                item.get("product") or "",
                fallback_topic=item.get("issue_type") or item.get("content") or "voc_alert",
                topic_kind="voc_alert",
                source_table="vkpi_legacy_voc_alerts_staging",
                source_id=str(item["id"]),
                platform=item.get("normalized_platform") or item.get("platform"),
                sentiment=item.get("sentiment"),
            )
            _upsert_market_signal(
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

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return market_signal_summary(source_ref=source_scope) | {"batch_uid": batch_uid, "build_counts": dict(counters)}


def market_signals(*, query: str = "", signal_type: str = "", limit: int = 100) -> dict[str, Any]:
    ensure_memory_schema()
    safe_limit = _safe_limit(limit, default=100, max_limit=500)
    where = ["f.fact_type='market_signal'"]
    params: list[Any] = []
    if _text(signal_type):
        where.append("f.fact_key LIKE ?")
        params.append(f"{_text(signal_type)}:%")
    if _text(query):
        like = f"%{_text(query).lower()}%"
        where.append(
            """
            (
              lower(e.display_name) LIKE ?
              OR lower(e.identity_key) LIKE ?
              OR lower(f.fact_value_text) LIKE ?
              OR lower(f.fact_json) LIKE ?
            )
            """
        )
        params.extend([like, like, like, like])
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            f"""
            SELECT f.id AS fact_id, f.fact_uid, f.fact_type, f.fact_key,
                   f.fact_value_text, f.confidence_score AS fact_confidence_score,
                   f.source_ref, f.source_table, f.source_id, f.fact_json,
                   f.source_json, f.metadata_json AS fact_metadata_json,
                   f.observed_at,
                   e.id AS entity_id, e.entity_uid, e.entity_type, e.identity_key,
                   e.display_name, e.status AS entity_status,
                   e.confidence_score AS entity_confidence_score,
                   e.identity_json, e.metadata_json AS entity_metadata_json,
                   e.updated_at AS entity_updated_at
            FROM vkpi_memory_facts f
            JOIN vkpi_memory_entities e ON e.id=f.entity_id
            WHERE {' AND '.join(where)}
            ORDER BY f.observed_at DESC, f.id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    ]
    return {
        "query": _text(query),
        "signal_type": _text(signal_type),
        "items": [_public_market_signal(row) for row in rows],
        "total_returned": len(rows),
    }


def market_signal_summary(*, source_ref: str = "") -> dict[str, Any]:
    ensure_memory_schema()
    where = "WHERE fact_type='market_signal'"
    params: list[Any] = []
    if _text(source_ref):
        where += " AND source_ref LIKE ?"
        params.append(f"{_text(source_ref)}%")
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            f"""
            SELECT f.*, e.entity_type
            FROM vkpi_memory_facts f
            JOIN vkpi_memory_entities e ON e.id=f.entity_id
            {where}
            """,
            params,
        ).fetchall()
    ]
    by_signal: Counter[str] = Counter()
    by_entity_type: Counter[str] = Counter()
    by_confidence_target: Counter[str] = Counter()
    for row in rows:
        payload = _load_json(row.get("fact_json") or "{}", {})
        signal = _text(payload.get("signal_type") if isinstance(payload, dict) else "") or _text(row.get("fact_key")).split(":")[0]
        by_signal[signal] += 1
        by_entity_type[_text(row.get("entity_type"))] += 1
        confidence = float(row.get("confidence_score") or 0)
        by_confidence_target["product_attached" if confidence >= 0.8 else "topic_attached"] += 1
    return {
        "source_ref": _text(source_ref),
        "total_signals": len(rows),
        "signals": dict(sorted(by_signal.items())),
        "target_entity_types": dict(sorted(by_entity_type.items())),
        "attachment": dict(sorted(by_confidence_target.items())),
    }


def _market_target_for_product(
    product_name: str,
    *,
    fallback_topic: str,
    topic_kind: str,
    source_table: str,
    source_id: str,
    platform: str = "",
    sentiment: str = "",
) -> dict[str, Any]:
    normalized = _normalize_product_family(product_name)
    if normalized.get("status") == "normalized":
        family = _product_family_for_normalized(normalized, source_table=source_table, source_id=source_id)
        return family | {"target_kind": "product_family", "normalization": normalized}
    topic = _market_topic_entity(
        fallback_topic=fallback_topic,
        topic_kind=topic_kind,
        platform=platform,
        sentiment=sentiment,
        normalization=normalized,
        source_table=source_table,
        source_id=source_id,
    )
    return topic | {"target_kind": "market_topic", "normalization": normalized}


def _product_family_for_normalized(normalized: dict[str, Any], *, source_table: str, source_id: str) -> dict[str, Any]:
    family_key = _text(normalized.get("family_key"))
    row = get_conn().execute(
        "SELECT * FROM vkpi_memory_entities WHERE entity_type='product_family' AND identity_key=?",
        (family_key,),
    ).fetchone()
    if row:
        return _row_to_dict(row)
    return _upsert_entity(
        entity_type="product_family",
        identity_key=family_key,
        display_name=_text(normalized.get("family_name")),
        source_table="vkpi_memory_market_signal_v0",
        source_id=source_id,
        status="active",
        confidence_score=float(normalized.get("confidence") or 0.75),
        identity={
            "family_key": family_key,
            "family_name": normalized.get("family_name"),
            "normalization_version": "v0",
        },
        metadata={"normalization": normalized, "source_table": source_table, "source_id": source_id},
    )


def _market_topic_entity(
    *,
    fallback_topic: str,
    topic_kind: str,
    platform: str = "",
    sentiment: str = "",
    normalization: dict[str, Any] | None = None,
    source_table: str,
    source_id: str,
) -> dict[str, Any]:
    topic = _text(fallback_topic)[:120] or topic_kind
    identity_parts = [topic_kind, _text(platform).lower(), _text(sentiment).lower(), topic.lower()]
    identity_key = ":".join(part for part in identity_parts if part)
    display = f"{topic_kind}: {topic}"
    return _upsert_entity(
        entity_type="market_topic",
        identity_key=identity_key,
        display_name=display,
        source_table="vkpi_memory_market_signal_v0",
        source_id=source_id,
        status="active",
        confidence_score=0.65,
        identity={"topic_kind": topic_kind, "topic": topic, "platform": platform, "sentiment": sentiment},
        metadata={"normalization": normalization or {}, "source_table": source_table, "source_id": source_id},
    )


def _official_account_entity(platform: str, account: str) -> dict[str, Any] | None:
    clean_account = _text(account)
    clean_platform = _text(platform).lower()
    if not clean_account and not clean_platform:
        return None
    identity_key = f"{clean_platform}:{clean_account.lower()}"
    display = clean_account or clean_platform
    if clean_platform and clean_account:
        display = f"{clean_account} ({clean_platform})"
    return _upsert_entity(
        entity_type="official_account",
        identity_key=identity_key,
        display_name=display,
        source_table="vkpi_legacy_official_content_staging",
        source_id="official_account",
        status="active",
        confidence_score=0.85,
        identity={"platform": clean_platform, "official_account": clean_account},
        metadata={},
    )


def _upsert_market_signal(
    *,
    target: dict[str, Any],
    signal_type: str,
    source_ref: str,
    source_table: str,
    source_id: str,
    signal_date: Any,
    value: str,
    confidence_score: float,
    payload: dict[str, Any],
    batch_uid: str,
) -> dict[str, Any]:
    signal_payload = {
        "signal_type": signal_type,
        "signal_date": _text(signal_date),
        "target_entity_uid": target.get("entity_uid"),
        "target_entity_type": target.get("entity_type"),
        "target_kind": target.get("target_kind") or target.get("entity_type"),
        "normalization": target.get("normalization") or {},
        **payload,
    }
    return _upsert_fact(
        entity_id=int(target["id"]),
        entity_uid=target["entity_uid"],
        fact_type="market_signal",
        fact_key=f"{signal_type}:{source_id}",
        value=_text(value) or signal_type,
        source_ref=source_ref,
        source_table=source_table,
        source_id=source_id,
        confidence_score=confidence_score,
        fact=signal_payload,
        source={"batch_uid": batch_uid},
        metadata={"market_memory_version": "v0"},
    )


