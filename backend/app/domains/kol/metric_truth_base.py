"""metric_truth 的底层原语(CC 战役 2026-08-30 从 metric_truth.py 平移,行为逐字节不变)。

内容:未知词表/状态词表等常量、标量解析(_number)、来源脱敏(_public_source_ref)、
raw payload 游走与字段证据收集、字段级对账(_raw_metric_match)、raw 来源状态归纳
(_raw_source_state)。本模块零 app 依赖(纯 stdlib),被 metric_truth.py 单向引用。
红线不变:绝不从缺失推数值、绝不把缺失当零;不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
import math
import urllib.parse
from typing import Any, Iterable, Mapping

UNKNOWN_TOKENS = frozenset(
    {"", "unknown", "n/a", "na", "none", "null", "nil", "-", "--", "未知", "未提供", "待补全"}
)
SUCCESS_STATUSES = frozenset({"ok", "success", "succeeded", "synced", "ready", "complete", "completed"})
FAILURE_STATUSES = frozenset({"error", "failed", "failure", "not_found", "no_results", "timeout", "blocked"})

_FIELD_ALIASES = {
    "followers": {
        "followers",
        "followerscount",
        "followercount",
        "follower_count",
        "subscribercount",
        "subscriber_count",
        "subscribers",
        "fans",
        "fanscount",
    },
    "avg_views": {"avg_views", "averageviews", "average_views", "meanviews", "mean_views"},
    "avg_likes": {"avg_likes", "averagelikes", "average_likes", "meanlikes", "mean_likes"},
    "avg_comments": {
        "avg_comments",
        "averagecomments",
        "average_comments",
        "meancomments",
        "mean_comments",
    },
    "engagement_rate": {"engagement_rate", "engagementrate", "engagement", "er"},
}
_CONTENT_ALIASES = {
    "avg_views": {"viewcount", "view_count", "views", "playcount", "play_count", "videoplaycount"},
    "avg_likes": {"likecount", "like_count", "likes", "diggcount", "digg_count"},
    "avg_comments": {"commentcount", "comment_count", "comments", "replycount", "reply_count"},
}
_STATUS_KEYS = {
    "provider_status",
    "providerstatus",
    "sync_status",
    "syncstatus",
    "scrape_status",
    "scrapestatus",
    "kpi_status",
    "kpistatus",
    "status",
}
_HIDDEN_FOLLOWER_KEYS = {"hiddensubscribercount", "hidden_subscriber_count", "followershidden"}
_PLAN_STATUSES = {"planned", "plan", "proposed", "proposal", "recommended", "potential", "draft", "pending"}
_CONFIRMED_STATUSES = {"confirmed", "completed", "published", "observed", "active", "contracted", "paid"}
_COLLAB_EVIDENCE_KEYS = {
    "evidence_url",
    "content_url",
    "published_url",
    "observed_at",
    "published_at",
    "evidence_id",
    "contract_id",
    "campaign_id",
    "project_id",
    "source_ref",
}
_OBSERVED_AT_KEYS = {
    "metrics_scraped_at",
    "metricsscrapedat",
    "scraped_at",
    "scrapedat",
    "fetched_at",
    "fetchedat",
    "observed_at",
    "observedat",
    "collected_at",
    "collectedat",
    "updated_at",
    "updatedat",
}
_SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "bearer",
    "api_key",
    "apikey",
    "token",
    "authorization",
)
_RECOGNIZED_SOURCE_TOKENS = (
    "youtube", "instagram", "tiktok", "facebook", "reddit", "apify", "crawler", "api", "profile_crawl"
)
_CONTENT_KIND_TOKENS = ("video", "post", "tweet", "reel", "short", "clip")


def _key(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _known_text(value: Any) -> bool:
    return _text(value).casefold() not in UNKNOWN_TOKENS


def _public_source_type(value: Any) -> str | None:
    if not _known_text(value):
        return None
    safe = "".join(character for character in _text(value) if character.isalnum() or character in "_.:-")
    return safe[:64] or None


def _url_source_ref(parsed: urllib.parse.SplitResult) -> str:
    """URL 形态的来源:保 scheme/host/port,含密钥味的 path 打码。"""
    try:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        host, port = "", ""
    path_lowered = parsed.path.casefold()
    safe_path = "" if any(marker in path_lowered for marker in _SECRET_MARKERS) else parsed.path[:120]
    return f"{parsed.scheme}://{host}{port}{safe_path}"[:180] if host else "redacted_source_ref"


def _basename_source_ref(text: str) -> str:
    """非 URL 形态:只留 basename,含密钥味/含 @ 的整体打码。"""
    token_free = text.split("?", 1)[0].split("#", 1)[0].replace("\\", "/")
    basename = token_free.rsplit("/", 1)[-1]
    lowered = basename.casefold()
    if "@" in basename or any(secret in lowered for secret in _SECRET_MARKERS):
        return "redacted_source_ref"
    return basename[:120] or "source_ref_present"


def _public_source_ref(value: Any) -> str | None:
    """Return a useful provenance hint without leaking paths, query tokens, or credentials."""

    if not _known_text(value):
        return None
    text = _text(value)
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme and parsed.netloc:
        return _url_source_ref(parsed)
    return _basename_source_ref(text)


def _public_timestamp(value: Any) -> str | None:
    return _text(value)[:64] if _known_text(value) else None


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _parse_non_negative_finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and value.strip().casefold() in UNKNOWN_TOKENS:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _number(value: Any, *, percent: bool = False) -> int | float | None:
    parsed = _parse_non_negative_finite(value)
    if parsed is None or (percent and parsed > 100):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _walk(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Iterable[dict[str, Any]]:
    if budget is None:
        budget = [5000]
    if depth > 10 or budget[0] <= 0:
        return
    if isinstance(value, dict):
        budget[0] -= 1
        yield value
        for nested in value.values():
            yield from _walk(nested, depth=depth + 1, budget=budget)
    elif isinstance(value, list):
        for nested in value[:500]:
            yield from _walk(nested, depth=depth + 1, budget=budget)


def _values_for_keys(value: Any, aliases: set[str]) -> list[int | float]:
    values: list[int | float] = []
    normalized_aliases = {_key(alias) for alias in aliases}
    for record in _walk(value):
        if _record_has_failure_marker(record):
            continue
        for raw_key, raw_value in record.items():
            if _key(raw_key) not in normalized_aliases:
                continue
            parsed = _number(raw_value)
            if parsed is not None:
                values.append(parsed)
    return values


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).casefold() in {"1", "true", "yes", "y"}


def _record_has_failure_marker(record: Mapping[str, Any]) -> bool:
    for raw_key, raw_value in record.items():
        normalized = _key(raw_key)
        token = _text(raw_value).casefold().replace("-", "_").replace(" ", "_")
        if normalized in _STATUS_KEYS and token in FAILURE_STATUSES:
            return True
        if normalized in {"error", "error_code", "errorcode", "reason"} and (
            token in FAILURE_STATUSES
            or "no_result" in token
            or "not_found" in token
            or "failed" in token
        ):
            return True
    return False


def _content_kind_verdict(kind: str) -> bool | None:
    """kind/type 字段直判:频道/主页类=False,视频/帖子类=True,判不出=None。"""
    if "channel" in kind or "profile" in kind or "user" == kind:
        return False
    if any(token in kind for token in _CONTENT_KIND_TOKENS):
        return True
    return None


def _content_record_shape(keys: set[str]) -> bool:
    """键形状兜底:有身份键 + (内容键或指标键)才算一条内容记录。"""
    has_identity = bool(keys & {"id", "url", "videourl", "webvideourl", "content_url", "shortcode"})
    has_content = bool(keys & {"title", "text", "caption", "createtime", "publishedat", "timestamp"})
    has_metric = bool(keys & set().union(*_CONTENT_ALIASES.values()))
    return has_identity and (has_content or has_metric)


def _content_record(record: Mapping[str, Any]) -> bool:
    if _record_has_failure_marker(record):
        return False
    kind = _text(record.get("kind") or record.get("type") or record.get("mediaType")).casefold()
    verdict = _content_kind_verdict(kind)
    if verdict is not None:
        return verdict
    return _content_record_shape({_key(key) for key in record})


def _content_metric_values(raw: Any, field: str) -> list[int | float]:
    aliases = _CONTENT_ALIASES.get(field) or set()
    values: list[int | float] = []
    if not aliases:
        return values
    for record in _walk(raw):
        if not _content_record(record):
            continue
        values.extend(_values_for_keys(record, aliases))
    return values


def _raw_metric_evidence(
    raw: Any,
    field: str,
) -> tuple[list[int | float], list[int | float]]:
    """Collect field evidence once for all truth checks on one Pool card."""
    return (
        _values_for_keys(raw, _FIELD_ALIASES.get(field) or set()),
        _content_metric_values(raw, field),
    )


def _matches(value: int | float, expected: int | float, *, tolerance: float) -> bool:
    return abs(float(value) - float(expected)) <= tolerance


def _explicit_match(
    explicit: list[int | float], stored: int | float, tolerance: float, basis: str
) -> tuple[bool, str | None, int]:
    matched = any(_matches(value, stored, tolerance=tolerance) for value in explicit)
    return matched, basis if matched else None, len(explicit)


def _content_mean_match(content: list[int | float], stored: int | float) -> tuple[bool, str | None, int]:
    if not content:
        return False, None, 0
    sample_mean = sum(float(value) for value in content) / len(content)
    mean_tolerance = max(1.0, abs(sample_mean) * 0.03)
    matched = _matches(sample_mean, stored, tolerance=mean_tolerance)
    return matched, "raw_content_sample_mean" if matched else None, len(content)


def _raw_metric_match(
    raw: Any,
    field: str,
    stored: int | float,
    *,
    explicit_values: list[int | float] | None = None,
    content_values: list[int | float] | None = None,
) -> tuple[bool, str | None, int]:
    """Require field-level agreement; raw field presence alone is not a receipt."""

    explicit = (
        explicit_values
        if explicit_values is not None
        else _values_for_keys(raw, _FIELD_ALIASES.get(field) or set())
    )
    if field == "followers":
        return _explicit_match(explicit, stored, 0.5, "raw_profile_value")
    if field == "engagement_rate":
        tolerance = max(0.01, abs(float(stored)) * 0.02)
        return _explicit_match(explicit, stored, tolerance, "raw_explicit_engagement_rate")
    average = _explicit_match(explicit, stored, max(1.0, abs(float(stored)) * 0.02), "raw_explicit_average")
    if average[0]:
        return average
    content = content_values if content_values is not None else _content_metric_values(raw, field)
    return _content_mean_match(content, stored)


def _scan_record_fields(record: Mapping[str, Any], state: dict[str, Any]) -> None:
    """单条 record 扫状态/隐藏粉丝/观测时间,累进 state(顺序与老实现逐字节一致)。"""
    for raw_key, value in record.items():
        normalized = _key(raw_key)
        if not state["observed_at"] and normalized in _OBSERVED_AT_KEYS and _known_text(value):
            state["observed_at"] = _text(value)
        if normalized in _STATUS_KEYS and _known_text(value):
            state["statuses"].append(_text(value).casefold().replace("-", "_").replace(" ", "_"))
        if normalized in _HIDDEN_FOLLOWER_KEYS and _truthy(value):
            state["hidden_followers"] = True


def _scan_source_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {"statuses": [], "hidden_followers": False, "source": "", "observed_at": ""}
    for record in records:
        if not state["source"]:
            state["source"] = _text(
                record.get("source") or record.get("provider_source") or record.get("metrics_source")
            )
        _scan_record_fields(record, state)
        if _record_has_failure_marker(record):
            state["statuses"].append("failure")
    return state


def _source_successful(statuses: list[str], source: str) -> bool:
    has_success = any(status in SUCCESS_STATUSES for status in statuses)
    has_failure = any(status in FAILURE_STATUSES for status in statuses)
    recognized_source = bool(
        source and any(token in source.casefold() for token in _RECOGNIZED_SOURCE_TOKENS)
    )
    return bool(has_success or (recognized_source and not has_failure))


def _raw_source_state(raw: Any, *, records: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not isinstance(raw, (dict, list)):
        return {"present": False, "successful": False, "source": None}
    state = _scan_source_records(records if records is not None else _walk(raw))
    return {
        "present": True,
        "successful": _source_successful(state["statuses"], state["source"]),
        "source": _public_source_ref(state["source"]),
        "observed_at": _public_timestamp(state["observed_at"]),
        "hidden_followers": state["hidden_followers"],
        "statuses": sorted(set(state["statuses"])),
    }
