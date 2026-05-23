"""KOL decision audit trail for P3 decision labels."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.services.vkpi import audit
from app.services.vkpi.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)

DECISION_OPTIONS = {
    "contact": "可联系",
    "watch": "可观察",
    "caution": "谨慎",
    "avoid": "避开",
}
SEVERITIES = {"low", "medium", "high", "critical"}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value or ""))
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _text(value: Any, limit: int = 5000) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def ensure_kol_decision_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_decision_audit (
                id SERIAL PRIMARY KEY,
                decision_uid TEXT NOT NULL UNIQUE,
                kol_pool_id INTEGER NOT NULL,
                staff_id INTEGER,
                decision_key TEXT NOT NULL,
                decision_label TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                rationale TEXT DEFAULT '',
                source_table TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                query TEXT DEFAULT '',
                evidence_sections_json TEXT NOT NULL DEFAULT '[]',
                evidence_snapshot_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_decision_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_uid TEXT NOT NULL UNIQUE,
                kol_pool_id INTEGER NOT NULL,
                staff_id INTEGER,
                decision_key TEXT NOT NULL,
                decision_label TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                rationale TEXT DEFAULT '',
                source_table TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                query TEXT DEFAULT '',
                evidence_sections_json TEXT NOT NULL DEFAULT '[]',
                evidence_snapshot_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_kol_time ON vkpi_kol_decision_audit(kol_pool_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_staff_time ON vkpi_kol_decision_audit(staff_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_key_time ON vkpi_kol_decision_audit(decision_key, created_at DESC)")
    conn.commit()


def _row_to_decision(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["evidence_sections"] = _loads(data.get("evidence_sections_json"), [])
    data["evidence_snapshot"] = _loads(data.get("evidence_snapshot_json"), {})
    data["metadata"] = _loads(data.get("metadata_json"), {})
    data.pop("evidence_sections_json", None)
    data.pop("evidence_snapshot_json", None)
    data.pop("metadata_json", None)
    return data


def create_decision(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_kol_decision_schema()
    kol_pool_id = _int(payload.get("kol_pool_id") or payload.get("kolPoolId"))
    if kol_pool_id <= 0:
        raise ValueError("kol_pool_id is required")
    decision_key = _text(payload.get("decision_key") or payload.get("decisionKey"), 40).lower()
    if decision_key not in DECISION_OPTIONS:
        raise ValueError("invalid decision_key")
    decision_label = _text(payload.get("decision_label") or payload.get("decisionLabel") or DECISION_OPTIONS[decision_key], 60)
    severity = _text(payload.get("severity") or "medium", 20).lower()
    if severity not in SEVERITIES:
        severity = "medium"
    evidence_sections = payload.get("evidence_sections") or payload.get("evidenceSections") or []
    if not isinstance(evidence_sections, list):
        evidence_sections = []
    evidence_snapshot = payload.get("evidence_snapshot") or payload.get("evidenceSnapshot") or {}
    if not isinstance(evidence_snapshot, dict):
        evidence_snapshot = {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    uid = f"kold_{uuid.uuid4().hex[:18]}"
    now = _now()
    actor = resolve_staff_id(staff)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_kol_decision_audit
            (decision_uid, kol_pool_id, staff_id, decision_key, decision_label, severity,
             rationale, source_table, source_id, query, evidence_sections_json,
             evidence_snapshot_json, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            kol_pool_id,
            int(actor or 0),
            decision_key,
            decision_label,
            severity,
            _text(payload.get("rationale") or payload.get("detail")),
            _text(payload.get("source_table") or payload.get("sourceTable"), 120),
            _text(payload.get("source_id") or payload.get("sourceId"), 120),
            _text(payload.get("query"), 500),
            _json(evidence_sections),
            _json(evidence_snapshot),
            _json(metadata),
            now,
        ),
    )
    conn.commit()
    try:
        audit.log_business_event(
            staff_id=int(actor or 0),
            action_type="kol_decision_label",
            target_type="kol_decision",
            target_id=uid,
            detail=f"{decision_label} / kol_pool_id={kol_pool_id}",
            metadata={
                "kol_pool_id": kol_pool_id,
                "decision_key": decision_key,
                "decision_label": decision_label,
                "severity": severity,
                "source_table": _text(payload.get("source_table") or payload.get("sourceTable"), 120),
                "source_id": _text(payload.get("source_id") or payload.get("sourceId"), 120),
            },
        )
    except Exception as exc:
        logger.warning("kol decision audit business log failed for %s: %s", uid, exc)
    row = conn.execute("SELECT * FROM vkpi_kol_decision_audit WHERE decision_uid=?", (uid,)).fetchone()
    return {"decision": _row_to_decision(row), "ok": True}


def list_decisions(*, kol_pool_id: int = 0, decision_key: str = "", limit: int = 100) -> dict[str, Any]:
    ensure_kol_decision_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    where: list[str] = []
    params: list[Any] = []
    if kol_pool_id:
        where.append("kol_pool_id=?")
        params.append(int(kol_pool_id))
    clean_key = _text(decision_key, 40).lower()
    if clean_key:
        where.append("decision_key=?")
        params.append(clean_key)
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_kol_decision_audit {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, safe_limit),
    ).fetchall()
    return {"decisions": [_row_to_decision(row) for row in rows], "count": len(rows)}
