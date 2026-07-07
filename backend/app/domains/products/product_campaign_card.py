"""P5.70 read-only product campaign planning card."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.products import product_aliases


logger = get_logger(__name__)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


from app.core.coerce import _text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value if value is not None else default)
        return parsed if parsed == parsed else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        logger.debug("Failed to decode product campaign JSON payload", exc_info=True)
        return fallback


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


def _normalize(value: Any) -> str:
    return product_aliases.normalize_alias(value)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", _normalize(value)).strip()


def _fetch_sku(sku: str = "") -> dict[str, Any]:
    if not _table_exists("vkpi_product_spec_facts"):
        return {}
    conn = get_conn()
    if sku:
        row = conn.execute(
            """
            SELECT *
            FROM vkpi_product_spec_facts
            WHERE UPPER(sku)=UPPER(?)
            LIMIT 1
            """,
            (_text(sku),),
        ).fetchone()
        return dict(row) if row else {}
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_product_spec_facts
        ORDER BY completeness_score DESC, source_confidence DESC, sku ASC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else {}


def _fetch_aliases(sku: str) -> list[dict[str, Any]]:
    if not sku or not _table_exists("vkpi_product_aliases"):
        return []
    rows = get_conn().execute(
        """
        SELECT sku, alias, alias_norm, alias_type, confidence
        FROM vkpi_product_aliases
        WHERE UPPER(sku)=UPPER(?)
        ORDER BY confidence DESC, alias_type ASC
        LIMIT 80
        """,
        (sku,),
    ).fetchall()
    return [dict(row) for row in rows]


def _kol_rows(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_kol_pool"):
        return []
    rows = get_conn().execute(
        """
        SELECT id, platform, handle, display_name, bio, followers, avg_views,
               engagement_rate, viltrox_fit_score, raw_platform_data,
               brand_collaborations_json, recommended_product_lines_json,
               potential_concerns_json, updated_at
        FROM vkpi_kol_pool
        ORDER BY COALESCE(viltrox_fit_score, 0) DESC, COALESCE(followers, 0) DESC, updated_at DESC
        LIMIT ?
        """,
        (max(1, min(500, int(limit or 200))),),
    ).fetchall()
    return [dict(row) for row in rows]


def _competitor_rows(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_competitor_signals"):
        return []
    rows = get_conn().execute(
        """
        SELECT id, brand, normalized_brand, signal_type, severity, score,
               product_hints_json, source_url, platform, detail, review_status
        FROM vkpi_competitor_signals
        ORDER BY score DESC, created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(200, int(limit or 80))),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["product_hints"] = _loads(data.get("product_hints_json"), [])
        items.append(data)
    return items


def _kol_corpus(kol: dict[str, Any]) -> str:
    parts = [
        kol.get("display_name"),
        kol.get("handle"),
        kol.get("platform"),
        kol.get("bio"),
        kol.get("raw_platform_data"),
        kol.get("brand_collaborations_json"),
        kol.get("recommended_product_lines_json"),
        kol.get("potential_concerns_json"),
    ]
    return _compact(" ".join(_text(part) for part in parts if _text(part)))


def _contains(corpus: str, phrase: str) -> bool:
    if not phrase or len(phrase) < 3:
        return False
    return f" {phrase} " in f" {corpus} "


def _sku_terms(fact: dict[str, Any]) -> list[str]:
    mount = fact.get("mount") or fact.get("lens_mount")
    mount_norm = fact.get("mount_norm") or fact.get("lens_mount_norm")
    terms = [
        fact.get("sku"),
        mount,
        mount_norm,
        fact.get("focal_length_label"),
        fact.get("max_aperture_label"),
        fact.get("series"),
    ]
    generic = {"af", "lens", "camera lens", "mount"}
    return [term for term in (_normalize(value) for value in terms) if term and term not in generic]


def _score_kol(kol: dict[str, Any], fact: dict[str, Any], aliases: list[dict[str, Any]]) -> dict[str, Any]:
    corpus = _kol_corpus(kol)
    evidence: list[dict[str, Any]] = []
    alias_score = 0.0
    for alias in aliases:
        alias_norm = _text(alias.get("alias_norm"))
        if _contains(corpus, alias_norm):
            score = min(28.0, 20.0 * max(0.2, _float(alias.get("confidence"), 0.5)))
            alias_score = max(alias_score, score)
            evidence.append({"type": "alias_match", "value": alias.get("alias"), "score": round(score, 2)})
    spec_score = 0.0
    for term in _sku_terms(fact):
        if _contains(corpus, term):
            weight = 10.0 if term in {_normalize(fact.get("mount")), _normalize(fact.get("mount_norm"))} else 7.0
            spec_score = min(22.0, spec_score + weight)
            evidence.append({"type": "spec_context", "value": term, "score": weight})
    fit_score = min(35.0, max(0.0, _float(kol.get("viltrox_fit_score")) * 0.35))
    audience_score = min(18.0, math.log10(max(1, _int(kol.get("followers")))) * 3.0)
    views_score = min(12.0, math.log10(max(1, _int(kol.get("avg_views")))) * 2.4)
    raw_score = fit_score + audience_score + views_score + alias_score + spec_score
    risk_flags: list[str] = []
    concerns = _compact(kol.get("potential_concerns_json"))
    if any(token in concerns for token in ("competitor", "risk", "avoid")):
        risk_flags.append("profile_concern")
        raw_score -= 8.0
    score = round(max(0.0, min(100.0, raw_score)), 2)
    confidence = round(min(1.0, 0.25 + min(len(evidence), 5) * 0.12 + (0.18 if fit_score > 0 else 0.0)), 3)
    return {
        "kol_pool_id": kol.get("id"),
        "platform": kol.get("platform"),
        "handle": kol.get("handle"),
        "display_name": kol.get("display_name"),
        "followers": kol.get("followers"),
        "avg_views": kol.get("avg_views"),
        "viltrox_fit_score": kol.get("viltrox_fit_score"),
        "score": score,
        "confidence": confidence,
        "risk_flags": risk_flags,
        "evidence": evidence[:6],
    }


