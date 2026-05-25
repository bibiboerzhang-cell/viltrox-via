"""Product normalization and product-family memory surfaces."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.legacy_import_audit import _text
from app.domains.memory.common import (
    _load_json,
    _public_entity,
    _row_to_dict,
    _safe_limit,
    _upsert_entity,
    _upsert_fact,
    _upsert_link,
    ensure_memory_schema,
)

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


