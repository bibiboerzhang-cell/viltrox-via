"""Result, grounding, and freshness contracts shared by AI Today consumers."""
from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

_FRESH_HOURS = 36
_RESULT_CONTRACT_VERSION = "market_intel.v1"


def _contract_result(
    status: str,
    value: Any,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status if status in {"ready", "degraded", "invalid"} else "invalid",
        "value": value,
        "errors": list(errors or []),
    }


def _strict_text(
    value: Any,
    field: str,
    *,
    required: bool = True,
    max_length: int = 500,
) -> tuple[str, list[str], list[str]]:
    if value is None:
        return "", [], [f"{field}:missing"] if required else []
    if not isinstance(value, str):
        return "", [f"{field}:expected_string"], []
    text = value.strip()
    if not text:
        return "", [], [f"{field}:empty"] if required else []
    if len(text) > max_length:
        return "", [f"{field}:too_long"], []
    return text, [], []


def _strict_text_list(
    value: Any,
    field: str,
    *,
    required: bool = True,
    max_items: int = 8,
    max_item_length: int = 600,
) -> tuple[list[str], list[str], list[str]]:
    if value is None:
        return [], [], [f"{field}:missing"] if required else []
    if not isinstance(value, list):
        return [], [f"{field}:expected_list"], []
    if len(value) > max_items:
        return [], [f"{field}:too_many_items"], []

    normalized: list[str] = []
    invalid: list[str] = []
    partial: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            invalid.append(f"{field}[{index}]:expected_string")
            continue
        text = item.strip()
        if not text:
            partial.append(f"{field}[{index}]:empty")
            continue
        if len(text) > max_item_length:
            invalid.append(f"{field}[{index}]:too_long")
            continue
        normalized.append(text)
    if required and not normalized:
        partial.append(f"{field}:empty")
    return normalized, invalid, partial


def _public_http_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local"))
    ):
        return ""
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return ""
    except ValueError:
        if "." not in hostname:
            return ""
    return url


def _validate_grounding_sources(value: Any) -> dict[str, Any]:
    """Validate citation metadata without coercing strings or mappings into lists."""
    if value is None:
        return _contract_result("degraded", [], ["sources:missing"])
    if not isinstance(value, list):
        return _contract_result("invalid", [], ["sources:expected_list"])

    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw_source in enumerate(value):
        if not isinstance(raw_source, dict):
            errors.append(f"sources[{index}]:expected_object")
            continue
        relation_raw = raw_source.get("relation_type")
        provider_raw = raw_source.get("provider")
        if relation_raw is not None and not isinstance(relation_raw, str):
            errors.append(f"sources[{index}].relation_type:expected_string")
            continue
        if provider_raw is not None and not isinstance(provider_raw, str):
            errors.append(f"sources[{index}].provider:expected_string")
            continue
        relation = str(relation_raw or "").strip().lower()
        provider = str(provider_raw or "").strip().lower()
        is_grounding = relation == "grounding" or (
            not relation and provider in {"", "google", "google_search"}
        )
        if not is_grounding:
            continue

        raw_url = raw_source.get("url") if raw_source.get("url") is not None else raw_source.get("uri")
        url = _public_http_url(raw_url)
        if not url:
            errors.append(f"sources[{index}].url:invalid_public_http_url")
            continue
        title_raw = raw_source.get("title")
        if title_raw is not None and not isinstance(title_raw, str):
            errors.append(f"sources[{index}].title:expected_string")
            continue
        if url in seen:
            continue
        seen.add(url)
        if len(normalized) < 8:
            normalized.append({**raw_source, "url": url, "relation_type": "grounding"})

    if errors:
        return _contract_result("invalid", normalized, errors)
    if not normalized:
        return _contract_result("degraded", [], ["sources:no_grounding_urls"])
    return _contract_result("ready", normalized)


def _validate_ai_today_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _contract_result("invalid", {}, ["result:expected_object"])

    invalid: list[str] = []
    partial: list[str] = []
    headline, field_invalid, field_partial = _strict_text(value.get("headline"), "headline", max_length=120)
    invalid.extend(field_invalid)
    partial.extend(field_partial)
    shooting_plans, field_invalid, field_partial = _strict_text_list(
        value.get("shooting_plans"),
        "shooting_plans",
        max_items=5,
        max_item_length=600,
    )
    invalid.extend(field_invalid)
    partial.extend(field_partial)
    hot_topics, field_invalid, field_partial = _strict_text_list(
        value.get("hot_topics"),
        "hot_topics",
        max_items=5,
        max_item_length=400,
    )
    invalid.extend(field_invalid)
    partial.extend(field_partial)
    hot_brands, field_invalid, field_partial = _strict_text_list(
        value.get("hot_brands"),
        "hot_brands",
        required=False,
        max_items=12,
        max_item_length=80,
    )
    invalid.extend(field_invalid)
    partial.extend(field_partial)

    normalized = {
        "headline": headline,
        "shooting_plans": shooting_plans,
        "hot_topics": hot_topics,
    }
    if "hot_brands" in value:
        normalized["hot_brands"] = hot_brands
    if invalid:
        return _contract_result("invalid", normalized, invalid + partial)
    if partial:
        return _contract_result("degraded", normalized, partial)
    return _contract_result("ready", normalized)


