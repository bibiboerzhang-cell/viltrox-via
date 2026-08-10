"""Fail-closed runtime fence used while a production release is being proven.

The deploy controller creates the root-owned marker before it starts a new
release.  Web ingress, schedulers and both worker runtimes consult the same
marker immediately before any mutable or externally billed action.  Read-only
health/search validation can continue; removing the marker is the single
activation step after every release gate has passed.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
import stat
from typing import Any
from urllib.parse import parse_qs

from app.core.config import IS_PRODUCTION


PRODUCTION_FENCE_PATH = Path("/run/vkpi-release-validation.fence")
FENCE_PAYLOAD = "vkpi-release-validation/v1\n"
_LOCAL_FENCE_ENV = "VKPI_RELEASE_VALIDATION_FENCE_PATH"
_READ_ONLY_POST_PATHS = frozenset(
    {
        "/api/admin/vkpi/intelligent/query",
        "/api/admin/vkpi/event-radar/refresh-preview",
        "/api/marketing/intelligent/query",
    }
)
_CONTENT_FIT_SUFFIX = "/content-fit"
_TRUTHY_QUERY_VALUES = frozenset({"1", "true", "yes", "on"})
_READ_ONLY_GET_EXACT_PATHS = frozenset(
    {
        "/",
        "/account",
        "/activate",
        "/admin",
        "/api/admin/vkpi/analytics/intents",
        "/api/admin/vkpi/autonomy/licenses",
        "/api/admin/vkpi/board-series",
        "/api/admin/vkpi/canned-queries",
        "/api/admin/vkpi/creative-segments/search",
        "/api/admin/vkpi/dashboard",
        "/api/admin/vkpi/data-quality",
        "/api/admin/vkpi/dealers",
        "/api/admin/vkpi/dealers/candidate-staging",
        "/api/admin/vkpi/dealers/coverage",
        "/api/admin/vkpi/dealers/locations",
        "/api/admin/vkpi/dealers/us-source-registry",
        "/api/admin/vkpi/event-radar",
        "/api/admin/vkpi/event-radar/candidate-staging",
        "/api/admin/vkpi/event-radar/us-source-registry",
        "/api/admin/vkpi/events",
        "/api/admin/vkpi/global-search",
        "/api/admin/vkpi/intelligent/stats",
        "/api/admin/vkpi/intelligent/suggestions",
        "/api/admin/vkpi/inventory",
        "/api/admin/vkpi/kol-pool",
        "/api/admin/vkpi/kol-pool/discovery/providers",
        "/api/admin/vkpi/kol-pool/favorites",
        "/api/admin/vkpi/kol-pool/summary",
        "/api/admin/vkpi/kol-pool/workspace",
        "/api/admin/vkpi/kol-search-history",
        "/api/admin/vkpi/launch/assemble",
        "/api/admin/vkpi/market/voice-report-ext",
        "/api/admin/vkpi/market-brain/gtm-plan/preview",
        "/api/admin/vkpi/market-brain/summary",
        "/api/admin/vkpi/marketing-advisor/memory",
        "/api/admin/vkpi/marketing-advisor/readiness",
        "/api/admin/vkpi/my-kol/board-ext",
        "/api/admin/vkpi/projects",
        "/api/admin/vkpi/projects/deliverable-stages-summary",
        "/api/admin/vkpi/publish/pending",
        "/api/admin/vkpi/reply-queue",
        "/api/admin/vkpi/reports",
        "/api/admin/vkpi/settings/scheduler-tasks",
        "/api/admin/vkpi/shopify/gmv",
        "/api/admin/vkpi/skills",
        "/api/admin/vkpi/sku/list",
        "/api/admin/vkpi/strategy/category-tracks",
        "/api/admin/vkpi/strategy/industry-benchmark",
        "/api/admin/vkpi/strategy/performance",
        "/api/admin/vkpi/triage/jobs",
        "/api/admin/vkpi/weekly-reports/list",
        "/api/auth/me",
        "/api/intelligence/market/trends",
        "/favicon.ico",
        "/favicon.svg",
        "/health",
        "/login",
        "/react",
        "/redeem",
        "/reset",
        "/robots.txt",
        "/student-signup",
    }
)
_READ_ONLY_GET_PREFIXES = (
    "/account/",
    "/admin/",
    "/assets/",
    "/react/",
    "/redeem/",
    "/uploads/reward_images/",
    "/uploads/staff_avatars/",
    "/uploads/student_cards/",
    "/uploads/vkpi_evidence/",
    "/vid/",
)
_CAPTURED_READ_ONLY_GET_PATHS = frozenset(
    {
        "/api/admin/runtime/metrics",
        "/api/admin/vkpi/actions/inbox",
        "/api/admin/vkpi/agents/loop/trace",
        "/api/admin/vkpi/alerts",
        "/api/admin/vkpi/attribution",
        "/api/admin/vkpi/attribution/unmatched",
        "/api/admin/vkpi/brand-signals",
        "/api/admin/vkpi/channels/assignments",
        "/api/admin/vkpi/channels/official-matrix",
        "/api/admin/vkpi/costs",
        "/api/admin/vkpi/dashboard/ai-today-hot",
        "/api/admin/vkpi/dashboard/competitor-radar",
        "/api/admin/vkpi/dashboard/copilot-brief",
        "/api/admin/vkpi/dashboard/fit-movers",
        "/api/admin/vkpi/dashboard/kol-distribution-pack",
        "/api/admin/vkpi/dashboard/product-performance",
        "/api/admin/vkpi/dashboard/recent-content",
        "/api/admin/vkpi/dashboard/revenue-trend",
        "/api/admin/vkpi/dashboard/tasks",
        "/api/admin/vkpi/dealers/rankings",
        "/api/admin/vkpi/event-radar/summary",
        "/api/admin/vkpi/events/upcoming",
        "/api/admin/vkpi/goaffpro/creds",
        "/api/admin/vkpi/goaffpro/summary",
        "/api/admin/vkpi/gtm/northstar",
        "/api/admin/vkpi/industry-data/hashtag-trends/v0",
        "/api/admin/vkpi/industry-data/market-intelligence/cards/v0",
        "/api/admin/vkpi/kol-pool/needs-analysis",
        "/api/admin/vkpi/kols",
        "/api/admin/vkpi/kpi-ledger",
        "/api/admin/vkpi/learning/weekly-scorecard",
        "/api/admin/vkpi/links",
        "/api/admin/vkpi/market/prd-referrals",
        "/api/admin/vkpi/market/voice-feed",
        "/api/admin/vkpi/market/voice-report",
        "/api/admin/vkpi/marketing-advisor/threads",
        "/api/admin/vkpi/media/image-proxy",
        "/api/admin/vkpi/morning-brief",
        "/api/admin/vkpi/my-kol/aggregate",
        "/api/admin/vkpi/my-kol/contribution-rollup",
        "/api/admin/vkpi/my-kol/daily-digest",
        "/api/admin/vkpi/my-kol/risk-index",
        "/api/admin/vkpi/official-matrix",
        "/api/admin/vkpi/ops/cost-ledger",
        "/api/admin/vkpi/prediction-ledger/summary",
        "/api/admin/vkpi/product-analysis/launches",
        "/api/admin/vkpi/product-costs",
        "/api/admin/vkpi/progress/center",
        "/api/admin/vkpi/projects/content-posts",
        "/api/admin/vkpi/projects/due-list",
        "/api/admin/vkpi/projects/observation-windows",
        "/api/admin/vkpi/reply-queue/kpi-series",
        "/api/admin/vkpi/settings/preferences",
        "/api/admin/vkpi/shopify/status",
        "/api/admin/vkpi/skills/runs",
        "/api/admin/vkpi/staff-directory",
        "/api/admin/vkpi/staff-groups",
        "/api/admin/vkpi/staff-kpi",
        "/api/admin/vkpi/task-queue/compact",
        "/api/admin/vkpi/tasks",
        "/api/admin/vkpi/tasks/realtime-status",
    }
)
_READ_ONLY_GET_PATTERNS = (
    re.compile(r"^/api/admin/vkpi/channels/[1-9][0-9]*/posts$"),
    re.compile(r"^/api/admin/vkpi/kol-pool/[1-9][0-9]*$"),
    re.compile(r"^/api/admin/vkpi/kol-pool/[1-9][0-9]*/content-fit$"),
    re.compile(r"^/api/admin/vkpi/kol-pool/[1-9][0-9]*/detail-bundle$"),
    re.compile(r"^/api/admin/vkpi/kol-pool/[1-9][0-9]*/(?:signature|videos)$"),
    re.compile(r"^/api/admin/vkpi/kol-search-sessions/[1-9][0-9]*$"),
    re.compile(r"^/api/(?:admin/vkpi/media|vkpi-media)/image-cache/[0-9a-f]{64}$"),
    # Persisted Advisor history is a bounded owner-scoped SELECT.  Keep the
    # pattern exact so the neighbouring provider-backed ``/messages/stream``
    # route remains fenced during release validation.
    re.compile(
        r"^/api/admin/vkpi/marketing-advisor/threads/[A-Za-z0-9_-]{1,80}/messages$"
    ),
    re.compile(r"^/api/admin/vkpi/sku/[^/]+/profile$"),
    re.compile(r"^/api/admin/vkpi/weekly-reports/[0-9]+$"),
)


def release_validation_fence_path() -> Path | None:
    """Return the immutable production path or an explicit local test path."""

    if IS_PRODUCTION:
        return PRODUCTION_FENCE_PATH
    configured = str(os.environ.get(_LOCAL_FENCE_ENV) or "").strip()
    return Path(configured).expanduser() if configured else None


def release_validation_status() -> dict[str, Any]:
    """Return bounded, non-secret evidence; malformed markers stay active."""

    path = release_validation_fence_path()
    if path is None:
        return {
            "active": False,
            "valid": True,
            "source": "not_configured",
        }
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"active": False, "valid": True, "source": "absent"}
    except OSError:
        return {"active": True, "valid": False, "source": "unreadable"}

    expected_uid = 0 if IS_PRODUCTION else os.geteuid()
    valid_shape = bool(
        stat.S_ISREG(metadata.st_mode)
        and not path.is_symlink()
        and metadata.st_uid == expected_uid
        and stat.S_IMODE(metadata.st_mode) == 0o444
        and metadata.st_nlink == 1
        and metadata.st_size == len(FENCE_PAYLOAD.encode("utf-8"))
    )
    valid_payload = False
    if valid_shape:
        try:
            valid_payload = path.read_text(encoding="utf-8") == FENCE_PAYLOAD
        except OSError:
            valid_payload = False
    return {
        # Presence means fenced even when tampered or unreadable.
        "active": True,
        "valid": bool(valid_shape and valid_payload),
        "source": "verified_marker" if valid_shape and valid_payload else "invalid_marker",
    }


def release_validation_active() -> bool:
    return bool(release_validation_status()["active"])


def _query_flag(query: object, name: str) -> bool:
    value: object = None
    if isinstance(query, Mapping):
        value = query.get(name)
    elif hasattr(query, "get"):
        try:
            value = query.get(name)
        except Exception:
            value = None
    elif isinstance(query, bytes):
        value = parse_qs(query.decode("utf-8", errors="replace")).get(name, [None])[-1]
    elif isinstance(query, str):
        value = parse_qs(query).get(name, [None])[-1]
    if isinstance(value, (list, tuple)):
        value = value[-1] if value else None
    return str(value or "").strip().lower() in _TRUTHY_QUERY_VALUES


def _canonical_request_path(path: str) -> str:
    if path == "/api/marketing" or path.startswith("/api/marketing/"):
        return "/api/admin/vkpi" + path.removeprefix("/api/marketing")
    return path


def _reviewed_read_only_get(path: str, query: object) -> bool:
    path = _canonical_request_path(path)
    if path.endswith(_CONTENT_FIT_SUFFIX) and (
        _query_flag(query, "analyze") or _query_flag(query, "force")
    ):
        return False
    return (
        path in _READ_ONLY_GET_EXACT_PATHS
        or path in _CAPTURED_READ_ONLY_GET_PATHS
        or any(path.startswith(prefix) for prefix in _READ_ONLY_GET_PREFIXES)
        or any(pattern.fullmatch(path) for pattern in _READ_ONLY_GET_PATTERNS)
    )


def release_validation_request_allowed(
    method: object,
    path: object,
    query: object = None,
) -> bool:
    """Allow reviewed reads while rejecting GET-shaped mutation/provider work."""

    normalized_method = str(method or "").strip().upper()
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    if normalized_method in {"GET", "HEAD"}:
        return _reviewed_read_only_get(normalized_path, query)
    return normalized_method == "OPTIONS" or (
        normalized_method == "POST" and normalized_path in _READ_ONLY_POST_PATHS
    )


__all__ = [
    "FENCE_PAYLOAD",
    "PRODUCTION_FENCE_PATH",
    "release_validation_active",
    "release_validation_fence_path",
    "release_validation_request_allowed",
    "release_validation_status",
]
