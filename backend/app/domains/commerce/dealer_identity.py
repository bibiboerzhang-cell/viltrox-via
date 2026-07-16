"""Deterministic, claim-free Dealer identity contract.

The helpers propose stable keys for reviewed Dealer organizations and locations.
They do not perform fuzzy matching, write the database, or infer authorization,
inventory, event participation, sales, ROI or local influence.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit


def normalize_identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def normalize_official_domain(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    host = str(parsed.hostname or "").casefold().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _digest(namespace: str, *parts: str) -> str:
    material = "\x1f".join([namespace, *parts]).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def propose_stable_org_key(name: Any, *, country_code: Any = "", official_domain: Any = "") -> str:
    """Propose a reviewable key; persist the accepted value instead of recomputing it."""
    normalized_name = normalize_identity_text(name)
    if not normalized_name:
        raise ValueError("dealer organization name is required")
    country = str(country_code or "").strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country_code must be uppercase ISO alpha-2")
    domain = normalize_official_domain(official_domain)
    return f"dealer_org_{_digest('dealer_org_v1', normalized_name, country, domain)}"


def propose_stable_location_key(
    stable_org_key: Any,
    *,
    country_code: Any,
    address: Any,
    postal_code: Any = "",
) -> str:
    org_key = str(stable_org_key or "").strip()
    country = str(country_code or "").strip().upper()
    normalized_address = normalize_identity_text(address)
    normalized_postal = normalize_identity_text(postal_code).replace(" ", "")
    if not org_key.startswith("dealer_org_"):
        raise ValueError("stable_org_key is required")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country_code must be uppercase ISO alpha-2")
    if not normalized_address:
        raise ValueError("dealer location address is required")
    return f"dealer_loc_{_digest('dealer_location_v1', org_key, country, normalized_address, normalized_postal)}"


def build_alias_contract(
    *,
    alias_type: str,
    alias_value: Any,
    country_code: Any = "",
    source_url: Any = "",
) -> dict[str, str]:
    allowed = {"official_name", "store_name", "domain", "social", "event_host"}
    kind = str(alias_type or "").strip()
    if kind not in allowed:
        raise ValueError("unsupported dealer alias_type")
    normalized = (
        normalize_official_domain(alias_value)
        if kind == "domain"
        else normalize_identity_text(alias_value)
    )
    if not normalized:
        raise ValueError("dealer alias_value is required")
    country = str(country_code or "").strip().upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country_code must be uppercase ISO alpha-2")
    url = str(source_url or "").strip()
    if url and (not url.startswith("https://") or not normalize_official_domain(url)):
        raise ValueError("source_url must be HTTPS when present")
    return {
        "alias_type": kind,
        "alias_value": str(alias_value).strip(),
        "alias_normalized": normalized,
        "country_code": country,
        "source_url": url,
    }


__all__ = [
    "normalize_identity_text",
    "normalize_official_domain",
    "propose_stable_org_key",
    "propose_stable_location_key",
    "build_alias_contract",
]
