"""Deterministic classifier for raw V-KPI market mentions.

R4 is deliberately dry-run only. It reads raw market mentions and proposes
reviewable competitor-signal candidates without writing promotion rows.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn


VILTROX_TERMS = {"viltrox", "weeylite", "viltrox epic", "viltrox jy"}
BRAND_ALIASES = {
    "tt artisan": "ttartisan",
    "small rig": "smallrig",
    "red camera": "red",
    "arri light": "arri",
}
PROBLEM_PATTERN = re.compile(r"\b(faulty|stuck|problem|issue|fail|broken|compatibility|fix|overheat|noise)\b", re.I)
QUESTION_PATTERN = re.compile(r"\b(question|advice|tips|recommend|choice|which|equivalent|worth|upgrade|compact)\b", re.I)
PRICE_PATTERN = re.compile(r"\b(price|cheap|expensive|budget|deal|discount|cost)\b", re.I)
COMPARISON_PATTERN = re.compile(r"\b(compare|comparison|vs|versus|equivalent|alternative|worth)\b", re.I)
LAUNCH_PATTERN = re.compile(r"\b(new|launch|released|announce|preorder|pre-order|rumor|prototype)\b", re.I)


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _safe_float(value: Any) -> float:
    try:
        parsed = float(value or 0)
        return parsed if parsed == parsed else 0.0
    except Exception:
        return 0.0


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _split_csv(value: Any) -> list[str]:
    brands = set()
    for item in str(value or "").split(","):
        brand = item.strip().lower()
        if brand:
            brands.add(BRAND_ALIASES.get(brand, brand))
    return sorted(brands)


def _severity(score: float, signal_type: str) -> str:
    if signal_type in {"voc_issue", "pricing_sensitive"} and score >= 0.3:
        return "medium"
    if score >= 0.65:
        return "high"
    if score >= 0.34:
        return "medium"
    return "low"


def _signal_type(text: str, competitor_products: list[str], metadata: dict[str, Any]) -> str:
    lowered = text.lower()
    if PRICE_PATTERN.search(lowered):
        return "pricing_sensitive"
    if PROBLEM_PATTERN.search(lowered):
        return "voc_issue"
    if COMPARISON_PATTERN.search(lowered):
        return "product_comparison"
    if LAUNCH_PATTERN.search(lowered):
        return "competitor_launch"
    groups = metadata.get("keyword_groups") if isinstance(metadata.get("keyword_groups"), dict) else {}
    if any(groups.get(name) for name in ("tier1_lighting_competitors", "tier1_cross_industry", "tier2_wireless_video")):
        return "competitor_focus"
    if QUESTION_PATTERN.search(lowered):
        return "product_question"
    if competitor_products:
        return "competitor_mention"
    return "market_noise"


def _primary_category(product_sku: str, competitors: list[str], signal_type: str, score: float) -> str:
    product = product_sku.lower()
    if any(term in product for term in VILTROX_TERMS):
        return "viltrox_mention"
    if competitors:
        return "competitor_signal"
    if signal_type in {"product_question", "pricing_sensitive"} and score >= 0.28:
        return "product_opportunity"
    return "noise"


def _confidence(score: float, competitors: list[str], product_sku: str, metadata: dict[str, Any]) -> float:
    groups = metadata.get("keyword_groups") if isinstance(metadata.get("keyword_groups"), dict) else {}
    group_count = sum(1 for value in groups.values() if value)
    confidence = 0.36 + score * 0.45 + min(group_count, 4) * 0.04
    if competitors:
        confidence += 0.08
    if product_sku:
        confidence += 0.06
    return round(max(0.0, min(0.95, confidence)), 3)


def _candidate_uid(mention_id: int, brand: str, signal_type: str) -> str:
    basis = f"market-mention:{mention_id}:{brand}:{signal_type}"
    return f"mktcls-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def _classify_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = _loads(row.get("metadata_json"), {})
    mention_text = _text(row.get("mention_text"))
    product_sku = _text(row.get("product_sku")).lower()
    competitors = _split_csv(row.get("competitor_product"))
    score = _safe_float(row.get("score"))
    signal_type = _signal_type(mention_text, competitors, metadata)
    category = _primary_category(product_sku, competitors, signal_type, score)
    if category == "viltrox_mention":
        signal_type = "viltrox_product_mention"
    confidence = _confidence(score, competitors, product_sku, metadata)
    source_url = _text(metadata.get("source_url"), row.get("source_url"))
    source_id = row.get("id")
    proposed_signals = []
    if category == "competitor_signal":
        for brand in competitors:
            proposed_signals.append(
                {
                    "signal_uid": _candidate_uid(int(source_id or 0), brand, signal_type),
                    "brand": brand,
                    "normalized_brand": brand,
                    "signal_type": signal_type,
                    "severity": _severity(score, signal_type),
                    "score": round(score * 100, 3),
                    "product_hints": sorted(set(metadata.get("keyword_hits") or []))[:10],
                    "source_table": "vkpi_market_mentions",
                    "source_id": source_id,
                    "source_url": source_url,
                    "platform": _text(row.get("platform"), "reddit"),
                    "detail": mention_text,
                    "evidence": {
                        "classifier": "market_signal_classifier_v0",
                        "category": category,
                        "confidence": confidence,
                        "market_mention_id": source_id,
                        "source_url": source_url,
                        "keyword_groups": metadata.get("keyword_groups") or {},
                        "raw_review_status": metadata.get("review_status") or "raw_unreviewed",
                    },
                    "review_status": "pending_review",
                }
            )
    return {
        "mention_id": source_id,
        "platform": _text(row.get("platform"), "reddit"),
        "handle": _text(row.get("handle")),
        "mention_text": mention_text,
        "source_url": source_url,
        "product_sku": product_sku,
        "competitor_products": competitors,
        "score": score,
        "category": category,
        "signal_type": signal_type,
        "severity": _severity(score, signal_type),
        "confidence": confidence,
        "keyword_hits": metadata.get("keyword_hits") or [],
        "keyword_groups": metadata.get("keyword_groups") or {},
        "proposed_signals": proposed_signals,
    }


def _market_mentions(limit: int, run_id: int | None = None) -> list[dict[str, Any]]:
    safe_limit = max(1, min(500, int(limit or 100)))
    where = ""
    params: list[Any] = []
    if run_id is not None:
        where = "WHERE m.run_id=?"
        params.append(int(run_id))
    rows = get_conn().execute(
        f"""
        SELECT m.*, s.source_url AS joined_source_url
        FROM vkpi_market_mentions m
        LEFT JOIN vkpi_market_sources s ON s.id=m.source_id
        {where}
        ORDER BY m.score DESC, m.id ASC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        metadata = _loads(data.get("metadata_json"), {})
        if not metadata.get("source_url") and data.get("joined_source_url"):
            metadata["source_url"] = data.get("joined_source_url")
            data["metadata_json"] = metadata
        items.append(data)
    return items


