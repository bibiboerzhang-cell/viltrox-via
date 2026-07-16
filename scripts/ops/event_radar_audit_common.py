"""Pure helpers and contracts shared by the offline Event/Dealer audit."""
from __future__ import annotations

import ast
import hashlib
import re
import unicodedata
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG = REPO_ROOT / "backend/app/domains/events/radar_seed_catalog.json"
DEFAULT_DEALER_SOURCE = REPO_ROOT / "backend/app/domains/commerce/dealer_scrape.py"
DEALER_CANDIDATE_SYMBOL = "_REVIEWED_PUBLIC_RETAILERS"
COVERAGE_CONTRACT_ID = "vkpi.event_dealer.coverage_quality"
COVERAGE_CONTRACT_VERSION = 1
DEFAULT_STALE_AFTER_DAYS = 30

ALLOWED_SOURCE_KINDS = {"major_expo", "dealer_event", "venue_calendar"}
ALLOWED_SOURCE_STATUSES = {"active", "hold", "retired", "blocked"}
ALLOWED_LANES = {"major_expo", "dealer_event", "local_activity", "brand_event"}
ALLOWED_EVENT_STATUSES = {"scheduled", "postponed", "cancelled", "ended", "unknown"}
ALLOWED_VERIFICATION_STATUSES = {"verified", "provisional", "conflict", "needs_review"}
ALLOWED_EVIDENCE_GRADES = {"A1", "A2", "B", "X"}
ALLOWED_DATE_PRECISIONS = {"date", "date_time", "month_only", "tbd"}
ALLOWED_VILTROX_PRESENCE = {"unknown", "not_found", "brand_listed", "confirmed_exhibitor"}
NON_ACTIONABLE_SOURCE_STATUSES = {"hold", "retired", "blocked"}

UNSUPPORTED_BUSINESS_CLAIM_FIELDS = {
    "authorization_status", "authorized", "is_authorized",
    "viltrox_authorization_status", "stock_status", "in_stock", "inventory",
    "inventory_quantity", "roi", "estimated_roi", "gmv", "sales",
    "sales_effect", "sales_attribution", "attendance", "attendee_count",
    "local_impact", "business_outcome", "business_outcome_status",
}
UNKNOWN_CLAIM_VALUES = {
    "", "unknown", "unverified", "unmeasured", "not_measured",
    "not measured", "pending", "none", "n/a",
}
DEALER_CONTACT_FIELDS = ("phone", "contact_email", "store_hours", "public_services")
DEALER_POSITIVE_CLAIM_FIELDS = {
    "authorized", "is_authorized", "official_dealer", "official_viltrox_dealer",
    "in_stock", "inventory", "inventory_quantity", "sales", "sales_attribution",
    "gmv", "roi", "local_impact",
}
DEALER_NON_EQUIVALENT_FACTS = [
    "Viltrox authorization", "official dealer status", "current inventory or stock",
    "price or sell-through", "sales or sales attribution", "event participation",
    "local market impact",
]


def is_https_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or any(char.isspace() for char in text):
        return False
    parsed = urlsplit(text)
    return (
        parsed.scheme == "https" and bool(parsed.hostname)
        and parsed.username is None and parsed.password is None
    )


def host(value: Any) -> str:
    return str(urlsplit(str(value or "")).hostname or "").lower().rstrip(".")


def related_hosts(left: Any, right: Any) -> bool:
    a, b = host(left), host(right)
    return bool(a and b and (a == b or a.endswith(f".{b}") or b.endswith(f".{a}")))


def parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None


def parse_checked_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def claim_is_inferred(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return str(value).strip().lower() not in UNKNOWN_CLAIM_VALUES


def as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return value.astimezone(timezone.utc)


def parse_as_of(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = parse_checked_at(value)
    if parsed is None:
        raise ValueError("as_of must be an ISO timestamp with timezone")
    return parsed.astimezone(timezone.utc)


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def candidate_key(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(normalized_text(part) for part in parts if normalized_text(part))
    if not material:
        return ""
    return f"{prefix}_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:16]}"


def dealer_domain(candidate: dict[str, Any]) -> str:
    for field in ("location_source_url", "brand_listing_url", "official_url", "website_url"):
        value = host(candidate.get(field))
        if value:
            return value[4:] if value.startswith("www.") else value
    return ""


def _load_literal_assignment(path: Path, symbol: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target_name = ""
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign):
            value_node = node.value
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    target_name = target.id
                    break
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name, value_node = node.target.id, node.value
        if target_name == symbol and value_node is not None:
            return ast.literal_eval(value_node)
    raise ValueError(f"literal assignment {symbol!r} not found in {path}")


def load_reviewed_dealer_candidates(
    path: Path = DEFAULT_DEALER_SOURCE,
) -> list[dict[str, Any]]:
    raw = _load_literal_assignment(path, DEALER_CANDIDATE_SYMBOL)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{DEALER_CANDIDATE_SYMBOL} must be a list of objects")
    return [deepcopy(item) for item in raw]


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None
