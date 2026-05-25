"""System monitoring routes mounted under /api/admin/intel."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.security import require_admin
from app.services.cache import cache_clear, get_cache_stats
from app.services.intelligence import get_bh_summary
from app.services.scheduler import get_scheduler_status
from app.services.security import get_rate_limit_stats
from app.services.security.rate_limiter import rate_limit

router = APIRouter()


@router.get("/system/cache")
def system_cache_stats(request: Request):
    """In-memory cache stats."""
    require_admin(request)
    return get_cache_stats()


@router.post("/system/cache/clear")
@rate_limit("admin_mutation", max_requests=20, window_sec=300)
def system_cache_clear(request: Request, prefix: str = Query(default="")):
    """Clear cache. prefix='' clears all; a prefix scopes the clear."""
    require_admin(request)
    cleared = cache_clear(prefix=prefix)
    return {"cleared": cleared, "prefix": prefix}


@router.get("/system/rate-limit")
def system_rate_limit_stats(request: Request):
    """Rate limiter stats."""
    require_admin(request)
    return get_rate_limit_stats()


@router.get("/system/scheduler")
def system_scheduler_status(request: Request):
    """Scheduler status and next run time."""
    require_admin(request)
    return get_scheduler_status()


@router.get("/system/health")
def system_health(request: Request):
    """Full system health snapshot."""
    require_admin(request)
    return {
        "cache": get_cache_stats(),
        "rate_limit": get_rate_limit_stats(),
        "scheduler": get_scheduler_status(),
        "bh_summary": get_bh_summary(),
    }
