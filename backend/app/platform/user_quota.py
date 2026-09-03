"""Per-staff daily quota and per-user burst limit for expensive POST routes.

Beta gap C1/C2 (2026-09-02): every budget gate was global or per feature
scope, so one tester could exhaust the company's monthly allowance, and the
150 vkpi routers used no ``@rate_limit`` at all (IP-only limits live on the
auth endpoints).  This module closes both holes with a single middleware:

* **Daily quota** — counted per ``staff_id`` per UTC day for four expensive
  action families (online discovery search, video deep analysis, deep crawl,
  outreach generation).  Over quota → 429 with an honest, human-readable
  message and reset time.  Only successful requests (status < 400) consume
  quota, so validation errors never burn a tester's allowance.
* **Per-user burst limit** — the same expensive paths share one short window
  keyed by the authenticated staff (never by IP), reusing
  :mod:`app.platform.rate_limit_store` so Redis-backed deployments count
  across worker processes (memory fallback is process-local; honest note).

Configuration is env-only with defaults, no migration:

* ``VKPI_USER_QUOTA_ENABLED`` (default ``1``)
* ``VKPI_USER_DAILY_QUOTA_SMART_SEARCH_ONLINE`` (default 30)
* ``VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS`` (default 20)
* ``VKPI_USER_DAILY_QUOTA_DEEP_CRAWL`` (default 40)
* ``VKPI_USER_DAILY_QUOTA_OUTREACH_SEND`` (default 60)
* ``VKPI_USER_RATE_LIMIT_EXPENSIVE`` (default ``12/60`` = 12 requests / 60 s)

A quota value ``<= 0`` disables that single action (unlimited).  Nothing here
touches ``viltrox_fit_score`` or ``rule_v0``; the module is import-safe
without a database and never logs request bodies.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi.responses import JSONResponse

from app.core.config import REDIS_RATE_LIMIT_PREFIX
from app.core.logging import get_logger
from app.platform import rate_limit_store

logger = get_logger(__name__)

ENV_ENABLED = "VKPI_USER_QUOTA_ENABLED"
ENV_BURST = "VKPI_USER_RATE_LIMIT_EXPENSIVE"
DEFAULT_BURST = (12, 60)
BURST_BUCKET = "user_expensive"
_ROUTE_PREFIXES = ("/api/admin/vkpi", "/api/marketing")
_DISCOVERY_BODY_FLAGS = ("include_new_discovery", "include_discovery", "execute_new_discovery")


@dataclass(frozen=True)
class QuotaAction:
    """One expensive action family: label is user-facing, env carries the cap."""

    key: str
    label: str
    env: str
    default: int


ACTIONS: dict[str, QuotaAction] = {
    "smart_search_online": QuotaAction(
        "smart_search_online", "智能搜索·在线发现", "VKPI_USER_DAILY_QUOTA_SMART_SEARCH_ONLINE", 30
    ),
    "video_deep_analysis": QuotaAction(
        "video_deep_analysis", "视频深度分析", "VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS", 20
    ),
    "deep_crawl": QuotaAction("deep_crawl", "账号深抓", "VKPI_USER_DAILY_QUOTA_DEEP_CRAWL", 40),
    "outreach_send": QuotaAction("outreach_send", "外联生成/发送", "VKPI_USER_DAILY_QUOTA_OUTREACH_SEND", 60),
}

# (regex on the path *after* the router prefix, action key, body flags that must be truthy).
# Empty flags = every POST to the path counts.
_ROUTE_RULES: tuple[tuple[re.Pattern[str], str, tuple[str, ...]], ...] = (
    (re.compile(r"^/kol-smart-search$"), "smart_search_online", _DISCOVERY_BODY_FLAGS),
    (re.compile(r"^/kol-smart-search/profile-advance-job$"), "smart_search_online", ()),
    (re.compile(r"^/kol-pool/discovery/federated-search/refresh$"), "smart_search_online", ()),
    (re.compile(r"^/kol-pool/\d+/enqueue-(video-analysis|video-keyframe-qa|all-videos)$"), "video_deep_analysis", ()),
    (re.compile(r"^/kol-pool/enqueue-video-analysis-batch$"), "video_deep_analysis", ()),
    (re.compile(r"^/kol-memory/\d+/video-fullscan-enqueue$"), "video_deep_analysis", ()),
    (re.compile(r"^/kol-url-deep-crawl$"), "deep_crawl", ()),
    (re.compile(r"^/kol-search-sessions/\d+/items/\d+/profile-crawl$"), "deep_crawl", ()),
    (re.compile(r"^/kol-search-sessions/\d+/advance(-job)?$"), "deep_crawl", ()),
    (re.compile(r"^/kols/\d+/analyze-account$"), "deep_crawl", ()),
    (re.compile(r"^/kol-pool/\d+/audience-stats/refresh$"), "deep_crawl", ()),
    (re.compile(r"^/kol-search-sessions/\d+/generate-outreach$"), "outreach_send", ()),
    (re.compile(r"^/kol-pool/outreach-(draft/enqueue|optimize)$"), "outreach_send", ()),
    (re.compile(r"^/kol-pool/\d+/outreach-pack$"), "outreach_send", ()),
)

_memory_counters: dict[str, int] = {}
_memory_day: str = ""


# ── configuration (env only; defaults keep the gate live without ops work) ──


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else int(default)
    except ValueError:
        return int(default)


def quota_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "1").strip().lower() not in {"0", "false", "no", "off"}


def daily_limit(action_key: str) -> int:
    """Return the per-staff daily cap; ``<= 0`` means unlimited for that action."""
    action = ACTIONS.get(action_key)
    return _env_int(action.env, action.default) if action else 0


def burst_limit() -> tuple[int, int]:
    """Parse ``VKPI_USER_RATE_LIMIT_EXPENSIVE`` as ``max/window_sec``."""
    raw = os.environ.get(ENV_BURST, "").strip()
    match = re.fullmatch(r"(\d+)\s*/\s*(\d+)", raw)
    if not match:
        return DEFAULT_BURST
    max_requests, window = int(match.group(1)), int(match.group(2))
    return (max_requests, window) if window > 0 else DEFAULT_BURST


# ── day-scoped counter (Redis when configured, otherwise process memory) ──


def _utc_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).strftime("%Y%m%d")


def reset_at(now: float | None = None) -> datetime:
    current = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
    return (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _seconds_until_reset(now: float | None = None) -> int:
    current = now if now is not None else time.time()
    return max(1, int(reset_at(current).timestamp() - current))


def _counter_key(action_key: str, staff_id: int, day: str) -> str:
    return f"{REDIS_RATE_LIMIT_PREFIX}:quota:{action_key}:{day}:staff:{int(staff_id)}"


def _memory_bucket(day: str) -> dict[str, int]:
    global _memory_day
    if _memory_day != day:
        _memory_counters.clear()
        _memory_day = day
    return _memory_counters


def used_today(action_key: str, staff_id: int, *, now: float | None = None) -> int:
    day = _utc_day(now)
    key = _counter_key(action_key, staff_id, day)
    client = rate_limit_store._get_redis()
    if client is not None:
        try:
            return int(client.get(key) or 0)
        except Exception as exc:  # Redis hiccup: fail open for reads, log once per call
            logger.warning("user_quota.redis_read_failed action=%s err=%s", action_key, type(exc).__name__)
            return 0
    return int(_memory_bucket(day).get(key, 0))


def consume(action_key: str, staff_id: int, *, now: float | None = None) -> int:
    """Increment the day counter (call only after the route succeeded); return new value."""
    day = _utc_day(now)
    key = _counter_key(action_key, staff_id, day)
    client = rate_limit_store._get_redis()
    if client is not None:
        try:
            current = int(client.incr(key))
            if current == 1:
                client.expire(key, _seconds_until_reset(now) + 60)
            return current
        except Exception as exc:
            logger.warning("user_quota.redis_write_failed action=%s err=%s", action_key, type(exc).__name__)
            return 0
    bucket = _memory_bucket(day)
    bucket[key] = int(bucket.get(key, 0)) + 1
    return bucket[key]


def reset_state_for_tests() -> None:
    global _memory_day
    _memory_counters.clear()
    _memory_day = ""


def snapshot(staff_id: int, *, now: float | None = None) -> dict[str, Any]:
    """Honest per-staff view: limit/used/remaining for every action (UI or ops use)."""
    actions = {}
    for key, action in ACTIONS.items():
        limit = daily_limit(key)
        used = used_today(key, staff_id, now=now)
        actions[key] = {
            "label": action.label,
            "limit": limit,
            "used": used,
            "remaining": None if limit <= 0 else max(0, limit - used),
            "unlimited": limit <= 0,
        }
    return {"enabled": quota_enabled(), "resets_at": reset_at(now).isoformat(), "actions": actions}


# ── request classification ──


def _strip_prefix(path: str) -> str | None:
    for prefix in _ROUTE_PREFIXES:
        if path.startswith(prefix + "/"):
            return path[len(prefix):]
    return None


def match_route(method: str, path: str) -> tuple[str, tuple[str, ...]] | None:
    """Return ``(action_key, body_flags)`` for an expensive POST path, else None."""
    if method.upper() != "POST":
        return None
    rest = _strip_prefix(path)
    if rest is None:
        return None
    for pattern, action_key, flags in _ROUTE_RULES:
        if pattern.match(rest):
            return action_key, flags
    return None


def _staff_id_from_request(request: Any) -> int:
    staff = getattr(getattr(request, "state", None), "vkpi_authorized_staff", None)
    if not isinstance(staff, dict):
        return 0
    try:
        return int(staff.get("id") or staff.get("staff_id") or staff.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


async def _body_has_flag(request: Any, flags: tuple[str, ...]) -> bool:
    if not flags:
        return True
    try:
        body = await request.json()
    except Exception:
        return False
    return isinstance(body, dict) and any(bool(body.get(flag)) for flag in flags)


# ── responses (门面文案:只说人话,不带内部术语) ──


def _burst_response(max_requests: int, window: int, staff_id: int) -> JSONResponse:
    headers = {
        "Retry-After": str(window),
        "X-RateLimit-Limit": str(max_requests),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Bucket": BURST_BUCKET,
        "X-RateLimit-Scope": "per_user",
    }
    logger.warning("user_quota.burst_blocked staff_id=%s limit=%s/%ss", staff_id, max_requests, window)
    return JSONResponse(
        {
            "detail": f"操作太频繁:每 {window} 秒最多发起 {max_requests} 次高成本操作,请稍后再试。",
            "code": "user_rate_limited",
            "scope": "per_user",
            "limit": max_requests,
            "window_sec": window,
        },
        status_code=429,
        headers=headers,
    )


def _quota_response(action: QuotaAction, used: int, limit: int, staff_id: int, now: float | None) -> JSONResponse:
    resets = reset_at(now)
    logger.warning("user_quota.daily_blocked staff_id=%s action=%s used=%s limit=%s", staff_id, action.key, used, limit)
    return JSONResponse(
        {
            "detail": (
                f"今日「{action.label}」额度已用完({used}/{limit} 次),"
                f"将于 {resets.strftime('%Y-%m-%d %H:%M')} UTC 重置;如需提额请联系管理员。"
            ),
            "code": "user_daily_quota_exceeded",
            "scope": "per_user",
            "action": action.key,
            "used": used,
            "limit": limit,
            "resets_at": resets.isoformat(),
        },
        status_code=429,
        headers={
            "Retry-After": str(_seconds_until_reset(now)),
            "X-Quota-Action": action.key,
            "X-Quota-Limit": str(limit),
            "X-Quota-Used": str(used),
        },
    )


@dataclass(frozen=True)
class Decision:
    """What the middleware decided before calling the route."""

    staff_id: int
    action_key: str | None  # None → burst-limited only, no daily quota
    blocked: JSONResponse | None = None


async def evaluate_request(request: Any, *, now: float | None = None) -> Decision | None:
    """Classify one request; returns None when the quota gate does not apply."""
    if not quota_enabled():
        return None
    matched = match_route(str(request.method), str(request.url.path))
    if matched is None:
        return None
    staff_id = _staff_id_from_request(request)
    if staff_id <= 0:
        return None  # unauthenticated requests are rejected upstream by RBAC
    max_requests, window = burst_limit()
    allowed, remaining = rate_limit_store.check_rate_limit(BURST_BUCKET, f"user:{staff_id}", max_requests, window)
    if not allowed:
        return Decision(staff_id, None, _burst_response(max_requests, window, staff_id))
    request.state.rate_limit_headers = {
        "X-RateLimit-Limit": str(max_requests),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Bucket": BURST_BUCKET,
        "X-RateLimit-Scope": "per_user",
    }
    action_key, flags = matched
    limit = daily_limit(action_key)
    if limit <= 0 or not await _body_has_flag(request, flags):
        return Decision(staff_id, None)
    used = used_today(action_key, staff_id, now=now)
    if used >= limit:
        return Decision(staff_id, action_key, _quota_response(ACTIONS[action_key], used, limit, staff_id, now))
    return Decision(staff_id, action_key)


async def quota_middleware(request: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
    decision = await evaluate_request(request)
    if decision is not None and decision.blocked is not None:
        return decision.blocked
    response = await call_next(request)
    if decision is None:
        return response
    for key, value in (getattr(request.state, "rate_limit_headers", None) or {}).items():
        response.headers[str(key)] = str(value)  # self-contained: main.py's metrics middleware also copies these
    if decision.action_key is None or int(response.status_code) >= 400:
        return response
    used = consume(decision.action_key, decision.staff_id)
    limit = daily_limit(decision.action_key)
    response.headers["X-Quota-Action"] = decision.action_key
    response.headers["X-Quota-Limit"] = str(limit)
    response.headers["X-Quota-Remaining"] = str(max(0, limit - used))
    return response


def install(app: Any) -> None:
    """Register the middleware.  Call it *before* the RBAC middleware is declared so
    it runs after RBAC (Starlette wraps later registrations outside earlier ones)
    and can read ``request.state.vkpi_authorized_staff``."""
    app.middleware("http")(quota_middleware)


__all__ = [
    "ACTIONS",
    "BURST_BUCKET",
    "DEFAULT_BURST",
    "Decision",
    "ENV_BURST",
    "ENV_ENABLED",
    "QuotaAction",
    "burst_limit",
    "consume",
    "daily_limit",
    "evaluate_request",
    "install",
    "match_route",
    "quota_enabled",
    "quota_middleware",
    "reset_at",
    "reset_state_for_tests",
    "snapshot",
    "used_today",
]
