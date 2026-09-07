"""Versioned, provider-free language hints and independent evidence clocks.

YouTube relevanceLanguage is a ranking hint, not a language filter; channel
publishedAt is channel creation, not activity. Public references:
https://developers.google.com/youtube/v3/docs/search/list
https://developers.google.com/youtube/v3/docs/search
No pinned Apify build/input schema has been verified for these new hints, so
Actor input is unchanged and its returned content needs a posterior window gate.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

SCHEMA = "provider_discovery_policy_v1"
TEMPORAL_SCHEMA = "provider_temporal_evidence_v1"
MAX_DISCOVERY_DAYS = 45
_MODES = frozenset({"require", "include_unknown", "exclude"})
_NORMALIZED_LANGUAGES = frozenset({
    "ar", "de", "en", "es", "fr", "id", "it", "ja", "ko", "ms", "nl",
    "pl", "pt", "ru", "sv", "th", "tr", "vi", "zh",
})


def _days(value: Any, *, maximum: int, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("provider_policy_invalid_window")
    return value


def _language_filter(value: Any) -> dict[str, Any]:
    if value is None:
        return {"requested": False, "mode": "require", "values": [], "invalid": []}
    if not isinstance(value, dict) or set(value) - {"requested", "mode", "values", "invalid", "maximum"}:
        raise ValueError("provider_policy_invalid_language_filter")
    if "maximum" in value and (type(value["maximum"]) is not int or value["maximum"] != 8):
        raise ValueError("provider_policy_invalid_language_filter")
    values = value.get("values")
    requested = value.get("requested")
    mode = value.get("mode", "require")
    if (not isinstance(values, list) or len(values) > 8 or type(requested) is not bool
            or not isinstance(mode, str) or mode not in _MODES or value.get("invalid") != []):
        raise ValueError("provider_policy_invalid_language_filter")
    if any(not isinstance(item, str) or item not in _NORMALIZED_LANGUAGES for item in values):
        raise ValueError("provider_policy_invalid_language_filter")
    if requested != bool(values):
        raise ValueError("provider_policy_invalid_language_filter")
    return {"requested": requested, "mode": mode, "values": sorted(set(values)), "invalid": []}


def build_provider_discovery_policy(
    *, language_filter: dict[str, Any] | None = None,
    historical_topic_max_age_days: int | None = None,
    recent_activity_max_age_days: int = 45,
    discovery_max_age_days: int = MAX_DISCOVERY_DAYS,
) -> dict[str, Any]:
    """Accept server-normalized filters; never infer hard language from market."""
    policy = {
        "schema": SCHEMA, "version": 1,
        "language_filter": _language_filter(language_filter),
        "historical_topic_max_age_days": _days(historical_topic_max_age_days, maximum=36500, optional=True),
        "recent_activity_max_age_days": _days(recent_activity_max_age_days, maximum=36500),
        "discovery_max_age_days": _days(discovery_max_age_days, maximum=MAX_DISCOVERY_DAYS),
    }
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {**policy, "policy_hash": hashlib.sha256(encoded.encode()).hexdigest()}


def validate_provider_discovery_policy(value: Any) -> dict[str, Any] | None:
    """Reject malformed/tampered contracts before provider dispatch, not widen."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or type(value.get("version")) is not int:
        raise ValueError("provider_policy_invalid_contract")
    required = {"schema", "version", "language_filter", "historical_topic_max_age_days",
                "recent_activity_max_age_days", "discovery_max_age_days", "policy_hash"}
    if set(value) != required:
        raise ValueError("provider_policy_invalid_contract")
    rebuilt = build_provider_discovery_policy(
        language_filter=value["language_filter"],
        historical_topic_max_age_days=value["historical_topic_max_age_days"],
        recent_activity_max_age_days=value["recent_activity_max_age_days"],
        discovery_max_age_days=value["discovery_max_age_days"],
    )
    if value != rebuilt:
        raise ValueError("provider_policy_invalid_contract")
    return rebuilt


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # Date-only provider values are calendar observations, not fetched_at.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def youtube_discovery_hints(
    policy: dict[str, Any], *, video_evidence: bool, now: datetime | None = None,
) -> dict[str, str]:
    """One request stays one request; multi-language/exclude cannot fan out."""
    language = policy["language_filter"]
    hints = {}
    values = language["values"]
    # YouTube requires a Chinese script code. A generic operator 'zh' does not
    # authorize choosing Simplified over Traditional, so defer it to the gate.
    if language["mode"] != "exclude" and len(values) == 1 and values[0] != "zh":
        hints["relevanceLanguage"] = values[0]
    if video_evidence:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=policy["discovery_max_age_days"])
        hints["publishedAfter"] = _iso(cutoff)
    return hints


