"""KOL lookup orchestration use cases."""
from __future__ import annotations

from typing import Any

from app.domains.kol import account as account_domain
from app.domains.kol import claims as claims_domain
from app.domains.kol.lookup_tracking import (
    STAGE_SEARCH,
    STAGE_SUMMARIZING,
    STAGE_THINKING,
    LookupTracker,
)
from app.domains.access import scope


async def lookup_with_context(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    """Resolve a KOL and optionally scan/analyze their account.

    The whole run is recorded as a trackable, recoverable task: a
    ``kol_search_session`` (durable result/terminal state) plus a
    ``job_execution_ledger`` row (live stage for the 任务进度 board). Tracking is
    best-effort and never alters lookup behavior or scoring. ``session_id`` is
    returned so the client can re-fetch results after navigation.
    """

    tracker = LookupTracker(body=body or {}, staff=staff)
    tracker.open()

    def _with_session(payload: dict[str, Any]) -> dict[str, Any]:
        if tracker.session_id:
            payload["search_session_id"] = tracker.session_id
            payload["task_id"] = tracker.task_id
        return payload

    # --- search stage: resolve the KOL identity --------------------------
    try:
        result = claims_domain.lookup(body, staff=staff)
    except Exception as exc:
        tracker.finish(status="failed", result={}, reason=str(exc) or "lookup failed")
        raise

    tracker.set_query_text(result)
    kol = result.get("kol") or {}
    kol_id = int(kol.get("id") or 0) if isinstance(kol, dict) else 0
    if not kol_id:
        # 未命中真实 KOL:无 dossier/scan,记为 ready(查询本身成功,只是未匹配)。
        tracker.finish(
            status="ready",
            result=result,
            summary={"matched": False, "stage": STAGE_SEARCH},
        )
        return _with_session(result)

    try:
        claims_domain.assert_kol_access(kol_id, staff, allow_unclaimed=True)
    except scope.ScopeDenied as exc:
        reason = str(exc) or "kol claimed by another staff"
        result["dossier"] = {}
        result["can_claim"] = False
        result["access_status"] = "claimed_by_other"
        result["access_message"] = reason
        tracker.finish(
            status="failed",
            result=result,
            reason=reason,
            summary={"matched": True, "access_status": "claimed_by_other"},
        )
        return _with_session(result)

    scan_result = None
    analysis_result = None
    scan_requested = bool(body.get("scan_account") or body.get("scan_if_missing"))
    terminal_status = "ready"
    terminal_reason = ""
    try:
        if scan_requested:
            # thinking stage: 抓取并理解账号内容
            tracker.stage(STAGE_THINKING)
            max_posts = max(1, min(int(body.get("max_posts") or 24), 80))
            scan_result = await account_domain.scan_account(kol_id, max_posts=max_posts)
            content_count = int((scan_result or {}).get("content_count") or 0)
            if content_count > 0:
                analysis_result = await account_domain.analyze_account(
                    kol_id, product_sku=str(body.get("product_sku") or "")
                )
            else:
                # 抓到 0 条内容:查询成功但产物不完整 → partial,留可读原因。
                terminal_status = "partial"
                terminal_reason = str((scan_result or {}).get("status") or "no_content_scanned")

        # summarizing stage: 沉淀 dossier
        tracker.stage(STAGE_SUMMARIZING)
        result["dossier"] = account_domain.get_dossier(kol_id)
    except Exception as exc:
        result["dossier"] = result.get("dossier") or {}
        if scan_result is not None:
            result["scan_result"] = scan_result
        if analysis_result is not None:
            result["analysis_result"] = analysis_result
        tracker.finish(
            status="failed",
            result=result,
            reason=str(exc) or "account scan/analyze failed",
            summary={"matched": True, "scan_requested": scan_requested},
        )
        raise

    if scan_result is not None:
        result["scan_result"] = scan_result
    if analysis_result is not None:
        result["analysis_result"] = analysis_result

    tracker.finish(
        status=terminal_status,
        result=result,
        reason=terminal_reason,
        summary={
            "matched": True,
            "scan_requested": scan_requested,
            "content_count": int((scan_result or {}).get("content_count") or 0) if scan_result else 0,
            "analyzed": analysis_result is not None,
        },
    )
    return _with_session(result)
