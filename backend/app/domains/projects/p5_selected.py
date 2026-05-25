"""Selected P5 modules: campaigns, manager budget pools, offboarding."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import audit
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_p5_selected import ensure_vkpi_p5_selected_schema
from app.domains.projects.workflow_common import staff_id as resolve_staff_id

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _amount_cents(value: Any) -> int:
    try:
        return int(round(float(value or 0) * 100))
    except (TypeError, ValueError):
        return _int(value)


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


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


def _ensure() -> None:
    ensure_vkpi_schema()
    ensure_vkpi_p5_selected_schema()


def create_campaign(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure()
    name = str(body.get("campaign_name") or body.get("name") or "").strip()
    if not name:
        raise ValueError("campaign_name required")
    uid = str(body.get("campaign_uid") or f"camp-{secrets.token_hex(6)}")
    actor = _actor(staff)
    now = _utcnow()
    get_conn().execute(
        """
        INSERT INTO vkpi_campaigns
            (campaign_uid, campaign_name, owner_staff_id, platform, product_sku, period_start, period_end,
             status, goal_json, metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (uid, name, _int(body.get("owner_staff_id"), actor) or None, str(body.get("platform") or ""), str(body.get("product_sku") or ""), body.get("period_start"), body.get("period_end"), str(body.get("status") or "active"), _json(body.get("goal") or {}), _json(body.get("metadata") or {}), now, now),
    )
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_campaigns WHERE campaign_uid=?", (uid,)).fetchone()
    return {"campaign": dict(row) if row else {}}


def list_campaigns(limit: int = 100, status: str = "") -> dict[str, Any]:
    _ensure()
    where = "WHERE status=?" if status else ""
    params = (status, max(1, min(300, int(limit or 100)))) if status else (max(1, min(300, int(limit or 100))),)
    rows = get_conn().execute(f"SELECT * FROM vkpi_campaigns {where} ORDER BY updated_at DESC, id DESC LIMIT ?", params).fetchall()
    return {"campaigns": [dict(row) for row in rows]}


