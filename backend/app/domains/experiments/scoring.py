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


def rerank_arm_summary(days: int = 30) -> dict[str, Any]:
    """W-L2 A/B arm 只读汇总:开关状态 + 近 N 天各 arm 的快照数 / 已标注正例率(纯读,零写入)。

    这是实验域对「影子重排序 A/B」的唯一读口:arm 由 rerank_shadow.arm_for_staff 按 staff 哈希分流,
    这里只把分流结果与结果标签按 arm 聚合,供运营判断 treatment 是否真有提升;不暴露权重与公式。
    """
    from app.db.connection import table_exists
    from app.domains.recommendations import rerank_shadow

    summary: dict[str, Any] = {
        "flag": rerank_shadow.AB_FLAG_ENV,
        "enabled": rerank_shadow.ab_enabled(),
        "treatment_pct": rerank_shadow.treatment_pct(),
        "window_days": int(max(1, min(int(days or 30), 365))),
        "arms": {},
        "provider_calls": False,
        "write_db": False,
    }
    if not table_exists(rerank_shadow.SNAPSHOT_TABLE):
        summary["status"] = "snapshot_table_missing"
        return summary
    rows = get_conn().execute(
        f"""
        SELECT arm,
               COUNT(*) AS snapshots,
               SUM(CASE WHEN rerank_applied THEN 1 ELSE 0 END) AS applied,
               SUM(CASE WHEN outcome_label IS NOT NULL THEN 1 ELSE 0 END) AS labeled,
               SUM(CASE WHEN outcome_label = 1 THEN 1 ELSE 0 END) AS positives
        FROM {rerank_shadow.SNAPSHOT_TABLE}
        WHERE created_at >= ?
        GROUP BY arm
        """,
        ((datetime.now(timezone.utc) - timedelta(days=summary["window_days"])).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        labeled = int(row.get("labeled") or 0)
        positives = int(row.get("positives") or 0)
        summary["arms"][str(row.get("arm") or "off")] = {
            "snapshots": int(row.get("snapshots") or 0),
            "applied": int(row.get("applied") or 0),
            "labeled": labeled,
            "positives": positives,
            "positive_rate": round(positives / labeled, 4) if labeled else None,
        }
    summary["status"] = "ok" if rows else "no_snapshots_in_window"
    return summary
