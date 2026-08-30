"""Ports used by project workflows to coordinate creator lifecycle effects.

The project domain owns the business transaction.  KOL/search and
recommendation implementations are supplied by an outer composition layer so
the transaction owner never imports those domains directly.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class RecommendationFeedbackSink(Protocol):
    """Best-effort recommendation feedback emitted after a project commit."""

    def record_pool_action(
        self,
        kol_pool_id: Any,
        action: str,
        *,
        staff: dict[str, Any] | None = None,
        note: str = "",
        payload: dict[str, Any] | None = None,
        source: str = "",
    ) -> Mapping[str, Any]: ...

    def record_message_outreach(
        self,
        *,
        message_id: Any,
        project_id: Any,
        kol_id: Any,
        direction: Any,
        staff: dict[str, Any] | None = None,
        source: str = "message",
    ) -> Sequence[Mapping[str, Any]]: ...


class SearchSessionDraftPort(Protocol):
    """Owned smart-search session operations needed to compose a draft."""

    def get_session(
        self,
        session_id: int,
        *,
        staff: dict[str, Any] | None = None,
        scope_to_staff: bool = False,
    ) -> Mapping[str, Any]: ...

    def update_result_summary(
        self,
        session_id: int,
        *,
        status: str,
        summary_patch: dict[str, Any],
    ) -> Mapping[str, Any]: ...


class ClaimLifecyclePort(Protocol):
    """Release active creator claims after a committed terminal transition."""

    def auto_release_for_project(
        self,
        project_id: int,
        *,
        to_stage: str,
        actor_staff_id: int = 0,
        reason: str = "",
    ) -> Mapping[str, Any]: ...


__all__ = [
    "ClaimLifecyclePort",
    "RecommendationFeedbackSink",
    "SearchSessionDraftPort",
]
