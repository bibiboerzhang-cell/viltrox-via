"""
db/repositories/knowledge.py — platform ingest + L3 knowledge store persistence
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from app.db.connection import get_conn, is_postgres_runtime


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any, default: Any) -> str:
    data = default if value is None else value
    return json.dumps(data, ensure_ascii=False)


def _dedupe_list(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _loads_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default


def record_platform_ingest_event(
    *,
    source_platform: str,
    event_type: str,
    entity_type: str,
    external_id: str = "",
    creator_handle: str = "",
    region_code: str = "",
    dedupe_key: str = "",
    payload: Any = None,
    ingest_status: str = "queued",
    occurred_at: str = "",
    error_message: str = "",
) -> int:
    conn = get_conn()
    now = _utcnow()
    occurred = occurred_at or now
    key = dedupe_key or f"{source_platform}:{event_type}:{entity_type}:{external_id or creator_handle or occurred}"
    processed_at = None if is_postgres_runtime() else ""
    params = (
        key,
        source_platform,
        event_type,
        entity_type,
        external_id,
        creator_handle,
        region_code,
        ingest_status,
        _json(payload, {}),
        occurred,
        processed_at,
        error_message[:500],
        now,
    )
    if (
        is_postgres_runtime()
        and source_platform == "shopify"
        and entity_type == "order"
        and str(external_id or "").strip()
    ):
        conn.execute(
            """
            INSERT INTO platform_ingest_events (
                dedupe_key, source_platform, event_type, entity_type, external_id,
                creator_handle, region_code, ingest_status, payload_json,
                occurred_at, processed_at, error_message, created_at
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?)
            ON CONFLICT (source_platform, event_type, external_id)
            WHERE source_platform = 'shopify'
              AND entity_type = 'order'
              AND external_id <> ''
            DO UPDATE SET
                dedupe_key=excluded.dedupe_key,
                payload_json=excluded.payload_json,
                ingest_status=excluded.ingest_status,
                error_message=excluded.error_message,
                creator_handle=excluded.creator_handle,
                region_code=excluded.region_code,
                occurred_at=excluded.occurred_at
            """,
            params,
        )
        row = conn.execute(
            """
            SELECT id FROM platform_ingest_events
            WHERE source_platform=? AND event_type=? AND external_id=?
            """,
            (source_platform, event_type, external_id),
        ).fetchone()
    else:
        conn.execute(
            """
            INSERT INTO platform_ingest_events (
                dedupe_key, source_platform, event_type, entity_type, external_id,
                creator_handle, region_code, ingest_status, payload_json,
                occurred_at, processed_at, error_message, created_at
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
                payload_json=excluded.payload_json,
                ingest_status=excluded.ingest_status,
                error_message=excluded.error_message,
                creator_handle=excluded.creator_handle,
                region_code=excluded.region_code,
                occurred_at=excluded.occurred_at
            """,
            params,
        )
        row = conn.execute(
            "SELECT id FROM platform_ingest_events WHERE dedupe_key=?",
            (key,),
        ).fetchone()
    conn.commit()
    return int(row["id"])


def update_platform_ingest_event_status(event_id: int, ingest_status: str, error_message: str = "") -> None:
    conn = get_conn()
    processed_at = _utcnow() if ingest_status in {"done", "failed"} else (None if is_postgres_runtime() else "")
    conn.execute(
        """
        UPDATE platform_ingest_events
        SET ingest_status=?, processed_at=?, error_message=?
        WHERE id=?
        """,
        (ingest_status, processed_at, error_message[:500], event_id),
    )
    conn.commit()


def upsert_creator_memory_entry(
    *,
    user_id: int = 0,
    creator_handle: str = "",
    memory_kind: str,
    fact_key: str,
    fact_value: Any,
    confidence: float = 0.5,
    source_ref: str = "",
    memory_key: str = "",
) -> int:
    conn = get_conn()
    now = _utcnow()
    key = memory_key or f"{int(user_id or 0)}:{creator_handle}:{memory_kind}:{fact_key}"
    conn.execute(
        """
        INSERT INTO creator_memory_entries (
            memory_key, user_id, creator_handle, memory_kind, fact_key,
            fact_value_json, confidence, source_ref, created_at, updated_at
        ) VALUES (?,?,?,?,?, ?,?,?,?,?)
        ON CONFLICT(memory_key) DO UPDATE SET
            fact_value_json=excluded.fact_value_json,
            confidence=excluded.confidence,
            source_ref=excluded.source_ref,
            updated_at=excluded.updated_at
        """,
        (
            key,
            int(user_id or 0),
            creator_handle,
            memory_kind,
            fact_key,
            _json(fact_value, {}),
            float(confidence or 0),
            source_ref,
            now,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM creator_memory_entries WHERE memory_key=?",
        (key,),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def upsert_market_observation(
    *,
    source_platform: str,
    subject_type: str,
    subject_key: str,
    observation_type: str,
    summary: str = "",
    metrics: Any = None,
    evidence: Any = None,
    region_code: str = "",
    observed_at: str = "",
    observation_key: str = "",
) -> int:
    conn = get_conn()
    now = _utcnow()
    observed = observed_at or now
    key = observation_key or f"{source_platform}:{subject_type}:{subject_key}:{observation_type}:{observed[:13]}"
    conn.execute(
        """
        INSERT INTO market_observations (
            observation_key, source_platform, subject_type, subject_key,
            observation_type, summary, metrics_json, evidence_json,
            region_code, observed_at, created_at
        ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?)
        ON CONFLICT(observation_key) DO UPDATE SET
            summary=excluded.summary,
            metrics_json=excluded.metrics_json,
            evidence_json=excluded.evidence_json,
            region_code=excluded.region_code,
            observed_at=excluded.observed_at
        """,
        (
            key,
            source_platform,
            subject_type,
            subject_key,
            observation_type,
            summary[:1000],
            _json(metrics, {}),
            _json(evidence, []),
            region_code,
            observed,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM market_observations WHERE observation_key=?",
        (key,),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def upsert_product_knowledge(
    *,
    product_key: str,
    label: str,
    family: str = "",
    mount_type: str = "",
    alias_terms: Iterable[str] | None = None,
    feature_tags: Iterable[str] | None = None,
    scene_tags: Iterable[str] | None = None,
    status: str = "seed",
) -> str:
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO product_knowledge (
            product_key, label, family, mount_type, alias_terms_json,
            feature_tags_json, scene_tags_json, status, created_at, updated_at
        ) VALUES (?,?,?,?,?, ?,?,?,?,?)
        ON CONFLICT(product_key) DO UPDATE SET
            label=excluded.label,
            family=excluded.family,
            mount_type=excluded.mount_type,
            alias_terms_json=excluded.alias_terms_json,
            feature_tags_json=excluded.feature_tags_json,
            scene_tags_json=excluded.scene_tags_json,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            product_key,
            label,
            family,
            mount_type,
            _json(_dedupe_list(alias_terms or []), []),
            _json(_dedupe_list(feature_tags or []), []),
            _json(_dedupe_list(scene_tags or []), []),
            status,
            now,
            now,
        ),
    )
    conn.commit()
    return product_key


def append_product_visual_feature(
    *,
    product_key: str,
    feature_type: str,
    feature_vector: Any,
    asset_role: str = "",
    storage_key: str = "",
    detector_version: str = "",
) -> int:
    conn = get_conn()
    now = _utcnow()
    params = (
        product_key,
        asset_role,
        storage_key,
        feature_type,
        _json(feature_vector, {}),
        detector_version,
        now,
    )
    sql = """
        INSERT INTO product_visual_features (
            product_key, asset_role, storage_key, feature_type,
            feature_vector_json, detector_version, created_at
        ) VALUES (?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        feature_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        feature_id = int(cur.lastrowid)
    conn.commit()
    return feature_id


def upsert_region_market_fact(
    *,
    region_code: str,
    fact_type: str,
    fact_value: Any,
    source_platform: str = "",
    region_level: str = "country",
    observed_at: str = "",
    fact_key: str = "",
) -> int:
    conn = get_conn()
    now = _utcnow()
    observed = observed_at or now
    key = fact_key or f"{region_level}:{region_code}:{fact_type}:{observed[:13]}"
    conn.execute(
        """
        INSERT INTO region_market_facts (
            fact_key, region_code, region_level, fact_type,
            fact_value_json, source_platform, observed_at, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(fact_key) DO UPDATE SET
            fact_value_json=excluded.fact_value_json,
            source_platform=excluded.source_platform,
            observed_at=excluded.observed_at
        """,
        (
            key,
            region_code,
            region_level,
            fact_type,
            _json(fact_value, {}),
            source_platform,
            observed,
            now,
        ),
    )
    row = conn.execute(
        "SELECT id FROM region_market_facts WHERE fact_key=?",
        (key,),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def insert_feedback_event(
    *,
    source_type: str,
    event_type: str,
    payload: Any = None,
    source_id: str = "",
    actor_role: str = "",
    user_id: int = 0,
    submission_id: int = 0,
) -> int:
    conn = get_conn()
    params = (
        source_type,
        source_id,
        event_type,
        actor_role,
        int(user_id or 0),
        int(submission_id or 0),
        _json(payload, {}),
        _utcnow(),
    )
    sql = """
        INSERT INTO feedback_events (
            source_type, source_id, event_type, actor_role,
            user_id, submission_id, payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        event_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        event_id = int(cur.lastrowid)
    conn.commit()
    return event_id


def list_product_knowledge_rules(limit: int = 500) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT product_key, label, family, mount_type,
               alias_terms_json, feature_tags_json, scene_tags_json, status
        FROM product_knowledge
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "product_key": row["product_key"],
                "label": row["label"],
                "series": row["family"] or "",
                "mount_type": row["mount_type"] or "",
                "keywords": _dedupe_list(
                    [
                        row["label"],
                        row["product_key"],
                        *(_loads_json(row["alias_terms_json"], [])),
                        *(_loads_json(row["feature_tags_json"], [])),
                        *(_loads_json(row["scene_tags_json"], [])),
                    ]
                ),
                "status": row["status"] or "seed",
            }
        )
    return items
