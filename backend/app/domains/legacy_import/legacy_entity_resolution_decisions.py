"""Decision validation helpers for legacy entity resolution."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text

DECISION_ACTIONS = {"merge_with", "keep_separate", "drop", "escalate"}
DECISION_STATUS = {
    "merge_with": "resolved_merge",
    "keep_separate": "resolved_separate",
    "drop": "dropped",
    "escalate": "escalated",
}
BLOCKED_WEAK_LABELS = {"blocked_risk"}


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row.items()) if hasattr(row, "items") else dict(row)


def _fetch_entity_by_uid(entity_uid: str) -> dict[str, Any]:
    row = get_conn().execute(
        """
        SELECT e.*, b.batch_uid
        FROM vkpi_legacy_kol_entities e
        JOIN vkpi_legacy_import_batches b ON b.id=e.import_batch_id
        WHERE e.entity_uid=?
        """,
        (entity_uid,),
    ).fetchone()
    if not row:
        raise ValueError(f"entity not found: {entity_uid}")
    return _row_to_dict(row)


def identity_label(row: dict[str, Any]) -> str:
    platform = _text(row.get("normalized_platform"))
    handle = _text(row.get("normalized_handle"))
    display_name = _text(row.get("display_name"))
    if platform and handle:
        return f"{platform}:{handle}"
    if handle:
        return handle
    if display_name:
        return display_name
    return _text(row.get("canonical_key")) or _text(row.get("entity_uid"))


def is_blocked_entity(row: dict[str, Any]) -> bool:
    return _text(row.get("weak_label")) in BLOCKED_WEAK_LABELS


def validate_decision(
    entity: dict[str, Any],
    *,
    action: str,
    target_entity_uid: str = "",
    reason: str = "",
    note: str = "",
) -> dict[str, Any]:
    if action not in DECISION_ACTIONS:
        raise ValueError(f"unsupported decision action: {action}")
    if is_blocked_entity(entity) and action in {"merge_with", "keep_separate"}:
        raise ValueError("blocked_risk entities cannot be keep_separate or merge_with; use drop or escalate")
    if action == "drop" and not _text(reason):
        raise ValueError("drop decisions require --reason")
    if action == "escalate" and not _text(note):
        raise ValueError("escalate decisions require --note")

    target: dict[str, Any] = {}
    if action == "merge_with":
        if not _text(target_entity_uid):
            raise ValueError("merge_with decisions require --target")
        target = _fetch_entity_by_uid(target_entity_uid)
        if int(target["id"]) == int(entity["id"]):
            raise ValueError("merge_with target must be a different entity")
        if int(target["import_batch_id"]) != int(entity["import_batch_id"]):
            raise ValueError("merge_with target must be in the same import batch")
        if is_blocked_entity(target):
            raise ValueError("merge_with target cannot be blocked_risk")
        if _text(target.get("resolution_decision")) == "drop":
            raise ValueError("merge_with target cannot be a dropped entity")
    return target
