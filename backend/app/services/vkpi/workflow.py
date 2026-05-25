"""V-KPI workflow public compatibility module."""
from __future__ import annotations

from app.services.vkpi.workflow_common import (
    ALLOWED_TRANSITIONS,
    PRIMARY_STEP_FLOW,
    PROJECT_STAGES,
    REQUIRED_STAGE_FIELDS,
    SIDE_STAGES,
    STAGE_ALIASES,
    STAGE_LABELS,
    TERMINAL_STAGES,
    _amount_cents,
    _int,
    _json,
    _loads,
    _validate_transition,
    architecture_summary,
    normalize_stage,
    staff_id,
    stage_config,
    utcnow,
)
from app.services.vkpi.workflow_detail import project_detail
from app.services.vkpi.workflow_evidence import (
    add_project_content,
    add_project_message,
    add_project_shipment,
    upsert_project_terms,
)
from app.domains.projects.workflow_projects import (
    create_project,
    delete_project,
    list_projects,
    transition_project,
    update_project,
)

__all__ = [
    "PROJECT_STAGES",
    "PRIMARY_STEP_FLOW",
    "STAGE_LABELS",
    "SIDE_STAGES",
    "TERMINAL_STAGES",
    "STAGE_ALIASES",
    "ALLOWED_TRANSITIONS",
    "REQUIRED_STAGE_FIELDS",
    "utcnow",
    "_json",
    "_int",
    "_loads",
    "_amount_cents",
    "normalize_stage",
    "_validate_transition",
    "staff_id",
    "stage_config",
    "architecture_summary",
    "list_projects",
    "create_project",
    "update_project",
    "transition_project",
    "delete_project",
    "add_project_message",
    "add_project_content",
    "upsert_project_terms",
    "add_project_shipment",
    "project_detail",
]
