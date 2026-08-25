"""
services/security/admin_access.py — admin 路径访问防护
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from app.core.config import (
    ADMIN_ALLOWED_IPS,
    ADMIN_ENFORCE_IP_ALLOWLIST,
    ADMIN_REQUIRE_SAME_ORIGIN,
    ADMIN_TRUSTED_ORIGINS,
)
from app.services.security.rate_limiter import get_client_ip

_ADMIN_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_ADMIN_PATH_PREFIXES = ("/api/admin", "/api/vios")
_ADMIN_PRIVATE_VARY = ("Authorization", "Cookie")


def is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith(_ADMIN_PATH_PREFIXES)


def _normalize_origin(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return raw.rstrip("/")


def _extract_request_origin(request: Request) -> str:
    origin = _normalize_origin(request.headers.get("origin", ""))
    if origin:
        return origin
    referer = request.headers.get("referer", "")
    return _normalize_origin(referer)


def get_admin_security_response(request: Request) -> Response | None:
    path = request.url.path
    if not is_admin_path(path):
        return None

    client_ip = get_client_ip(request)
    if ADMIN_ENFORCE_IP_ALLOWLIST and ADMIN_ALLOWED_IPS and client_ip not in ADMIN_ALLOWED_IPS:
        return JSONResponse(
            status_code=403,
            content={"detail": "Admin access blocked from this IP"},
        )

    if not path.startswith("/api/"):
        return None

    if request.method not in _ADMIN_MUTATING_METHODS:
        return None

    if not ADMIN_REQUIRE_SAME_ORIGIN:
        return None

    auth_header = request.headers.get("authorization", "").strip().lower()
    if auth_header.startswith("bearer "):
        return None

    request_origin = _extract_request_origin(request)
    trusted = {_normalize_origin(item) for item in ADMIN_TRUSTED_ORIGINS if item}
    if request_origin and request_origin in trusted:
        return None

    return JSONResponse(
        status_code=403,
        content={"detail": "Blocked by admin same-origin policy"},
    )


def _merge_private_vary(response: Response) -> None:
    existing = [
        token.strip()
        for token in str(response.headers.get("Vary") or "").split(",")
        if token.strip()
    ]
    seen = {token.lower() for token in existing}
    for token in _ADMIN_PRIVATE_VARY:
        if token.lower() not in seen:
            existing.append(token)
            seen.add(token.lower())
    response.headers["Vary"] = ", ".join(existing)


def apply_admin_security_headers(path: str, response: Response) -> Response:
    if not is_admin_path(path):
        return response
    if not str(response.headers.get("Cache-Control") or "").strip():
        response.headers["Cache-Control"] = "no-store"
    cache_directives = {
        directive.strip().lower()
        for directive in str(response.headers.get("Cache-Control") or "").split(",")
        if directive.strip()
    }
    # JSON/admin responses without an explicit cache policy default to no-store
    # and must vary across both supported authentication transports.  Explicit
    # media policies (private/public max-age, immutable, Range video) are
    # intentional performance contracts and must not be replaced or fragmented
    # by a global Authorization/Cookie Vary.
    if "no-store" in cache_directives:
        _merge_private_vary(response)
    response.headers.setdefault("Pragma", "no-cache")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response
