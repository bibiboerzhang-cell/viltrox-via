"""P8 deterministic competitor brain preview."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.content_brain import COMPETITOR_BRANDS, VILTROX_TERMS


SCENARIO = "p8_competitor_brain_preview"
FORBIDDEN_WRITE_FLAGS = {"--commit", "--write-db", "--provider", "--crawl", "--record-cost"}

SIGNAL_WEIGHTS = {
    "competitor_mention": 20,
    "competitor_focus": 30,
    "product_comparison": 25,
    "pricing_sensitive": 15,
    "voc_issue": 15,
    "risk_watch": 10,
    "recent_90d": 5,
}

BRAND_ALIASES = {
    "fuji": "fujifilm",
}

GENERIC_PRODUCT_HINTS = {
    "ALL",
    "FE",
    "F1",
    "F2",
    "F4",
    "USD",
    "VAT",
    "RRP",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _normalize_brand(value: Any) -> str:
    brand = _lower(value)
    return BRAND_ALIASES.get(brand, brand)


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _safe_limit(value: int, *, default: int = 20, ceiling: int = 200) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(ceiling, parsed))


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
    return parsed


def _is_recent_90d(value: Any) -> bool:
    parsed = _parse_date(value)
    if not parsed:
        return False
    age_days = (datetime.now(timezone.utc) - parsed).days
    return 0 <= age_days <= 90


def _find_competitor_terms(text: str) -> list[str]:
    lowered = _lower(text)
    matches = []
    for brand in sorted(COMPETITOR_BRANDS):
        if brand in VILTROX_TERMS:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", lowered):
            matches.append(_normalize_brand(brand))
    return sorted(set(matches))


def _clean_product_hints(values: list[Any]) -> list[str]:
    hints = []
    for value in values:
        token = re.sub(r"\s+", " ", _text(value)).strip()
        if not token or token.upper() in GENERIC_PRODUCT_HINTS:
            continue
        hints.append(token)
    return sorted(set(hints))[:10]


def _product_hints_from_text(text: str) -> list[str]:
    hints = []
    for match in re.finditer(r"\b[A-Z]{1,4}\s?[0-9]{1,4}[A-Za-z]{0,4}\b", _text(text)):
        token = re.sub(r"\s+", " ", match.group(0)).strip()
        if token:
            hints.append(token)
    return _clean_product_hints(hints)[:8]


def _append_signal(bucket: dict[str, Any], signal: dict[str, Any]) -> None:
    bucket["signals"].append(signal)
    bucket["signal_count"] += 1
    signal_type = signal.get("signal_type") or "unknown"
    bucket["signal_types"][signal_type] += 1
    bucket["score"] += SIGNAL_WEIGHTS.get(signal_type, 0)
    if signal.get("recent_90d"):
        bucket["score"] += SIGNAL_WEIGHTS["recent_90d"]
    if signal_type != "competitor_mention" or signal.get("severity") in {"high", "danger"}:
        bucket["risk_count"] += 1
    for product in signal.get("product_hints") or []:
        if product:
            bucket["product_hints"][product] += 1


def _new_bucket(brand: str) -> dict[str, Any]:
    return {
        "brand": brand,
        "score": 0,
        "signal_count": 0,
        "risk_count": 0,
        "signal_types": Counter(),
        "product_hints": Counter(),
        "signals": [],
    }


def _content_brain_signals(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_industry_posts"):
        return []
    rows = get_conn().execute(
        """
        SELECT id, platform, post_url, title, caption, published_at,
               brand_mentions_json, product_intents_json, risk_flags_json, content_tags_json
        FROM vkpi_industry_posts
        WHERE COALESCE(NULLIF(analysis_status, ''), 'pending')='done'
        ORDER BY published_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(1000, int(limit or 1000))),),
    ).fetchall()
    signals: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        text_blob = " ".join([_text(data.get("title")), _text(data.get("caption"))])
        product_hints = [
            _text(item.get("product"))
            for item in _loads(data.get("product_intents_json"), [])
            if isinstance(item, dict) and _text(item.get("product"))
        ]
        if not product_hints:
            product_hints = _product_hints_from_text(text_blob)
        brands = [
            _normalize_brand(item.get("brand"))
            for item in _loads(data.get("brand_mentions_json"), [])
            if isinstance(item, dict) and _lower(item.get("brand")) and _lower(item.get("brand")) not in VILTROX_TERMS
        ]
        risk_flags = [
            item
            for item in _loads(data.get("risk_flags_json"), [])
            if isinstance(item, dict) and _text(item.get("flag_key"))
        ]
        for brand in sorted(set(brands)):
            signals.append(
                {
                    "brand": brand,
                    "signal_type": "competitor_mention",
                    "severity": "medium",
                    "product_hints": product_hints,
                    "source_table": "vkpi_industry_posts",
                    "source_id": data.get("id"),
                    "source_url": data.get("post_url"),
                    "platform": data.get("platform"),
                    "published_at": data.get("published_at"),
                    "recent_90d": _is_recent_90d(data.get("published_at")),
                    "detail": f"brand_mentions_json contains competitor brand {brand}",
                }
            )
            for flag in risk_flags:
                flag_key = _text(flag.get("flag_key"))
                if flag_key not in {"competitor_focus", "pricing_sensitive"}:
                    continue
                signals.append(
                    {
                        "brand": brand,
                        "signal_type": flag_key,
                        "severity": _text(flag.get("severity")) or "medium",
                        "product_hints": product_hints,
                        "source_table": "vkpi_industry_posts",
                        "source_id": data.get("id"),
                        "source_url": data.get("post_url"),
                        "platform": data.get("platform"),
                        "published_at": data.get("published_at"),
                        "recent_90d": _is_recent_90d(data.get("published_at")),
                        "detail": f"risk_flags_json contains {flag_key}",
                    }
                )
    return signals