def _validate_video_evidence(value: Any) -> dict[str, Any]:
    if value is None:
        return _contract_result("ready", [])
    if not isinstance(value, list):
        return _contract_result("invalid", [], ["evidence:expected_list"])

    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            errors.append(f"evidence[{index}]:expected_object")
            continue
        source_url = _public_http_url(raw_item.get("source_url") or raw_item.get("content_url"))
        if not source_url:
            errors.append(f"evidence[{index}].source_url:invalid_public_http_url")
            continue
        title_raw = raw_item.get("title")
        if title_raw is not None and not isinstance(title_raw, str):
            errors.append(f"evidence[{index}].title:expected_string")
            continue
        refs = raw_item.get("source_refs")
        if refs is not None and (
            not isinstance(refs, list) or any(not isinstance(ref, dict) for ref in refs)
        ):
            errors.append(f"evidence[{index}].source_refs:expected_object_list")
            continue
        normalized.append({**raw_item, "source_url": source_url})
    if errors:
        return _contract_result("invalid", normalized, errors)
    return _contract_result("ready", normalized)


def _result_status(
    contract_status: str,
    freshness_status: str,
    *,
    grounded: bool,
) -> str:
    if contract_status == "invalid":
        return "invalid"
    if contract_status != "ready" or freshness_status != "fresh" or not grounded:
        return "degraded"
    return "ready"


def _field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, dict):
            candidate = value.get(name)
            if candidate is not None:
                return candidate
            continue
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (str, bytes)):
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _extract_grounding_sources(response: Any) -> list[dict[str, str]]:
    """Extract current google.genai and legacy/REST grounding citation shapes."""
    candidates = _as_list(_field(response, "candidates"))
    if not candidates:
        legacy_result = _field(response, "_result", "result")
        candidates = _as_list(_field(legacy_result, "candidates"))
    if not candidates and _field(response, "grounding_metadata", "groundingMetadata") is not None:
        candidates = [response]

    sources: list[dict[str, str]] = []
    seen: set[str] = set()

    def append_source(value: Any) -> None:
        nested_source = _field(value, "web", "source")
        source = nested_source if nested_source is not None else value
        url = str(_field(source, "uri", "url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            return
        seen.add(url)
        sources.append(
            {
                "title": str(_field(source, "title") or urlparse(url).netloc or "Google 搜索来源")[:180],
                "url": url,
                "provider": "google_search",
                "relation_type": "grounding",
            }
        )

    # response.text is the first candidate's text; never use a later
    # candidate's citations to legitimize that claim.
    for candidate in candidates[:1]:
        metadata = _field(candidate, "grounding_metadata", "groundingMetadata")
        chunks = _as_list(_field(metadata, "grounding_chunks", "groundingChunks"))
        chunks.extend(_as_list(_field(candidate, "grounding_chunks", "groundingChunks")))
        for chunk in chunks:
            append_source(chunk)
            if len(sources) >= 8:
                return sources

        # Older GenerateAnswer/serialized responses may expose attributions
        # directly instead of nesting web chunks under grounding metadata.
        attributions = _as_list(_field(candidate, "grounding_attributions", "groundingAttributions"))
        attributions.extend(_as_list(_field(metadata, "grounding_attributions", "groundingAttributions")))
        for attribution in attributions:
            append_source(attribution)
            if len(sources) >= 8:
                return sources

        citation_metadata = _field(candidate, "citation_metadata", "citationMetadata")
        for citation in _as_list(_field(citation_metadata, "citations", "citationSources", "citation_sources")):
            append_source(citation)
            if len(sources) >= 8:
                return sources

    return sources


def _stored_grounding_sources(value: Any) -> list[dict[str, Any]]:
    """Return only persisted direct citations; later brand-context links do not ground a claim."""
    return list(_validate_grounding_sources(value).get("value") or [])


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: Any) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return ""
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _freshness_payload(*values: Any) -> dict[str, Any]:
    generated = None
    for value in values:
        generated = _parse_datetime(value)
        if generated is not None:
            break
    if generated is None:
        return {
            "freshness_status": "unknown",
            "freshness_label": "更新时间未记录",
            "age_hours": None,
        }
    age_hours = max(0.0, (datetime.now(tz=timezone.utc) - generated).total_seconds() / 3600)
    stale = age_hours > _FRESH_HOURS
    return {
        "freshness_status": "stale" if stale else "fresh",
        "freshness_label": f"已过期 · {max(1, round(age_hours / 24))} 天前" if stale else f"新鲜 · {max(1, round(age_hours))} 小时前",
        "age_hours": round(age_hours, 1),
    }
