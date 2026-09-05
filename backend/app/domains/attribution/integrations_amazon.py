"""Amazon attribution report parsing for V-KPI integrations."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from itertools import islice
from datetime import date
from typing import Any

from app.domains import attribution
from app.domains.attribution.integrations_money import currency_code, exact_cents


AMAZON_COLUMN_ALIASES = {
    "amazon_tag": {"amazon_tag", "tag", "tracking_id", "tracking id", "trackingid", "associate tag", "tracking id (tag)"},
    "asin": {"asin", "product asin", "parent asin", "child asin"},
    "marketplace": {"marketplace", "market", "country", "store"},
    "report_date": {"report_date", "date", "ordered date", "shipped date", "day"},
    "revenue_usd": {"revenue_usd", "revenue", "ordered revenue", "shipped revenue", "sales", "total revenue", "price"},
    "commission_usd": {"commission_usd", "commission", "earnings", "fees", "advertising fees", "advertising fee"},
    "orders": {"orders", "ordered items", "items ordered", "items shipped", "quantity", "qty"},
    "currency": {"currency", "currency code", "currency_code"},
    "transaction_type": {"transaction_type", "transaction type", "type", "report_type"},
    "source_ref": {"source_ref", "transaction_id", "transaction id"},
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
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("amazon report too large")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("amazon report must be UTF-8 CSV or TSV") from exc
    delimiter = "\t" if filename.lower().endswith(".tsv") or text[:2000].count("\t") > text[:2000].count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
    headers = list(reader.fieldnames or [])
    if not headers:
        raise ValueError("amazon report has no header")
    header_map = _normalized_header_map(headers)
    if not any(key in header_map for key in ("revenue_usd", "commission_usd", "orders")):
        raise ValueError("amazon report has no recognized accounting columns")
    # Reports without transaction IDs are daily aggregate snapshots. A repeated
    # upload replaces that same grain; filenames and row ordering are not IDs.
    grouped: dict[str, dict[str, Any]] = {}
    try:
        raw_rows = list(islice(reader, 100001))
    except csv.Error as exc:
        raise ValueError("malformed Amazon CSV/TSV quoting") from exc
    if len(raw_rows) > 100000:
        raise ValueError("amazon report exceeds 100000 rows")
    for line, raw in enumerate(raw_rows, 2):
        if None in raw or any(v is None for v in raw.values()):
            raise ValueError(f"amazon row {line}: inconsistent column count")
        def value(key: str) -> str:
            source = header_map.get(key)
            return str(raw.get(source, "") if source else "").strip()

        tag = _pick(value("amazon_tag"), default_values.get("amazon_tag"), default_values.get("tag"))
        asin = _pick(value("asin"), default_values.get("asin"))
        report_date = _pick(value("report_date"), default_values.get("report_date"))
        marketplace = _pick(value("marketplace"), default_values.get("marketplace")).upper()
        explicit_usd = any(header_map.get(k, "").lower().endswith("_usd") for k in ("revenue_usd", "commission_usd"))
        currency = currency_code(_pick(value("currency"), default_values.get("currency"), "USD" if explicit_usd else ""))
        if explicit_usd and currency != "USD":
            raise ValueError(f"amazon row {line}: USD columns conflict with currency")
        if not report_date:
            raise ValueError(f"amazon row {line}: report_date is required for replay safety")
        try:
            date.fromisoformat(report_date)
        except ValueError as exc:
            raise ValueError(f"amazon row {line}: report_date must be YYYY-MM-DD") from exc
        revenue_cents = exact_cents(value("revenue_usd") or 0)
        commission_cents = exact_cents(value("commission_usd") or 0)
        orders = exact_cents(value("orders") or 0, already_cents=True)
        kind = (value("transaction_type") or "sale").lower()
        if kind in {"refund", "return", "returned", "refunded"}:
            kind = "refund"
            revenue_cents, commission_cents, orders = -abs(revenue_cents), -abs(commission_cents), -abs(orders)
        elif kind not in {"sale", "order", "shipped", "earnings"}:
            raise ValueError(f"amazon row {line}: unsupported transaction type")
        if not any([tag, asin, revenue_cents, commission_cents, orders]):
            continue
        explicit_ref = value("source_ref")
        grain = [tag, asin, marketplace, report_date, currency, kind]
        source_ref = explicit_ref or "amazon:daily:" + hashlib.sha256(json.dumps(grain).encode()).hexdigest()[:32]
        row = {
                "amazon_tag": tag,
                "asin": asin,
                "marketplace": marketplace,
                "report_date": report_date,
                "revenue_cents": revenue_cents,
                "commission_cents": commission_cents,
                "orders": orders,
                "currency": currency,
                "transaction_type": kind,
                "source_ref": source_ref,
                "evidence": {"source": "amazon_report_upload", "filename": filename, "raw": raw},
            }
        prior = grouped.get(source_ref)
        if prior and explicit_ref:
            if any(prior[k] != row[k] for k in ("revenue_cents", "commission_cents", "orders", "currency", "transaction_type")):
                raise ValueError(f"amazon row {line}: conflicting duplicate transaction_id")
            continue
        if prior:
            for key in ("revenue_cents", "commission_cents", "orders"):
                prior[key] += row[key]
        else:
            grouped[source_ref] = row
    if not grouped:
        raise ValueError("amazon report has no accounting rows")
    return list(grouped.values())


def import_amazon_report(content: bytes, filename: str, defaults: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = parse_amazon_report_bytes(content, filename, defaults)
    result = attribution.import_amazon({**defaults, "rows": rows}, staff=staff)
    return {
        **result,
        "filename": filename,
        "parsed_rows": len(rows),
    }
