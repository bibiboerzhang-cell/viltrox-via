"""Fail-closed structured Event-feed adapters.

The adapters stop at migration-257 shaped candidate records.  They contain no
HTTP client, scheduler hook, SQL call, persistence method, or business-table
promotion path.  A caller must supply a registered source descriptor whose
terms/robots review is explicitly recorded and allowed before any payload is
parsed.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domains.events import us_coverage_registry
from app.domains.source_passport_core import canonical_json_sha256
from app.domains.source_passport_urls import source_url_identity


CONTRACT_ID = "vkpi.event_radar.structured_feed_candidates"
CONTRACT_VERSION = 1
CLAIM_STATUS = "descriptive_only"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
MAX_FEED_ITEMS = 500
MAX_REJECTIONS = 100

PARSER_TRIBE_JSON = "wordpress_tribe_json_v1"
PARSER_ICS = "ics_vevent_v1"
PARSER_ATOM = "atom_event_v1"
SUPPORTED_PARSER_PROFILES = frozenset(
    {PARSER_TRIBE_JSON, PARSER_ICS, PARSER_ATOM}
)
ALLOWED_TERMS_ROBOTS_STATUSES = frozenset(
    {"reviewed_allowed", "reviewed_public_feed_allowed"}
)

# These policies are code-owned, reviewed identities.  Caller-supplied source
# objects cannot widen feed or evidence hosts.  RIT is intentionally absent:
# its activity page is registered, but no exact public ICS endpoint is reviewed.
STRUCTURED_SOURCE_FEED_POLICIES: dict[str, dict[str, Any]] = {
    "dealer_samys_photo_school_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://samysphotoschool.com/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("samysphotoschool.com", "www.samysphotoschool.com"),
    },
    "dealer_hunts_photo_calendar_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://edu.huntsphoto.com/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("edu.huntsphoto.com",),
    },
    "dealer_natcam_events_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://www.natcam.com/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("www.natcam.com", "natcam.com"),
    },
    "dealer_pauls_creative_academy_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://creativephotoacademy.com/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("creativephotoacademy.com", "www.creativephotoacademy.com"),
    },
    "photo_asmp_chapters_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://www.asmp.org/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("www.asmp.org", "asmp.org"),
    },
    "school_hcp_events_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://hcponline.org/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("hcponline.org", "www.hcponline.org"),
    },
    "school_maine_media_photography_us": {
        "parser_profile": PARSER_TRIBE_JSON,
        "feed_url": "https://www.mainemedia.edu/wp-json/tribe/events/v1/events",
        "evidence_hosts": ("www.mainemedia.edu", "mainemedia.edu"),
    },
    "university_gw_corcoran_events_dc_us": {
        "parser_profile": PARSER_ICS,
        "feed_url": "https://calendar.gwu.edu/corcoran/calendar.ics",
        "evidence_hosts": ("calendar.gwu.edu",),
    },
    "brand_viltrox_official_event_feed_us": {
        "parser_profile": PARSER_ATOM,
        "feed_url": "https://viltrox.com/blogs/event.atom",
        "evidence_hosts": ("viltrox.com", "www.viltrox.com"),
    },
}
STRUCTURED_SOURCE_PARSER_PROFILES = {
    source_id: str(policy["parser_profile"])
    for source_id, policy in STRUCTURED_SOURCE_FEED_POLICIES.items()
}

_REVIEWER_RE = re.compile(r"^staff_[1-9][0-9]{0,18}$")
_SPACE_RE = re.compile(r"\s+")
_STATE_TOKEN_RE = re.compile(r"(?:^|[\s,])([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?(?:$|[,\s])")
_ZIP_SUFFIX_RE = re.compile(r"\s+\d{5}(?:-\d{4})?\s*$")
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)
_US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "washington, dc": "DC",
}


class EventFeedBlocked(RuntimeError):
    """The source has not passed the explicit public-feed fetch gate."""


class MalformedEventFeed(ValueError):
    """The bounded structured payload cannot be decoded."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_text(value: Any, *, limit: int = 4000) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(str(value or ""))
    except (ValueError, TypeError):
        return ""
    return _SPACE_RE.sub(" ", html.unescape(" ".join(parser.parts))).strip()[:limit]


