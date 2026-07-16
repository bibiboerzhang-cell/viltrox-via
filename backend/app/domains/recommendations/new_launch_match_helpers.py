"""Shared helpers for deterministic new-launch matching."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.shared.vkpi_utils import json_loads, row_to_dict, to_float, to_int, to_text

def _row_to_dict(row: Any) -> dict[str, Any]:
    return row_to_dict(row)


def _text(value: Any) -> str:
    return to_text(value)


def _lower(value: Any) -> str:
    return _text(value).lower()


def _load_json(value: Any, default: Any) -> Any:
    return json_loads(value, default)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_date(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _within_days(value: Any, days: int, *, now: datetime) -> bool:
    parsed = _parse_date(value)
    if not parsed:
        return False
    return abs((now - parsed).days) <= int(days)


def _safe_int(value: Any, default: int = 0) -> int:
    return to_int(value, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    return to_float(value, default)


def _safe_limit(limit: int) -> int:
    return max(1, min(500, int(limit or 100)))


def _split_csv(value: str) -> set[str]:
    return {_country_key(part) for part in str(value or "").split(",") if _country_key(part)}


_COUNTRY_ALIASES = {
    "美国": "united states",
    "usa": "united states",
    "us": "united states",
    "u.s.": "united states",
    "英国": "united kingdom",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "德国": "germany",
    "日本": "japan",
    "法国": "france",
    "加拿大": "canada",
    "澳大利亚": "australia",
    "韩国": "south korea",
    "意大利": "italy",
    "西班牙": "spain",
    "荷兰": "netherlands",
    "波兰": "poland",
    "印度": "india",
    "巴西": "brazil",
    "墨西哥": "mexico",
}


def _country_key(value: Any) -> str:
    key = re.sub(r"\s+", " ", _lower(value))
    return _COUNTRY_ALIASES.get(key, key)


def _family_tokens(value: str) -> dict[str, Any]:
    text = _lower(value).replace("/", " ")
    focal = ""
    aperture = ""
    series: set[str] = set()
    mount: set[str] = set()
    focal_match = re.search(r"(\d{2,3})\s*mm", text)
    if not focal_match:
        focal_match = re.search(r"\b(\d{2,3})\b", text)
    if focal_match:
        focal = f"{focal_match.group(1)}mm"
    aperture_match = re.search(r"f\s*(\d+(?:\.\d+)?)", text)
    if aperture_match:
        aperture = f"f{aperture_match.group(1)}"
    for token in ("lab", "air", "evo", "pro", "lite"):
        if token in text:
            series.add(token)
    for token in ("fe", "e", "z", "xf", "x", "m43", "pl", "ef", "rf"):
        if re.search(rf"\b{re.escape(token)}\b", text):
            mount.add(token)
    return {"focal": focal, "aperture": aperture, "series": series, "mount": mount}


def _clean_product_query(query: str) -> str:
    return re.sub(r"\s+", " ", _lower(query)).strip()


def _source_fields(row: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    default_table = "vkpi_memory_links" if row.get("link_type") or row.get("link_uid") else "computed:p4_new_launch_match"
    return {
        "source_table": row.get("source_table") or default_table,
        "source_id": str(row.get("source_id") or row.get("id") or payload.get("source_id") or ""),
        "source_ref": row.get("source_ref") or payload.get("source_ref") or "p4_new_launch_match:computed",
        "source_sheet": payload.get("source_sheet") or "",
        "source_row": payload.get("source_row") or "",
        "confidence_score": _safe_float(row.get("confidence_score"), 1.0),
    }


def _evidence(
    *,
    evidence_type: str,
    polarity: str,
    severity: str,
    detail: str,
    score_component: str,
    row: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = row or {}
    base = _source_fields(row, payload)
    return {
        "type": evidence_type,
        "polarity": polarity,
        "severity": severity,
        "detail": detail,
        "score_component": score_component,
        **base,
    }


def _fact_payload(row: dict[str, Any]) -> dict[str, Any]:
    fact = _load_json(row.get("fact_json") or "{}", {})
    return fact if isinstance(fact, dict) else {}


def _source_payload(row: dict[str, Any]) -> dict[str, Any]:
    source = _load_json(row.get("source_json") or "{}", {})
    return source if isinstance(source, dict) else {}


def _entity_payload(row: dict[str, Any], key: str) -> dict[str, Any]:
    payload = _load_json(row.get(key) or "{}", {})
    return payload if isinstance(payload, dict) else {}


def _select_target_family(product_query: str) -> dict[str, Any]:
    query = _clean_product_query(product_query)
    if not query:
        raise ValueError("product query is required")
    conn = get_conn()
    like = f"%{query}%"
    exact = conn.execute(
        """
        SELECT *
        FROM vkpi_memory_entities
        WHERE entity_type='product_family'
          AND (lower(display_name)=? OR lower(identity_key)=?)
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (query, query),
    ).fetchone()
    if exact:
        return _row_to_dict(exact)

    family = conn.execute(
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
        LIMIT 1
        """,
        (like, like),
    ).fetchone()
    if family:
        return _row_to_dict(family)

    product = conn.execute(
        """
        SELECT f.*
        FROM vkpi_memory_entities p
        JOIN vkpi_memory_links nl
          ON nl.source_entity_id=p.id
         AND nl.link_type='normalized_to_product_family'
        JOIN vkpi_memory_entities f ON f.id=nl.target_entity_id
        WHERE p.entity_type='product'
          AND (lower(p.display_name) LIKE ? OR lower(p.identity_key) LIKE ?)
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 1
        """,
        (like, like),
    ).fetchone()
    if product:
        return _row_to_dict(product)
    raise ValueError(f"no product_family found for query: {product_query}")


# Brand prefixes seen in operator queries / catalog model names. Used to build a short
# distinctive "<brand> <focal>" probe (e.g. "epic 65", "af 35") that is a likely
# substring of a real product_family display_name.
_BRAND_TOKENS = ("epic", "af", "mc", "dc", "vp", "mf", "lab", "air", "evo", "pro")


def _focal_of(text: str) -> str:
    """Extract a 2-3 digit focal length token from free text (e.g. '65mm' -> '65')."""
    low = _lower(text)
    match = (
        re.search(r"(\d{2,3})\s*mm", low)
        or re.search(r"(\d{2,3})\s*macro", low)
        or re.search(r"\b(\d{2,3})\b", low)
    )
    return match.group(1) if match else ""


def _ascii_clean(query: str) -> str:
    """Strip punctuation + non-ASCII (e.g. Chinese suffix) so a real-product query like
    'EPIC-65mm 微距电影镜头' collapses to the catalog-shaped 'epic 65mm'."""
    low = re.sub(r"[^a-z0-9 ]", " ", _lower(query))
    return re.sub(r"\s+", " ", low).strip()


def _query_derived_candidates(query: str) -> list[str]:
    """Cheap, resolver-free candidate queries derived from the raw operator text alone.

    Handles the common real-product-with-noise case (hyphens / Chinese marketing suffix)
    without touching the catalog: the ASCII-clean form plus a short '<brand> <focal>' probe.
    """
    out: list[str] = []
    cleaned = _ascii_clean(query)
    if cleaned and cleaned != _lower(query).strip():
        out.append(cleaned)
    tokens = cleaned.split()
    focal = _focal_of(query) or _focal_of(cleaned)
    brand = next((tok for tok in tokens if tok in _BRAND_TOKENS), "")
    if brand and focal:
        out.append(f"{brand} {focal}")
    return out


def _resolver_derived_candidates(query: str) -> list[str]:
    """Candidate queries derived by resolving raw text against the real product catalog.

    Calls kol.product_resolver.resolve_product (read only, never raises) to turn glued /
    nickname operator text ('epic 65macro', '550pro') into a canonical SKU, then emits
    catalog-name forms ordered from most-distinctive to broadest so the first one that is a
    substring of a product_family display_name wins. Returns [] if the resolver has no hit.
    """
    try:
        from app.domains.kol.product_resolver import resolve_product

        resolved = resolve_product(query)
    except Exception:
        resolved = None
    if not resolved:
        return []
    out: list[str] = []
    series = _lower(resolved.get("series")).strip()
    # Prefer the focal the operator actually asked for; fall back to the resolved name.
    focal = _focal_of(query) or _focal_of(
        _text(resolved.get("model_name") or resolved.get("marketing_name"))
    )
    if series and focal:
        out.append(f"{series} {focal}")
    for key in ("marketing_name", "model_name"):
        name = _lower(resolved.get(key)).strip()
        if not name:
            continue
        name = re.sub(r"^viltrox\s+", "", name).strip()
        tokens = name.split()
        # Longest-prefix-first: the family name is usually a leading substring of the full
        # catalog marketing name (e.g. 'epic 65mm t2.8 macro' ⊂ '...1.33x pl ... cine lens').
        for take in range(len(tokens), 1, -1):
            out.append(" ".join(tokens[:take]))
    return out


def resolve_target_family(product_query: str) -> tuple[dict[str, Any] | None, str, str]:
    """Graceful product-family resolution: never raises, returns (family|None, used_query, reason).

    Tries, in order, until a product_family is found:
      1. the raw query verbatim (preserves all currently-working direct matches);
      2. query-derived candidates (ASCII-clean + '<brand> <focal>' short probe);
      3. resolver-derived candidates (real catalog SKU → catalog-name prefixes).

    On success returns (family_dict, the_candidate_that_matched, ""). On total miss returns
    (None, "", reason) so callers can honestly surface recs=0 instead of crashing. Read only;
    never writes any score field.
    """
    raw = _text(product_query)
    candidates: list[str] = [raw]
    candidates.extend(_query_derived_candidates(raw))
    candidates.extend(_resolver_derived_candidates(raw))

    tried: list[str] = []
    for candidate in candidates:
        candidate = _text(candidate)
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        try:
            return _select_target_family(candidate), candidate, ""
        except ValueError:
            continue
        except Exception:  # pragma: no cover - DB hiccup; treat as a miss, don't crash caller
            continue
    return (
        None,
        "",
        f"no product_family matched query '{raw}' or {len(tried)} derived candidate(s)",
    )


def _product_family_maps() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    conn = get_conn()
    product_to_family: dict[int, dict[str, Any]] = {}
    family_by_id: dict[int, dict[str, Any]] = {}
    rows = conn.execute(
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
    for raw in rows:
        row = _row_to_dict(raw)
        product_to_family[int(row["product_id"])] = row
        family_by_id[int(row["family_id"])] = {
            "id": int(row["family_id"]),
            "entity_uid": row["family_uid"],
            "display_name": row["family_name"],
            "identity_key": row["family_key"],
            "metadata_json": row["family_metadata_json"],
        }
    return product_to_family, family_by_id


def _kol_entities() -> list[dict[str, Any]]:
    return [
        _row_to_dict(row)
        for row in get_conn().execute(
        """
        SELECT id, entity_uid, display_name, status, identity_json, metadata_json
        FROM vkpi_memory_entities
        WHERE entity_type='kol'
          AND status IN ('active', 'imported', 'needs_human_review')
        ORDER BY id
        """
        ).fetchall()
    ]


def _pool_by_source_ref() -> dict[str, dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT id, platform, handle, display_name, country, source_ref,
               sync_status, raw_platform_data,
               followers, avg_views, avg_comments, engagement_rate
        FROM vkpi_kol_pool
        WHERE source_type='legacy_excel_p2d'
        """
    ).fetchall()
    return {_text(row["source_ref"]): _row_to_dict(row) for row in rows}


