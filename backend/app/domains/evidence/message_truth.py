"""Compatibility exports for the shared, pure message capture contract."""
from app.shared.message_truth import (
    _capture_id,
    capture_member_lookup,
    capture_message_fields,
    project_message_record,
    resolve_capture_kol,
)

__all__ = ["capture_message_fields", "capture_member_lookup", "resolve_capture_kol", "project_message_record"]
