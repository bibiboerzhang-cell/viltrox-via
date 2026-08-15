"""Audited projection boundary for single-item KOL contact responses."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.core.permissions import check_kol_pool_employee_contact_permission
from app.domains.kol.pool_common import CONTACT_VISIBILITY_MASKED
from app.services.security.rate_limiter import check_rate_limit, get_client_ip


PRIVATE_CONTACT_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Authorization, Cookie",
}
CONTACT_READ_BUCKET = "contact_reveal"
CONTACT_READ_MAX_REQUESTS = 30
CONTACT_READ_WINDOW_SEC = 300


def enforce_contact_read_rate_limit(request: Request, staff: dict) -> None:
    """Limit audited contact reads across both detail endpoints and reveal."""
    actor_id = staff.get("user_id") or staff.get("staff_id") or staff.get("id")
    actor_key = f"user:{actor_id}" if actor_id else f"ip:{get_client_ip(request)}"
    allowed, remaining = check_rate_limit(
        CONTACT_READ_BUCKET,
        actor_key,
        CONTACT_READ_MAX_REQUESTS,
        CONTACT_READ_WINDOW_SEC,
    )
    rate_headers = {
        "X-RateLimit-Limit": str(CONTACT_READ_MAX_REQUESTS),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Bucket": CONTACT_READ_BUCKET,
    }
    request.state.rate_limit_headers = rate_headers
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many KOL contact reads.",
            headers={
                **PRIVATE_CONTACT_HEADERS,
                **rate_headers,
                "Retry-After": str(CONTACT_READ_WINDOW_SEC),
            },
        )


def single_contact_projection(
    request: Request,
    staff: dict,
    *,
    kol_pool_id: int,
    page_path: str,
    surface: str,
) -> tuple[str, str]:
    """Compatibility helper for value-free legacy GET projections.

    Plaintext is available only from the typed pool POST reveal boundary after
    verification and suppression checks.  This helper deliberately performs
    no rate-limit consumption or sensitive-access audit because it never
    returns a contact value.
    """
    if not check_kol_pool_employee_contact_permission(staff):
        raise HTTPException(
            status_code=403,
            detail={"code": "kol_contact_access_not_authorized"},
            headers=PRIVATE_CONTACT_HEADERS,
        )
    del request, kol_pool_id, page_path, surface
    return CONTACT_VISIBILITY_MASKED, "summary_only"


__all__ = [
    "CONTACT_READ_BUCKET",
    "CONTACT_READ_MAX_REQUESTS",
    "CONTACT_READ_WINDOW_SEC",
    "PRIVATE_CONTACT_HEADERS",
    "enforce_contact_read_rate_limit",
    "single_contact_projection",
]
