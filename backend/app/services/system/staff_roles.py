"""Staff role definitions and permission matrix helpers."""
from __future__ import annotations

from app.core.permissions import SYSTEM_PERMISSION_KEYS, TAB_PERMISSION_KEYS, default_permissions_for_role


ROLES = {
    "admin": {
        "label": "Admin",
        "description": "Full access including staff + billing",
        "permissions": {
            "content": ["view", "approve", "reject", "bulk"],
            "creators": ["view", "edit", "block", "flag"],
            "commerce": ["view", "approve_payouts", "process_payouts", "override_attribution"],
            "intelligence": ["view", "generate_insights"],
            "via": ["view", "approve_proposals", "edit_personas"],
            "system": ["view", "edit_integrations", "manage_staff"],
            "trust": ["view", "edit_rules", "block_user"],
            "staff": ["view", "invite", "update", "suspend"],
        },
    },
    "operations": {
        "label": "Operations",
        "description": "Day-to-day content + creator ops",
        "permissions": {
            "content": ["view", "approve", "reject", "bulk"],
            "creators": ["view", "edit", "flag"],
            "commerce": ["view"],
            "intelligence": ["view"],
            "via": ["view"],
            "system": [],
            "trust": ["view"],
            "staff": [],
        },
    },
    "employee": {
        "label": "Marketing Employee",
        "description": "Viltrox Marketing employee workspace",
        "permissions": {
            "content": ["view"],
            "creators": ["view"],
            "commerce": [],
            "intelligence": ["view"],
            "via": [],
            "system": [],
            "trust": [],
            "staff": [],
        },
    },
    "manager": {
        "label": "Marketing Manager",
        "description": "Viltrox Marketing team management",
        "permissions": {
            "content": ["view", "approve", "reject"],
            "creators": ["view", "edit", "flag"],
            "commerce": ["view"],
            "intelligence": ["view", "generate_insights"],
            "via": ["view"],
            "system": ["view"],
            "trust": ["view"],
            "staff": ["view"],
        },
    },
    "analyst": {
        "label": "Analyst",
        "description": "Read-only across all modules + can generate insights",
        "permissions": {
            "content": ["view"],
            "creators": ["view"],
            "commerce": ["view"],
            "intelligence": ["view", "generate_insights"],
            "via": ["view"],
            "system": ["view"],
            "trust": ["view"],
            "staff": [],
        },
    },
    "readonly": {
        "label": "Read-only",
        "description": "View only — no writes",
        "permissions": {
            "content": ["view"],
            "creators": ["view"],
            "commerce": ["view"],
            "intelligence": ["view"],
            "via": ["view"],
            "system": [],
            "trust": [],
            "staff": [],
        },
    },
}


def list_roles() -> dict:
    return {"roles": [{"key": key, **value} for key, value in ROLES.items()]}


def permission_matrix() -> dict:
    """Flat matrix display: {role: {module: [permissions]}}."""
    return {
        "modules": [*TAB_PERMISSION_KEYS, *SYSTEM_PERMISSION_KEYS],
        "roles": {key: value["permissions"] for key, value in ROLES.items()},
        "defaults": {
            "admin": default_permissions_for_role("admin"),
            "readonly": default_permissions_for_role("readonly"),
        },
    }
