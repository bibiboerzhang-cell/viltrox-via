"""Request normalization and stable Ask & Find v2 response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "ask_find_v2"
QUERY_VERSION = "ask_find_v2"
MAX_QUERY_LENGTH = 512
MAX_THREAD_LENGTH = 80
MAX_CLIENT_REQUEST_LENGTH = 120
MAX_FILTER_TEXT_LENGTH = 120

SUPPORTED_INTENTS = frozenset(
    {
        "kol.pool.overview",
        "kol.video_topic.count",
        "project.search",
        "market.viltrox.weekly_voice",
    }
)
VALID_MODES = frozenset({"auto", "deterministic", "search"})
VALID_SCOPE_MODES = frozenset({"auto", "all", "own", "team"})


class QueryValidationError(ValueError):
    """Raised before any DB read when a query request is malformed."""


class QueryScopeDenied(PermissionError):
    """Raised before a scoped DB read when the requested scope is forbidden."""


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _positive_int(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, parsed))


def _parse_date(value: Any, *, field: str) -> date:
    raw = _bounded_text(value, 32)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise QueryValidationError(f"{field} must use YYYY-MM-DD") from exc


def _utc_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


@dataclass(frozen=True)
class QueryWindow:
    start: datetime
    end: datetime
    preset: str

    @property
    def start_iso(self) -> str:
        return self.start.isoformat(timespec="seconds").replace("+00:00", "Z")

    @property
    def end_iso(self) -> str:
        return self.end.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class QueryScope:
    mode: str
    requested_staff_id: int | None


@dataclass(frozen=True)
class NormalizedRequest:
    query: str
    locale: str
    thread_id: str
    scope: QueryScope
    window: QueryWindow
    filters: dict[str, Any]
    mode: str
    client_request_id: str
    request_id: str


def _normalize_locale(value: Any) -> str:
    raw = _bounded_text(value, 20).lower().replace("_", "-")
    if not raw or raw in {"zh", "zh-cn", "zh-hans"}:
        return "zh-CN"
    if raw in {"en", "en-us", "en-gb"}:
        return "en-US"
    raise QueryValidationError("locale must be zh-CN or en-US")


def _normalize_scope(value: Any) -> QueryScope:
    requested_staff_id: int | None = None
    if value in (None, ""):
        return QueryScope(mode="auto", requested_staff_id=None)
    if isinstance(value, str):
        mode = _bounded_text(value, 20).lower()
    elif isinstance(value, dict):
        mode = _bounded_text(value.get("mode") or "auto", 20).lower()
        raw_staff_id = value.get("staff_id")
        if raw_staff_id not in (None, ""):
            try:
                requested_staff_id = int(raw_staff_id)
            except (TypeError, ValueError) as exc:
                raise QueryValidationError("scope.staff_id must be a positive integer") from exc
            if requested_staff_id <= 0:
                raise QueryValidationError("scope.staff_id must be a positive integer")
    else:
        raise QueryValidationError("scope must be a string or object")
    if mode not in VALID_SCOPE_MODES:
        raise QueryValidationError("scope.mode must be auto, all, own or team")
    return QueryScope(mode=mode, requested_staff_id=requested_staff_id)


def _normalize_window(value: Any, *, now: datetime) -> QueryWindow:
    now_utc = now.astimezone(timezone.utc)
    if value in (None, "", "7d"):
        return QueryWindow(start=now_utc - timedelta(days=7), end=now_utc, preset="7d")
    if isinstance(value, str):
        token = _bounded_text(value, 20).lower()
        if token == "30d":
            return QueryWindow(start=now_utc - timedelta(days=30), end=now_utc, preset="30d")
        raise QueryValidationError("time_range must be 7d, 30d or an object")
    if not isinstance(value, dict):
        raise QueryValidationError("time_range must be 7d, 30d or an object")
    preset = _bounded_text(value.get("preset"), 20).lower()
    if preset in {"7d", "30d"} and not value.get("start") and not value.get("end"):
        days = 7 if preset == "7d" else 30
        return QueryWindow(start=now_utc - timedelta(days=days), end=now_utc, preset=preset)
    if not value.get("start") or not value.get("end"):
        raise QueryValidationError("custom time_range requires start and end")
    start_day = _parse_date(value.get("start"), field="time_range.start")
    end_day = _parse_date(value.get("end"), field="time_range.end")
    # Date ranges are inclusive in the request and exclusive at the SQL end.
    start = _utc_day_start(start_day)
    end = _utc_day_start(end_day + timedelta(days=1))
    if end <= start:
        raise QueryValidationError("time_range.end must not be before start")
    if end - start > timedelta(days=366):
        raise QueryValidationError("time_range may not exceed 366 days")
    return QueryWindow(start=start, end=end, preset="custom")


def _normalize_filters(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise QueryValidationError("filters must be an object")
    out: dict[str, Any] = {}
    for key in ("intent", "topic", "keyword", "platform", "country", "stage"):
        if key in value and value.get(key) not in (None, ""):
            out[key] = _bounded_text(value.get(key), MAX_FILTER_TEXT_LENGTH)
    if "intent" in out and out["intent"] not in SUPPORTED_INTENTS:
        raise QueryValidationError("filters.intent is not supported")
    out["limit"] = _positive_int(value.get("limit"), default=20, maximum=50)
    return out


def normalize_request(payload: Any, *, now: datetime | None = None) -> NormalizedRequest:
    if not isinstance(payload, dict):
        raise QueryValidationError("request body must be an object")
    query = _bounded_text(payload.get("query"), MAX_QUERY_LENGTH + 1)
    if not query:
        raise QueryValidationError("query is required")
    if len(query) > MAX_QUERY_LENGTH:
        raise QueryValidationError(f"query may not exceed {MAX_QUERY_LENGTH} characters")
    mode = _bounded_text(payload.get("mode") or "auto", 20).lower()
    if mode not in VALID_MODES:
        raise QueryValidationError("mode must be auto, deterministic or search")
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return NormalizedRequest(
        query=query,
        locale=_normalize_locale(payload.get("locale")),
        thread_id=_bounded_text(payload.get("thread_id") or "default", MAX_THREAD_LENGTH) or "default",
        scope=_normalize_scope(payload.get("scope")),
        window=_normalize_window(payload.get("time_range"), now=now_utc),
        filters=_normalize_filters(payload.get("filters")),
        mode=mode,
        client_request_id=_bounded_text(payload.get("client_request_id"), MAX_CLIENT_REQUEST_LENGTH),
        request_id="iq_" + uuid4().hex,
    )


def empty_response(request: NormalizedRequest, *, intent: str, scope: dict[str, Any]) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request.request_id,
        "status": "ready",
        "intent": intent,
        "answer": "",
        "facts": [],
        "evidence": [],
        "coverage": {
            "status": "unknown",
            "matched_entities": 0,
            "evidence_count": 0,
            "notes": [],
        },
        "freshness": {
            "status": "unknown",
            "generated_at": generated_at,
            "timezone": "UTC",
        },
        "missing_fields": [],
        "actions": [],
        "trace": {
            "request_id": request.request_id,
            "client_request_id": request.client_request_id,
            "thread_id": request.thread_id,
            "scope": scope,
            "mode": request.mode,
            "deterministic": True,
            "query_version": QUERY_VERSION,
            "took_ms": 0,
        },
    }
