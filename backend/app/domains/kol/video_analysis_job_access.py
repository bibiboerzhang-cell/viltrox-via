"""Authorization derivation for durable final-v1 video-analysis children."""
from __future__ import annotations

from typing import Any

from app.domains.kol.provider_job_access import (
    FENCE_KEY,
    VIDEO_ANALYSIS,
    VIDEO_URL_RESOLVE,
    ProviderJobAccessError,
    build_video_analysis_provider_fence,
    issue_server_owned_provider_capability,
    revalidate_provider_job_fence,
)
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _session(session_id: int) -> dict[str, Any]:
    from app.domains.kol import search_sessions

    return search_sessions.get_session(int(session_id))


def _same_video(left: Any, right: Any) -> bool:
    try:
        first = parse_supported_video_url(str(left or ""))
        second = parse_supported_video_url(str(right or ""))
    except VideoUrlIdentityError:
        return False
    return (
        first.platform == second.platform
        and first.video_id == second.video_id
        and first.normalized_url == second.normalized_url
    )


def _session_product_sku(session: dict[str, Any] | None) -> str:
    source = (session or {}).get("input_payload")
    source = source if isinstance(source, dict) else {}
    return str(source.get("product_sku") or "").strip()


def authorize_video_analysis_job(
    conn: Any,
    payload: dict[str, Any],
    *,
    evidence: dict[str, Any],
    source_payload: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a signed child fence derived from URL parent or live request actor."""

    child = dict(payload)
    source = dict(source_payload or {})
    session_id = _int(child.get("search_session_id"))
    session = _session(session_id) if session_id > 0 else None
    server_capability = None
    actor = staff

    if source:
        source_fence = source.get(FENCE_KEY)
        if not isinstance(source_fence, dict):
            raise ProviderJobAccessError("video_analysis_parent_authorization_required", 403)
        if str(source_fence.get("action") or "").strip().lower() != VIDEO_URL_RESOLVE:
            raise ProviderJobAccessError("video_analysis_parent_action_unsupported", 403)
        actor = revalidate_provider_job_fence(
            conn,
            source,
            expected_action=VIDEO_URL_RESOLVE,
        )
        source_session_id = _int(source.get("search_session_id"))
        if source_session_id != session_id:
            raise ProviderJobAccessError("search_session_target_drifted", 409)
        item_id = _int(source.get("search_session_item_id"))
        if session_id > 0 and item_id <= 0:
            raise ProviderJobAccessError("video_analysis_session_item_required", 409)
        child["search_session_item_id"] = item_id or None
        if not _same_video(source.get("source_url") or source.get("url"), evidence.get("content_url")):
            raise ProviderJobAccessError("video_analysis_parent_evidence_mismatch", 409)
        if actor.get("server_owned") is True:
            if session_id > 0:
                raise ProviderJobAccessError("server_owned_session_must_be_root", 403)
            server_capability = issue_server_owned_provider_capability(
                action=VIDEO_ANALYSIS,
                target_id=str(child.get("target_id") or ""),
                search_session_id=None,
            )
            actor = None
    elif session_id <= 0 or not isinstance(staff, dict):
        raise ProviderJobAccessError("video_analysis_authorization_required", 403)

    child["product_sku"] = _session_product_sku(session) or None
    child[FENCE_KEY] = build_video_analysis_provider_fence(
        payload=child,
        evidence=evidence,
        session=session,
        staff=actor,
        server_owned_capability=server_capability,
    )
    # Parent-derived jobs already have a durable item row.  Revalidating the
    # child here prevents enqueue-time KOL/evidence/session substitution.
    if source:
        revalidate_provider_job_fence(
            conn,
            child,
            expected_action=VIDEO_ANALYSIS,
        )
    return child


def video_analysis_authorization_scope(payload: dict[str, Any]) -> str:
    """Stable idempotency namespace; never attach across actors or sessions."""

    fence = payload.get(FENCE_KEY)
    if isinstance(fence, dict):
        session = fence.get("session") if isinstance(fence.get("session"), dict) else {}
        target = fence.get("target") if isinstance(fence.get("target"), dict) else {}
        if fence.get("mode") == "server_owned":
            return f"system:session:{_int(session.get('search_session_id'))}"
        actor = fence.get("actor") if isinstance(fence.get("actor"), dict) else {}
        return (
            f"user:{_int(actor.get('user_id'))}:"
            f"session:{_int(session.get('search_session_id'))}:"
            f"item:{_int(target.get('search_session_item_id'))}"
        )
    from app.domains.kol.my_kol_paid_action_access import FENCE_KEY as MY_KOL_FENCE_KEY

    my_kol = payload.get(MY_KOL_FENCE_KEY)
    if isinstance(my_kol, dict):
        return f"mykol:staff:{_int(my_kol.get('staff_id'))}"
    if payload.get("local_evaluation") is True:
        return f"local-evaluation:user:{_int(payload.get('triggered_by_user_id'))}"
    return "authorization-missing"


__all__ = ["authorize_video_analysis_job", "video_analysis_authorization_scope"]
