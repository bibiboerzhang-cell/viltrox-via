"""Default outer adapters for project-to-creator lifecycle ports."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.domains.kol import claims, search_sessions
from app.domains.recommendations import pool_action_bridge


class ServiceRecommendationFeedbackSink:
    def record_pool_action(
        self,
        kol_pool_id: Any,
        action: str,
        *,
        staff: dict[str, Any] | None = None,
        note: str = "",
        payload: dict[str, Any] | None = None,
        source: str = "",
    ) -> Mapping[str, Any]:
        return pool_action_bridge.bridge_pool_action(
            kol_pool_id,
            action,
            staff=staff,
            note=note,
            payload=payload,
            source=source,
        )

    def record_message_outreach(
        self,
        *,
        message_id: Any,
        project_id: Any,
        kol_id: Any,
        direction: Any,
        staff: dict[str, Any] | None = None,
        source: str = "message",
    ) -> Sequence[Mapping[str, Any]]:
        return pool_action_bridge.bridge_message_outreach(
            message_id=message_id,
            project_id=project_id,
            kol_id=kol_id,
            direction=direction,
            staff=staff,
            source=source,
        )


class KolSearchSessionDraftAdapter:
    def get_session(
        self,
        session_id: int,
        *,
        staff: dict[str, Any] | None = None,
        scope_to_staff: bool = False,
    ) -> Mapping[str, Any]:
        return search_sessions.get_session(
            session_id,
            staff=staff,
            scope_to_staff=scope_to_staff,
        )

    def update_result_summary(
        self,
        session_id: int,
        *,
        status: str,
        summary_patch: dict[str, Any],
    ) -> Mapping[str, Any]:
        return search_sessions.update_session_result_summary(
            session_id,
            status=status,
            summary_patch=summary_patch,
        )


class KolClaimLifecycleAdapter:
    def auto_release_for_project(
        self,
        project_id: int,
        *,
        to_stage: str,
        actor_staff_id: int = 0,
        reason: str = "",
    ) -> Mapping[str, Any]:
        return claims.auto_release_claims_for_project(
            project_id,
            to_stage=to_stage,
            actor_staff_id=actor_staff_id,
            reason=reason,
        )


DEFAULT_RECOMMENDATION_FEEDBACK_SINK: Final = ServiceRecommendationFeedbackSink()
DEFAULT_SEARCH_SESSION_DRAFT_PORT: Final = KolSearchSessionDraftAdapter()
DEFAULT_CLAIM_LIFECYCLE_PORT: Final = KolClaimLifecycleAdapter()


__all__ = [
    "DEFAULT_CLAIM_LIFECYCLE_PORT",
    "DEFAULT_RECOMMENDATION_FEEDBACK_SINK",
    "DEFAULT_SEARCH_SESSION_DRAFT_PORT",
    "KolClaimLifecycleAdapter",
    "KolSearchSessionDraftAdapter",
    "ServiceRecommendationFeedbackSink",
]
