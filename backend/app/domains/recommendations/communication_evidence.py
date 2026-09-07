"""Compatibility exports for the shared, pure communication truth contract."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.shared.communication_truth import (
    COMMUNICATION_NODES,
    LABEL_SEMANTICS,
    LABEL_SEMANTICS_VERSION,
    _ACTION_TIMES,
    _first_noncommunication_action,
    blocked_communication_record,
    communication_evidence,
    project_outcome_communications,
)
