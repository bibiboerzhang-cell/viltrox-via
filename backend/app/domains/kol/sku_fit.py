"""Read-only KOL x SKU Product Fit v1 rule engine."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.products import product_aliases


logger = get_logger(__name__)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        logger.debug("Postgres table lookup failed for %s; trying sqlite fallback", table_name, exc_info=True)
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _norm(value: Any) -> str:
    return product_aliases.normalize_alias(value)


def _compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _norm(value)).strip()


def _json_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    parsed = _loads(value, value)
    return json.dumps(parsed, ensure_ascii=False, default=str) if isinstance(parsed, (dict, list)) else _text(parsed)


def _fetch_kol(kol_pool_id: int) -> dict[str, Any]:
    row = get_conn().execute(
        """
        SELECT id, platform, handle, display_name, bio, followers, avg_views,
               engagement_rate, viltrox_fit_score, raw_platform_data,
               brand_collaborations_json, recommended_product_lines_json,
               potential_concerns_json, source_ref, updated_at
        FROM vkpi_kol_pool
        WHERE id=?
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return _row_dict(row)


def select_default_kol_id(*, query: str = "viltrox") -> int:
    query_norm = f"%{_text(query).lower()}%"
    row = get_conn().execute(
        """
        SELECT id
        FROM vkpi_kol_pool
        WHERE LOWER(COALESCE(raw_platform_data, '') || ' ' || COALESCE(bio, '') || ' ' || COALESCE(recommended_product_lines_json, '')) LIKE ?
        ORDER BY COALESCE(viltrox_fit_score, 0) DESC, updated_at DESC, id DESC
        LIMIT 1
        """,
        (query_norm,),
    ).fetchone()
    if row:
        return _int(row["id"])
    row = get_conn().execute(
        """
        SELECT id
        FROM vkpi_kol_pool
        ORDER BY COALESCE(viltrox_fit_score, 0) DESC, updated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return _int(row["id"]) if row else 0


def _kol_corpus(kol: dict[str, Any]) -> tuple[str, dict[str, str]]:
    parts = {
        "identity": " ".join([_text(kol.get("display_name")), _text(kol.get("handle")), _text(kol.get("platform"))]),
        "bio": _text(kol.get("bio")),
        "raw_platform_data": _json_text(kol.get("raw_platform_data")),
        "brand_collaborations": _json_text(kol.get("brand_collaborations_json")),
        "recommended_product_lines": _json_text(kol.get("recommended_product_lines_json")),
        "potential_concerns": _json_text(kol.get("potential_concerns_json")),
    }
    norm_parts = {key: _compact_text(value) for key, value in parts.items() if _text(value)}
    corpus = " ".join(norm_parts.values())
    return corpus, norm_parts


def _fetch_sku_facts(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_product_spec_facts"):
        return []
    rows = get_conn().execute(
        """
        SELECT sku, category_main, category_detail, series, mount, mount_norm,
               lens_mount, lens_mount_norm, focal_length_label,
               focal_length_min_mm, focal_length_max_mm, max_aperture_label,
               max_aperture_f, min_aperture_label, weight_grams, filter_size_mm,
               price_usd, product_url, fit_tags_json, source_confidence,
               completeness_score, missing_fields_json, raw_spec_fields_json
        FROM vkpi_product_spec_facts
        ORDER BY completeness_score DESC, source_confidence DESC, sku ASC
        LIMIT ?
        """,
        (max(1, min(2000, int(limit or 500))),),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _fetch_aliases_by_sku(skus: list[str], *, per_sku_limit: int = 12) -> dict[str, list[dict[str, Any]]]:
    if not skus or not _table_exists("vkpi_product_aliases"):
        return {sku: [] for sku in skus}
    rows = get_conn().execute(
        """
        SELECT sku, alias, alias_norm, alias_type, confidence
        FROM vkpi_product_aliases
        WHERE sku IN ({})
        ORDER BY sku ASC, confidence DESC, alias_type ASC
        """.format(",".join(["?"] * len(skus))),
        tuple(skus),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {sku: [] for sku in skus}
    for row in rows:
        item = _row_dict(row)
        sku = _text(item.get("sku"))
        if len(grouped.setdefault(sku, [])) < per_sku_limit:
            grouped[sku].append(item)
    return grouped


def _load_profile_product_fit(kol_pool_id: int) -> dict[str, dict[str, Any]]:
    if not _table_exists("vkpi_kol_profile_deep"):
        return {}
    row = get_conn().execute(
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
    payload = _loads(row["dimensions_11_json"], {})
    block4 = payload.get("block4_specialty") if isinstance(payload, dict) and isinstance(payload.get("block4_specialty"), dict) else {}
    raw_fit = block4.get("product_fit") if isinstance(block4.get("product_fit"), dict) else {}
    raw_conf = block4.get("product_fit_confidence") if isinstance(block4.get("product_fit_confidence"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for label, score in raw_fit.items():
        norm = _norm(label)
        if not norm:
            continue
        result[norm] = {
            "label": label,
            "score": max(0.0, min(100.0, _float(score))),
            "confidence": max(0.0, min(1.0, _float(raw_conf.get(label), 0.0))),
            "profile_deep_id": row["id"],
        }
    return result


def _contains_phrase(corpus: str, phrase: str) -> bool:
    if not phrase or len(phrase) < 3:
        return False
    return f" {phrase} " in f" {corpus} "


def _alias_component(corpus: str, aliases: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    weights = {
        "sku": 48.0,
        "sku_spaced": 44.0,
        "sku_slug": 44.0,
        "model": 42.0,
        "marketing": 40.0,
        "compact_brand": 38.0,
        "compact_model": 35.0,
        "official_handle": 30.0,
        "spec_combo_mount": 22.0,
        "spec_combo_series": 18.0,
        "spec_combo": 14.0,
    }
    matched: list[dict[str, Any]] = []
    best = 0.0
    for alias in aliases:
        alias_norm = _text(alias.get("alias_norm"))
        if not _contains_phrase(corpus, alias_norm):
            continue
        weight = weights.get(_text(alias.get("alias_type")), 10.0)
        confidence = max(0.2, min(1.0, _float(alias.get("confidence"), 0.5)))
        score = weight * confidence
        best = max(best, score)
        matched.append(
            {
                "type": "alias_match",
                "alias": alias.get("alias"),
                "alias_type": alias.get("alias_type"),
                "score": round(score, 2),
            }
        )
    matched.sort(key=lambda item: float(item["score"]), reverse=True)
    return round(best, 2), matched[:5]


def _spec_component(corpus: str, fact: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    score = 0.0
    focal = _text(fact.get("focal_length_label")).split("/")[0]
    aperture = _text(fact.get("max_aperture_label"))
    series = _text(fact.get("series"))
    mount = _text(fact.get("mount"))
    for label, weight, evidence_type in (
        (focal, 12.0, "focal_match"),
        (aperture, 10.0, "aperture_match"),
        (series, 6.0, "series_match"),
        (mount, 5.0, "mount_match"),
    ):
        norm = _norm(label)
        if norm and _contains_phrase(corpus, norm):
            score += weight
            evidence.append({"type": evidence_type, "value": label, "score": weight})
    if any(token in corpus for token in ("camera", "lens", "review", "photography", "cinematic", "filmmaking", "bokeh")):
        category = _text(fact.get("category_detail") or fact.get("category_main")).lower()
        if "lens" in category or "cine" in category:
            score += 8.0
            evidence.append({"type": "category_context", "value": category, "score": 8.0})
    return round(min(35.0, score), 2), evidence


def _profile_component(profile_fit: dict[str, dict[str, Any]], aliases: list[dict[str, Any]], fact: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    if not profile_fit:
        return 0.0, []
    candidate_norms = {_norm(fact.get("sku"))}
    candidate_norms.update(_text(alias.get("alias_norm")) for alias in aliases if _text(alias.get("alias_norm")))
    best_score = 0.0
    best: dict[str, Any] | None = None
    for norm, item in profile_fit.items():
        if any(norm and candidate and (norm in candidate or candidate in norm) for candidate in candidate_norms):
            component = (_float(item.get("score")) / 100.0) * max(0.1, _float(item.get("confidence"), 0.0)) * 25.0
            if component > best_score:
                best_score = component
                best = item
    if not best:
        return 0.0, []
    return round(best_score, 2), [
        {
            "type": "dimensions11_product_fit",
            "label": best.get("label"),
            "score": round(best_score, 2),
            "profile_deep_id": best.get("profile_deep_id"),
        }
    ]


def _score_sku(corpus: str, aliases: list[dict[str, Any]], fact: dict[str, Any], profile_fit: dict[str, dict[str, Any]]) -> dict[str, Any]:
    alias_score, alias_evidence = _alias_component(corpus, aliases)
    spec_score, spec_evidence = _spec_component(corpus, fact)
    profile_score, profile_evidence = _profile_component(profile_fit, aliases, fact)
    viltrox_bonus = 5.0 if "viltrox" in corpus and (alias_evidence or spec_evidence or profile_evidence) else 0.0
    completeness = max(0.0, min(100.0, _float(fact.get("completeness_score"), 0.0)))
    raw_score = alias_score + spec_score + profile_score + viltrox_bonus
    evidence = alias_evidence + spec_evidence + profile_evidence
    confidence = min(1.0, round((completeness / 100.0) * 0.35 + min(len(evidence), 5) * 0.12 + (_float(fact.get("source_confidence"), 0.0) * 0.15), 3))
    return {
        "sku": fact.get("sku"),
        "score": round(min(100.0, raw_score), 2),
        "confidence": confidence,
        "score_breakdown": {
            "alias": alias_score,
            "spec": spec_score,
            "dimensions11": profile_score,
            "viltrox_context": viltrox_bonus,
            "spec_completeness": completeness,
        },
        "evidence": evidence,
        "spec_fact": {
            "mount_norm": fact.get("mount_norm"),
            "focal_length_label": fact.get("focal_length_label"),
            "max_aperture_label": fact.get("max_aperture_label"),
            "weight_grams": fact.get("weight_grams"),
            "price_usd": fact.get("price_usd"),
            "product_url": fact.get("product_url"),
            "missing_fields": _loads(fact.get("missing_fields_json"), []),
        },
    }


def build_kol_sku_fit_report(*, kol_pool_id: int = 0, query: str = "viltrox", sku_limit: int = 500, top_n: int = 12) -> dict[str, Any]:
    selected_id = int(kol_pool_id or 0) or select_default_kol_id(query=query)
    kol = _fetch_kol(selected_id) if selected_id else {}
    facts = _fetch_sku_facts(sku_limit)
    aliases_by_sku = _fetch_aliases_by_sku([_text(fact.get("sku")) for fact in facts if _text(fact.get("sku"))])
    profile_fit = _load_profile_product_fit(selected_id) if selected_id else {}
    corpus, corpus_sections = _kol_corpus(kol)
    scored = [
        _score_sku(corpus, aliases_by_sku.get(_text(fact.get("sku")), []), fact, profile_fit)
        for fact in facts
    ]
    scored = [item for item in scored if float(item.get("score") or 0) > 0 or item["evidence"]]
    scored.sort(key=lambda item: (float(item.get("score") or 0), float(item.get("confidence") or 0)), reverse=True)
    top = scored[: max(1, min(50, int(top_n or 12)))]
    checks = {
        "kol_selected": bool(kol),
        "spec_facts_available": bool(facts),
        "aliases_available": any(aliases_by_sku.values()),
        "top_sku_candidates_generated": bool(top),
        "top_candidates_have_evidence": all(bool(item.get("evidence")) for item in top),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "write_db_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p5_64_kol_sku_fit_dry_run",
        "generated_at": _now(),
        "kol_pool_id": selected_id,
        "query": query,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "sku_fact_count": len(facts),
            "scored_sku_count": len(scored),
            "top_count": len(top),
            "profile_product_fit_candidates": len(profile_fit),
            "corpus_section_count": len(corpus_sections),
        },
        "kol": {
            "id": kol.get("id"),
            "platform": kol.get("platform"),
            "handle": kol.get("handle"),
            "display_name": kol.get("display_name"),
            "viltrox_fit_score": kol.get("viltrox_fit_score"),
        },
        "top_skus": top,
    }
