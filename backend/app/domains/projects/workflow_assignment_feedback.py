"""Non-blocking creator-feedback projection for assignment stage writes."""
from __future__ import annotations

import logging
from typing import Any

from app.shared.project_creator_lifecycle_ports import RecommendationFeedbackSink


def record_contact_feedback(
    feedback_sink: RecommendationFeedbackSink | None,
    updated: dict[str, Any],
    *,
    project_id: int,
    staff: dict[str, Any] | None,
    logger: logging.Logger,
) -> None:
    """Publish committed contact evidence without failing the primary write."""
    if feedback_sink is None:
        logger.warning(
            "project.feedback_sink_missing project_id=%s source=assignment_stage",
            project_id,
        )
        return
    try:
        feedback_sink.record_pool_action(
            updated.get("kol_pool_id"),
            "contact",
            staff=staff,
            payload={
                "stage": "contacted",
                "project_id": int(project_id),
                "assignment_id": updated.get("id"),
            },
            source="assignment_stage",
        )
    except Exception:
        logger.warning(
            "project.feedback_sink_failed project_id=%s source=assignment_stage",
            project_id,
            exc_info=True,
        )
