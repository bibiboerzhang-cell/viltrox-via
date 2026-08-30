"""Compatibility exports for the authentication-free platform counter."""
from app.platform.rate_limit_store import (
    check_rate_limit,
    cleanup_old_buckets,
    get_rate_limit_stats,
)

__all__ = ["check_rate_limit", "cleanup_old_buckets", "get_rate_limit_stats"]