def add_project_to_campaign(campaign_id: int, project_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure()
    get_conn().execute("INSERT OR IGNORE INTO vkpi_campaign_projects (campaign_id, project_id, added_by_staff_id, added_at) VALUES (?,?,?,?)", (int(campaign_id), int(project_id), _actor(staff) or None, _utcnow()))
    get_conn().commit()
    return campaign_progress(campaign_id)


def campaign_progress(campaign_id: int) -> dict[str, Any]:
    _ensure()
    campaign = get_conn().execute("SELECT * FROM vkpi_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
    if not campaign:
        raise LookupError("campaign not found")
    rows = get_conn().execute(
        """
        SELECT p.*, k.channel_name AS kol_name,
               COALESCE((SELECT SUM(revenue_cents) FROM vkpi_sales_attributions a WHERE a.project_id=p.id),0) AS revenue_cents,
               COALESCE((SELECT SUM(amount_cents) FROM vkpi_cost_ledger c WHERE c.project_id=p.id AND c.status!='void'),0) AS cost_cents
        FROM vkpi_campaign_projects cp
        JOIN vkpi_projects p ON p.id = cp.project_id
        LEFT JOIN kols k ON k.id = p.kol_id
        WHERE cp.campaign_id=?
        ORDER BY p.updated_at DESC
        """,
        (int(campaign_id),),
    ).fetchall()
    projects = [dict(row) for row in rows]
    return {"campaign": dict(campaign), "projects": projects, "summary": {"project_count": len(projects), "sales_cents": sum(_int(row.get("revenue_cents")) for row in projects), "cost_cents": sum(_int(row.get("cost_cents")) for row in projects)}}


def create_budget_pool(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure()
    name = str(body.get("pool_name") or body.get("name") or "").strip()
    if not name:
        raise ValueError("pool_name required")
    uid = str(body.get("pool_uid") or f"budget-{secrets.token_hex(6)}")
    actor_staff_id = _actor(staff)
    total_cents = _int(body.get("total_budget_cents")) or _amount_cents(body.get("total_budget_usd", body.get("total_budget", 0)))
    now = _utcnow()
    get_conn().execute(
        """
        INSERT INTO vkpi_budget_pools
            (pool_uid, pool_name, period_start, period_end, total_budget_cents, currency, owner_staff_id,
             status, metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (uid, name, body.get("period_start"), body.get("period_end"), total_cents, str(body.get("currency") or "USD"), _int(body.get("owner_staff_id"), actor_staff_id) or None, str(body.get("status") or "active"), _json(body.get("metadata") or {}), now, now),
    )
    get_conn().commit()
    row = get_conn().execute("SELECT * FROM vkpi_budget_pools WHERE pool_uid=?", (uid,)).fetchone()
    budget_pool = dict(row) if row else {}
    if budget_pool:
        _log_business_audit(
            actor_staff_id=actor_staff_id,
            action_type="budget_pool_create",
            target_type="budget_pool",
            target_id=budget_pool.get("id") or uid,
            detail=name,
            metadata={
                "pool_uid": uid,
                "pool_name": name,
                "total_budget_cents": total_cents,
                "currency": budget_pool.get("currency") or str(body.get("currency") or "USD"),
                "owner_staff_id": budget_pool.get("owner_staff_id"),
                "period_start": budget_pool.get("period_start"),
                "period_end": budget_pool.get("period_end"),
                "status": budget_pool.get("status"),
            },
        )
    return {"budget_pool": budget_pool}


def _spent_by_pool(pool_id: int) -> int:
    row = get_conn().execute(
        """
        SELECT COALESCE(SUM(cl.amount_cents),0) AS spent
        FROM vkpi_budget_allocations ba
        JOIN vkpi_cost_ledger cl
          ON (ba.project_id IS NOT NULL AND cl.project_id = ba.project_id)
          OR (ba.staff_id IS NOT NULL AND cl.staff_id = ba.staff_id)
        WHERE ba.budget_pool_id=? AND cl.status='actual'
        """,
        (int(pool_id),),
    ).fetchone()
    return _int(row["spent"] if row else 0)


def list_budget_pools(limit: int = 100) -> dict[str, Any]:
    _ensure()
    rows = get_conn().execute("SELECT * FROM vkpi_budget_pools ORDER BY updated_at DESC, id DESC LIMIT ?", (max(1, min(200, int(limit or 100))),)).fetchall()
    result = []
    for row in rows:
        data = dict(row)
        allocated = get_conn().execute("SELECT COALESCE(SUM(allocated_cents),0) AS n FROM vkpi_budget_allocations WHERE budget_pool_id=?", (int(data["id"]),)).fetchone()
        spent = _spent_by_pool(int(data["id"]))
        data["allocated_cents"] = _int(allocated["n"] if allocated else 0)
        data["spent_cents"] = spent
        data["available_cents"] = _int(data.get("total_budget_cents")) - data["allocated_cents"]
        result.append(data)
    return {"budget_pools": result}


def allocate_budget(pool_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure()
    cents = _int(body.get("allocated_cents")) or _amount_cents(body.get("allocated_usd", body.get("amount_usd", 0)))
    if cents <= 0:
        raise ValueError("allocated amount required")
    actor_staff_id = _actor(staff)
    get_conn().execute(
        """
        INSERT INTO vkpi_budget_allocations
            (budget_pool_id, campaign_id, project_id, staff_id, allocated_cents, note, created_by_staff_id, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (int(pool_id), _int(body.get("campaign_id")) or None, _int(body.get("project_id")) or None, _int(body.get("staff_id")) or None, cents, str(body.get("note") or ""), actor_staff_id or None, _utcnow()),
    )
    get_conn().commit()
    allocation = get_conn().execute(
        "SELECT * FROM vkpi_budget_allocations WHERE budget_pool_id=? ORDER BY id DESC LIMIT 1",
        (int(pool_id),),
    ).fetchone()
    pool = get_conn().execute("SELECT * FROM vkpi_budget_pools WHERE id=?", (int(pool_id),)).fetchone()
    allocation_data = dict(allocation) if allocation else {}
    _log_business_audit(
        actor_staff_id=actor_staff_id,
        action_type="budget_pool_allocate",
        target_type="budget_pool",
        target_id=int(pool_id),
        detail=f"Allocated {cents} cents",
        metadata={
            "allocation_id": allocation_data.get("id"),
            "budget_pool_id": int(pool_id),
            "budget_pool_uid": dict(pool).get("pool_uid") if pool else "",
            "campaign_id": _int(body.get("campaign_id")) or None,
            "project_id": _int(body.get("project_id")) or None,
            "target_staff_id": _int(body.get("staff_id")) or None,
            "allocated_cents": cents,
            "note": str(body.get("note") or ""),
        },
    )
    return list_budget_pools(limit=200)


def initiate_offboarding(staff_id: int, *, new_owner_staff_id: int | None = None, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    _ensure()
    conn = get_conn()
    actor_staff_id = _actor(staff)
    active_claims = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_claims WHERE staff_id=? AND status='active'", (int(staff_id),)).fetchone()
    active_projects = conn.execute("SELECT COUNT(*) AS n FROM vkpi_projects WHERE assigned_staff_id=? AND stage_status!='closed' AND stage NOT IN ('closed','released','lost','cancelled')", (int(staff_id),)).fetchone()
    try:
        channels = conn.execute("SELECT COUNT(*) AS n FROM vkpi_employee_channels WHERE staff_id=? AND deleted_at IS NULL", (int(staff_id),)).fetchone()
    except Exception:
        channels = {"n": 0}
    actual_costs = conn.execute("SELECT COUNT(*) AS n FROM vkpi_cost_ledger WHERE staff_id=? AND status='actual'", (int(staff_id),)).fetchone()
    uid = f"off-{secrets.token_hex(8)}"
    conn.execute(
        """
        INSERT INTO vkpi_offboarding_runs
            (run_uid, staff_id, initiated_by_staff_id, new_owner_staff_id, status, active_claims_count,
             active_projects_count, channels_count, actual_costs_count, actions_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (uid, int(staff_id), actor_staff_id or None, int(new_owner_staff_id) if new_owner_staff_id else None, "pending", _int(active_claims["n"] if active_claims else 0), _int(active_projects["n"] if active_projects else 0), _int(channels["n"] if channels else 0), _int(actual_costs["n"] if actual_costs else 0), _json(["release_claims", "transfer_projects", "unbind_channels"]), _utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_offboarding_runs WHERE run_uid=?", (uid,)).fetchone()
    offboarding = dict(row) if row else {}
    if offboarding:
        _log_business_audit(
            actor_staff_id=actor_staff_id,
            action_type="staff_offboarding_initiate",
            target_type="staff",
            target_id=int(staff_id),
            detail=f"Initiated offboarding run {uid}",
            metadata={
                "run_id": offboarding.get("id"),
                "run_uid": uid,
                "target_staff_id": int(staff_id),
                "new_owner_staff_id": int(new_owner_staff_id) if new_owner_staff_id else None,
                "active_claims_count": _int(active_claims["n"] if active_claims else 0),
                "active_projects_count": _int(active_projects["n"] if active_projects else 0),
                "channels_count": _int(channels["n"] if channels else 0),
                "actual_costs_count": _int(actual_costs["n"] if actual_costs else 0),
                "actions": ["release_claims", "transfer_projects", "unbind_channels"],
            },
        )
    return {"offboarding": offboarding}


def execute_offboarding(run_id: int, *, staff: dict[str, Any] | None = None, actions: list[str] | None = None) -> dict[str, Any]:
    _ensure()
    conn = get_conn()
    actor_staff_id = _actor(staff)
    run = conn.execute("SELECT * FROM vkpi_offboarding_runs WHERE id=?", (int(run_id),)).fetchone()
    if not run:
        raise LookupError("offboarding run not found")
    data = dict(run)
    if data["status"] not in {"pending", "in_progress"}:
        _log_business_audit(
            actor_staff_id=actor_staff_id,
            action_type="staff_offboarding_execute_skipped",
            target_type="offboarding_run",
            target_id=int(run_id),
            detail=f"Skipped offboarding run {int(run_id)} because status={data.get('status')}",
            metadata={"run_id": int(run_id), "run_uid": data.get("run_uid"), "status": data.get("status")},
        )
        return {"offboarding": data, "skipped": True}
    chosen = actions or ["release_claims", "transfer_projects", "unbind_channels"]
    staff_id = int(data["staff_id"])
    new_owner = _int(data.get("new_owner_staff_id"))
    log: list[dict[str, Any]] = []
    now = _utcnow()
    if "release_claims" in chosen:
        res = conn.execute("UPDATE vkpi_kol_claims SET status='released', release_reason='offboarding', released_at=?, released_by_staff_id=?, updated_at=? WHERE staff_id=? AND status='active'", (now, _actor(staff) or None, now, staff_id))
        log.append({"action": "release_claims", "rows": getattr(res, "rowcount", -1)})
    if "transfer_projects" in chosen and new_owner:
        res = conn.execute("UPDATE vkpi_projects SET assigned_staff_id=?, updated_at=? WHERE assigned_staff_id=? AND stage_status!='closed' AND stage NOT IN ('closed','released','lost','cancelled')", (new_owner, now, staff_id))
        log.append({"action": "transfer_projects", "rows": getattr(res, "rowcount", -1), "new_owner_staff_id": new_owner})
    if "unbind_channels" in chosen:
        try:
            res = conn.execute("UPDATE vkpi_employee_channels SET status='revoked', deleted_at=?, updated_at=? WHERE staff_id=? AND deleted_at IS NULL", (now, now, staff_id))
            log.append({"action": "unbind_channels", "rows": getattr(res, "rowcount", -1)})
        except Exception as exc:
            log.append({"action": "unbind_channels", "error": str(exc)})
    # Do not void actual historical costs. They stay as financial evidence.
    log.append({"action": "preserve_actual_costs", "reason": "actual/void status model; historical actual costs are not changed"})
    conn.execute("UPDATE vkpi_offboarding_runs SET status='completed', result_json=?, executed_at=? WHERE id=?", (_json(log), now, int(run_id)))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_offboarding_runs WHERE id=?", (int(run_id),)).fetchone()
    offboarding = dict(row) if row else {}
    _log_business_audit(
        actor_staff_id=actor_staff_id,
        action_type="staff_offboarding_execute",
        target_type="offboarding_run",
        target_id=int(run_id),
        detail=f"Executed offboarding run {int(run_id)}",
        metadata={
            "run_id": int(run_id),
            "run_uid": data.get("run_uid"),
            "target_staff_id": staff_id,
            "new_owner_staff_id": new_owner or None,
            "actions": chosen,
            "result": log,
            "completed_status": offboarding.get("status"),
        },
    )
    return {"offboarding": offboarding, "result": log}


def list_offboarding(limit: int = 100) -> dict[str, Any]:
    _ensure()
    rows = get_conn().execute("SELECT * FROM vkpi_offboarding_runs ORDER BY created_at DESC, id DESC LIMIT ?", (max(1, min(200, int(limit or 100))),)).fetchall()
    return {"runs": [dict(row) for row in rows]}