def _review_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat()


def _public_https_url(value: Any) -> str | None:
    identity = source_url_identity(value)
    canonical = identity.get("canonical_url") if identity["valid"] else None
    if not canonical:
        return None
    host = str(urlsplit(canonical).hostname or "").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        return None
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return canonical
    if not address.is_global:
        return None
    return canonical


def _registered_event_sources() -> dict[str, dict[str, Any]]:
    return {
        str(row.get("id") or ""): row
        for row in us_coverage_registry.audit_registry().get("event_sources", [])
    }


def _bound_feed_url(source_id: str, value: Any) -> tuple[str | None, str | None]:
    """Return the code-registered feed identity or one fail-closed reason."""
    raw_text = str(value or "").strip()
    actual = _public_https_url(value)
    if not actual:
        return None, "feed_url_not_public_https"
    policy = STRUCTURED_SOURCE_FEED_POLICIES.get(source_id)
    if not policy:
        return None, "structured_feed_policy_missing"
    expected = _public_https_url(policy.get("feed_url"))
    if not expected:
        return None, "feed_url_not_public_https"
    actual_parts = urlsplit(actual)
    expected_parts = urlsplit(expected)
    actual_base = urlunsplit(
        (actual_parts.scheme, actual_parts.netloc, actual_parts.path, "", "")
    )
    expected_base = urlunsplit(
        (expected_parts.scheme, expected_parts.netloc, expected_parts.path, "", "")
    )
    if actual_base != expected_base:
        return None, "feed_url_registry_binding_mismatch"
    try:
        query_pairs = parse_qsl(urlsplit(raw_text).query, keep_blank_values=True)
    except ValueError:
        return None, "feed_url_query_not_allowed"
    if query_pairs:
        if str(policy.get("parser_profile")) != PARSER_TRIBE_JSON:
            return None, "feed_url_query_not_allowed"
        seen_query_keys: set[str] = set()
        for key, item in query_pairs:
            if (
                key not in {"page", "per_page"}
                or key in seen_query_keys
                or not item.isdecimal()
            ):
                return None, "feed_url_query_not_allowed"
            seen_query_keys.add(key)
            numeric = int(item)
            if numeric < 1 or numeric > (500 if key == "per_page" else 10000):
                return None, "feed_url_query_not_allowed"
    return expected, None


def _evidence_url_for_source(source_id: str, value: Any) -> str | None:
    canonical = _public_https_url(value)
    policy = STRUCTURED_SOURCE_FEED_POLICIES.get(source_id)
    if not canonical or not policy:
        return None
    host = str(urlsplit(canonical).hostname or "").casefold()
    allowed_hosts = {
        str(item or "").strip().casefold()
        for item in policy.get("evidence_hosts", ())
        if str(item or "").strip()
    }
    return canonical if host in allowed_hosts else None


