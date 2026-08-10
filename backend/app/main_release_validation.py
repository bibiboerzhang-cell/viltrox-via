"""Small release-validation adapters for the FastAPI entry point."""
from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.release_validation import (
    release_validation_active,
    release_validation_request_allowed,
    release_validation_status,
)


_PRIVATE_SCHEMA_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})
_ROBOTS_DENY_POLICY = "noindex, nofollow, noarchive, nosnippet, noimageindex"
_PRIVATE_SCHEMA_DENIAL_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": _ROBOTS_DENY_POLICY,
}


def safe_status() -> dict[str, Any]:
    try:
        return release_validation_status()
    except Exception:
        # An unreadable/tampered fence is never projected as inactive.
        return {"active": True, "valid": False, "source": "status_error"}


def normalize_marketing_api_path(scope: dict[str, Any]) -> None:
    path = str(scope.get("path") or "")
    if path == "/api/marketing" or path.startswith("/api/marketing/"):
        scope["path"] = "/api/admin/vkpi" + path.removeprefix("/api/marketing")


class ReleaseValidationFenceMiddleware(BaseHTTPMiddleware):
    """Keep only explicitly reviewed read paths open before activation."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not release_validation_active():
            return await call_next(request)

        request_path = str(request.scope.get("path") or "")
        if request.method.upper() in {"GET", "HEAD"} and request_path in _PRIVATE_SCHEMA_PATHS:
            if request.method.upper() == "HEAD":
                return Response(
                    status_code=404,
                    media_type="application/json",
                    headers=_PRIVATE_SCHEMA_DENIAL_HEADERS,
                )
            return JSONResponse(
                {"detail": "Not Found"},
                status_code=404,
                headers=_PRIVATE_SCHEMA_DENIAL_HEADERS,
            )

        if not release_validation_request_allowed(
            request.method,
            request_path,
            request.query_params,
        ):
            return JSONResponse(
                {
                    "detail": "发布验证中，写入和外部任务暂时冻结",
                    "code": "release_validation_fenced",
                },
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "5"},
            )
        return await call_next(request)


__all__ = [
    "ReleaseValidationFenceMiddleware",
    "normalize_marketing_api_path",
    "release_validation_active",
    "safe_status",
]