def _market_risks(fact: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    sku_terms = set(_sku_terms(fact))
    brand_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    related: list[dict[str, Any]] = []
    for signal in signals:
        brand = _text(signal.get("normalized_brand") or signal.get("brand") or "unknown")
        signal_type = _text(signal.get("signal_type") or "unknown")
        brand_counts[brand] += 1
        type_counts[signal_type] += 1
        detail = _compact(signal.get("detail"))
        hints = " ".join(_normalize(item) for item in (signal.get("product_hints") or []))
        if any(term and (term in detail or term in hints) for term in sku_terms):
            related.append(
                {
                    "id": signal.get("id"),
                    "brand": brand,
                    "signal_type": signal_type,
                    "score": _float(signal.get("score")),
                    "platform": signal.get("platform"),
                    "detail": _text(signal.get("detail"))[:180],
                }
            )
    risk_score = len(related) * 12 + sum(type_counts.get(key, 0) for key in ("pricing_sensitive", "risk_watch", "voc_issue")) * 3
    if risk_score >= 60:
        risk_tier = "high"
    elif risk_score >= 25:
        risk_tier = "medium"
    else:
        risk_tier = "low"
    return {
        "risk_tier": risk_tier,
        "risk_score": risk_score,
        "top_competitor_brands": [{"brand": brand, "count": count} for brand, count in brand_counts.most_common(6)],
        "signal_types": dict(type_counts),
        "related_signals": sorted(related, key=lambda item: float(item.get("score") or 0), reverse=True)[:8],
    }


def build_product_campaign_card(*, sku: str = "", kol_limit: int = 200, top_kols: int = 12) -> dict[str, Any]:
    fact = _fetch_sku(sku)
    aliases = _fetch_aliases(_text(fact.get("sku")))
    kols = _kol_rows(kol_limit)
    signals = _competitor_rows(100)
    candidates = [_score_kol(kol, fact, aliases) for kol in kols] if fact else []
    candidates = [item for item in candidates if item["score"] > 0 and item["evidence"]]
    candidates.sort(key=lambda item: (float(item["score"]), float(item["confidence"])), reverse=True)
    selected = candidates[: max(1, min(50, int(top_kols or 12)))]
    market = _market_risks(fact, signals) if fact else {"risk_tier": "unknown", "risk_score": 0, "top_competitor_brands": [], "signal_types": {}, "related_signals": []}
    missing_fields = _loads(fact.get("missing_fields_json"), []) if fact else []
    checks = {
        "sku_selected": bool(fact),
        "aliases_available": bool(aliases),
        "kol_pool_available": bool(kols),
        "kol_candidates_generated": bool(selected),
        "kol_candidates_have_evidence": all(bool(item.get("evidence")) for item in selected),
        "market_signals_available": bool(signals),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "writes_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p5_70_product_campaign_card",
        "generated_at": _now(),
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "sku": fact.get("sku"),
            "kol_candidates": len(selected),
            "market_risk_tier": market.get("risk_tier"),
            "missing_spec_fields": missing_fields,
            "source_scope": "existing_db_only",
        },
        "product": {
            "sku": fact.get("sku"),
            "series": fact.get("series"),
            "category_main": fact.get("category_main"),
            "category_detail": fact.get("category_detail"),
            "mount": fact.get("mount") or fact.get("lens_mount"),
            "focal_length_label": fact.get("focal_length_label"),
            "max_aperture_label": fact.get("max_aperture_label"),
            "weight_grams": fact.get("weight_grams"),
            "price_usd": fact.get("price_usd"),
            "product_url": fact.get("product_url"),
            "completeness_score": fact.get("completeness_score"),
            "missing_fields": missing_fields,
        },
        "kol_candidates": selected,
        "market_risk": market,
        "campaign_actions": [
            "Pick evidence-backed KOL candidates before outreach.",
            "Review related market risk signals before positioning copy.",
            "Create campaign/project records only after human approval.",
            "Do not use this card as an automatic recommendation.",
        ],
        "policy": {
            "read_only": True,
            "no_provider_or_llm": True,
            "no_project_created": True,
            "human_approval_required": True,
        },
    }
