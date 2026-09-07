"""Pure projection of captured messages, never a transport receipt resolver."""
from __future__ import annotations

from typing import Any


def _capture_id(value: Any, field: str) -> int:
    if value is None or value == "":
        return 0
    text = str(value).strip()
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not text.isdecimal() or int(text) <= 0:
        raise ValueError(f"{field} must be a positive id")
    return int(text)


def capture_message_fields(body: Any, *, project_id: int | None = None) -> dict[str, Any]:
    """Validate record fields without accepting transport claims or mutating input."""
    if not isinstance(body, dict):
        raise ValueError("message body must be an object")
    result = dict(body)
    for key in ("source", "direction", "sender", "receiver", "body", "message", "snippet", "evidence_url", "captured_at", "follow_up_due_at"):
        if result.get(key) is not None and not isinstance(result[key], str):
            raise ValueError(f"{key} must be text")
    if result.get("metadata") is not None and not isinstance(result["metadata"], dict):
        raise ValueError("metadata must be an object")
    for key in ("project_id", "kol_id", "staff_id", "assignment_id", "kol_pool_id"):
        if key in result:
            result[key] = _capture_id(result[key], key)
    metadata = result.get("metadata") or {}
    for key in ("project_id", "kol_id", "assignment_id", "kol_pool_id"):
        if key not in metadata:
            continue
        hinted = _capture_id(metadata[key], key)
        if result.get(key) and hinted and result[key] != hinted:
            raise ValueError(f"message {key} hints conflict")
        if hinted:
            result[key] = hinted
    if project_id is not None:
        pid = _capture_id(project_id, "project_id")
        if result.get("project_id") and result["project_id"] != pid:
            raise ValueError("message project_id does not match project path")
        result["project_id"] = pid
    if (result.get("assignment_id") or result.get("kol_pool_id")) and not result.get("project_id"):
        raise ValueError("project_id required for assignment or pool identity")
    return result


def capture_member_lookup(body: dict[str, Any], project_kol_id: Any) -> tuple[str, tuple[int, ...]] | None:
    """Build a bounded lookup; every identity hint is untrusted until joined."""
    requested = int(body.get("kol_id") or 0)
    known = int(project_kol_id or 0)
    assignment_id, pool_id = int(body.get("assignment_id") or 0), int(body.get("kol_pool_id") or 0)
    if not assignment_id and not pool_id and (not requested or requested == known):
        return None
    return (
        "SELECT a.id AS assignment_id, a.project_id, a.kol_pool_id, p.linked_main_kol_id AS kol_id "
        "FROM vkpi_project_kol_assignments a JOIN vkpi_kol_pool p ON p.id=a.kol_pool_id "
        "WHERE a.project_id=? AND COALESCE(a.stage_status,'active')<>'deleted' "
        "AND (?=0 OR a.id=?) AND (?=0 OR a.kol_pool_id=?) "
        "AND (?=0 OR p.linked_main_kol_id=?) LIMIT 2",
        (int(body.get("project_id") or 0), assignment_id, assignment_id, pool_id, pool_id, requested, requested),
    )


def resolve_capture_kol(body: dict[str, Any], project_kol_id: Any, *, members: Any = ()) -> int:
    """Accept primary or exact server-joined members; never ignore explicit hints."""
    if capture_member_lookup(body, project_kol_id) is None:
        return int(body.get("kol_id") or project_kol_id or 0)
    rows = [dict(row) for row in members]
    hints = {key: int(body.get(key) or 0) for key in ("project_id", "kol_id", "assignment_id", "kol_pool_id")}
    if any(int(row.get(key) or 0) != expected for row in rows for key, expected in hints.items() if expected):
        raise ValueError("message identity hints do not match joined membership")
    main_ids = {int(row.get("kol_id") or 0) for row in rows}
    if len(main_ids) != 1 or 0 in main_ids:
        raise ValueError("message kol_id does not match project membership")
    return main_ids.pop()


def project_message_record(row: Any) -> dict[str, Any]:
    """Keep the record while overriding all caller-supplied communication truth.

    Existing message rows have no independent provider receipt. Channel labels,
    direction, URLs, timestamps and metadata therefore never establish delivery.
    """
    record = dict(row) if row is not None else {}
    internal = str(record.get("direction") or "").strip().lower() == "internal_note"
    record["communication_truth"] = {
        "record_kind": "internal_note" if internal else "manual_capture",
        "transport_status": "unverified",
        "evidence_class": "manual_record",
        "claim_status": "descriptive_only",
        "sent": None,
        "replied": None,
        "transport_outcome_eligible": False,
    }
    return record


__all__ = ["capture_message_fields", "capture_member_lookup", "resolve_capture_kol", "project_message_record"]