def _voc_signals() -> list[dict[str, Any]]:
    if not _table_exists("vkpi_legacy_voc_alerts_staging"):
        return []
    rows = get_conn().execute(
        """
        SELECT id, source_sheet, source_row, platform, product, issue_type, sentiment,
               content, link, issue_date, severity, status, owner
        FROM vkpi_legacy_voc_alerts_staging
        WHERE review_status='ready'
        ORDER BY id ASC
        LIMIT 1000
        """
    ).fetchall()
    signals: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        text_blob = " ".join([_text(data.get("product")), _text(data.get("content")), _text(data.get("link"))])
        brands = _find_competitor_terms(text_blob)
        product_hints = _clean_product_hints([data.get("product")]) if _text(data.get("product")) else _product_hints_from_text(text_blob)
        for brand in brands:
            signals.append(
                {
                    "brand": brand,
                    "signal_type": "voc_issue",
                    "severity": _text(data.get("severity")) or "medium",
                    "product_hints": product_hints,
                    "source_table": "vkpi_legacy_voc_alerts_staging",
                    "source_id": data.get("id"),
                    "source_sheet": data.get("source_sheet"),
                    "source_row": data.get("source_row"),
                    "platform": data.get("platform"),
                    "published_at": data.get("issue_date"),
                    "recent_90d": _is_recent_90d(data.get("issue_date")),
                    "detail": _text(data.get("content")) or f"VOC mentions {brand}",
                }
            )
    return signals


def _risk_watch_signals() -> list[dict[str, Any]]:
    if not _table_exists("vkpi_legacy_risk_watchlist_staging"):
        return []
    rows = get_conn().execute(
        """
        SELECT id, source_sheet, source_row, platform, handle, display_name,
               risk_type, risk_reason, severity, evidence, status
        FROM vkpi_legacy_risk_watchlist_staging
        WHERE review_status='ready'
        ORDER BY id ASC
        LIMIT 1000
        """
    ).fetchall()
    signals: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        text_blob = " ".join([_text(data.get("risk_type")), _text(data.get("risk_reason")), _text(data.get("evidence"))])
        for brand in _find_competitor_terms(text_blob):
            signals.append(
                {
                    "brand": brand,
                    "signal_type": "risk_watch",
                    "severity": _text(data.get("severity")) or "medium",
                    "product_hints": _product_hints_from_text(text_blob),
                    "source_table": "vkpi_legacy_risk_watchlist_staging",
                    "source_id": data.get("id"),
                    "source_sheet": data.get("source_sheet"),
                    "source_row": data.get("source_row"),
                    "platform": data.get("platform"),
                    "handle": data.get("handle"),
                    "published_at": None,
                    "recent_90d": False,
                    "detail": _text(data.get("risk_reason")) or f"Risk watch mentions {brand}",
                }
            )
    return signals


