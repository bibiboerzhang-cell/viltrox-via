"""Authorization derivation for content-fit child jobs."""
from __future__ import annotations

from typing import Any


def authorize_content_fit_followup(
    payload: dict[str, Any],
    *,
    source_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Attach either a derived My KOL fence or a signed server capability."""

    child = dict(payload)
    source = dict(source_payload or {})
    kol_pool_id = int(child.get("kol_pool_id") or 0)
    if kol_pool_id <= 0:
        raise ValueError("kol_pool_id required")

    from app.domains.kol.my_kol_paid_action_access import (
        FENCE_KEY as MY_KOL_FENCE_KEY,
    )

    if isinstance(source.get(MY_KOL_FENCE_KEY), dict):
        from app.db.connection import db_connection_sync_scope, get_conn
        from app.domains.kol.my_kol_paid_action_access import (
            build_target_fence,
            revalidate_target_fence,
        )

        with db_connection_sync_scope():
            source_fence = source[MY_KOL_FENCE_KEY]
            source_kol_pool_id = int(
                source.get("kol_pool_id")
                or source_fence.get("kol_pool_id")
                or 0
            )
            if source_kol_pool_id != kol_pool_id:
                from app.domains.kol.my_kol_paid_action_access import (
                    MyKolPaidActionError,
                )

                raise MyKolPaidActionError(
                    "content_fit_parent_target_drifted",
                    409,
                )
            actor = revalidate_target_fence(
                get_conn(),
                source,
                expected_action="video_analysis",
            )
            if not isinstance(actor, dict):
                raise ValueError("source paid-action actor required")
            child[MY_KOL_FENCE_KEY] = build_target_fence(
                get_conn(),
                action="content_fit_analysis",
                kol_pool_id=kol_pool_id,
                staff=actor,
            )
        return child

    from app.domains.kol.provider_job_access import (
        CONTENT_FIT_ANALYSIS,
        FENCE_KEY as PROVIDER_FENCE_KEY,
        ProviderJobAccessError,
        SESSION_ADVANCE,
        SMART_SEARCH_PROFILE_ADVANCE,
        VIDEO_URL_RESOLVE,
        build_content_fit_provider_fence,
        issue_server_owned_provider_capability,
        revalidate_provider_job_fence,
    )

    source_provider_fence = source.get(PROVIDER_FENCE_KEY)
    if not isinstance(source_provider_fence, dict):
        raise ProviderJobAccessError("content_fit_parent_authorization_required", 403)
    source_action = str(source_provider_fence.get("action") or "").strip().lower()
    if source_action not in {
        SESSION_ADVANCE,
        SMART_SEARCH_PROFILE_ADVANCE,
        VIDEO_URL_RESOLVE,
    }:
        raise ProviderJobAccessError("content_fit_parent_action_unsupported", 403)
    from app.db.connection import db_connection_sync_scope, get_conn

    session_id = int(source.get("search_session_id") or child.get("search_session_id") or 0)
    with db_connection_sync_scope():
        conn = get_conn()
        parent_actor = revalidate_provider_job_fence(
            conn,
            source,
            expected_action=source_action,
        )
        if session_id > 0:
            item_id = int(source.get("search_session_item_id") or 0)
            params: list[int] = [session_id, kol_pool_id]
            item_clause = ""
            if item_id > 0:
                item_clause = " AND id=?"
                params.append(item_id)
            matched = conn.execute(
                f"""
                SELECT id FROM vkpi_kol_search_session_items
                WHERE session_id=? AND kol_pool_id=?{item_clause}
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if not matched:
                raise ProviderJobAccessError(
                    "content_fit_parent_target_mismatch",
                    409,
                )
            child["search_session_item_id"] = int(dict(matched).get("id") or item_id)
        elif int(source.get("kol_pool_id") or 0) != kol_pool_id:
            # Without a session membership row, the verified parent payload
            # itself must bind this exact KOL; no arbitrary child derivation.
            raise ProviderJobAccessError(
                "content_fit_parent_target_mismatch",
                409,
            )
    if session_id > 0:
        child["search_session_id"] = session_id
    session = None
    if session_id:
        from app.domains.kol import search_sessions

        session = search_sessions.get_session(session_id)
    capability = None
    staff = parent_actor
    if parent_actor.get("server_owned") is True:
        capability = issue_server_owned_provider_capability(
            action=CONTENT_FIT_ANALYSIS,
            target_id=str(kol_pool_id),
            search_session_id=session_id or None,
        )
        staff = None
    child[PROVIDER_FENCE_KEY] = build_content_fit_provider_fence(
        payload=child,
        session=session,
        staff=staff,
        server_owned_capability=capability,
    )
    return child


__all__ = ["authorize_content_fit_followup"]