def build_market_signal_classification(*, limit: int = 100, run_id: int | None = None) -> dict[str, Any]:
    rows = _market_mentions(limit, run_id=run_id)
    classifications = [_classify_row(row) for row in rows]
    proposed_signals = [signal for item in classifications for signal in item.get("proposed_signals") or []]
    category_counts = Counter(item["category"] for item in classifications)
    signal_type_counts = Counter(item["signal_type"] for item in classifications)
    brand_counts = Counter(
        brand
        for item in classifications
        for brand in item.get("competitor_products") or []
    )
    checks = {
        "write_db_blocked": True,
        "llm_calls_blocked": True,
        "reads_market_mentions": bool(rows),
        "proposed_signals_have_source": all(bool(item.get("source_id")) for item in proposed_signals),
        "viltrox_not_promoted_to_competitor": all(
            str(signal.get("brand") or "").lower() not in VILTROX_TERMS for signal in proposed_signals
        ),
    }
    return {
        "mode": "market_signal_classifier_v0",
        "generated_at": _now_z(),
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "filters": {"limit": limit, "run_id": run_id},
        "summary": {
            "mentions_loaded": len(rows),
            "classified": len(classifications),
            "promotion_candidates": len(proposed_signals),
            "category_counts": dict(category_counts),
            "signal_type_counts": dict(signal_type_counts),
            "brand_counts": dict(brand_counts),
            "avg_confidence": round(sum(item["confidence"] for item in classifications) / len(classifications), 3) if classifications else 0.0,
        },
        "classifications": classifications,
        "promotion_candidates": proposed_signals,
        "policy": {
            "raw_mentions_only": True,
            "promotion_requires_review": True,
            "no_competitor_signal_write": True,
            "no_llm_fact_generation": True,
        },
    }


def write_classification_report(payload: dict[str, Any], *, out_dir: str | Path = "runtime/ops") -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out / f"{stamp}-market-signal-classifier-v0.json"
    md_path = out / f"{stamp}-market-signal-classifier-v0.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_classification_markdown(payload), encoding="utf-8")
    return {"json_path": str(json_path.resolve()), "md_path": str(md_path.resolve())}


def render_classification_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Market Signal Classifier v0",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- write_db: `{payload['write_db']}`",
        f"- llm_calls: `{payload['llm_calls']}`",
        f"- passed: `{payload['passed']}`",
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    summary = payload.get("summary") or {}
    for key in ("mentions_loaded", "classified", "promotion_candidates", "avg_confidence"):
        lines.append(f"| {key} | {summary.get(key)} |")
    lines.extend(["", "## Category Counts", ""])
    for key, value in (summary.get("category_counts") or {}).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Signal Types", ""])
    for key, value in (summary.get("signal_type_counts") or {}).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Promotion Candidates", ""])
    for signal in (payload.get("promotion_candidates") or [])[:15]:
        lines.append(
            f"- `{signal.get('brand')}` · type `{signal.get('signal_type')}` · "
            f"severity `{signal.get('severity')}` · score `{signal.get('score')}` · "
            f"{signal.get('detail')} · {signal.get('source_url')}"
        )
    lines.extend(["", "## Viltrox / Opportunity / Noise Samples", ""])
    for item in (payload.get("classifications") or [])[:25]:
        if item.get("category") == "competitor_signal":
            continue
        lines.append(
            f"- `{item.get('category')}` · confidence `{item.get('confidence')}` · "
            f"{item.get('mention_text')} · {item.get('source_url')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- No DB write.",
            "- No promotion to `vkpi_competitor_signals`.",
            "- No LLM/Gemini call.",
        ]
    )
    return "\n".join(lines) + "\n"