def _legacy_entities_by_uid() -> dict[str, dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT id, entity_uid, weak_label, resolution_decision
        FROM vkpi_legacy_kol_entities
        """
    ).fetchall()
    return {_text(row["entity_uid"]): _row_to_dict(row) for row in rows}


def _kol_facts() -> dict[int, list[dict[str, Any]]]:
    rows = get_conn().execute(
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
    for row in rows:
        item = _row_to_dict(row)
        facts[int(item["entity_id"])].append(item)
    return facts


def _worked_links() -> dict[int, list[dict[str, Any]]]:
    rows = get_conn().execute(
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
    for row in rows:
        item = _row_to_dict(row)
        links[int(item["source_entity_id"])].append(item)
    return links


def _target_market_signals(target_family_id: int) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        """
        SELECT *
        FROM vkpi_memory_facts
        WHERE entity_id=?
          AND fact_type IN ('market_signal', 'launch_plan')
        ORDER BY observed_at DESC, id DESC
        """,
        (int(target_family_id),),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _latest_fact_value(facts: list[dict[str, Any]], fact_type: str) -> str:
    for fact in facts:
        if _text(fact.get("fact_type")) == fact_type:
            return _text(fact.get("fact_value_text"))
    return ""


def _first_fact(facts: list[dict[str, Any]], fact_type: str) -> dict[str, Any]:
    for fact in facts:
        if _text(fact.get("fact_type")) == fact_type:
            return fact
    return {}


def _evidence_count(facts: list[dict[str, Any]]) -> int:
    total = 0
    for fact in facts:
        if _text(fact.get("fact_type")) != "evidence_count":
            continue
        payload = _fact_payload(fact)
        total += _safe_int(payload.get("count"), _safe_int(fact.get("fact_value_text")))
    return total


def _risk_count(facts: list[dict[str, Any]]) -> int:
    return sum(1 for fact in facts if _text(fact.get("fact_type")) == "risk_flag")


def _contact_score(contact_status: str, pool: dict[str, Any] | None) -> tuple[int, str]:
    raw = _load_json((pool or {}).get("raw_platform_data") or "{}", {})
    has_email = bool(raw.get("contact_has_email")) if isinstance(raw, dict) else False
    has_phone = bool(raw.get("contact_has_phone")) if isinstance(raw, dict) else False
    if has_email and has_phone:
        return 10, "email_and_phone_available_restricted"
    if has_email:
        return 7, "email_available_restricted"
    normalized = _lower(contact_status)
    if normalized in {"available_restricted", "dm_only"}:
        return 4, normalized
    if normalized == "missing":
        return 0, "missing"
    return 2, normalized or "unknown"


def _cooperation_score(count: int) -> int:
    if count >= 10:
        return 15
    if count >= 5:
        return 10
    if count >= 2:
        return 6
    if count == 1:
        return 3
    return 0


def _freshness_score(count: int) -> int:
    if count >= 10:
        return 5
    if count >= 5:
        return 3
    if count > 0:
        return 1
    return 0


def _region_score(country: str, primary: set[str], secondary: set[str]) -> tuple[int, str]:
    key = _country_key(country)
    if not key:
        return 5, "missing_country"
    if primary and key in primary:
        return 10, "primary_target_market"
    if secondary and key in secondary:
        return 6, "secondary_target_market"
    if not primary and not secondary:
        return 3, "known_country_no_target_market"
    return 3, "other_known_market"


def _product_match_score(
    *,
    target_family: dict[str, Any],
    links: list[dict[str, Any]],
    product_to_family: dict[int, dict[str, Any]],
) -> tuple[int, str, dict[str, Any] | None]:
    target_family_id = int(target_family["id"])
    target_tokens = _family_tokens(_text(target_family.get("display_name") or target_family.get("identity_key")))
    best: tuple[int, str, dict[str, Any] | None] = (0, "no_product_family_match", None)
    for link in links:
        family = product_to_family.get(int(link["target_entity_id"]))
        if not family:
            continue
        if int(family["family_id"]) == target_family_id:
            return 25, "direct_family_match", link
        tokens = _family_tokens(_text(family.get("family_name") or family.get("family_key")))
        same_focal = bool(target_tokens["focal"] and target_tokens["focal"] == tokens["focal"])
        shared_mount = bool(target_tokens["mount"] and tokens["mount"] and target_tokens["mount"] & tokens["mount"])
        if same_focal or shared_mount:
            if best[0] < 15:
                best = (15, "adjacent_family_match", link)
            continue
        if target_tokens["focal"] or tokens["focal"]:
            if best[0] < 10:
                best = (10, "same_product_type", link)
    return best


def _market_signal_score(signals: list[dict[str, Any]], *, now: datetime) -> tuple[int, list[dict[str, Any]]]:
    score = 0
    evidence_rows: list[dict[str, Any]] = []
    launch = None
    official = None
    for signal in signals:
        payload = _fact_payload(signal)
        signal_type = _text(payload.get("signal_type") or signal.get("fact_type"))
        date_value = payload.get("signal_date") or payload.get("launch_date") or payload.get("publish_date")
        if signal_type == "launch_plan" and launch is None and _within_days(date_value, 30, now=now):
            launch = signal
        if signal_type in {"official_content", "official_material"} and official is None and _within_days(date_value, 90, now=now):
            official = signal
        if launch and official:
            break
    if launch:
        score += 10
        evidence_rows.append(launch)
    if official:
        score += 5
        evidence_rows.append(official)
    return score, evidence_rows


def _percentile(index: int, total: int) -> float:
    if total <= 1:
        return 100.0
    return round(100 * (total - 1 - index) / (total - 1), 1)


def _median_score(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    scores = sorted(float(item["score"]) for item in items)
    mid = len(scores) // 2
    if len(scores) % 2:
        return scores[mid]
    return round((scores[mid - 1] + scores[mid]) / 2, 1)


def _distribution(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "p90_plus": sum(1 for item in items if int(item["percentile_rank"]) >= 90),
        "p75_to_p90": sum(1 for item in items if 75 <= int(item["percentile_rank"]) < 90),
        "p50_to_p75": sum(1 for item in items if 50 <= int(item["percentile_rank"]) < 75),
        "below_p50": sum(1 for item in items if int(item["percentile_rank"]) < 50),
    }


def _open_link(entity_uid: str) -> str:
    return f"/kol/{entity_uid}"


__all__ = [name for name in globals() if name.startswith("_") and not name.startswith("__")]
