"""KOL lookup orchestration use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import account as account_domain
from app.domains.kol import claims as claims_domain
from app.services.vkpi import scope


async def lookup_with_context(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    result = claims_domain.lookup(body, staff=staff)
    kol = result.get("kol") or {}
    kol_id = int(kol.get("id") or 0) if isinstance(kol, dict) else 0
    if not kol_id:
        return result

    try:
        claims_domain.assert_kol_access(kol_id, staff, allow_unclaimed=True)
    except scope.ScopeDenied as exc:
        result["dossier"] = {}
        result["can_claim"] = False
        result["access_status"] = "claimed_by_other"
        result["access_message"] = str(exc) or "kol claimed by another staff"
        return result

    scan_result = None
    analysis_result = None
    if body.get("scan_account") or body.get("scan_if_missing"):
        max_posts = max(1, min(int(body.get("max_posts") or 24), 80))
        scan_result = await account_domain.scan_account(kol_id, max_posts=max_posts)
        if int(scan_result.get("content_count") or 0) > 0:
            analysis_result = await account_domain.analyze_account(kol_id, product_sku=str(body.get("product_sku") or ""))

    result["dossier"] = account_domain.get_dossier(kol_id)
    if scan_result is not None:
        result["scan_result"] = scan_result
    if analysis_result is not None:
        result["analysis_result"] = analysis_result
    return result
