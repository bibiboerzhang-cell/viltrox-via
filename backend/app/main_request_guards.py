"""Process-local request admission and admin route permission mapping.

This module keeps the FastAPI application bootstrap focused on composition.
The admission limiter is intentionally process- and event-loop-local because
the synchronous PostgreSQL pool has the same process boundary.
"""
from __future__ import annotations

import asyncio
import weakref
from typing import Any

from fastapi.responses import JSONResponse

from app.core.config import POSTGRES_POOL_MAX_SIZE, POSTGRES_POOL_TIMEOUT_SEC


PRIVATE_INTERNAL_UPLOAD_PREFIXES = (
    "/uploads/staff_avatars/",
    "/uploads/vkpi_evidence/",
)
DB_REQUEST_ADMISSION_LIMIT = max(1, int(POSTGRES_POOL_MAX_SIZE) - 1)
DB_REQUEST_ADMISSION_TIMEOUT_SEC = max(1.0, float(POSTGRES_POOL_TIMEOUT_SEC))
_DB_REQUEST_ADMISSION_BY_LOOP: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def db_request_admission_limiter() -> asyncio.BoundedSemaphore:
    """Return a limiter bound to the current process and asyncio loop."""
    loop = asyncio.get_running_loop()
    limiter = _DB_REQUEST_ADMISSION_BY_LOOP.get(loop)
    if limiter is None:
        limiter = asyncio.BoundedSemaphore(DB_REQUEST_ADMISSION_LIMIT)
        _DB_REQUEST_ADMISSION_BY_LOOP[loop] = limiter
    return limiter


def request_path_requires_db_admission(request: Any, *, postgres_runtime: bool) -> bool:
    """Bound DB-backed HTTP work without throttling static/frontend traffic."""
    if not postgres_runtime:
        return False
    scope = getattr(request, "scope", None)
    path = str(scope.get("path") or "") if isinstance(scope, dict) else ""
    if not path:
        try:
            path = str(request.url.path or "")
        except Exception:
            return False
    return (
        path == "/api"
        or path.startswith("/api/")
        or path.startswith(PRIVATE_INTERNAL_UPLOAD_PREFIXES)
    )


def db_admission_unavailable_response(timeout_seconds: float) -> JSONResponse:
    """Return a stable retryable response when bounded DB admission expires."""
    retry_after = max(1, min(5, int(timeout_seconds)))
    return JSONResponse(
        {
            "detail": "Database request capacity is busy; retry shortly",
            "code": "db_request_admission_timeout",
        },
        status_code=503,
        headers={"Retry-After": str(retry_after)},
    )


def admin_permission_for_request(path: str, method: str) -> tuple[str, str, bool] | None:
    """Map a protected route to tab/system permission, level and namespace."""
    if method.upper() == "OPTIONS":
        return None
    if path in {"/api/admin/staff/accept-invite", "/api/admin/staff/invite/status"}:
        return None
    mutating = method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    level = "write" if mutating else "read"
    if path.startswith(PRIVATE_INTERNAL_UPLOAD_PREFIXES):
        return ("vkpi", "read", False)
    if path.startswith("/api/admin/system/keys"):
        return ("system.api_keys", "write", True)
    if path.startswith("/api/admin/system/restart"):
        return ("system.restart", "write", True)
    if path.startswith("/api/admin/system/providers"):
        return ("system.api_keys", "read", True)
    if path.startswith("/api/admin/system/models"):
        return ("system.models", level, True)
    if path.startswith("/api/admin/staff/api-tokens"):
        return ("system.api_keys", level, True)
    if path.startswith("/api/admin/staff"):
        if mutating:
            return ("system.members", "write", True)
        return ("system", "read", False)
    if path.startswith("/api/admin/runtime") or path.startswith("/api/admin/integrations"):
        return ("runtime", level, False)
    if path.startswith("/api/admin/trust"):
        return ("command", level, False)
    if path.startswith("/api/admin/kol"):
        return ("kol_ops", level, False)
    if path.startswith("/api/admin/deepsight"):
        return ("deepsight", level, False)
    if path.startswith("/api/admin/activities") or path.startswith("/api/public/event"):
        return ("activities", level, False)
    if path.startswith("/api/admin/dashboard"):
        return ("vkpi", level, False)
    if path.startswith("/api/admin/vkpi") or path.startswith("/api/marketing"):
        return ("vkpi", level, False)
    if path.startswith("/api/admin/insights/"):
        return ("insights", level, False)
    if path.startswith("/api/admin/intel/student"):
        return ("student", level, False)
    if path.startswith("/api/admin/intel/via"):
        return ("via", level, False)
    if path.startswith("/api/admin/intel/system"):
        return ("runtime", level, False)
    if path.startswith("/api/intelligence/market") or path.startswith("/api/intelligence/brand"):
        return ("analytics", level, False)
    if path.startswith("/api/admin/intel") or path.startswith("/api/intelligence"):
        return ("intelligence", level, False)
    if (
        path.startswith("/api/admin/analytics")
        or path.startswith("/api/admin/benchmarks")
        or path.startswith("/api/admin/learning")
    ):
        return ("analytics", level, False)
    if (
        path.startswith("/api/admin/orders")
        or path.startswith("/api/admin/payouts")
        or path.startswith("/api/admin/attribution")
        or path.startswith("/api/admin/webhook-events")
        or path.startswith("/api/admin/affiliate")
    ):
        return ("operations", level, False)
    if (
        path.startswith("/api/admin/rewards")
        or path.startswith("/api/admin/product_catalog")
        or path.startswith("/api/admin/creator-public/shop-heroes")
        or path.startswith("/api/admin/upload/reward-image")
    ):
        return ("products", level, False)
    if path.startswith("/api/admin/creator") or path.startswith("/api/admin/creators"):
        return ("creators", level, False)
    if path.startswith("/api/admin/users/") and (
        path.endswith("/block")
        or path.endswith("/unblock")
        or path.endswith("/flag")
        or path.endswith("/clear-flag")
        or path.endswith("/adjust-score")
    ):
        return ("command", level, False)
    if (
        path.startswith("/api/admin/users")
        or path.startswith("/api/admin/social-accounts")
        or path.startswith("/api/admin/verifications")
        or path.startswith("/api/admin/submissions")
        or path.startswith("/api/admin/approve")
        or path.startswith("/api/admin/reject")
        or path.startswith("/api/admin/reanalyze")
        or path.startswith("/api/admin/redemptions")
        or path.startswith("/api/verify/queue")
        or path.startswith("/api/verify/admin")
        or path.endswith("/scan")
        or path.endswith("/approve")
        or path.endswith("/reject")
    ):
        return ("operations", level, False)
    if path.startswith("/api/admin/student") or path.startswith("/api/student/admin"):
        return ("student", level, False)
    if path.startswith("/api/vios"):
        return ("analytics", level, False)
    if path.startswith("/api/admin"):
        return ("overview", level, False)
    return None


__all__ = [
    "DB_REQUEST_ADMISSION_LIMIT",
    "DB_REQUEST_ADMISSION_TIMEOUT_SEC",
    "PRIVATE_INTERNAL_UPLOAD_PREFIXES",
    "admin_permission_for_request",
    "db_admission_unavailable_response",
    "db_request_admission_limiter",
    "request_path_requires_db_admission",
]
