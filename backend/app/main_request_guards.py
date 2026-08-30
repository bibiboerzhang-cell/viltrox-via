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

# These POST endpoints carry structured query bodies but perform no business
# mutation.  Keep authentication/CSRF/admission intact while mapping their tab
# permission to read, matching the route dependency and product contract.
READ_ONLY_POST_PATHS = frozenset({
    "/api/admin/vkpi/intelligent/query",
})

_ADMIN_PERMISSION_EXEMPT_PATHS = frozenset({
    "/api/admin/staff/accept-invite",
    "/api/admin/staff/invite/status",
})
_FIXED_PERMISSION_RULES: tuple[tuple[tuple[str, ...], str, str | None, bool], ...] = (
    (("/api/admin/system/keys",), "system.api_keys", "write", True),
    (("/api/admin/system/restart",), "system.restart", "write", True),
    (("/api/admin/system/providers",), "system.api_keys", "read", True),
    (("/api/admin/system/models",), "system.models", None, True),
    (("/api/admin/staff/api-tokens",), "system.api_keys", None, True),
)
_LEVEL_PERMISSION_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("/api/admin/runtime", "/api/admin/integrations"), "runtime", False),
    (("/api/admin/trust",), "command", False),
    (("/api/admin/kol",), "kol_ops", False),
    (("/api/admin/deepsight",), "deepsight", False),
    (("/api/admin/activities", "/api/public/event"), "activities", False),
    (("/api/admin/dashboard",), "vkpi", False),
    (("/api/admin/vkpi", "/api/marketing"), "vkpi", False),
    (("/api/admin/insights/",), "insights", False),
    (("/api/admin/intel/student",), "student", False),
    (("/api/admin/intel/via",), "via", False),
    (("/api/admin/intel/system",), "runtime", False),
    (("/api/intelligence/market", "/api/intelligence/brand"), "analytics", False),
    (("/api/admin/intel", "/api/intelligence"), "intelligence", False),
    (("/api/admin/analytics", "/api/admin/benchmarks", "/api/admin/learning"), "analytics", False),
    ((
        "/api/admin/orders",
        "/api/admin/payouts",
        "/api/admin/attribution",
        "/api/admin/webhook-events",
        "/api/admin/affiliate",
    ), "operations", False),
    ((
        "/api/admin/rewards",
        "/api/admin/product_catalog",
        "/api/admin/creator-public/shop-heroes",
        "/api/admin/upload/reward-image",
    ), "products", False),
    (("/api/admin/creator", "/api/admin/creators"), "creators", False),
)
_USER_COMMAND_SUFFIXES = ("/block", "/unblock", "/flag", "/clear-flag", "/adjust-score")
_OPERATIONS_PREFIXES = (
    "/api/admin/users",
    "/api/admin/social-accounts",
    "/api/admin/verifications",
    "/api/admin/submissions",
    "/api/admin/approve",
    "/api/admin/reject",
    "/api/admin/reanalyze",
    "/api/admin/redemptions",
    "/api/verify/queue",
    "/api/verify/admin",
)
_OPERATIONS_SUFFIXES = ("/scan", "/approve", "/reject")
_TAIL_PERMISSION_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("/api/admin/student", "/api/student/admin"), "student", False),
    (("/api/vios",), "analytics", False),
    (("/api/admin",), "overview", False),
)


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


def _fixed_permission_for_path(path: str, level: str) -> tuple[str, str, bool] | None:
    for prefixes, permission, fixed_level, system_namespace in _FIXED_PERMISSION_RULES:
        if path.startswith(prefixes):
            return (permission, fixed_level or level, system_namespace)
    return None


def _level_permission_for_path(
    path: str,
    level: str,
    rules: tuple[tuple[tuple[str, ...], str, bool], ...],
) -> tuple[str, str, bool] | None:
    for prefixes, permission, system_namespace in rules:
        if path.startswith(prefixes):
            return (permission, level, system_namespace)
    return None


def _is_user_command_path(path: str) -> bool:
    return path.startswith("/api/admin/users/") and path.endswith(_USER_COMMAND_SUFFIXES)


def admin_permission_for_request(path: str, method: str) -> tuple[str, str, bool] | None:
    """Map a protected route to tab/system permission, level and namespace."""
    normalized_method = method.upper()
    if normalized_method == "OPTIONS" or path in _ADMIN_PERMISSION_EXEMPT_PATHS:
        return None
    mutating = (
        normalized_method in {"POST", "PUT", "PATCH", "DELETE"}
        and path not in READ_ONLY_POST_PATHS
    )
    level = "write" if mutating else "read"
    if path.startswith(PRIVATE_INTERNAL_UPLOAD_PREFIXES):
        return ("vkpi", "read", False)
    fixed = _fixed_permission_for_path(path, level)
    if fixed is not None:
        return fixed
    if path.startswith("/api/admin/staff"):
        if mutating:
            return ("system.members", "write", True)
        return ("system", "read", False)
    mapped = _level_permission_for_path(path, level, _LEVEL_PERMISSION_RULES)
    if mapped is not None:
        return mapped
    if _is_user_command_path(path):
        return ("command", level, False)
    if path.startswith(_OPERATIONS_PREFIXES) or path.endswith(_OPERATIONS_SUFFIXES):
        return ("operations", level, False)
    return _level_permission_for_path(path, level, _TAIL_PERMISSION_RULES)


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
