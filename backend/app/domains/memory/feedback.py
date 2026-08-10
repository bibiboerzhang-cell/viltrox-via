"""Readiness and feedback surfaces for Memory v0."""
from __future__ import annotations

from typing import Any

from app.core.release_validation import release_validation_active
from app.db.connection import get_conn
from app.domains.legacy_import.legacy_import_audit import _text
from app.domains.legacy_import.legacy_import_staging import json_dumps
from app.domains.memory.common import (
    _load_json,
    _market_signal_counts,
    _public_feedback,
    _readiness_gate,
    _row_to_dict,
    _safe_limit,
    _slug,
    _table_exists,
    _utcnow,
    ensure_memory_schema,
)

def readiness() -> dict[str, Any]:
    """Return a deterministic P4 dry-run readiness check for Memory."""

    # The release gate runs this existing read surface in a database-enforced
    # read-only transaction.  Migrations already ran before the fence was
    # installed; attempting the compatibility bootstrap here would turn an
    # otherwise bounded SELECT into CREATE TABLE and abort the request.
    if not release_validation_active():
        ensure_memory_schema()
    conn = get_conn()
    entity_counts = {
        row["entity_type"]: int(row["n"])
        for row in conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM vkpi_memory_entities GROUP BY entity_type"
        ).fetchall()
    }
    fact_counts = {
        row["fact_type"]: int(row["n"])
        for row in conn.execute(
            "SELECT fact_type, COUNT(*) AS n FROM vkpi_memory_facts GROUP BY fact_type"
        ).fetchall()
    }
    link_counts = {
        row["link_type"]: int(row["n"])
        for row in conn.execute(
            "SELECT link_type, COUNT(*) AS n FROM vkpi_memory_links GROUP BY link_type"
        ).fetchall()
    }
    feedback_counts = {
        row["status"]: int(row["n"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM vkpi_memory_feedback GROUP BY status"
        ).fetchall()
    }
    normalization_counts = {
        row["fact_value_text"]: int(row["n"])
        for row in conn.execute(
            """
            SELECT fact_value_text, COUNT(*) AS n
            FROM vkpi_memory_facts
            WHERE fact_type='product_normalization'
              AND fact_key='status'
            GROUP BY fact_value_text
            """
        ).fetchall()
    }
    market_counts = _market_signal_counts()
    budget_caps_count = int(
        conn.execute("SELECT COUNT(*) AS n FROM vkpi_provider_budget_caps").fetchone()["n"]
        if _table_exists("vkpi_provider_budget_caps")
        else 0
    )
    gates = [
        _readiness_gate("kol_memory", entity_counts.get("kol", 0), 1000, "critical"),
        _readiness_gate("product_family_memory", entity_counts.get("product_family", 0), 1, "critical"),
        _readiness_gate("historical_product_links", link_counts.get("worked_on_product", 0), 1, "critical"),
        _readiness_gate("market_signals", fact_counts.get("market_signal", 0), 1, "critical"),
        _readiness_gate("launch_signals", market_counts.get("launch_plan", 0), 1, "critical"),
        _readiness_gate("official_content_signals", market_counts.get("official_content", 0), 1, "warning"),
        _readiness_gate("voc_signals", market_counts.get("voc_alert", 0), 1, "warning"),
        _readiness_gate("budget_guard_tables", 1 if _table_exists("vkpi_provider_budget_caps") else 0, 1, "warning"),
        _readiness_gate("budget_guard_caps", budget_caps_count, 5, "warning"),
    ]
    blockers = [gate for gate in gates if gate["severity"] == "critical" and gate["status"] != "pass"]
    warnings = [gate for gate in gates if gate["severity"] == "warning" and gate["status"] != "pass"]
    open_feedback = int(feedback_counts.get("open", 0) or 0)
    if open_feedback:
        warnings.append(
            {
                "key": "open_memory_feedback",
                "status": "warn",
                "severity": "warning",
                "actual": open_feedback,
                "expected_min": 0,
                "detail": "open feedback should be reviewed before using Memory for production recommendation writes",
            }
        )
    return {
        "status": "ready_for_p4_dry_run" if not blockers else "blocked",
        "provider_calls_allowed": False,
        "provider_gate": "P4 dry-run may read Memory without provider calls; provider calls still require Budget Guard integration",
        "gates": gates,
        "blockers": blockers,
        "warnings": warnings,
        "counts": {
            "entities": entity_counts,
            "facts": fact_counts,
            "links": link_counts,
            "feedback": feedback_counts,
            "product_normalization_status": normalization_counts,
            "market_signals": market_counts,
        },
    }


def list_feedback(
    *,
    status: str = "",
    entity_uid: str = "",
    feedback_type: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    ensure_memory_schema()
    safe_limit = _safe_limit(limit, default=100, max_limit=500)
    where: list[str] = []
    params: list[Any] = []
    if _text(status):
        where.append("fb.status=?")
        params.append(_text(status))
    if _text(feedback_type):
        where.append("fb.feedback_type=?")
        params.append(_text(feedback_type))
    if _text(entity_uid):
        where.append("e.entity_uid=?")
        params.append(_text(entity_uid))
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = [
        _row_to_dict(row)
        for row in get_conn().execute(
            f"""
            SELECT fb.*, e.entity_uid, e.entity_type, e.identity_key,
                   e.display_name, e.status AS entity_status,
                   e.confidence_score AS entity_confidence_score,
                   e.identity_json, e.metadata_json AS entity_metadata_json,
                   e.updated_at AS entity_updated_at
            FROM vkpi_memory_feedback fb
            LEFT JOIN vkpi_memory_entities e ON e.id=fb.entity_id
            {clause}
            ORDER BY fb.created_at DESC, fb.id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    ]
    counts = {
        row["status"]: int(row["n"])
        for row in get_conn().execute(
            "SELECT status, COUNT(*) AS n FROM vkpi_memory_feedback GROUP BY status ORDER BY status"
        ).fetchall()
    }
    return {
        "filters": {"status": _text(status), "entity_uid": _text(entity_uid), "feedback_type": _text(feedback_type)},
        "counts": counts,
        "items": [_public_feedback(row) for row in rows],
    }


def update_feedback(
    feedback_uid: str,
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_memory_schema()
    uid = _text(feedback_uid)
    if not uid:
        raise ValueError("feedback_uid is required")
    current = get_conn().execute("SELECT * FROM vkpi_memory_feedback WHERE feedback_uid=?", (uid,)).fetchone()
    if not current:
        raise LookupError("memory feedback not found")
    status = _text(body.get("status") or body.get("feedback_status") or current["status"])
    allowed = {"open", "triaged", "resolved", "dismissed"}
    if status not in allowed:
        raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
    resolution = {
        "status": status,
        "action": _text(body.get("resolution_action")),
        "note": _text(body.get("resolution_note") or body.get("note")),
        "updated_at": _utcnow(),
    }
    staff_id = None
    if staff:
        staff_id = staff.get("id") or staff.get("staff_id")
    resolved_at = _utcnow() if status in {"resolved", "dismissed"} else None
    row = get_conn().execute(
        """
        UPDATE vkpi_memory_feedback
        SET status=?,
            resolved_by_staff_id=?,
            resolution_json=?,
            resolved_at=?,
            updated_at=?
        WHERE feedback_uid=?
        RETURNING *
        """,
        (
            status,
            int(staff_id) if staff_id else None,
            json_dumps(resolution),
            resolved_at,
            _utcnow(),
            uid,
        ),
    ).fetchone()
    get_conn().commit()
    return {"item": _public_feedback(_row_to_dict(row))}


def record_feedback(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_memory_schema()
    entity_uid = _text(body.get("entity_uid"))
    entity_id = None
    if entity_uid:
        row = get_conn().execute("SELECT id FROM vkpi_memory_entities WHERE entity_uid=?", (entity_uid,)).fetchone()
        if row:
            entity_id = int(row["id"])
    feedback_type = _text(body.get("feedback_type")) or "note"
    uid = "mem_feedback_" + _slug(f"{entity_uid}:{feedback_type}:{_utcnow()}:{body}")
    staff_id = None
    if staff:
        staff_id = staff.get("id") or staff.get("staff_id")
    row = get_conn().execute(
        """
        INSERT INTO vkpi_memory_feedback (
          feedback_uid, entity_id, feedback_type, rating, status,
          created_by_staff_id, feedback_json, metadata_json
        ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?)
        RETURNING *
        """,
        (
            uid,
            entity_id,
            feedback_type,
            int(body["rating"]) if str(body.get("rating") or "").strip().lstrip("-").isdigit() else None,
            int(staff_id) if staff_id else None,
            json_dumps(body),
            json_dumps({"staff": staff or {}}),
        ),
    ).fetchone()
    get_conn().commit()
    return {"item": _public_feedback(_row_to_dict(row))}
