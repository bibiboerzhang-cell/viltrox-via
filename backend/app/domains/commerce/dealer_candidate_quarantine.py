"""Read-only Dealer candidate quarantine built from reviewed technical preflights.

This module deliberately stops before the legal/source-activation gate.  It can
inspect a newly captured publisher page and describe complete public US store
addresses, but it cannot activate a registry source, write a candidate staging
row, write ``vkpi_dealers``, geocode an address, or upgrade any Viltrox truth.

The extraction surface is intentionally narrow:

* only sources already recorded as reachable and path-allowed by a supplied
  technical preflight are eligible for a capture;
* structured JSON-LD with a complete US postal address is preferred;
* otherwise a complete address visibly published in page/PDF text may be
  described, with a lower evidence tier;
* incomplete city/state lists, organization-only PDF rows, locator shells and
  JavaScript-only placeholders produce zero candidates rather than guesses.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit


CONTRACT_ID = "vkpi.us_dealer.technical_candidate_quarantine"
CONTRACT_VERSION = 1
CLAIM_STATUS = "descriptive_only"
MAX_CAPTURE_BYTES = 8 * 1024 * 1024

_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)
_STATE_PATTERN = "|".join(sorted(_US_STATE_CODES))
_STREET_SUFFIX = (
    r"(?:Avenue|Ave|Boulevard|Blvd|Circle|Cir|Court|Ct|Drive|Dr|Highway|Hwy|"
    r"Lane|Ln|Parkway|Pkwy|Place|Pl|Road|Rd|Street|St|Terrace|Ter|Trail|Trl|Way)\.?"
)
_ADDRESS_RE = re.compile(
    rf"(?P<line1>\b\d{{1,6}}[A-Za-z]?(?:\s+[A-Za-z0-9#.&'/-]+){{1,10}}\s+{_STREET_SUFFIX})"
    rf"\s*(?:,|\||\n|\r)+\s*"
    rf"(?P<city>[A-Za-z][A-Za-z .'-]{{1,45}}?)\s*(?:,|\||\n|\r)+\s*"
    rf"(?P<state>{_STATE_PATTERN})\s+(?P<postal>[0-9]{{5}}(?:-[0-9]{{4}})?)\b",
    re.IGNORECASE,
)
_ADDRESS_INLINE_RE = re.compile(
    rf"(?P<line1>\b\d{{1,6}}[A-Za-z]?(?:\s+[A-Za-z0-9#.&'/-]+){{1,10}}\s+{_STREET_SUFFIX})"
    rf"\s*,?\s+(?P<city>[A-Za-z][A-Za-z .'-]{{1,32}}?)\s*,\s*"
    rf"(?P<state>{_STATE_PATTERN})\s+(?P<postal>[0-9]{{5}}(?:-[0-9]{{4}})?)\b",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<![0-9])(?:\+?1[ .-]?)?\(?[2-9][0-9]{2}\)?[ .-][0-9]{3}[ .-][0-9]{4}(?:\s*(?:x|ext\.?)\s*[0-9]{1,6})?(?![0-9])",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


class _HtmlEvidenceParser(HTMLParser):
    """Collect JSON-LD and visible page text without executing scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._script_kind: str | None = None
        self._script_chunks: list[str] = []
        self._ignored_depth = 0
        self.json_ld: list[Any] = []
        self.visible_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        if lowered == "script":
            media_type = attributes.get("type", "").casefold().split(";", 1)[0].strip()
            self._script_kind = media_type
            self._script_chunks = []
            return
        if lowered in {"style", "noscript", "template"}:
            self._ignored_depth += 1
            return
        if lowered in {"address", "article", "br", "div", "footer", "li", "p", "section"}:
            self.visible_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._script_kind is not None:
            if self._script_kind == "application/ld+json":
                self._script_chunks.append(data)
            return
        if self._ignored_depth == 0 and str(data or "").strip():
            self.visible_chunks.append(data)
            self.visible_chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered == "script" and self._script_kind is not None:
            if self._script_kind == "application/ld+json":
                text = "".join(self._script_chunks).strip()
                if text:
                    try:
                        self.json_ld.append(json.loads(text))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            self._script_kind = None
            self._script_chunks = []
            return
        if lowered in {"style", "noscript", "template"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if lowered in {"address", "article", "div", "footer", "li", "p", "section"}:
            self.visible_chunks.append("\n")

    def visible_text(self) -> str:
        lines = []
        for line in "".join(self.visible_chunks).splitlines():
            normalized = _SPACE_RE.sub(" ", line).strip(" ,|\t")
            if normalized:
                lines.append(normalized)
        return "\n".join(lines)


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized(value: Any, *, limit: int = 500) -> str | None:
    text = _SPACE_RE.sub(" ", str(value or "")).strip()
    return text[:limit] or None


def _public_http_url(value: Any, *, base_url: str) -> str:
    """Return one evidence-safe HTTP(S) URL, never a stringified collection."""
    if not isinstance(value, str) or not value.strip():
        return base_url
    resolved = urljoin(base_url, value.strip())
    parsed = urlsplit(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return base_url
    return resolved


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _iter_json_objects(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        graph = value.get("@graph")
        if isinstance(graph, (list, Mapping)):
            yield from _iter_json_objects(graph, f"{path}.@graph")
        for key, nested in value.items():
            if key == "@graph":
                continue
            if isinstance(nested, (list, Mapping)):
                yield from _iter_json_objects(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_json_objects(item, f"{path}[{index}]")


def _country_is_us(value: Any) -> bool:
    if isinstance(value, Mapping):
        value = _first(value, "name", "@id", "identifier")
    normalized = re.sub(r"[^A-Za-z]", "", str(value or "US")).casefold()
    return normalized in {"us", "usa", "unitedstates", "unitedstatesofamerica"}


def _address_from_mapping(value: Mapping[str, Any]) -> dict[str, str] | None:
    raw_address = value.get("address")
    nested = raw_address if isinstance(raw_address, Mapping) else value
    line1 = _normalized(
        _first(nested, "streetAddress", "address1", "line1", "street")
    )
    line2 = _normalized(_first(nested, "address2", "line2", "suite"))
    city = _normalized(_first(nested, "addressLocality", "city", "locality"))
    state = str(_first(nested, "addressRegion", "state", "region") or "").strip().upper()
    postal = str(_first(nested, "postalCode", "postal_code", "zip", "zipcode") or "").strip()
    country = _first(nested, "addressCountry", "country", "country_code")
    if (
        not line1
        or not city
        or state not in _US_STATE_CODES
        or not re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", postal)
        or not _country_is_us(country)
    ):
        return None
    return {
        "line1": line1,
        "line2": line2 or "",
        "city": city,
        "state": state,
        "postal_code": postal,
        "country_code": "US",
        "formatted": ", ".join(
            item for item in (line1, line2, f"{city}, {state} {postal}", "US") if item
        ),
    }


def _coordinates(value: Mapping[str, Any]) -> tuple[float | None, float | None]:
    raw = value.get("geo")
    nested = raw if isinstance(raw, Mapping) else value
    try:
        latitude = float(_first(nested, "latitude", "lat"))
        longitude = float(_first(nested, "longitude", "lng", "lon"))
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    return latitude, longitude


def _contact_near(text: str, start: int, end: int) -> tuple[str | None, str | None]:
    window_start = max(0, start - 320)
    window = text[window_start : min(len(text), end + 320)]

    def distance(matched: re.Match[str]) -> tuple[int, int]:
        absolute_start = window_start + matched.start()
        absolute_end = window_start + matched.end()
        if absolute_end <= start:
            return start - absolute_end, 1
        if absolute_start >= end:
            return absolute_start - end, 0
        return 0, 0

    phone_matches = list(_PHONE_RE.finditer(window))
    email_matches = list(_EMAIL_RE.finditer(window))
    phone_match = min(phone_matches, key=distance) if phone_matches else None
    email_match = min(email_matches, key=distance) if email_matches else None
    return (
        _normalized(phone_match.group(0), limit=40) if phone_match else None,
        _normalized(email_match.group(0), limit=254) if email_match else None,
    )


def _address_dedupe_material(address: Mapping[str, Any]) -> dict[str, str]:
    def compact(value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    return {
        "line1": compact(address.get("line1")),
        "line2": compact(address.get("line2")),
        "city": compact(address.get("city")),
        "state": compact(address.get("state")),
        "postal_code": compact(address.get("postal_code")),
        "country_code": "us",
    }


def _candidate(
    *,
    source: Mapping[str, Any],
    address: Mapping[str, Any],
    branch_name: Any,
    phone: Any,
    email: Any,
    website: Any,
    latitude: float | None,
    longitude: float | None,
    captured_at: str,
    final_url: str,
    snapshot_sha256: str,
    evidence_method: str,
    evidence_locator: str,
    evidence_excerpt: str | None,
) -> dict[str, Any]:
    source_id = str(source.get("source_registry_id") or source.get("id") or "")
    publisher = _normalized(source.get("publisher"), limit=200) or source_id
    normalized_address = _address_dedupe_material(address)
    address_sha256 = _canonical_json_sha256(normalized_address)
    source_entity_sha256 = _canonical_json_sha256(
        {"source_registry_id": source_id, "address": normalized_address}
    )
    quality_tier = "high" if evidence_method == "json_ld_complete_us_address" else "medium"
    quality_score = 0.95 if quality_tier == "high" else 0.76
    candidate = {
        "source_registry_id": source_id,
        "source_entity_key": f"dealer_candidate.{source_id}.{source_entity_sha256[:24]}",
        "cross_source_dedupe_key": f"us_address.{address_sha256[:32]}",
        "organization_name": publisher,
        "branch_name": _normalized(branch_name, limit=200),
        "address": dict(address),
        "contact": {
            "phone": _normalized(phone, limit=40),
            "email": _normalized(email, limit=254),
            "website": _normalized(website, limit=1000) or final_url,
        },
        "map_fields": {
            "latitude": latitude,
            "longitude": longitude,
            "geocoding_status": "publisher_coordinates" if latitude is not None else "not_performed",
        },
        "evidence": {
            "method": evidence_method,
            "locator": evidence_locator,
            "quality_tier": quality_tier,
            "quality_score": quality_score,
            "criteria": [
                "publisher_owned_registered_page",
                "complete_public_us_postal_address",
                "capture_sha256_recorded",
                "structured_json_ld" if quality_tier == "high" else "explicit_visible_text",
            ],
            "excerpt": evidence_excerpt,
        },
        "provenance": {
            "source_url": final_url,
            "captured_at": captured_at,
            "snapshot_sha256": snapshot_sha256,
            "publisher_bound": True,
        },
        "truth_dimensions": {
            "source_publisher": publisher,
            "manufacturer_authorization_scope": _normalized(
                source.get("manufacturer_authorization_scope"), limit=500
            ),
            "physical_location": "public_candidate_requires_human_review",
            "viltrox_authorization": "unknown",
            "viltrox_product_presence": "unknown",
            "current_inventory": "unknown",
            "local_market_impact": "unknown",
        },
        "legal_approval": False,
        "source_activation": False,
        "promotion_eligible": False,
        "business_rows_written": 0,
        "candidate_only": True,
        "claim_status": CLAIM_STATUS,
    }
    candidate["content_sha256"] = _canonical_json_sha256(candidate)
    return candidate


def _extract_json_ld_candidates(
    parser: _HtmlEvidenceParser,
    *,
    source: Mapping[str, Any],
    captured_at: str,
    final_url: str,
    snapshot_sha256: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for document_index, document in enumerate(parser.json_ld):
        for path, record in _iter_json_objects(document):
            raw_kinds = record.get("@type")
            kinds = {
                str(item).casefold()
                for item in (raw_kinds if isinstance(raw_kinds, list) else [raw_kinds])
            }
            if not kinds.intersection({"store", "localbusiness", "electronicsstore"}):
                continue
            address = _address_from_mapping(record)
            if address is None:
                continue
            latitude, longitude = _coordinates(record)
            candidates.append(
                _candidate(
                    source=source,
                    address=address,
                    branch_name=_first(record, "name", "branch", "title"),
                    phone=_first(record, "telephone", "phone", "phoneNumber"),
                    email=record.get("email"),
                    # ``sameAs`` commonly contains a social-profile list.  It
                    # is not the branch website and must never be stringified
                    # into an invalid URL in the quarantine artifact.
                    website=_public_http_url(record.get("url"), base_url=final_url),
                    latitude=latitude,
                    longitude=longitude,
                    captured_at=captured_at,
                    final_url=final_url,
                    snapshot_sha256=snapshot_sha256,
                    evidence_method="json_ld_complete_us_address",
                    evidence_locator=f"json_ld[{document_index}]{path[1:]}",
                    evidence_excerpt=None,
                )
            )
    return candidates


def _extract_text_candidates(
    text: str,
    *,
    source: Mapping[str, Any],
    captured_at: str,
    final_url: str,
    snapshot_sha256: str,
    media_type: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    method = (
        "pdf_visible_text_complete_us_address"
        if media_type == "application/pdf"
        else "html_visible_text_complete_us_address"
    )
    seen_spans: set[tuple[int, int]] = set()
    for pattern in (_ADDRESS_RE, _ADDRESS_INLINE_RE):
        for matched in pattern.finditer(text):
            span = matched.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            line1 = _normalized(matched.group("line1"), limit=140)
            city = _normalized(matched.group("city"), limit=60)
            state = str(matched.group("state") or "").upper()
            postal = str(matched.group("postal") or "")
            if not line1 or not city or state not in _US_STATE_CODES:
                continue
            # A nearby phone fragment can otherwise be swallowed as a second
            # street number (for example ``7676 2438 W Anderson Ln``).
            if re.match(r"^[0-9]{3,6}\s+[0-9]{2,6}\s+", line1):
                continue
            address = {
                "line1": line1,
                "line2": "",
                "city": city,
                "state": state,
                "postal_code": postal,
                "country_code": "US",
                "formatted": f"{line1}, {city}, {state} {postal}, US",
            }
            phone, email = _contact_near(text, matched.start(), matched.end())
            excerpt = _SPACE_RE.sub(
                " ", text[max(0, matched.start() - 100) : min(len(text), matched.end() + 180)]
            ).strip()[:500]
            candidates.append(
                _candidate(
                    source=source,
                    address=address,
                    branch_name=None,
                    phone=phone,
                    email=email,
                    website=final_url,
                    latitude=None,
                    longitude=None,
                    captured_at=captured_at,
                    final_url=final_url,
                    snapshot_sha256=snapshot_sha256,
                    evidence_method=method,
                    evidence_locator=f"visible_text:{matched.start()}-{matched.end()}",
                    evidence_excerpt=excerpt,
                )
            )
    return candidates


def _pdf_text(content: bytes) -> tuple[str, str | None]:
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"pdftotext_failed:{exc.__class__.__name__}"
    if completed.returncode != 0:
        return "", f"pdftotext_exit_{completed.returncode}"
    return completed.stdout.decode("utf-8", "replace"), None


def extract_document_candidates(
    *,
    source: Mapping[str, Any],
    content: bytes,
    content_type: str,
    captured_at: str,
    final_url: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract candidates from one already captured publisher document."""
    bounded = bytes(content or b"")[:MAX_CAPTURE_BYTES]
    snapshot_sha256 = hashlib.sha256(bounded).hexdigest()
    media_type = str(content_type or "").split(";", 1)[0].strip().casefold()
    issues: list[str] = []
    candidates: list[dict[str, Any]] = []
    if media_type in {"text/html", "application/xhtml+xml"}:
        parser = _HtmlEvidenceParser()
        try:
            parser.feed(bounded.decode("utf-8", "replace"))
        except Exception as exc:
            issues.append(f"html_parser_failed:{exc.__class__.__name__}")
        candidates.extend(
            _extract_json_ld_candidates(
                parser,
                source=source,
                captured_at=captured_at,
                final_url=final_url,
                snapshot_sha256=snapshot_sha256,
            )
        )
        # Generic visible-text extraction is safe enough for a retailer-owned
        # location page, but not for a manufacturer directory shell: the only
        # complete address there is often the manufacturer's own office.
        if str(source.get("source_kind") or "") == "retailer_location_directory":
            candidates.extend(
                _extract_text_candidates(
                    parser.visible_text(),
                    source=source,
                    captured_at=captured_at,
                    final_url=final_url,
                    snapshot_sha256=snapshot_sha256,
                    media_type=media_type,
                )
            )
    elif media_type == "application/pdf":
        text, issue = _pdf_text(bounded)
        if issue:
            issues.append(issue)
        if text and str(source.get("source_kind") or "") == "retailer_location_directory":
            candidates.extend(
                _extract_text_candidates(
                    text,
                    source=source,
                    captured_at=captured_at,
                    final_url=final_url,
                    snapshot_sha256=snapshot_sha256,
                    media_type=media_type,
                )
            )
        elif text:
            issues.append("manufacturer_pdf_requires_explicit_dealer_rows")
    else:
        issues.append("unsupported_content_type")

    # One address can occur in JSON-LD, footer and visible contact text.  Keep
    # the strongest exact observation, not three apparent stores.
    deduped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = str(candidate["cross_source_dedupe_key"])
        existing = deduped.get(key)
        if existing is None or float(candidate["evidence"]["quality_score"]) > float(
            existing["evidence"]["quality_score"]
        ):
            deduped[key] = candidate
    return sorted(deduped.values(), key=lambda row: str(row["source_entity_key"])), issues


def eligible_preflight_sources(
    preflight: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select only rows proven reachable and robots-allowed by the artifact."""
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for raw in preflight.get("sources") or []:
        row = dict(raw) if isinstance(raw, Mapping) else {}
        source_id = str(row.get("source_registry_id") or "")
        robots = row.get("robots") if isinstance(row.get("robots"), Mapping) else {}
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), Mapping) else {}
        reasons: list[str] = []
        if str(row.get("technical_status") or "") != "reachable":
            reasons.append("technical_status_not_reachable")
        if robots.get("fetch_allowed") is not True:
            reasons.append("robots_path_not_allowed")
        if not (200 <= int(snapshot.get("http_status") or 0) < 400):
            reasons.append("preflight_snapshot_not_successful")
        if not source_id or not str(row.get("canonical_url") or "").startswith(("http://", "https://")):
            reasons.append("source_identity_invalid")
        if reasons:
            excluded.append(
                {
                    "source_registry_id": source_id or None,
                    "technical_status": row.get("technical_status"),
                    "robots_fetch_allowed": robots.get("fetch_allowed") is True,
                    "reasons": sorted(set(reasons)),
                    "network_called": False,
                }
            )
        else:
            eligible.append(row)
    eligible.sort(key=lambda item: str(item.get("source_registry_id") or ""))
    excluded.sort(key=lambda item: str(item.get("source_registry_id") or ""))
    return eligible, excluded


def _publisher_host_bound(canonical_url: str, final_url: str) -> bool:
    canonical_host = str(urlsplit(canonical_url).hostname or "").casefold().removeprefix("www.")
    final_host = str(urlsplit(final_url).hostname or "").casefold().removeprefix("www.")
    return bool(canonical_host and canonical_host == final_host)


def build_quarantine(
    *,
    preflight: Mapping[str, Any],
    registry: Mapping[str, Any],
    captured_at: str,
    fetch: Callable[[str], Mapping[str, Any]],
    preflight_sha256: str,
    registry_sha256: str,
) -> dict[str, Any]:
    """Capture and extract an evidence-only quarantine, never a business row."""
    eligible, excluded = eligible_preflight_sources(preflight)
    registry_by_id = {
        str(row.get("id") or ""): row
        for row in registry.get("dealer_discovery_sources") or []
        if isinstance(row, Mapping)
    }
    source_results: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    called_ids: list[str] = []
    for preflight_row in eligible:
        source_id = str(preflight_row.get("source_registry_id") or "")
        canonical_url = str(preflight_row.get("canonical_url") or "")
        registry_row = registry_by_id.get(source_id) or {}
        source = {
            **dict(registry_row),
            "source_registry_id": source_id,
            "publisher": preflight_row.get("publisher") or registry_row.get("publisher"),
            "source_kind": preflight_row.get("source_kind") or registry_row.get("source_kind"),
        }
        called_ids.append(source_id)
        try:
            response = dict(fetch(canonical_url))
        except Exception as exc:
            source_results.append(
                {
                    "source_registry_id": source_id,
                    "publisher": source.get("publisher"),
                    "source_kind": source.get("source_kind"),
                    "canonical_url": canonical_url,
                    "status": "capture_failed",
                    "error": f"{exc.__class__.__name__}: {str(exc)[:240]}",
                    "candidate_count": 0,
                    "page_contains_public_physical_store_data": False,
                    "legal_approval": False,
                    "source_activation": False,
                    "business_rows_written": 0,
                }
            )
            continue
        status_code = int(response.get("status_code") or 0)
        final_url = str(response.get("final_url") or canonical_url)
        content_type = str(response.get("content_type") or "")
        content = bytes(response.get("content") or b"")
        bounded = content[:MAX_CAPTURE_BYTES]
        capture_sha256 = hashlib.sha256(bounded).hexdigest()
        preflight_snapshot = (
            preflight_row.get("snapshot")
            if isinstance(preflight_row.get("snapshot"), Mapping)
            else {}
        )
        issues: list[str] = []
        if not (200 <= status_code < 400):
            issues.append("capture_http_status_not_successful")
        if not _publisher_host_bound(canonical_url, final_url):
            issues.append("final_url_not_publisher_host_bound")
        candidates: list[dict[str, Any]] = []
        if not issues:
            candidates, extraction_issues = extract_document_candidates(
                source=source,
                content=bounded,
                content_type=content_type,
                captured_at=captured_at,
                final_url=final_url,
            )
            issues.extend(extraction_issues)
        source_result = {
            "source_registry_id": source_id,
            "publisher": source.get("publisher"),
            "source_kind": source.get("source_kind"),
            "canonical_url": canonical_url,
            "preflight_gate": {
                "technical_status": preflight_row.get("technical_status"),
                "robots_status": (
                    preflight_row.get("robots", {}).get("status")
                    if isinstance(preflight_row.get("robots"), Mapping)
                    else None
                ),
                "robots_fetch_allowed": (
                    preflight_row.get("robots", {}).get("fetch_allowed") is True
                    if isinstance(preflight_row.get("robots"), Mapping)
                    else False
                ),
                "robots_reason": (
                    preflight_row.get("robots", {}).get("reason")
                    if isinstance(preflight_row.get("robots"), Mapping)
                    else None
                ),
                "robots_sha256": (
                    preflight_row.get("robots", {}).get("sha256")
                    if isinstance(preflight_row.get("robots"), Mapping)
                    else None
                ),
                "terms_legal_approval": False,
            },
            "status": (
                "quarantined_candidates_extracted"
                if candidates
                else "no_complete_public_us_address_detected"
                if not issues
                else "capture_not_extractable"
            ),
            "candidate_count": len(candidates),
            "page_contains_public_physical_store_data": bool(candidates),
            "snapshot": {
                "captured_at": captured_at,
                "http_status": status_code,
                "final_url": final_url,
                "content_type": content_type.split(";", 1)[0].strip().casefold() or None,
                "response_bytes": len(content),
                "captured_bytes": len(bounded),
                "truncated": len(content) > MAX_CAPTURE_BYTES,
                "sha256": capture_sha256,
                "hash_scope": "prefix" if len(content) > MAX_CAPTURE_BYTES else "complete_response",
                "preflight_sha256": preflight_snapshot.get("sha256"),
                "preflight_hash_match": capture_sha256 == preflight_snapshot.get("sha256"),
            },
            "issues": sorted(set(issues)),
            "candidates": candidates,
            "legal_approval": False,
            "source_activation": False,
            "business_rows_written": 0,
            "claim_status": CLAIM_STATUS,
        }
        source_results.append(source_result)
        all_candidates.extend(candidates)

    source_results.sort(key=lambda item: str(item.get("source_registry_id") or ""))
    all_candidates.sort(
        key=lambda item: (str(item.get("cross_source_dedupe_key")), str(item.get("source_entity_key")))
    )
    groups: dict[str, list[str]] = {}
    for candidate in all_candidates:
        groups.setdefault(str(candidate["cross_source_dedupe_key"]), []).append(
            str(candidate["source_entity_key"])
        )
    duplicate_groups = [
        {"cross_source_dedupe_key": key, "source_entity_keys": values, "count": len(values)}
        for key, values in sorted(groups.items())
        if len(values) > 1
    ]
    near_groups: dict[str, list[dict[str, str]]] = {}
    for candidate in all_candidates:
        address = candidate["address"]
        house_match = re.match(r"^([0-9]{1,6})\b", str(address.get("line1") or ""))
        if not house_match:
            continue
        material = {
            "house_number": house_match.group(1),
            "city": re.sub(r"[^a-z0-9]", "", str(address.get("city") or "").casefold()),
            "state": str(address.get("state") or ""),
            "postal_code": str(address.get("postal_code") or "")[:5],
        }
        key = "us_address_review." + _canonical_json_sha256(material)[:24]
        near_groups.setdefault(key, []).append(
            {
                "source_entity_key": str(candidate["source_entity_key"]),
                "formatted_address": str(address.get("formatted") or ""),
            }
        )
    possible_near_duplicates = [
        {"review_key": key, "count": len(values), "candidates": values}
        for key, values in sorted(near_groups.items())
        if len(values) > 1
        and len({item["formatted_address"] for item in values}) > 1
    ]
    candidate_source_ids = sorted(
        {
            str(row["source_registry_id"])
            for row in source_results
            if int(row.get("candidate_count") or 0) > 0
        }
    )
    state_codes = sorted(
        {str(candidate["address"]["state"]) for candidate in all_candidates}
    )
    phone_count = sum(bool(row["contact"].get("phone")) for row in all_candidates)
    email_count = sum(bool(row["contact"].get("email")) for row in all_candidates)
    coordinate_count = sum(row["map_fields"].get("latitude") is not None for row in all_candidates)
    website_count = sum(bool(row["contact"].get("website")) for row in all_candidates)
    contact_any_count = sum(
        bool(row["contact"].get("phone") or row["contact"].get("email"))
        for row in all_candidates
    )
    manufacturer_scope_count = sum(
        bool(row["truth_dimensions"].get("manufacturer_authorization_scope"))
        for row in all_candidates
    )
    hash_match_count = sum(
        row.get("snapshot", {}).get("preflight_hash_match") is True
        for row in source_results
        if isinstance(row.get("snapshot"), Mapping)
    )
    blocked_ids = {
        str(row.get("source_registry_id") or "")
        for row in excluded
        if "robots_path_not_allowed" in set(row.get("reasons") or [])
    }
    blocked_source_calls = sorted(blocked_ids & set(called_ids))
    candidate_count = len(all_candidates)

    def coverage_rate(count: int) -> float:
        return round(count / candidate_count, 6) if candidate_count else 0.0

    payload = {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "read_only": True,
            "technical_quarantine_only": True,
            "database_accessed": False,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "direct_import_available": False,
            "geocoding_performed": False,
            "legal_approval": False,
            "source_activation": False,
        },
        "generated_at": captured_at,
        "registry_version": registry.get("registry_version"),
        "input_provenance": {
            "technical_preflight_sha256": preflight_sha256,
            "source_registry_sha256": registry_sha256,
        },
        "summary": {
            "registered_source_count": len(registry_by_id),
            "preflight_source_count": len(preflight.get("sources") or []),
            "eligible_source_count": len(eligible),
            "excluded_source_count": len(excluded),
            "fetched_source_count": len(called_ids),
            "sources_with_candidates": len(candidate_source_ids),
            "source_candidate_coverage_rate": (
                round(len(candidate_source_ids) / len(eligible), 6) if eligible else 0.0
            ),
            "candidate_count": len(all_candidates),
            "entity_candidate_count": len(all_candidates),
            "complete_address_count": len(all_candidates),
            "unique_address_count": len(groups),
            "cross_source_duplicate_group_count": len(duplicate_groups),
            "possible_near_duplicate_group_count": len(possible_near_duplicates),
            "state_coverage_count": len(state_codes),
            "state_codes": state_codes,
            "phone_coverage_count": phone_count,
            "phone_coverage_rate": coverage_rate(phone_count),
            "email_coverage_count": email_count,
            "email_coverage_rate": coverage_rate(email_count),
            "website_coverage_count": website_count,
            "website_coverage_rate": coverage_rate(website_count),
            "phone_or_email_coverage_count": contact_any_count,
            "phone_or_email_coverage_rate": coverage_rate(contact_any_count),
            "publisher_coordinate_count": coordinate_count,
            "publisher_coordinate_coverage_rate": coverage_rate(coordinate_count),
            "manufacturer_authorization_scope_field_count": manufacturer_scope_count,
            "manufacturer_authorization_scope_field_rate": coverage_rate(
                manufacturer_scope_count
            ),
            "viltrox_authorization_evidence_count": 0,
            "viltrox_product_presence_evidence_count": 0,
            "preflight_snapshot_hash_match_count": hash_match_count,
            "blocked_source_call_count": len(blocked_source_calls),
            "legal_approval_count": 0,
            "source_activation_count": 0,
            "business_rows_written": 0,
        },
        "called_source_ids": called_ids,
        "blocked_source_calls": blocked_source_calls,
        "excluded_sources": excluded,
        "candidate_source_ids": candidate_source_ids,
        "cross_source_duplicate_groups": duplicate_groups,
        "possible_near_duplicate_groups": possible_near_duplicates,
        "sources": source_results,
        "claim_status": CLAIM_STATUS,
        "truth_note": (
            "Rows are read-only, evidence-bound quarantine candidates. They do not prove "
            "Viltrox authorization, Viltrox product presence, inventory, local impact, legal "
            "approval, source activation, or a Dealer business-table write."
        ),
    }
    payload["artifact_content_sha256"] = _canonical_json_sha256(payload)
    return payload


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "MAX_CAPTURE_BYTES",
    "build_quarantine",
    "eligible_preflight_sources",
    "extract_document_candidates",
]