def source_fetch_preflight(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one already-reviewed source descriptor without fetching it."""
    if not isinstance(source, Mapping):
        raise ValueError("source must be an object")
    source_id = str(source.get("id") or "").strip()
    registered = _registered_event_sources().get(source_id)
    reasons: list[str] = []
    if registered is None:
        reasons.append("source_not_registered")
    registered_canonical_url = (
        _public_https_url(registered.get("canonical_url")) if registered else None
    )
    canonical_url = _public_https_url(source.get("canonical_url"))
    if not canonical_url:
        reasons.append("canonical_url_not_public_https")
    elif registered and canonical_url != registered_canonical_url:
        reasons.append("canonical_url_registry_mismatch")
    feed_url, feed_url_reason = _bound_feed_url(source_id, source.get("feed_url"))
    if feed_url_reason:
        reasons.append(feed_url_reason)
    parser_profile = str(
        source.get("parser_profile")
        or STRUCTURED_SOURCE_PARSER_PROFILES.get(source_id)
        or ""
    ).strip()
    expected_profile = STRUCTURED_SOURCE_PARSER_PROFILES.get(source_id)
    if parser_profile not in SUPPORTED_PARSER_PROFILES:
        reasons.append("parser_profile_not_supported")
    elif expected_profile and parser_profile != expected_profile:
        reasons.append("parser_profile_registry_binding_mismatch")
    if str(source.get("status") or "").strip().casefold() != "active":
        reasons.append("source_not_active")
    if source.get("enabled") is not True:
        reasons.append("source_not_enabled")
    terms_status = str(source.get("terms_robots_status") or "").strip().casefold()
    if terms_status not in ALLOWED_TERMS_ROBOTS_STATUSES:
        reasons.append("terms_robots_not_reviewed_allowed")
    if not _REVIEWER_RE.fullmatch(str(source.get("terms_robots_reviewer_id") or "").strip()):
        reasons.append("terms_robots_reviewer_missing")
    review_timestamp = _review_timestamp(source.get("terms_robots_reviewed_at"))
    if not review_timestamp:
        reasons.append("terms_robots_review_timestamp_invalid")
    if source.get("requires_human_review") is not True:
        reasons.append("candidate_human_review_not_required")
    if source.get("direct_import_allowed") is not False:
        reasons.append("direct_import_must_remain_disabled")
    return {
        "allowed": not reasons,
        "source_registry_id": source_id or None,
        "parser_profile": parser_profile or None,
        "canonical_url": canonical_url,
        "feed_url": feed_url,
        "terms_robots_status": terms_status or "unavailable",
        "terms_robots_reviewed_at": review_timestamp,
        "reasons": reasons,
        "candidate_only": True,
        "automatic_promotion": False,
    }


def _bounded_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise MalformedEventFeed("feed payload exceeds bounded size")
        try:
            return payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MalformedEventFeed("feed payload is not UTF-8") from exc
    if isinstance(payload, str):
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise MalformedEventFeed("feed payload exceeds bounded size")
        return payload
    if isinstance(payload, Mapping):
        try:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MalformedEventFeed("JSON feed payload is not serializable") from exc
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise MalformedEventFeed("feed payload exceeds bounded size")
        return payload
    raise MalformedEventFeed("unsupported feed payload type")


def _source_timezone(source: Mapping[str, Any]) -> str:
    name = str(source.get("timezone") or "UTC").strip() or "UTC"
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("source timezone is invalid") from exc
    return name


def _organization_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("organization_id must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("organization_id must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError("organization_id must be a positive integer")
    return parsed


def _normalize_state(value: Any) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if not text:
        return ""
    upper = text.upper()
    if upper in _US_STATE_CODES:
        return upper
    return _US_STATE_NAMES.get(text.casefold(), "")


def _state_from_location_segment(value: Any) -> str:
    """Resolve one comma-delimited state segment without fuzzy guessing."""
    text = _plain_text(value, limit=80).strip(" ,")
    if not text:
        return ""
    return _normalize_state(_ZIP_SUFFIX_RE.sub("", text).strip(" ,"))


def _location_parts(
    *,
    venue: Any = "",
    address: Any = "",
    city: Any = "",
    region: Any = "",
    country: Any = "",
) -> dict[str, str]:
    venue_text = _plain_text(venue, limit=300)
    address_text = _plain_text(address, limit=500)
    city_text = _plain_text(city, limit=160)
    region_code = _normalize_state(region)
    country_text = str(country or "").strip().casefold()
    country_code = "US" if country_text in {
        "us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"
    } else ""
    combined = ", ".join(part for part in (venue_text, address_text) if part)
    parts = [part.strip() for part in combined.split(",") if part.strip()]
    state_index = -1
    if not region_code:
        for index in range(len(parts) - 1, -1, -1):
            inferred = _state_from_location_segment(parts[index])
            if inferred:
                region_code = inferred
                state_index = index
                break
        if not region_code:
            match = _STATE_TOKEN_RE.search(combined.upper())
            if match and match.group(1) in _US_STATE_CODES:
                region_code = match.group(1)
    elif parts:
        state_index = next(
            (
                index
                for index in range(len(parts) - 1, -1, -1)
                if _state_from_location_segment(parts[index]) == region_code
            ),
            -1,
        )
    if region_code:
        country_code = "US"
    if not city_text and combined and region_code:
        if state_index > 0:
            city_text = parts[state_index - 1][:160]
    return {
        "venue": venue_text,
        "address": address_text,
        "city": city_text,
        "region": region_code,
        "country_code": country_code,
    }


def _parse_datetime(value: Any, *, timezone_name: str) -> tuple[str, str, str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing event time")
    parsed: datetime
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            raise ValueError("invalid event time") from None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("event timezone is invalid") from exc
    if parsed.tzinfo is None:
        valid_candidates: list[datetime] = []
        seen_offsets: set[timedelta | None] = set()
        for fold in (0, 1):
            candidate = parsed.replace(tzinfo=zone, fold=fold)
            round_trip = (
                candidate.astimezone(timezone.utc)
                .astimezone(zone)
                .replace(tzinfo=None)
            )
            if round_trip != parsed or candidate.utcoffset() in seen_offsets:
                continue
            seen_offsets.add(candidate.utcoffset())
            valid_candidates.append(candidate)
        if not valid_candidates:
            raise ValueError("event local time does not exist in source timezone")
        if len(valid_candidates) > 1:
            raise ValueError("event local time is ambiguous in source timezone")
        parsed = valid_candidates[0]
    else:
        parsed = parsed.astimezone(zone)
    return parsed.isoformat(), parsed.date().isoformat(), "date_time"


def _parse_date(value: Any) -> tuple[str, str, str]:
    text = str(value or "").strip()
    try:
        parsed = (
            date.fromisoformat(text[:10])
            if "-" in text
            else datetime.strptime(text, "%Y%m%d").date()
        )
    except ValueError as exc:
        raise ValueError("invalid event date") from exc
    return parsed.isoformat(), parsed.isoformat(), "date"


def _tribe_items(payload: Any, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise MalformedEventFeed("Tribe feed is not valid JSON") from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("events"), list):
        raise MalformedEventFeed("Tribe feed must contain an events array")
    result: list[dict[str, Any]] = []
    for raw in data["events"][: MAX_FEED_ITEMS + 1]:
        if not isinstance(raw, Mapping):
            result.append({"_error": "event_not_object"})
            continue
        venue = raw.get("venue") if isinstance(raw.get("venue"), Mapping) else {}
        organizer_raw = raw.get("organizer")
        if isinstance(organizer_raw, list):
            organizer = ", ".join(
                str(item.get("organizer") or "").strip()
                for item in organizer_raw
                if isinstance(item, Mapping) and item.get("organizer")
            )
        elif isinstance(organizer_raw, Mapping):
            organizer = str(organizer_raw.get("organizer") or "").strip()
        else:
            organizer = ""
        result.append(
            {
                "uid": str(raw.get("global_id") or raw.get("id") or "").strip(),
                "title": _plain_text(raw.get("title"), limit=500),
                "description": _plain_text(raw.get("description")),
                "start": raw.get("start_date"),
                "end": raw.get("end_date"),
                "timezone": str(raw.get("timezone") or source.get("timezone") or "UTC"),
                "all_day": bool(raw.get("all_day")),
                "venue": venue.get("venue"),
                "address": ", ".join(
                    str(value).strip()
                    for value in (venue.get("address"), venue.get("zip"))
                    if str(value or "").strip()
                ),
                "city": venue.get("city"),
                "region": venue.get("stateprovince") or venue.get("state"),
                "country": venue.get("country"),
                "organizer": organizer,
                "evidence_url": raw.get("url") or raw.get("website"),
                "published_at": raw.get("date"),
                "updated_at": raw.get("modified"),
                "is_online": bool(raw.get("is_virtual")),
                "raw_hash": canonical_json_sha256(raw),
            }
        )
    return result


def _unfold_ics(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_unescape(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ics_items(payload: Any, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, str):
        raise MalformedEventFeed("ICS feed must be text")
    if "BEGIN:VCALENDAR" not in payload or "END:VCALENDAR" not in payload:
        raise MalformedEventFeed("ICS VCALENDAR envelope is missing")
    events: list[dict[str, tuple[dict[str, str], str]]] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in _unfold_ics(payload):
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise MalformedEventFeed("nested ICS VEVENT is invalid")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise MalformedEventFeed("ICS VEVENT end without start")
            events.append(current)
            current = None
            if len(events) > MAX_FEED_ITEMS:
                break
            continue
        if current is None or ":" not in line:
            continue
        head, value = line.split(":", 1)
        parts = head.split(";")
        name = parts[0].upper()
        params = {
            key.upper(): item
            for part in parts[1:]
            if "=" in part
            for key, item in [part.split("=", 1)]
        }
        current.setdefault(name, (params, _ics_unescape(value)))
    if current is not None:
        raise MalformedEventFeed("unterminated ICS VEVENT")
    result: list[dict[str, Any]] = []
    source_timezone = str(source.get("timezone") or "UTC")
    for event in events:
        start_params, start = event.get("DTSTART", ({}, ""))
        end_params, end = event.get("DTEND", ({}, ""))
        location = event.get("LOCATION", ({}, ""))[1]
        city = event.get("X-VKPI-CITY", ({}, ""))[1]
        region = event.get("X-VKPI-STATE", ({}, ""))[1]
        country = event.get("X-VKPI-COUNTRY", ({}, ""))[1]
        timezone_name = start_params.get("TZID") or source_timezone
        all_day = start_params.get("VALUE", "").upper() == "DATE" or bool(
            re.fullmatch(r"\d{8}", start)
        )
        result.append(
            {
                "uid": event.get("UID", ({}, ""))[1].strip(),
                "title": _plain_text(event.get("SUMMARY", ({}, ""))[1], limit=500),
                "description": _plain_text(event.get("DESCRIPTION", ({}, ""))[1]),
                "start": start,
                "end": end,
                "timezone": timezone_name,
                "all_day": all_day,
                "ics_end_exclusive": all_day and bool(end),
                "venue": location.split(",", 1)[0].strip() if location else "",
                "address": location,
                "city": city,
                "region": region,
                "country": country,
                "organizer": event.get("ORGANIZER", ({}, ""))[1],
                "evidence_url": event.get("URL", ({}, ""))[1],
                "published_at": event.get("CREATED", ({}, ""))[1],
                "updated_at": event.get("LAST-MODIFIED", ({}, ""))[1],
                "is_online": event.get(
                    "X-MICROSOFT-CDO-LOCATIONDISPLAYNAME", ({}, "")
                )[1].casefold()
                == "online",
                "raw_hash": canonical_json_sha256(
                    {key: {"params": params, "value": value} for key, (params, value) in event.items()}
                ),
            }
        )
    return result


def _xml_local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].casefold()


def _atom_items(payload: Any, source: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, str):
        raise MalformedEventFeed("Atom feed must be text")
    if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
        raise MalformedEventFeed("Atom DTD and entity declarations are not allowed")
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise MalformedEventFeed("Atom feed is not valid XML") from exc
    if _xml_local_name(root.tag) != "feed":
        raise MalformedEventFeed("Atom feed root must be feed")
    result: list[dict[str, Any]] = []
    for entry in [node for node in list(root) if _xml_local_name(node.tag) == "entry"][: MAX_FEED_ITEMS + 1]:
        values: dict[str, str] = {}
        evidence_url = ""
        for child in list(entry):
            name = _xml_local_name(child.tag)
            if name == "link":
                rel = str(child.attrib.get("rel") or "alternate").casefold()
                if rel == "alternate" and not evidence_url:
                    evidence_url = str(child.attrib.get("href") or "")
                continue
            if name not in values:
                values[name] = "".join(child.itertext()).strip()
        result.append(
            {
                "uid": values.get("id", ""),
                "title": _plain_text(values.get("title"), limit=500),
                "description": _plain_text(values.get("content") or values.get("summary")),
                # Atom publication time is deliberately not treated as an event
                # date.  An explicit event extension is required.
                "start": values.get("start") or values.get("event-start"),
                "end": values.get("end") or values.get("event-end"),
                "timezone": values.get("timezone") or str(source.get("timezone") or "UTC"),
                "all_day": values.get("all-day", "").casefold() == "true",
                "venue": values.get("venue", ""),
                "address": values.get("address", ""),
                "city": values.get("city", ""),
                "region": values.get("state") or values.get("region", ""),
                "country": values.get("country", ""),
                "organizer": values.get("author", "") or str(source.get("publisher") or ""),
                "evidence_url": evidence_url,
                "published_at": values.get("published", ""),
                "updated_at": values.get("updated", ""),
                "is_online": values.get("online", "").casefold() == "true",
                "raw_hash": canonical_json_sha256(values),
            }
        )
    return result


def _normalize_item(
    raw: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    preflight: Mapping[str, Any],
    observed_at: str,
    organization_id: int,
) -> dict[str, Any]:
    if raw.get("_error"):
        raise ValueError(str(raw["_error"]))
    uid = str(raw.get("uid") or "").strip()
    title = _plain_text(raw.get("title"), limit=500)
    if not uid:
        raise ValueError("missing_event_uid")
    if not title:
        raise ValueError("missing_title")
    source_id = str(preflight["source_registry_id"])
    raw_evidence_url = _public_https_url(raw.get("evidence_url"))
    if not raw_evidence_url:
        raise ValueError("missing_public_evidence_url")
    evidence_url = _evidence_url_for_source(source_id, raw_evidence_url)
    if not evidence_url:
        raise ValueError("evidence_url_host_not_allowlisted")
    timezone_name = str(raw.get("timezone") or _source_timezone(source)).strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("event timezone is invalid") from exc
    all_day = bool(raw.get("all_day"))
    if all_day:
        start_at, start_date, precision = _parse_date(raw.get("start"))
        if raw.get("end"):
            end_value = _parse_date(raw.get("end"))[1]
            if raw.get("ics_end_exclusive"):
                end_value = (date.fromisoformat(end_value) - timedelta(days=1)).isoformat()
            end_at = end_date = end_value
        else:
            end_at = end_date = start_date
    else:
        start_at, start_date, precision = _parse_datetime(
            raw.get("start"), timezone_name=timezone_name
        )
        if raw.get("end"):
            end_at, end_date, _ = _parse_datetime(
                raw.get("end"), timezone_name=timezone_name
            )
        else:
            end_at, end_date = start_at, start_date
    if end_date < start_date:
        raise ValueError("end_before_start")
    location = _location_parts(
        venue=raw.get("venue"),
        address=raw.get("address"),
        city=raw.get("city"),
        region=raw.get("region"),
        country=raw.get("country"),
    )
    if location["country_code"] != "US":
        raise ValueError("us_location_unverified")
    if not location["region"]:
        raise ValueError("us_state_or_dc_missing")

    dedupe_time = (
        start_date
        if precision == "date"
        else datetime.fromisoformat(start_at).astimezone(timezone.utc).isoformat()
    )
    dedupe_fingerprint = canonical_json_sha256(
        {
            "title": _SPACE_RE.sub(" ", title).casefold(),
            "start": dedupe_time,
            "city": location["city"].casefold(),
            "region": location["region"],
        }
    )
    core_payload = {
        "event_uid": uid,
        "title": title,
        "description": _plain_text(raw.get("description")),
        "organizer": _plain_text(raw.get("organizer"), limit=300),
        "start_at": start_at,
        "end_at": end_at,
        "start_date": start_date,
        "end_date": end_date,
        "timezone": timezone_name,
        "date_precision": precision,
        **location,
        "is_online": bool(raw.get("is_online")),
        "evidence_url": evidence_url,
        "source_published_at": str(raw.get("published_at") or "").strip() or None,
        "source_updated_at": str(raw.get("updated_at") or "").strip() or None,
        "source_content_sha256": str(raw.get("raw_hash") or ""),
        "dedupe_fingerprint": dedupe_fingerprint,
        "claim_status": CLAIM_STATUS,
        "evidence_limitations": [
            "does_not_prove_viltrox_participation",
            "does_not_prove_attendance",
            "does_not_prove_sales_roi_or_local_impact",
        ],
    }
    normalized_content_sha = canonical_json_sha256(core_payload)
    entity_digest = sha256(f"{source_id}\0{uid}".encode("utf-8")).hexdigest()[:40]
    source_entity_key = f"event.{entity_digest}"
    candidate_payload = {
        **core_payload,
        "provenance": {
            "source_registry_id": source_id,
            "publisher": str(source.get("publisher") or "").strip(),
            "parser_profile": preflight["parser_profile"],
            "feed_url": preflight["feed_url"],
            "evidence_url": evidence_url,
            "external_uid": uid,
            "observed_at": observed_at,
            "terms_robots_status": preflight["terms_robots_status"],
            "terms_robots_reviewed_at": preflight["terms_robots_reviewed_at"],
            "normalized_content_sha256": normalized_content_sha,
        },
    }
    content_sha = canonical_json_sha256(candidate_payload)
    candidate_id = "cand_" + canonical_json_sha256(
        {
            "organization_id": organization_id,
            "candidate_type": "event_opportunity",
            "source_registry_id": source_id,
            "source_entity_key": source_entity_key,
        }
    )[:32]
    return {
        "id": candidate_id,
        "organization_id": organization_id,
        "record_only": True,
        "candidate_type": "event_opportunity",
        "source_registry_id": source_id,
        "source_entity_key": source_entity_key,
        "source_url": evidence_url,
        "stable_org_key": "",
        "stable_location_key": "",
        "content_sha256": content_sha,
        "candidate_payload": candidate_payload,
        "review_status": "pending",
        "promotion_gate_status": "blocked",
        "claim_status": CLAIM_STATUS,
    }


def adapt_feed_to_candidates(
    source: Mapping[str, Any],
    payload: Any,
    *,
    observed_at: datetime,
    organization_id: Any,
) -> dict[str, Any]:
    """Parse a bounded offline payload into record-only candidate envelopes."""
    preflight = source_fetch_preflight(source)
    if not preflight["allowed"]:
        raise EventFeedBlocked("event feed source blocked: " + ",".join(preflight["reasons"]))
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    org_id = _organization_id(organization_id)
    observed_iso = observed_at.astimezone(timezone.utc).isoformat()
    bounded = _bounded_payload(payload)
    parser_profile = str(preflight["parser_profile"])
    if parser_profile == PARSER_TRIBE_JSON:
        raw_items = _tribe_items(bounded, source)
    elif parser_profile == PARSER_ICS:
        raw_items = _ics_items(bounded, source)
    elif parser_profile == PARSER_ATOM:
        raw_items = _atom_items(bounded, source)
    else:  # pragma: no cover - preflight owns this boundary
        raise EventFeedBlocked("event feed parser is unsupported")
    if len(raw_items) > MAX_FEED_ITEMS:
        raise MalformedEventFeed("feed item count exceeds bounded limit")

    candidates_by_identity: dict[str, dict[str, Any]] = {}
    natural_identities: dict[str, str] = {}
    natural_key_by_identity: dict[str, str] = {}
    conflicted: set[str] = set()
    rejections: list[dict[str, Any]] = []
    rejected_count = 0
    duplicate_count = 0
    for index, raw in enumerate(raw_items):
        try:
            candidate = _normalize_item(
                raw,
                source=source,
                preflight=preflight,
                observed_at=observed_iso,
                organization_id=org_id,
            )
        except (TypeError, ValueError) as exc:
            rejected_count += 1
            if len(rejections) < MAX_REJECTIONS:
                rejections.append({"index": index, "reason": str(exc)[:160]})
            continue
        identity = candidate["source_entity_key"]
        if identity in conflicted:
            duplicate_count += 1
            continue
        existing = candidates_by_identity.get(identity)
        if existing:
            duplicate_count += 1
            if existing["content_sha256"] != candidate["content_sha256"]:
                candidates_by_identity.pop(identity, None)
                previous_natural_key = natural_key_by_identity.pop(identity, None)
                if previous_natural_key:
                    natural_identities.pop(previous_natural_key, None)
                conflicted.add(identity)
                rejected_count += 1
                if len(rejections) < MAX_REJECTIONS:
                    rejections.append({"index": index, "reason": "duplicate_identity_conflict"})
            continue
        payload_row = candidate["candidate_payload"]
        natural_key = str(payload_row["dedupe_fingerprint"])
        natural_existing = natural_identities.get(natural_key)
        if natural_existing:
            duplicate_count += 1
            continue
        candidates_by_identity[identity] = candidate
        natural_identities[natural_key] = identity
        natural_key_by_identity[identity] = natural_key

    candidates = sorted(
        candidates_by_identity.values(),
        key=lambda row: (
            str(row["candidate_payload"]["start_at"]),
            str(row["candidate_payload"]["title"]).casefold(),
            row["source_entity_key"],
        ),
    )
    return {
        "status": "ready" if candidates else "empty",
        "record_only": True,
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "network_accessed": False,
            "database_accessed": False,
            "provider_calls": 0,
            "scheduler_enabled": False,
            "business_rows_written": 0,
            "candidate_rows_written": 0,
        },
        "source": {
            "source_registry_id": preflight["source_registry_id"],
            "parser_profile": preflight["parser_profile"],
            "feed_url": preflight["feed_url"],
            "terms_robots_status": preflight["terms_robots_status"],
            "terms_robots_reviewed_at": preflight["terms_robots_reviewed_at"],
        },
        "organization_id": org_id,
        "counts": {
            "parsed_items": len(raw_items),
            "candidate_items": len(candidates),
            "duplicate_items": duplicate_count,
            "rejected_items": rejected_count,
        },
        "candidates": candidates,
        "rejections": rejections,
        "promotion_gate": {
            "status": "blocked",
            "automatic_promotion": False,
            "human_review_required": True,
        },
        "dedupe": {
            "automatic_cross_source_merge": False,
            "within_source_payload": True,
            "cross_source_review_fingerprint": "candidate_payload.dedupe_fingerprint",
        },
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
    }


__all__ = [
    "ALLOWED_TERMS_ROBOTS_STATUSES",
    "EventFeedBlocked",
    "MalformedEventFeed",
    "PARSER_ATOM",
    "PARSER_ICS",
    "PARSER_TRIBE_JSON",
    "STRUCTURED_SOURCE_FEED_POLICIES",
    "STRUCTURED_SOURCE_PARSER_PROFILES",
    "SUPPORTED_PARSER_PROFILES",
    "adapt_feed_to_candidates",
    "source_fetch_preflight",
]
