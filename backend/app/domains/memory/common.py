"""Shared helpers and read surfaces for V-KPI Memory v0."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.legacy_import.legacy_import_staging import ensure_legacy_staging_schema, json_dumps


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




def _safe_limit(value: int | str | None, *, default: int, max_limit: int) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default
    return max(1, min(max_limit, parsed))


def _readiness_gate(key: str, actual: int, expected_min: int, severity: str) -> dict[str, Any]:
    return {
        "key": key,
        "status": "pass" if int(actual or 0) >= int(expected_min) else "fail",
        "severity": severity,
        "actual": int(actual or 0),
        "expected_min": int(expected_min),
    }


def _market_signal_counts() -> dict[str, int]:
    counts: Counter[str] = Counter()
    rows = get_conn().execute(
        """
        SELECT fact_key, fact_json
        FROM vkpi_memory_facts
        WHERE fact_type='market_signal'
        """
    ).fetchall()
    for row in rows:
        payload = _load_json(row["fact_json"] or "{}", {})
        signal_type = _text(payload.get("signal_type") if isinstance(payload, dict) else "")
        if not signal_type:
            signal_type = _text(row["fact_key"]).split(":")[0]
        if signal_type:
            counts[signal_type] += 1
    return dict(sorted(counts.items()))


def _table_exists(table_name: str) -> bool:
    row = get_conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (_text(table_name),),
    ).fetchone()
    return bool(row)


def _memory_entity_by_uid(entity_uid: str) -> dict[str, Any] | None:
    row = get_conn().execute("SELECT * FROM vkpi_memory_entities WHERE entity_uid=?", (_text(entity_uid),)).fetchone()
    return _row_to_dict(row) if row else None


def _kol_feature_summary(entity_id: int) -> dict[str, Any]:
    conn = get_conn()
    entity_row = conn.execute("SELECT * FROM vkpi_memory_entities WHERE id=?", (int(entity_id),)).fetchone()
    if not entity_row:
        raise LookupError("memory entity not found")
    entity = _row_to_dict(entity_row)
    identity = _load_json(entity.get("identity_json") or "{}", {})
    metadata = _load_json(entity.get("metadata_json") or "{}", {})
    fact_rows = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_memory_facts
            WHERE entity_id=?
            ORDER BY observed_at DESC, id DESC
            """,
            (int(entity_id),),
        ).fetchall()
    ]
    link_stats = conn.execute(
        """
        SELECT COUNT(*) AS cooperation_count,
               COUNT(DISTINCT target_entity_id) AS product_count
        FROM vkpi_memory_links
        WHERE source_entity_id=?
          AND link_type='worked_on_product'
        """,
        (int(entity_id),),
    ).fetchone()

    latest: dict[str, str] = {}
    evidence_count = 0
    risk_flag_count = 0
    for fact in fact_rows:
        fact_type = _text(fact.get("fact_type"))
        if fact_type and fact_type not in latest:
            latest[fact_type] = _text(fact.get("fact_value_text"))
        if fact_type == "risk_flag":
            risk_flag_count += 1
        if fact_type == "evidence_count" and _text(fact.get("fact_key")) == "evidence_count":
            payload = _load_json(fact.get("fact_json") or "{}", {})
            try:
                evidence_count = int(payload.get("count") if isinstance(payload, dict) else fact.get("fact_value_text") or 0)
            except Exception:
                evidence_count = 0

    return {
        "platform": identity.get("platform") or "",
        "handle": identity.get("handle") or "",
        "country": latest.get("country") or identity.get("country") or "",
        "sync_status": latest.get("sync_status") or entity.get("status") or "",
        "weak_label": latest.get("weak_label") or metadata.get("weak_label") or "",
        "review_state": latest.get("review_state") or metadata.get("review_state") or "",
        "contact_status": latest.get("contact_status") or "",
        "cooperation_count": int(link_stats["cooperation_count"] or 0) if link_stats else 0,
        "product_count": int(link_stats["product_count"] or 0) if link_stats else 0,
        "risk_flag_count": risk_flag_count,
        "evidence_count": evidence_count,
        "confidence_score": float(entity.get("confidence_score") or 0),
    }


