"""V-KPI Memory v0 service.

Memory v0 stores explainable facts from committed operational data. It is not a
vector store and does not train models; P4+ can read these facts directly.
"""
from __future__ import annotations

import hashlib
import json
import re
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


_PRODUCT_MOUNT_TOKENS = {
    "e",
    "fe",
    "z",
    "xf",
    "x",
    "n",
    "s",
    "rf",
    "mft",
    "m43",
    "m4/3",
}
_PRODUCT_SERIES_KEEP_TOKENS = {"air", "lab", "evo", "chip", "pro", "macro", "tube", "light", "flash"}
_PRODUCT_NOISE_WORDS = {
    "宣发推广",
    "宣发",
    "推广",
    "合作",
    "计划",
    "排期",
    "样机",
    "镜头",
    "官方",
}
_PRODUCT_COLOR_WORDS = {"灰", "黑", "白", "银", "蓝", "红", "绿", "green", "black", "white", "gray", "grey", "silver"}
_PRODUCT_SERIES_DISPLAY = {
    "air": "Air",
    "lab": "LAB",
    "evo": "EVO",
    "chip": "Chip",
    "pro": "Pro",
    "macro": "Macro",
    "tube": "Tube",
    "light": "Light",
    "flash": "Flash",
}


def _normalize_product_family(product_name: str) -> dict[str, Any]:
    original = _text(product_name)
    cleaned = _clean_product_name(original)
    if not cleaned:
        return {"status": "empty", "family_key": "", "family_name": "", "confidence": 0.0, "rules": ["empty"]}

    lower = cleaned.lower().strip()
    compact_mount = lower.replace(" ", "")
    if compact_mount in _PRODUCT_MOUNT_TOKENS:
        return {
            "status": "ambiguous_mount_only",
            "family_key": "",
            "family_name": "",
            "confidence": 0.0,
            "rules": ["mount_only"],
            "original_name": original,
            "cleaned_name": cleaned,
        }

    lens = _normalize_lens_family(cleaned)
    if lens:
        return {
            **lens,
            "status": "normalized",
            "original_name": original,
            "cleaned_name": cleaned,
        }

    model = _normalize_model_family(cleaned)
    if model:
        return {
            **model,
            "status": "normalized",
            "original_name": original,
            "cleaned_name": cleaned,
        }

    return {
        "status": "unclassified",
        "family_key": "",
        "family_name": "",
        "confidence": 0.0,
        "rules": ["no_model_pattern"],
        "original_name": original,
        "cleaned_name": cleaned,
    }


