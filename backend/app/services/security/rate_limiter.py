"""
services/security/rate_limiter.py — Redis-backed tiered rate limiting
"""
from __future__ import annotations

import asyncio
import inspect
from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request

from app.core.contracts import ACTOR_TIERS, normalize_actor_tier
from app.platform import rate_limit_store as _rate_limit_store


# Compatibility injection point retained for tests and legacy callers that
# replace the Redis resolver on this facade.
_get_redis = _rate_limit_store._get_redis
cleanup_old_buckets = _rate_limit_store.cleanup_old_buckets
get_rate_limit_stats = _rate_limit_store.get_rate_limit_stats


def check_rate_limit(
    bucket: str,
    client_id: str,
    max_requests: int,
    window_sec: int,
) -> tuple[bool, int]:
    return _rate_limit_store.check_rate_limit(
        bucket,
        client_id,
        max_requests,
        window_sec,
        redis_getter=_get_redis,
    )

TIER_LIMITS = {
    "login_register": {
        ACTOR_TIERS["anonymous"]: (10, 60),
        "*": (10, 60),
    },
    "student_claim": {
        ACTOR_TIERS["anonymous"]: (6, 300),
        ACTOR_TIERS["student"]: (20, 300),
        "*": (10, 300),
    },
    "upload_video": {
        ACTOR_TIERS["anonymous"]: (0, 300),
        ACTOR_TIERS["authenticated"]: (6, 600),
        ACTOR_TIERS["creator"]: (10, 600),
        ACTOR_TIERS["verified_creator"]: (16, 600),
        ACTOR_TIERS["vip_platinum"]: (40, 600),
        ACTOR_TIERS["admin"]: (10000, 600),
        ACTOR_TIERS["founder_internal"]: (120, 600),
        "*": (6, 600),
    },
    "audit_submit": {
        ACTOR_TIERS["anonymous"]: (0, 300),
        ACTOR_TIERS["authenticated"]: (6, 600),
        ACTOR_TIERS["creator"]: (10, 600),
        ACTOR_TIERS["verified_creator"]: (16, 600),
        ACTOR_TIERS["vip_platinum"]: (40, 600),
        ACTOR_TIERS["admin"]: (10000, 600),
        ACTOR_TIERS["founder_internal"]: (120, 600),
        "*": (6, 600),
    },
    "via_chat": {
        ACTOR_TIERS["anonymous"]: (5, 60),
        ACTOR_TIERS["authenticated"]: (30, 60),
        ACTOR_TIERS["creator"]: (60, 60),
        ACTOR_TIERS["verified_creator"]: (90, 60),
        ACTOR_TIERS["student"]: (45, 60),
        ACTOR_TIERS["vip_platinum"]: (180, 60),
        ACTOR_TIERS["founder_internal"]: (600, 60),
        "*": (30, 60),
    },
    "verify_binding": {
        ACTOR_TIERS["anonymous"]: (0, 300),
        ACTOR_TIERS["authenticated"]: (10, 300),
        ACTOR_TIERS["creator"]: (20, 300),
        ACTOR_TIERS["verified_creator"]: (30, 300),
        ACTOR_TIERS["admin"]: (120, 300),
        "*": (10, 300),
    },
    "admin_mutation": {
        ACTOR_TIERS["admin"]: (120, 60),
        ACTOR_TIERS["founder_internal"]: (240, 60),
        "*": (30, 60),
    },
}


def get_client_ip(request: Request) -> str:
    headers = request.headers
    cf_ip = headers.get("cf-connecting-ip", "")
    if cf_ip:
        return cf_ip.strip()
    xff = headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    real_ip = headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


def _resolve_user(request: Request):
    try:
        from app.core.security import get_current_user

        return get_current_user(request)
    except Exception:
        return None


def _resolve_tier(user: dict | None) -> str:
    if not user:
        return ACTOR_TIERS["anonymous"]
    role = str(user.get("role") or "").strip().lower()
    tier_status = str(user.get("tier_status") or "").strip().lower()
    if role in {"admin"}:
        return ACTOR_TIERS["admin"]
    if role in {"founder", "internal"} or tier_status in {"founder", "internal"}:
        return ACTOR_TIERS["founder_internal"]
    if tier_status in {"platinum", "vip"}:
        return ACTOR_TIERS["vip_platinum"]
    if str(user.get("student_status") or "").strip().lower() in {"active", "verified"}:
        return ACTOR_TIERS["student"]
    if tier_status in {"approved", "verified", "gold", "silver"}:
        return ACTOR_TIERS["verified_creator"]
    if role in {"creator"}:
        return ACTOR_TIERS["creator"]
    return normalize_actor_tier("authenticated")


def _resolve_limits(bucket: str, tier: str, max_requests: int, window_sec: int) -> tuple[int, int]:
    table = TIER_LIMITS.get(bucket)
    if not table:
        return max_requests, window_sec
    return table.get(tier) or table.get("*") or (max_requests, window_sec)


def rate_limit(bucket: str, max_requests: int = 60, window_sec: int = 60):
    def decorator(fn: Callable) -> Callable:
        def _resolve_request(args, kwargs):
            request = kwargs.get("request")
            if request is None:
                for item in args:
                    if isinstance(item, Request):
                        request = item
                        break
            return request

        def _check_request(request: Request):
            user = _resolve_user(request)
            tier = _resolve_tier(user)
            resolved_max, resolved_window = _resolve_limits(bucket, tier, max_requests, window_sec)
            if resolved_max <= 0:
                raise HTTPException(status_code=429, detail="This action is not available for your current tier.")
            actor_key = f"user:{user['id']}" if user and user.get("id") else f"ip:{get_client_ip(request)}"
            allowed, remaining = check_rate_limit(bucket, actor_key, resolved_max, resolved_window)
            request.state.rate_limit_headers = {
                "X-RateLimit-Limit": str(resolved_max),
                "X-RateLimit-Remaining": str(max(0, remaining)),
                "X-RateLimit-Bucket": bucket,
                "X-RateLimit-Tier": tier,
            }
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests for {bucket}. Limit: {resolved_max}/{resolved_window}s.",
                    headers={
                        "Retry-After": str(resolved_window),
                        "X-RateLimit-Limit": str(resolved_max),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Bucket": bucket,
                        "X-RateLimit-Tier": tier,
                    },
                )

        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                request = _resolve_request(args, kwargs)
                if request is None:
                    return await fn(*args, **kwargs)
                _check_request(request)
                return await fn(*args, **kwargs)

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            request = _resolve_request(args, kwargs)
            if request is None:
                return fn(*args, **kwargs)
            _check_request(request)
            return fn(*args, **kwargs)

        return sync_wrapper

    return decorator
