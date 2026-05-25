"""KOL decision audit trail for P3 decision labels."""
from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)

DECISION_OPTIONS = {
    "contact": "可联系",
    "watch": "可观察",
    "caution": "谨慎",
    "avoid": "避开",
}
SEVERITIES = {"low", "medium", "high", "critical"}
FOLLOWUP_OUTCOMES = {
    "effective": "判断有效",
    "ineffective": "判断无效",
    "unclear": "结果不明确",
    "snooze": "延后回访",
}


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


def _parse_dt(value: Any) -> datetime | None:
    text = _text(value, 80)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def _format_dt(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def ensure_kol_decision_followup_schema() -> None:
    ensure_kol_decision_schema()
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_decision_followups (
                id SERIAL PRIMARY KEY,
                followup_uid TEXT NOT NULL UNIQUE,
                decision_uid TEXT NOT NULL,
                kol_pool_id INTEGER NOT NULL,
                staff_id INTEGER,
                outcome_key TEXT NOT NULL,
                outcome_label TEXT NOT NULL,
                outcome_note TEXT DEFAULT '',
                metric_snapshot_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_kol_decision_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                followup_uid TEXT NOT NULL UNIQUE,
                decision_uid TEXT NOT NULL,
                kol_pool_id INTEGER NOT NULL,
                staff_id INTEGER,
                outcome_key TEXT NOT NULL,
                outcome_label TEXT NOT NULL,
                outcome_note TEXT DEFAULT '',
                metric_snapshot_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_followup_decision_time ON vkpi_kol_decision_followups(decision_uid, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_followup_kol_time ON vkpi_kol_decision_followups(kol_pool_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_kol_decision_followup_outcome_time ON vkpi_kol_decision_followups(outcome_key, created_at DESC)")
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


def _row_to_followup(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["metric_snapshot"] = _loads(data.get("metric_snapshot_json"), {})
    data["metadata"] = _loads(data.get("metadata_json"), {})
    data.pop("metric_snapshot_json", None)
    data.pop("metadata_json", None)
    return data


def _latest_followups(decision_uids: list[str]) -> dict[str, dict[str, Any]]:
    if not decision_uids:
        return {}
    placeholders = ",".join("?" for _ in decision_uids)
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_kol_decision_followups
        WHERE decision_uid IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        """,
        tuple(decision_uids),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = _row_to_followup(row)
        uid = _text(item.get("decision_uid"), 80)
        if uid and uid not in latest:
            latest[uid] = item
    return latest


def _decision_followup_item(row: Any, latest_followup: dict[str, Any] | None, *, days_after: int, now: datetime) -> dict[str, Any]:
    decision = _row_to_decision(row)
    created = _parse_dt(decision.get("created_at")) or now
    due_at = created + timedelta(days=days_after)
    days_since = max(0, int((now - created).total_seconds() // 86400))
    is_due = now >= due_at
    status = "completed" if latest_followup else ("due" if is_due else "not_due")
    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    evidence_snapshot = decision.get("evidence_snapshot") if isinstance(decision.get("evidence_snapshot"), dict) else {}
    return {
        "decision": decision,
        "followup_status": status,
        "due_at": _format_dt(due_at),
        "days_since_decision": days_since,
        "days_after": days_after,
        "latest_followup": latest_followup or None,
        "kol": {
            "kol_pool_id": decision.get("kol_pool_id"),
            "platform": _text(row.get("platform")),
            "handle": _text(row.get("handle")),
            "display_name": _text(row.get("display_name")),
            "profile_url": _text(row.get("profile_url")),
            "followers": _int(row.get("followers")),
        },
        "decision_context": {
            "source": _text(metadata.get("source")),
            "title": _text(metadata.get("title"), 300),
            "handle": _text(metadata.get("handle") or row.get("handle")),
            "platform": _text(metadata.get("platform") or row.get("platform")),
            "evidence_sections": decision.get("evidence_sections") or evidence_snapshot.get("evidence_sections") or [],
            "provider_calls": bool(evidence_snapshot.get("provider_calls")),
            "llm_calls": bool(evidence_snapshot.get("llm_calls")),
            "write_db": bool(evidence_snapshot.get("write_db")),
        },
    }


def list_followup_queue(
    *,
    status: str = "due",
    days_after: int = 30,
    decision_key: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    ensure_kol_decision_followup_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    safe_days = max(1, min(365, int(days_after or 30)))
    clean_status = _text(status or "due", 20).lower()
    if clean_status not in {"all", "due", "pending", "completed", "not_due"}:
        clean_status = "due"
    clean_key = _text(decision_key, 40).lower()
    where: list[str] = []
    params: list[Any] = []
    if clean_key:
        where.append("d.decision_key=?")
        params.append(clean_key)
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = get_conn().execute(
        f"""
        SELECT d.*, kp.platform, kp.handle, kp.display_name, kp.profile_url, kp.followers
        FROM vkpi_kol_decision_audit d
        LEFT JOIN vkpi_kol_pool kp ON kp.id = d.kol_pool_id
        {clause}
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (*params, 500),
    ).fetchall()
    decision_uids = [_text(row["decision_uid"], 80) for row in rows if _text(row["decision_uid"], 80)]
    latest = _latest_followups(decision_uids)
    now = datetime.now(UTC)
    all_items = [
        _decision_followup_item(row, latest.get(_text(row["decision_uid"], 80)), days_after=safe_days, now=now)
        for row in rows
    ]
    if clean_status == "pending":
        items = [item for item in all_items if item["followup_status"] in {"due", "not_due"}]
    elif clean_status != "all":
        items = [item for item in all_items if item["followup_status"] == clean_status]
    else:
        items = all_items
    outcome_counts = Counter(_text((item.get("latest_followup") or {}).get("outcome_key")) for item in all_items if item.get("latest_followup"))
    status_counts = Counter(_text(item.get("followup_status")) for item in all_items)
    completed = int(status_counts.get("completed") or 0)
    effective = int(outcome_counts.get("effective") or 0)
    ineffective = int(outcome_counts.get("ineffective") or 0)
    return {
        "mode": "kol_decision_30d_followup_v0",
        "generated_at": _now(),
        "days_after": safe_days,
        "status_filter": clean_status,
        "count": min(len(items), safe_limit),
        "items": items[:safe_limit],
        "summary": {
            "total_decisions_considered": len(all_items),
            "due": int(status_counts.get("due") or 0),
            "not_due": int(status_counts.get("not_due") or 0),
            "completed": completed,
            "pending": int(status_counts.get("due") or 0) + int(status_counts.get("not_due") or 0),
            "outcome_counts": dict(outcome_counts),
            "effective_rate": round(effective / completed, 4) if completed else None,
            "ineffective_rate": round(ineffective / completed, 4) if completed else None,
        },
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
    }


def create_followup(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_kol_decision_followup_schema()
    decision_uid = _text(payload.get("decision_uid") or payload.get("decisionUid"), 80)
    if not decision_uid:
        raise ValueError("decision_uid is required")
    outcome_key = _text(payload.get("outcome_key") or payload.get("outcomeKey"), 40).lower()
    if outcome_key not in FOLLOWUP_OUTCOMES:
        raise ValueError("invalid outcome_key")
    conn = get_conn()
    decision = conn.execute("SELECT * FROM vkpi_kol_decision_audit WHERE decision_uid=?", (decision_uid,)).fetchone()
    if not decision:
        raise KeyError("decision not found")
    metric_snapshot = payload.get("metric_snapshot") or payload.get("metricSnapshot") or {}
    if not isinstance(metric_snapshot, dict):
        metric_snapshot = {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    uid = f"kolf_{uuid.uuid4().hex[:18]}"
    now = _now()
    actor = resolve_staff_id(staff)
    conn.execute(
        """
        INSERT INTO vkpi_kol_decision_followups
            (followup_uid, decision_uid, kol_pool_id, staff_id, outcome_key, outcome_label,
             outcome_note, metric_snapshot_json, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            decision_uid,
            int(decision["kol_pool_id"]),
            int(actor or 0),
            outcome_key,
            FOLLOWUP_OUTCOMES[outcome_key],
            _text(payload.get("outcome_note") or payload.get("outcomeNote") or payload.get("note"), 3000),
            _json(metric_snapshot),
            _json(metadata),
            now,
        ),
    )
    conn.commit()
    try:
        audit.log_business_event(
            staff_id=int(actor or 0),
            action_type="kol_decision_followup",
            target_type="kol_decision_followup",
            target_id=uid,
            detail=f"{FOLLOWUP_OUTCOMES[outcome_key]} / decision_uid={decision_uid}",
            metadata={
                "decision_uid": decision_uid,
                "kol_pool_id": int(decision["kol_pool_id"]),
                "outcome_key": outcome_key,
            },
        )
    except Exception as exc:
        logger.warning("kol decision followup business log failed for %s: %s", uid, exc)
    row = conn.execute("SELECT * FROM vkpi_kol_decision_followups WHERE followup_uid=?", (uid,)).fetchone()
    return {"followup": _row_to_followup(row), "ok": True}
