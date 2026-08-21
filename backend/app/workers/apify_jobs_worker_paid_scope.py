"""Durable My KOL authorization gate used before provider job dispatch."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any


PAID_JOB_ACTIONS = {
    "video": "video_analysis",
    "kol_pool_comments_collect": "comments_collect",
    "kol_audience_stats_refresh": "audience_refresh",
    "kol_outreach_draft": "outreach_draft",
    "kol_content_fit_analysis": "content_fit_analysis",
}


def revalidate_paid_job_scope(
    payload: dict[str, Any],
    job_type: str,
    *,
    connection_scope: Callable[[], AbstractContextManager[Any]],
) -> tuple[str, str, dict[str, Any] | None]:
    """Return action, block reason, and the live actor without provider I/O."""

    paid_action = PAID_JOB_ACTIONS.get(str(job_type or "").strip().lower(), "")
    if not paid_action:
        return "", "", None

    from app.core.release_validation import release_validation_active
    from app.db import connection
    from app.domains.access import scope as access_scope
    from app.domains.kol.my_kol_paid_action_access import (
        FENCE_KEY as MY_KOL_FENCE_KEY,
        MyKolPaidActionError,
        revalidate_target_fence,
    )
    from app.domains.kol.provider_job_access import (
        FENCE_KEY as PROVIDER_FENCE_KEY,
        ProviderJobAccessError,
        revalidate_provider_job_fence,
    )

    if release_validation_active():
        return paid_action, "release_validation_fenced", None
    has_my_kol_fence = isinstance(payload.get(MY_KOL_FENCE_KEY), dict)
    has_provider_fence = isinstance(payload.get(PROVIDER_FENCE_KEY), dict)
    final_v1_video = (
        job_type == "video"
        and str(payload.get("derive_method") or "").strip().lower()
        == "video_analysis_final_v1"
    )
    local_evaluation = payload.get("local_evaluation") is True
    if job_type == "kol_outreach_draft" and not has_my_kol_fence:
        # Fail without even opening a database connection: old direct jobs do
        # not have durable actor/target evidence and cannot safely be replayed.
        return paid_action, "my_kol_paid_action_fence_required", None
    if job_type == "kol_content_fit_analysis" and not (
        has_my_kol_fence or has_provider_fence
    ):
        return paid_action, "content_fit_authorization_fence_required", None
    if final_v1_video and local_evaluation and not isinstance(
        payload.get("_local_evaluation_capability"), dict
    ):
        return paid_action, "local_evaluation_capability_required", None
    if final_v1_video and not local_evaluation and not (
        has_my_kol_fence or has_provider_fence
    ):
        return paid_action, "video_analysis_authorization_fence_required", None

    actor: dict[str, Any] | None = None
    try:
        with connection_scope():
            if has_my_kol_fence:
                actor = revalidate_target_fence(
                    connection.get_conn(),
                    payload,
                    expected_action=paid_action,
                )
            elif (
                job_type == "kol_content_fit_analysis" or final_v1_video
            ) and has_provider_fence:
                actor = revalidate_provider_job_fence(
                    connection.get_conn(),
                    payload,
                    expected_action=paid_action,
                )
            project_id = int(payload.get("project_id") or 0)
            if job_type == "kol_outreach_draft" and project_id > 0:
                if not isinstance(actor, dict):
                    raise MyKolPaidActionError(
                        "my_kol_paid_action_actor_inactive",
                        403,
                    )
                access_scope.assert_project_access(project_id, actor, write=False)
    except (TypeError, ValueError):
        return paid_action, "project_identity_invalid", None
    except MyKolPaidActionError as exc:
        return paid_action, exc.code, None
    except ProviderJobAccessError as exc:
        return paid_action, exc.code, None
    except access_scope.ScopeDenied:
        return paid_action, "project_scope_denied", None
    return paid_action, "", actor


def final_v1_scope_checkpoint(
    conn: Any,
    job: dict[str, Any],
    payload: dict[str, Any],
    derive_method: str,
    *,
    provider_calls_performed: bool | None,
    block_job: Callable[..., Any],
    connection_scope: Callable[[], AbstractContextManager[Any]],
    raw: dict[str, Any] | None = None,
) -> bool:
    """Revalidate a signed final_v1 child between paid/external phases.

    Returns True when the job may continue.  Returns False after terminalizing
    the job through ``block_job`` when the actor, target fence, or local
    evaluation capability is no longer valid, or when the analyzer child
    already reported ``raw["scope_revoked"]`` from one of its own stage gates.
    Other derive methods are not paid MY KOL actions and always pass.
    """

    if derive_method != "video_analysis_final_v1":
        return True
    paid_action = PAID_JOB_ACTIONS["video"]
    child_revoked = str((raw or {}).get("scope_revoked") or "").strip()
    if child_revoked:
        block_job(
            conn,
            int(job["id"]),
            child_revoked,
            {
                "provider_calls_performed": provider_calls_performed,
                "paid_action": paid_action,
                "stage": str((raw or {}).get("scope_revoked_stage") or ""),
            },
        )
        return False
    if payload.get("local_evaluation") is True:
        from app.platform.llm_local_evaluation import verify_job_local_evaluation_capability

        authorization = verify_job_local_evaluation_capability(
            payload,
            job_id=int(job["id"]),
        )
        forbidden_lineage = any(
            payload.get(key) not in (None, "", 0, "0")
            for key in (
                "staff_id",
                "triggered_by_user_id",
                "user_id",
                "search_session_id",
                "search_session_item_id",
            )
        )
        if authorization.get("valid") and not forbidden_lineage:
            return True
        reason = (
            "local_evaluation_server_scope_required"
            if forbidden_lineage
            else str(authorization.get("reason") or "local_evaluation_capability_blocked")
        )
        block_job(
            conn,
            int(job["id"]),
            reason,
            {"provider_calls_performed": provider_calls_performed, "paid_action": paid_action},
        )
        return False
    paid_action, block_reason, _actor = revalidate_paid_job_scope(
        payload,
        "video",
        connection_scope=connection_scope,
    )
    if not block_reason:
        return True
    block_job(
        conn,
        int(job["id"]),
        block_reason,
        {
            "provider_calls_performed": provider_calls_performed,
            "paid_action": paid_action,
        },
    )
    return False


__all__ = [
    "PAID_JOB_ACTIONS",
    "final_v1_scope_checkpoint",
    "revalidate_paid_job_scope",
]