def _aggregate(signals: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    buckets: defaultdict[str, dict[str, Any]] = defaultdict(lambda: _new_bucket(""))
    for signal in signals:
        brand = _lower(signal.get("brand"))
        if not brand:
            continue
        if not buckets[brand]["brand"]:
            buckets[brand]["brand"] = brand
        _append_signal(buckets[brand], signal)
    items = []
    for bucket in buckets.values():
        evidence = sorted(
            bucket["signals"],
            key=lambda item: (
                0 if item.get("recent_90d") else 1,
                str(item.get("published_at") or ""),
                str(item.get("source_table") or ""),
                int(item.get("source_id") or 0),
            ),
            reverse=False,
        )[:10]
        items.append(
            {
                "brand": bucket["brand"],
                "score": int(bucket["score"]),
                "signal_count": int(bucket["signal_count"]),
                "risk_count": int(bucket["risk_count"]),
                "product_hints": [item for item, _ in bucket["product_hints"].most_common(10)],
                "top_signal_types": dict(bucket["signal_types"].most_common(10)),
                "evidence": evidence,
            }
        )
    return sorted(items, key=lambda item: (-int(item["score"]), -int(item["signal_count"]), item["brand"]))[:limit]


def build_competitor_brain_preview(
    *,
    limit: int = 20,
    json_out: str = "",
    md_out: str = "",
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    content_signals = _content_brain_signals(1000)
    voc_signals = _voc_signals()
    risk_watch_signals = _risk_watch_signals()
    all_signals = content_signals + voc_signals + risk_watch_signals
    competitors = _aggregate(all_signals, safe_limit)
    payload = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "summary": {
            "competitor_brands": len(competitors),
            "signals": len(all_signals),
            "content_signals": len(content_signals),
            "voc_signals": len(voc_signals),
            "risk_watch_signals": len(risk_watch_signals),
        },
        "competitors": competitors,
    }
    markdown = format_preview_summary(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(json.dumps({k: v for k, v in payload.items() if k != "markdown"}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def format_preview_summary(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# P8 Competitor Brain Preview",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
        f"competitor_brands={int(summary.get('competitor_brands') or 0)}",
        f"signals={int(summary.get('signals') or 0)}",
        f"content_signals={int(summary.get('content_signals') or 0)}",
        f"voc_signals={int(summary.get('voc_signals') or 0)}",
        f"risk_watch_signals={int(summary.get('risk_watch_signals') or 0)}",
        "```",
        "",
        "## Top Competitors",
        "",
    ]
    for idx, item in enumerate(payload.get("competitors") or [], start=1):
        lines.extend(
            [
                f"### {idx}. {item.get('brand', '')} score={item.get('score', 0)}",
                "",
                f"- signals={item.get('signal_count', 0)} risk_count={item.get('risk_count', 0)}",
                f"- product_hints={', '.join(item.get('product_hints') or []) or '-'}",
                f"- signal_types={json.dumps(item.get('top_signal_types') or {}, ensure_ascii=False)}",
                "- evidence:",
            ]
        )
        for evidence in (item.get("evidence") or [])[:5]:
            source = evidence.get("source_table") or evidence.get("source_sheet") or ""
            source_id = evidence.get("source_id") or evidence.get("source_row") or ""
            lines.append(
                f"  - {evidence.get('signal_type', '')} source={source}:{source_id} "
                f"detail={_text(evidence.get('detail'))[:160]}"
            )
        lines.append("")
    if not payload.get("competitors"):
        lines.append("No competitor signals found.")
    return "\n".join(lines).rstrip() + "\n"