def _window(published: datetime | None, now: datetime, days: int | None) -> dict[str, Any]:
    age = (now - published).total_seconds() / 86400 if published else None
    invalid_future = age is not None and age < -5 / 1440
    if age is None or invalid_future:
        status = "unknown"
    else:
        status = "within_window" if days is None or max(0, age) <= days else "outside_window"
    return {"published_at": _iso(published) if published else None,
            "age_days": round(max(0, age), 6) if age is not None and not invalid_future else None,
            "window_days": days, "status": status,
            "reason": "future_timestamp" if invalid_future else "timestamp_missing" if published is None else ""}


def _annotate_item(
    item: dict[str, Any], policy: dict[str, Any], *, provider: str,
    resource_kind: str, fetched_at: datetime,
) -> dict[str, Any]:
    result = deepcopy(item)
    content = resource_kind != "channel"
    published = _utc(item.get("published")) if content else None
    result["fetched_at"] = _iso(fetched_at)
    common = {"schema": TEMPORAL_SCHEMA, "policy_hash": policy["policy_hash"],
              "source": "provider_content_timestamp" if content else "channel_creation_not_activity"}
    result["historical_topic_evidence"] = {
        **common, **_window(published, fetched_at, policy["historical_topic_max_age_days"]),
        "relevance_status": "not_assessed", "proves_recent_activity": False,
    }
    result["recent_activity_evidence"] = {
        **common, **_window(published, fetched_at, policy["recent_activity_max_age_days"]),
        "latest_content_proven": False, "basis": "observed_content_sample",
    }
    result["discovery_window_evidence"] = {
        **common, **_window(published, fetched_at, policy["discovery_max_age_days"]),
        "postfilter_required": provider != "youtube_data_api" or not content,
    }
    return result


def annotate_discovery_result(
    result: dict[str, Any], policy: dict[str, Any] | None, *, provider: str,
    resource_kind: str = "content", fetched_at: datetime | None = None,
) -> dict[str, Any]:
    """Keep old evidence; never substitute fetch time or silently refill rows.

    The shared qualification gate consumes these independent window verdicts.
    Annotation does not enroll, discard, fetch more, or label topic relevance.
    """
    if policy is None:
        return result
    stamp = fetched_at or datetime.now(timezone.utc)
    output = deepcopy(result)
    output["items"] = [_annotate_item(item, policy, provider=provider, resource_kind=resource_kind, fetched_at=stamp)
                       for item in output.get("items", []) if isinstance(item, dict)]
    metadata = dict(output.get("metadata") or {})
    hints = youtube_discovery_hints(policy, video_evidence=False) if provider == "youtube_data_api" else {}
    if provider == "youtube_data_api" and resource_kind == "video" and metadata.get("published_after"):
        hints["publishedAfter"] = metadata["published_after"]
    metadata.update({"provider_discovery_policy": deepcopy(policy), "fetched_at": _iso(stamp),
        "discovery_hint_parameters": hints,
        "language_filter_enforced_by_provider": False,
        "language_postfilter_required": policy["language_filter"]["requested"],
        "discovery_window_enforced_by_provider": provider == "youtube_data_api" and resource_kind == "video",
        "discovery_window_complete": False,
        "discovery_postfilter_required": provider != "youtube_data_api" or resource_kind != "video",
        "actor_hint_schema_status": "not_applicable" if provider == "youtube_data_api" else "fixed_build_not_verified",
        "provider_input_expanded": False})
    output["metadata"] = metadata
    return output
