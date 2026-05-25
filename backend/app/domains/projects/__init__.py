"""Projects domain facade."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "add_project_content",
    "add_project_message",
    "add_project_shipment",
    "create_project",
    "delete_project",
    "list_projects",
    "project_detail",
    "transition_project",
    "update_project",
    "upsert_project_terms",
]

_EXPORTS = {
    "add_project_content": "app.domains.projects.workflow_evidence",
    "add_project_message": "app.domains.projects.workflow_evidence",
    "add_project_shipment": "app.domains.projects.workflow_evidence",
    "create_project": "app.domains.projects.workflow_projects",
    "delete_project": "app.domains.projects.workflow_projects",
    "list_projects": "app.domains.projects.workflow_projects",
    "project_detail": "app.domains.projects.workflow_detail",
    "transition_project": "app.domains.projects.workflow_projects",
    "update_project": "app.domains.projects.workflow_projects",
    "upsert_project_terms": "app.domains.projects.workflow_evidence",
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module = import_module(_EXPORTS[name])
    return getattr(module, name)
