"""Scoring experiments and model registry helpers."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id
from app.domains.recommendations.communication_evidence import LABEL_SEMANTICS, LABEL_SEMANTICS_VERSION, communication_evidence
from app.domains.recommendations.rerank_fit import POSITIVE_NODES, label_for_outcome

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _log_business_audit(
    *,
    actor_staff_id: int,
    action_type: str,
    target_type: str,
    target_id: str | int,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if not actor_staff_id:
        return
    try:
        audit.log_business_event(
            staff_id=int(actor_staff_id),
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            metadata=metadata or {},
        )
    except Exception:
        logger.warning("V-KPI business audit write failed", exc_info=True)


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
    actor_staff_id = resolve_staff_id(staff) or 0
    previous_active = [
        dict(row)
        for row in conn.execute(
            "SELECT id, model_version, model_type, activated_at, metadata_json FROM vkpi_model_registry WHERE status='active' ORDER BY id"
        ).fetchall()
    ]
    conn.execute("UPDATE vkpi_model_registry SET status='registered' WHERE status='active'")
    conn.execute(
        """
        INSERT INTO vkpi_model_registry (model_version, model_type, status, activated_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(model_version) DO UPDATE SET status='active', activated_at=excluded.activated_at, metadata_json=excluded.metadata_json
        """,
        (version, "rule" if version.startswith("rule") else "ml", "active", now, _json({"activated_by": actor_staff_id or None}), now),
    )
    conn.commit()
    _log_business_audit(
        actor_staff_id=actor_staff_id,
        action_type="automation_model_activate",
        target_type="model_registry",
        target_id=version,
        detail=f"Activated scoring model {version}",
        metadata={
            "previous_active_models": previous_active,
            "new_model_version": version,
            "activated_at": now,
        },
    )
    return models()


def _arm_observation_groups(conn: Any, snapshot_table: str, cutoff: str, *, outcomes_available: bool) -> list[Any]:
    # Group by the small Boolean outcome vocabulary instead of loading every
    # snapshot. Both PostgreSQL BOOLEAN and SQLite 0/1 use the same query shape.
    fields = ", ".join(f"o.{key}" for key in (*POSITIVE_NODES, "was_rejected"))
    projection = f", {fields}" if outcomes_available else ""
    join = "LEFT JOIN vkpi_recommendation_outcomes o ON o.recommendation_id=s.recommendation_id" if outcomes_available else ""
    return conn.execute(
        f"""
        SELECT s.arm, COUNT(*) AS snapshots,
               SUM(CASE WHEN s.rerank_applied THEN 1 ELSE 0 END) AS applied{projection}
        FROM {snapshot_table} s {join}
        WHERE s.created_at >= ?
        GROUP BY s.arm{projection}
        """, (cutoff,),
    ).fetchall()


def _summarize_arm_groups(rows: list[Any]) -> dict[str, dict[str, Any]]:
    arms: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        item = arms.setdefault(str(row.get("arm") or "off"), {
            "snapshots": 0, "applied": 0, "labeled": 0, "positives": 0, "positive_rate": None,
        })
        count = int(row.get("snapshots") or 0)
        item["snapshots"] += count
        item["applied"] += int(row.get("applied") or 0)
        label, _ = label_for_outcome(row, recommended_at=None)
        if label is not None:
            item["labeled"] += count
            item["positives"] += count if label == 1 else 0
    for item in arms.values():
        item["positive_rate"] = round(item["positives"] / item["labeled"], 4) if item["labeled"] else None
    return arms


def rerank_arm_summary(days: int = 30) -> dict[str, Any]:
    """按 arm 观察当前非通信运营偏好；不是转化率、训练就绪或模型效果证明。

    不读取旧 snapshot.outcome_label；缺 outcome 或无资格保持 pending。
    """
    from app.db.connection import table_exists
    from app.domains.recommendations import rerank_shadow

    summary: dict[str, Any] = {
        "flag": rerank_shadow.AB_FLAG_ENV,
        "enabled": rerank_shadow.ab_enabled(),
        "treatment_pct": rerank_shadow.treatment_pct(),
        "window_days": int(max(1, min(int(days or 30), 365))),
        "arms": {},
        "pending_by_arm": {},
        "label_semantics": LABEL_SEMANTICS,
        "label_semantics_version": LABEL_SEMANTICS_VERSION,
        "claim_status": "descriptive_only",
        "algorithm_effect_status": "not_evaluated",
        "communication_evidence": communication_evidence(),
        "metric_note": "positive_rate仅为有明确运营偏好标签者的正向比例；不代表收发、转化、KOL精准度或算法效果。",
        "provider_calls": False,
        "provider_calls_scope": "this_read_only_summary",
        "write_db": False,
    }
    if not table_exists(rerank_shadow.SNAPSHOT_TABLE):
        summary["status"] = "snapshot_table_missing"
        return summary
    outcomes_available = table_exists("vkpi_recommendation_outcomes")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=summary["window_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _arm_observation_groups(get_conn(), rerank_shadow.SNAPSHOT_TABLE, cutoff, outcomes_available=outcomes_available)
    summary["arms"] = _summarize_arm_groups(rows)
    summary["pending_by_arm"] = {arm: row["snapshots"] - row["labeled"] for arm, row in summary["arms"].items()}
    summary["status"] = ("ok" if rows else "no_snapshots_in_window") if outcomes_available else "outcome_table_missing"
    return summary
