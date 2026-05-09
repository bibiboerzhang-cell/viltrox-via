"""Scoring experiments and model registry helpers."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def list_experiments(limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute(
        "SELECT * FROM vkpi_scoring_experiments ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, min(300, int(limit or 100))),),
    ).fetchall()
    return {"experiments": [dict(row) for row in rows]}


def create_experiment(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("experiment name required")
    uid = f"exp-{secrets.token_hex(8)}"
    now = _utcnow()
    get_conn().execute(
        """
        INSERT INTO vkpi_scoring_experiments
            (experiment_uid, name, variant_a_strategy, variant_b_strategy, traffic_split, status,
             start_at, end_at, created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            name,
            str(payload.get("variant_a_strategy") or "rule_v0"),
            str(payload.get("variant_b_strategy") or "rule_v0"),
            float(payload.get("traffic_split") or 0),
            str(payload.get("status") or "draft"),
            payload.get("start_at") or None,
            payload.get("end_at") or None,
            resolve_staff_id(staff) or None,
            now,
            now,
        ),
    )
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_scoring_experiments WHERE experiment_uid=?", (uid,)).fetchone()
    return {"experiment": dict(row) if row else {"experiment_uid": uid}}


def update_status(experiment_id: int, status: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    clean = str(status or "").strip().lower()
    if clean not in {"draft", "running", "paused", "completed", "archived"}:
        raise ValueError("unsupported experiment status")
    get_conn().execute("UPDATE vkpi_scoring_experiments SET status=?, updated_at=? WHERE id=?", (clean, _utcnow(), int(experiment_id)))
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_scoring_experiments WHERE id=?", (int(experiment_id),)).fetchone()
    if not row:
        raise LookupError("experiment not found")
    return {"experiment": dict(row)}


def models() -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    rows = get_conn().execute("SELECT * FROM vkpi_model_registry ORDER BY created_at DESC, id DESC").fetchall()
    return {"models": [dict(row) for row in rows]}


def activate_model(model_version: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    version = str(model_version or "").strip()
    if not version:
        raise ValueError("model_version required")
    conn = get_conn()
    now = _utcnow()
    conn.execute("UPDATE vkpi_model_registry SET status='registered' WHERE status='active'")
    conn.execute(
        """
        INSERT INTO vkpi_model_registry (model_version, model_type, status, activated_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(model_version) DO UPDATE SET status='active', activated_at=excluded.activated_at
        """,
        (version, "rule" if version.startswith("rule") else "ml", "active", now, _json({"activated_by": resolve_staff_id(staff) or None}), now),
    )
    conn.commit()
    return models()
