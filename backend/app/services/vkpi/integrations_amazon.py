"""Amazon attribution report parsing for V-KPI integrations."""
from __future__ import annotations

import csv
import io
from typing import Any

from app.services.vkpi import attribution


AMAZON_COLUMN_ALIASES = {
    "amazon_tag": {"amazon_tag", "tag", "tracking_id", "tracking id", "trackingid", "associate tag", "tracking id (tag)"},
    "asin": {"asin", "product asin", "parent asin", "child asin"},
    "marketplace": {"marketplace", "market", "country", "store"},
    "report_date": {"report_date", "date", "ordered date", "shipped date", "day"},
    "revenue_usd": {"revenue_usd", "revenue", "ordered revenue", "shipped revenue", "sales", "total revenue", "price"},
    "commission_usd": {"commission_usd", "commission", "earnings", "fees", "advertising fees", "advertising fee"},
    "orders": {"orders", "ordered items", "items ordered", "items shipped", "quantity", "qty"},
}


def _pick(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip() or default))
    except (TypeError, ValueError):
        return default


def _money_cents(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    clean = text.replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return int(round(float(clean) * 100))
    except (TypeError, ValueError):
        return 0


def _normalized_header_map(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for header in headers:
        clean = str(header or "").strip().lower().replace("\ufeff", "")
        for target, aliases in AMAZON_COLUMN_ALIASES.items():
            if clean in aliases:
                mapping[target] = header
                break
    return mapping


def parse_amazon_report_bytes(content: bytes, filename: str = "", defaults: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    default_values = defaults or {}
    text = content.decode("utf-8-sig", errors="ignore")
    delimiter = "\t" if filename.lower().endswith(".tsv") or text[:2000].count("\t") > text[:2000].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    if not headers:
        return []
    header_map = _normalized_header_map(headers)
    rows: list[dict[str, Any]] = []
    for raw in reader:
        def value(key: str) -> str:
            source = header_map.get(key)
            return str(raw.get(source, "") if source else "").strip()

        tag = _pick(value("amazon_tag"), default_values.get("amazon_tag"), default_values.get("tag"))
        asin = _pick(value("asin"), default_values.get("asin"))
        report_date = _pick(value("report_date"), default_values.get("report_date"))
        marketplace = _pick(value("marketplace"), default_values.get("marketplace"), "US").upper()
        revenue_cents = _money_cents(value("revenue_usd"))
        commission_cents = _money_cents(value("commission_usd"))
        orders = _safe_int(value("orders"))
        if not any([tag, asin, revenue_cents, commission_cents, orders]):
            continue
        rows.append(
            {
                "amazon_tag": tag,
                "asin": asin,
                "marketplace": marketplace,
                "report_date": report_date,
                "revenue_cents": revenue_cents,
                "commission_cents": commission_cents,
                "orders": orders,
                "source_ref": _pick(raw.get("source_ref"), f"{tag}:{asin}:{marketplace}:{report_date}"),
                "evidence": {"source": "amazon_report_upload", "filename": filename, "raw": raw},
            }
        )
    return rows


def import_amazon_report(content: bytes, filename: str, defaults: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = parse_amazon_report_bytes(content, filename, defaults)
    result = attribution.import_amazon({**defaults, "rows": rows}, staff=staff)
    return {
        **result,
        "filename": filename,
        "parsed_rows": len(rows),
    }