def _clean_product_name(value: str) -> str:
    text = _text(value)
    replacements = {
        "＋": "+",
        "／": "/",
        "（": "(",
        "）": ")",
        "–": "-",
        "—": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    for word in _PRODUCT_NOISE_WORDS:
        text = text.replace(word, " ")
    text = re.sub(r"\s+", " ", text).strip(" -_/+")
    return text


def _normalize_lens_family(value: str) -> dict[str, Any] | None:
    normalized = value.replace("F/", "F").replace("f/", "f").replace("/F", "/").replace("/f", "/")
    match = re.search(
        r"\b(?P<prefix>af|mf)?\s*(?P<focal>\d{1,3})(?:\s*mm)?\s*(?:f\s*/?\s*|/\s*f?\s*)(?P<aperture>\d(?:\.\d)?)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    prefix = (match.group("prefix") or "AF").upper()
    focal = f"{int(match.group('focal'))}mm"
    aperture = _format_aperture(match.group("aperture"))
    tail = normalized[match.end() :]
    series = _product_series_tokens(tail)
    family_parts = [prefix, focal, aperture, *series]
    family_name = " ".join(part for part in family_parts if part)
    return {
        "family_key": family_name.lower(),
        "family_name": family_name,
        "confidence": 0.95,
        "rules": ["lens_focal_aperture", *[f"series:{token.lower()}" for token in series]],
        "mount_tokens": _product_mount_tokens(value),
    }


def _product_series_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for token in re.split(r"[\s/+,_()\\-]+", value):
        clean = token.strip()
        if not clean:
            continue
        lower = clean.lower()
        if lower in _PRODUCT_MOUNT_TOKENS or lower in _PRODUCT_COLOR_WORDS:
            continue
        if lower in _PRODUCT_SERIES_KEEP_TOKENS:
            tokens.append(_PRODUCT_SERIES_DISPLAY.get(lower, clean.title()))
    return tokens


def _product_mount_tokens(value: str) -> list[str]:
    found: list[str] = []
    for token in re.split(r"[\s/+,_()\\-]+", _text(value)):
        lower = token.strip().lower()
        if lower in _PRODUCT_MOUNT_TOKENS and lower not in found:
            found.append(lower)
    return found


def _format_aperture(value: str) -> str:
    numeric = _text(value)
    if "." in numeric:
        numeric = numeric.rstrip("0").rstrip(".")
    return f"F{numeric}"


def _normalize_model_family(value: str) -> dict[str, Any] | None:
    clean = value.strip()
    clean = re.sub(r"\s*-\s*[nsfc]\b$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\b(" + "|".join(re.escape(word) for word in _PRODUCT_COLOR_WORDS) + r")$", "", clean, flags=re.IGNORECASE).strip()
    clean = re.sub(r"\s+", " ", clean).strip(" -_/+")
    if not clean:
        return None
    if re.search(r"[a-zA-Z]+\s*-?\s*\d|\d\s*-?\s*[a-zA-Z]+", clean):
        family_name = _title_product_model(clean)
        return {
            "family_key": family_name.lower(),
            "family_name": family_name,
            "confidence": 0.8,
            "rules": ["model_token"],
            "mount_tokens": _product_mount_tokens(value),
        }
    if len(clean) > 4 and any(token.lower() in _PRODUCT_SERIES_KEEP_TOKENS for token in clean.split()):
        family_name = _title_product_model(clean)
        return {
            "family_key": family_name.lower(),
            "family_name": family_name,
            "confidence": 0.7,
            "rules": ["series_name"],
            "mount_tokens": _product_mount_tokens(value),
        }
    return None


def _title_product_model(value: str) -> str:
    parts: list[str] = []
    for token in value.split():
        if re.search(r"\d", token) or token.isupper():
            parts.append(token.upper())
        else:
            parts.append(token[:1].upper() + token[1:])
    return " ".join(parts)


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


def build_product_family_memory() -> dict[str, Any]:
    """Create product_family entities and product->family links from raw product memory."""

    ensure_memory_schema()
    conn = get_conn()
    counters: Counter[str] = Counter()
    source_scope = "memory_product_family:v0"
    products = [
        _row_to_dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM vkpi_memory_entities
            WHERE entity_type='product'
            ORDER BY id
            """
        ).fetchall()
    ]
    try:
        counters["reset_links"] = int(
            conn.execute(
                """
                DELETE FROM vkpi_memory_links
                WHERE link_type='normalized_to_product_family'
                  AND source_ref LIKE ?
                """,
                (f"{source_scope}:%",),
            ).rowcount
            or 0
        )
        counters["reset_facts"] = int(
            conn.execute(
                """
                DELETE FROM vkpi_memory_facts
                WHERE fact_type='product_normalization'
                  AND source_ref LIKE ?
                """,
                (f"{source_scope}:%",),
            ).rowcount
            or 0
        )
        counters["reset_families"] = int(
            conn.execute(
                """
                DELETE FROM vkpi_memory_entities
                WHERE entity_type='product_family'
                  AND (
                    source_table='vkpi_memory_product_family_v0'
                    OR (source_table='vkpi_memory_entities' AND metadata_json LIKE '%%normalization_version%%')
                  )
                """
            ).rowcount
            or 0
        )
        for product in products:
            normalized = _normalize_product_family(product.get("display_name") or product.get("identity_key") or "")
            source_ref = f"{source_scope}:product:{product['entity_uid']}"
            if normalized.get("status") != "normalized":
                _upsert_fact(
                    entity_id=int(product["id"]),
                    entity_uid=product["entity_uid"],
                    fact_type="product_normalization",
                    fact_key="status",
                    value=_text(normalized.get("status")),
                    source_ref=source_ref,
                    source_table="vkpi_memory_entities",
                    source_id=str(product["id"]),
                    confidence_score=0.5,
                    fact=normalized,
                    source={"product_entity_uid": product["entity_uid"]},
                )
                counters[f"skipped_{normalized.get('status') or 'unknown'}"] += 1
                continue

            family = _upsert_entity(
                entity_type="product_family",
                identity_key=_text(normalized["family_key"]),
                display_name=_text(normalized["family_name"]),
                source_table="vkpi_memory_product_family_v0",
                source_id=str(product["id"]),
                status="active",
                confidence_score=float(normalized.get("confidence") or 0.8),
                identity={
                    "family_key": normalized["family_key"],
                    "family_name": normalized["family_name"],
                    "normalization_version": "v0",
                },
                metadata={
                    "normalization": normalized,
                    "source_product_uid": product["entity_uid"],
                },
            )
            _upsert_link(
                source_entity_id=int(product["id"]),
                source_entity_uid=product["entity_uid"],
                target_entity_id=int(family["id"]),
                target_entity_uid=family["entity_uid"],
                link_type="normalized_to_product_family",
                source_ref=source_ref,
                weight=1.0,
                confidence_score=float(normalized.get("confidence") or 0.8),
                source={"product_entity_uid": product["entity_uid"]},
                metadata=normalized,
            )
            _upsert_fact(
                entity_id=int(product["id"]),
                entity_uid=product["entity_uid"],
                fact_type="product_normalization",
                fact_key="family",
                value=_text(normalized["family_key"]),
                source_ref=source_ref,
                source_table="vkpi_memory_entities",
                source_id=str(product["id"]),
                confidence_score=float(normalized.get("confidence") or 0.8),
                fact=normalized,
                source={"product_entity_uid": product["entity_uid"], "family_entity_uid": family["entity_uid"]},
            )
            counters["normalized_products"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return product_family_summary(limit=50) | {"build_counts": dict(counters)}


def product_family_summary(*, query: str = "", limit: int = 100) -> dict[str, Any]:
    ensure_memory_schema()
    safe_limit = _safe_limit(limit, default=100, max_limit=500)
    where = "WHERE f.entity_type='product_family'"
    params: list[Any] = []
    if _text(query):
        where += " AND (lower(f.display_name) LIKE ? OR lower(f.identity_key) LIKE ?)"
        like = f"%{_text(query).lower()}%"
        params.extend([like, like])
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            f"""
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
            {where}
            GROUP BY f.id
            ORDER BY cooperation_count DESC, member_count DESC, f.display_name
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    ]
    items = []
    for row in rows:
        members = [
            _public_entity(_row_to_dict(member))
            | {"link_count": int(member.get("link_count") or 0)}
            for member in get_conn().execute(
                """
                SELECT p.*,
                       (
                         SELECT COUNT(*)
                         FROM vkpi_memory_links w
                         WHERE w.target_entity_id=p.id
                           AND w.link_type='worked_on_product'
                       ) AS link_count
                FROM vkpi_memory_links nl
                JOIN vkpi_memory_entities p ON p.id=nl.source_entity_id
                WHERE nl.target_entity_id=?
                  AND nl.link_type='normalized_to_product_family'
                ORDER BY link_count DESC, p.display_name
                LIMIT 10
                """,
                (int(row["id"]),),
            ).fetchall()
        ]
        items.append(
            _public_entity(row)
            | {
                "member_count": int(row.get("member_count") or 0),
                "cooperation_count": int(row.get("cooperation_count") or 0),
                "members": members,
            }
        )
    total = get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_memory_entities WHERE entity_type='product_family'").fetchone()["n"]
    return {
        "query": _text(query),
        "total_families": int(total or 0),
        "matched_families": len(items),
        "items": items,
    }


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


def _safe_limit(value: int | str | None, *, default: int, max_limit: int) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default
    return max(1, min(max_limit, parsed))


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