def _memory_candidate_score(
    *,
    cooperation_count: int,
    matched_product_count: int,
    risk_flag_count: int,
    evidence_count: int,
    review_state: str,
    sync_status: str,
    contact_status: str,
) -> dict[str, Any]:
    cooperation_bonus = min(30, max(0, int(cooperation_count)) * 7)
    product_bonus = min(20, max(0, int(matched_product_count)) * 8)
    evidence_bonus = min(10, max(0, int(evidence_count)))
    review_penalty = 15 if _text(review_state) == "needs_human_review" else 0
    sync_penalty = 15 if _text(sync_status) == "needs_human_review" else 0
    risk_penalty = min(30, max(0, int(risk_flag_count)) * 12)
    contact_penalty = 8 if _text(contact_status) == "contact_missing" else 0
    raw_score = 35 + cooperation_bonus + product_bonus + evidence_bonus
    raw_score -= review_penalty + sync_penalty + risk_penalty + contact_penalty
    score = max(0, min(100, int(raw_score)))

    reasons: list[str] = []
    warnings: list[str] = []
    if cooperation_count:
        reasons.append(f"historical_product_cooperations={int(cooperation_count)}")
    if matched_product_count:
        reasons.append(f"matched_product_memory={int(matched_product_count)}")
    if evidence_count:
        reasons.append(f"legacy_evidence_rows={int(evidence_count)}")
    if sync_penalty or review_penalty:
        warnings.append("needs_human_review")
    if risk_flag_count:
        warnings.append(f"risk_flags={int(risk_flag_count)}")
    if contact_penalty:
        warnings.append("contact_missing")
    if not reasons:
        reasons.append("no_direct_product_memory")

    return {
        "score": score,
        "breakdown": {
            "base": 35,
            "cooperation_bonus": cooperation_bonus,
            "product_bonus": product_bonus,
            "evidence_bonus": evidence_bonus,
            "review_penalty": review_penalty,
            "sync_penalty": sync_penalty,
            "risk_penalty": risk_penalty,
            "contact_penalty": contact_penalty,
        },
        "reasons": reasons,
        "warnings": warnings,
    }


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


def _public_product_link(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "link_uid": row["link_uid"],
        "link_type": row["link_type"],
        "weight": float(row.get("weight") or 0),
        "confidence_score": float(row.get("confidence_score") or 0),
        "source_ref": row.get("source_ref") or "",
        "source": _load_json(row.get("source_json") or "{}", {}),
        "metadata": _load_json(row.get("metadata_json") or "{}", {}),
        "observed_at": row.get("observed_at"),
        "product": {
            "entity_uid": row.get("target_uid") or "",
            "entity_type": row.get("target_type") or "",
            "identity_key": row.get("target_identity_key") or "",
            "display_name": row.get("target_display_name") or "",
            "identity": _load_json(row.get("target_identity_json") or "{}", {}),
            "metadata": _load_json(row.get("target_metadata_json") or "{}", {}),
        },
    }


def _public_market_signal(row: dict[str, Any]) -> dict[str, Any]:
    fact = _load_json(row.get("fact_json") or "{}", {})
    source = _load_json(row.get("source_json") or "{}", {})
    return {
        "fact_uid": row.get("fact_uid") or "",
        "signal_type": fact.get("signal_type") if isinstance(fact, dict) else "",
        "signal_date": fact.get("signal_date") if isinstance(fact, dict) else "",
        "value": row.get("fact_value_text") or "",
        "confidence_score": float(row.get("fact_confidence_score") or 0),
        "source_ref": row.get("source_ref") or "",
        "source_table": row.get("source_table") or "",
        "source_id": row.get("source_id") or "",
        "fact": fact,
        "source": source,
        "observed_at": row.get("observed_at"),
        "entity": {
            "id": int(row["entity_id"]),
            "entity_uid": row.get("entity_uid") or "",
            "entity_type": row.get("entity_type") or "",
            "identity_key": row.get("identity_key") or "",
            "display_name": row.get("display_name") or "",
            "status": row.get("entity_status") or "",
            "confidence_score": float(row.get("entity_confidence_score") or 0),
            "identity": _load_json(row.get("identity_json") or "{}", {}),
            "metadata": _load_json(row.get("entity_metadata_json") or "{}", {}),
            "updated_at": row.get("entity_updated_at"),
        },
    }


def _public_feedback(row: dict[str, Any]) -> dict[str, Any]:
    entity_uid = row.get("entity_uid") or ""
    entity = None
    if entity_uid:
        entity = {
            "entity_uid": entity_uid,
            "entity_type": row.get("entity_type") or "",
            "identity_key": row.get("identity_key") or "",
            "display_name": row.get("display_name") or "",
            "status": row.get("entity_status") or "",
            "confidence_score": float(row.get("entity_confidence_score") or 0),
            "identity": _load_json(row.get("identity_json") or "{}", {}),
            "metadata": _load_json(row.get("entity_metadata_json") or "{}", {}),
            "updated_at": row.get("entity_updated_at"),
        }
    return {
        "feedback_uid": row["feedback_uid"],
        "entity_id": row.get("entity_id"),
        "entity": entity,
        "feedback_type": row.get("feedback_type") or "",
        "rating": row.get("rating"),
        "status": row.get("status") or "",
        "feedback": _load_json(row.get("feedback_json") or "{}", {}),
        "resolution": _load_json(row.get("resolution_json") or "{}", {}),
        "metadata": _load_json(row.get("metadata_json") or "{}", {}),
        "resolved_at": row.get("resolved_at"),
        "created_at": row.get("created_at"),
    }
