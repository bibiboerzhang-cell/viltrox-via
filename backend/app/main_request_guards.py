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


# ── board.* 板块可见性映射(2026-07-18 权限双洞修)─────────────────────────
# 前端权限抽屉写 board.<navKey> 进 permissions_json,此前后端零消费(纯前端遮挡,
# 直连 API 可越权)。此表把 /api/admin/vkpi/ 下的第一路径段映射到 navKey 集合;
# 共享前缀映射到多板块(OR 语义:任一板块可见即放行)。未命中 → 不闸。
# 语义:board.* 是「可见性闸」非读写闸——显式 'none' 才拦,写权仍由 tab level 管。
_BOARD_ROUTE_PREFIX = "/api/admin/vkpi/"
_BOARD_NAV_KEYS = frozenset({
    "dashboard", "my-kol", "kol-pool", "kolProfile", "projects", "events",
    "shopify", "dealers", "intelligent", "marketVoice", "sku360",
    "creativeLibrary", "replyQueue", "launchpad", "autonomy", "strategyBoard",
    "gtmCommand",
})
_BOARD_ROUTE_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    # 顺序敏感:更长/更具体的前缀在前。
    ("kol-smart-search", frozenset({"kol-pool"})),
    ("kol-search-sessions", frozenset({"kol-pool"})),
    ("kol-search-history", frozenset({"kol-pool", "kolProfile"})),
    ("kol-pool", frozenset({"kol-pool", "kolProfile"})),
    ("event-radar", frozenset({"events"})),
    ("events", frozenset({"events"})),
    ("inventory", frozenset({"events"})),
    ("dashboard", frozenset({"dashboard"})),
    ("alerts", frozenset({"dashboard"})),
    ("my-kol", frozenset({"my-kol"})),
    ("projects", frozenset({"projects", "launchpad"})),
    ("shopify", frozenset({"shopify"})),
    ("goaffpro", frozenset({"shopify"})),
    ("attribution", frozenset({"shopify", "projects"})),
    ("dealers", frozenset({"dealers"})),
    ("intelligent", frozenset({"intelligent"})),
    ("marketing-advisor", frozenset({"intelligent"})),
    ("market/voice-feed", frozenset({"marketVoice"})),
    ("market/voice-report", frozenset({"marketVoice"})),
    ("market/prd-referrals", frozenset({"marketVoice"})),
    ("sku/", frozenset({"sku360", "launchpad", "gtmCommand", "strategyBoard"})),
    ("industry-data/product-campaign-card", frozenset({"sku360"})),
    ("creative-segments", frozenset({"creativeLibrary"})),
    ("reply-queue", frozenset({"replyQueue", "marketVoice"})),
    ("launch", frozenset({"launchpad"})),
    ("publish", frozenset({"launchpad"})),
    ("product-analysis", frozenset({"launchpad"})),
    ("rates", frozenset({"launchpad"})),
    ("roster", frozenset({"launchpad"})),
    ("autonomy", frozenset({"autonomy"})),
    ("prediction-ledger", frozenset({"autonomy"})),
    ("agents/loop/trace", frozenset({"autonomy"})),
    ("actions/inbox", frozenset({"autonomy", "gtmCommand"})),
    ("learning/weekly-scorecard", frozenset({"autonomy", "strategyBoard"})),
    ("strategy/", frozenset({"strategyBoard"})),
    ("gtm/", frozenset({"gtmCommand"})),
    ("market-brain/", frozenset({"gtmCommand"})),
)


def board_requirement_for_request(path: str) -> frozenset[str] | None:
    """Return the navKey set whose visibility guards this path, or None (no gate)."""
    if not path.startswith(_BOARD_ROUTE_PREFIX):
        return None
    rest = path[len(_BOARD_ROUTE_PREFIX):]
    for prefix, boards in _BOARD_ROUTE_RULES:
        if rest.startswith(prefix):
            return boards
    return None


def board_requirement_for_board_series(board_param: str) -> frozenset[str] | None:
    """board-series 以 ?board=<key> 区分板块;非注册键(旧 V2 占位等)不闸。"""
    key = str(board_param or "").strip()
    if key in _BOARD_NAV_KEYS:
        return frozenset({key})
    return None


def board_gate_allows(staff: Any, path: str, query_board: Any) -> bool:
    """board.* 可见性闸(推断+检查+灰度合一;实现集中在此,main.py 只调一行)。

    灰度:BOARD_RBAC_ENFORCE=0(默认)只记 would_block 不拦,观察一个部署
    周期零误杀后置 1。纯内存 dict 查找,零额外查库。
    """
    import os

    from app.core.logging import get_logger
    from app.core.permissions import check_board_permission

    boards = board_requirement_for_request(path)
    if path == "/api/admin/vkpi/board-series":
        boards = board_requirement_for_board_series(str(query_board or ""))
    if not boards or check_board_permission(staff, boards):
        return True
    if str(os.getenv("BOARD_RBAC_ENFORCE", "0") or "0").strip() in {"1", "true", "yes"}:
        return False
    get_logger(__name__).warning(
        "board_rbac.would_block | path=%s staff_id=%s boards=%s",
        path,
        (staff or {}).get("id"),
        ",".join(sorted(boards)),
    )
    return True


__all__ = [
    "DB_REQUEST_ADMISSION_LIMIT",
    "DB_REQUEST_ADMISSION_TIMEOUT_SEC",
    "PRIVATE_INTERNAL_UPLOAD_PREFIXES",
    "admin_permission_for_request",
    "board_gate_allows",
    "board_requirement_for_board_series",
    "board_requirement_for_request",
    "db_admission_unavailable_response",
    "db_request_admission_limiter",
    "request_path_requires_db_admission",
]
