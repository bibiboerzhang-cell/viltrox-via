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

    from app.db import connection
    from app.domains.access import scope as access_scope
    from app.domains.kol.my_kol_paid_action_access import (
        FENCE_KEY,
        MyKolPaidActionError,
        revalidate_target_fence,
    )

    if job_type == "kol_outreach_draft" and not isinstance(payload.get(FENCE_KEY), dict):
        # Fail without even opening a database connection: old direct jobs do
        # not have durable actor/target evidence and cannot safely be replayed.
        return paid_action, "my_kol_paid_action_fence_required", None

    actor: dict[str, Any] | None = None
    try:
        with connection_scope():
            if isinstance(payload.get(FENCE_KEY), dict):
                actor = revalidate_target_fence(
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
    except access_scope.ScopeDenied:
        return paid_action, "project_scope_denied", None
    return paid_action, "", actor


__all__ = ["PAID_JOB_ACTIONS", "revalidate_paid_job_scope"]
