"""Short-lived, one-time authentication tickets for browser EventSource streams."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote, unquote

from app.core.config import REDIS_CACHE_PREFIX, REDIS_URL
from app.core.logging import get_logger

try:
    from redis import Redis
except Exception:  # pragma: no cover - optional in local-only runtimes
    Redis = None


logger = get_logger(__name__)

_TTL_SECONDS = max(10, min(120, int(os.getenv("SSE_TICKET_TTL_SEC", "30") or 30)))
_TICKET_BYTES = 32
_COOKIE_PREFIX = "sse_ticket_"
_ALLOWED_ENDPOINTS = (
    re.compile(r"^/api/admin/vkpi/activity/stream$"),
    re.compile(r"^/api/admin/vkpi/progress/center/stream$"),
    re.compile(r"^/api/audit/stream/[^/?#]+$"),
)

_redis_client = None
_memory: dict[str, tuple[float, dict[str, Any]]] = {}
_memory_lock = threading.RLock()


class SseTicketStoreUnavailable(RuntimeError):
    """Raised when the configured shared ticket store cannot be reached."""


def ticket_ttl_seconds() -> int:
    return _TTL_SECONDS


def normalize_sse_endpoint(value: str) -> str:
    endpoint = str(value or "").strip()
    if not endpoint.startswith("/") or "?" in endpoint or "#" in endpoint:
        raise ValueError("Invalid SSE endpoint")
    # Browser URLs and ASGI scope paths differ for encoded task IDs. Canonicalize
    # both forms to the same ASCII path before cookie scoping and store binding.
    endpoint = quote(unquote(endpoint), safe="/-._~")
    if not any(pattern.fullmatch(endpoint) for pattern in _ALLOWED_ENDPOINTS):
        raise ValueError("Unsupported SSE endpoint")
    return endpoint


def ticket_cookie_name(endpoint: str) -> str:
    normalized = normalize_sse_endpoint(endpoint)
    suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"{_COOKIE_PREFIX}{suffix}"


def _ticket_key(ticket: str) -> str:
    digest = hashlib.sha256(str(ticket or "").encode("utf-8")).hexdigest()
    return f"{REDIS_CACHE_PREFIX}:auth:sse-ticket:{digest}"


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None
    if Redis is None:
        raise SseTicketStoreUnavailable("SSE ticket store unavailable")
    try:
        client = Redis.from_url(REDIS_URL, decode_responses=False)
        client.ping()
        _redis_client = client
        return client
    except Exception as exc:
        # A configured Redis implies a multi-process deployment. Falling back to
        # process memory would make tickets fail nondeterministically across workers.
        raise SseTicketStoreUnavailable("SSE ticket store unavailable") from exc


def _memory_set(key: str, payload: dict[str, Any]) -> None:
    now = time.time()
    with _memory_lock:
        for stale_key, (expires_at, _) in list(_memory.items()):
            if expires_at <= now:
                _memory.pop(stale_key, None)
        _memory[key] = (now + _TTL_SECONDS, payload)


def _memory_pop(key: str) -> dict[str, Any] | None:
    with _memory_lock:
        entry = _memory.pop(key, None)
    if not entry or entry[0] <= time.time():
        return None
    return entry[1]


def issue_sse_ticket(*, user_id: int, endpoint: str) -> str:
    normalized = normalize_sse_endpoint(endpoint)
    ticket = secrets.token_urlsafe(_TICKET_BYTES)
    key = _ticket_key(ticket)
    payload = {"user_id": int(user_id), "endpoint": normalized}
    client = _get_redis()
    if client is None:
        _memory_set(key, payload)
        return ticket
    try:
        stored = client.set(key, json.dumps(payload), ex=_TTL_SECONDS, nx=True)
    except Exception as exc:
        raise SseTicketStoreUnavailable("SSE ticket store unavailable") from exc
    if not stored:  # practically impossible, but never return an untracked ticket
        raise SseTicketStoreUnavailable("SSE ticket could not be issued")
    return ticket


def consume_sse_ticket(*, ticket: str, endpoint: str) -> int | None:
    normalized = normalize_sse_endpoint(endpoint)
    raw_ticket = str(ticket or "").strip()
    if not raw_ticket or len(raw_ticket) > 256:
        return None
    key = _ticket_key(raw_ticket)
    client = _get_redis()
    if client is None:
        payload = _memory_pop(key)
    else:
        try:
            # Lua keeps GET + DEL atomic on both current Redis and Redis < 6.2.
            raw = client.eval(
                "local v=redis.call('GET',KEYS[1]); "
                "if v then redis.call('DEL',KEYS[1]); end; return v",
                1,
                key,
            )
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception as exc:
            raise SseTicketStoreUnavailable("SSE ticket store unavailable") from exc
    if not isinstance(payload, dict) or payload.get("endpoint") != normalized:
        return None
    try:
        user_id = int(payload.get("user_id") or 0)
    except (TypeError, ValueError):
        return None
    return user_id or None


def _reset_ticket_store_for_tests() -> None:
    """Clear the local fallback without exposing ticket contents."""
    global _redis_client
    with _memory_lock:
        _memory.clear()
    _redis_client = None
