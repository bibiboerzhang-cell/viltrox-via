"""发货审批门槛 —— 寄样发货前管理员人审(防随意发货浪费)。

红线:成员只能 request(请求发货);approve/reject 仅管理层(scope.can_view_all)。
只写隔离表 vkpi_shipment_approvals,零触 vkpi_kol_pool / viltrox_fit_score / 业务发货状态。
真正"卡住发货"由调用方在发货前查 is_approved();本模块只管审批状态机。
"""
from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.access import scope

logger = get_logger(__name__)

_TABLE = "vkpi_shipment_approvals"


class ShipmentNotApproved(ValueError):
    """发货前置审批未通过 —— 调用方(发货动作)应据此拦截,不得落库发货。"""


def _strict_enabled() -> bool:
    """严格模式:无任何审批记录也拦发货。默认 off(向后兼容,只拦"已请求未通过")。"""
    return str(os.getenv("SHIPMENT_APPROVAL_STRICT", "")).strip().lower() in {"1", "true", "yes", "on"}


def get_status(project_id: int, kol_pool_id: int) -> str | None:
    """返回该 (project,kol) 的审批状态(pending/approved/rejected),无记录返回 None。"""
    if not table_exists(_TABLE):
        return None
    try:
        row = get_conn().execute(
            f"SELECT status FROM {_TABLE} WHERE project_id = ? AND kol_pool_id = ?",
            (int(project_id), int(kol_pool_id)),
        ).fetchone()
        return str(dict(row).get("status")) if row else None
    except Exception:
        logger.debug("shipment_approval.get_status_failed", exc_info=True)
        return None


def assert_shippable(project_id: int, kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> None:
    """发货前置门槛(调用方在落库发货前调用)。
    - 已 approved → 放行;
    - 已 pending/rejected(已发起审批但未通过)→ 拦截(人审真生效);
    - 无审批记录 → 默认放行(向后兼容),strict 模式下拦截。
    """
    del staff
    pid, kid = int(project_id or 0), int(kol_pool_id or 0)
    if pid <= 0 or kid <= 0:
        return  # 无法定位 KOL → 不拦(向后兼容,避免误伤旧流程)
    status = get_status(pid, kid)
    if status == "approved":
        return
    if status in {"pending", "rejected"}:
        raise ShipmentNotApproved(f"发货被拦截:审批状态={status}(需管理员通过发货审批后再发货)")
    if _strict_enabled():
        raise ShipmentNotApproved("发货被拦截:严格模式下需先申请并通过发货审批")
    return


def _actor(staff: dict[str, Any] | None) -> int | None:
    try:
        v = int(scope.actor_staff_id(staff))
        return v or None
    except Exception:
        return None


def request_approval(project_id: int, kol_pool_id: int, *, staff: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    """成员请求发货 → upsert 一条 pending(已存在则不降级已审状态)。"""
    if not table_exists(_TABLE):
        return {"ok": False, "reason": "migration_183_not_applied"}
    pid, kid = int(project_id or 0), int(kol_pool_id or 0)
    if pid <= 0 or kid <= 0:
        return {"ok": False, "reason": "project_id + kol_pool_id required"}
    conn = get_conn()
    # 校验 project/kol 真实存在,不存在则拒绝——否则会落孤儿 approval 行(指向不存在项目)。
    if conn.execute("SELECT 1 FROM vkpi_projects WHERE id = ?", (pid,)).fetchone() is None:
        return {"ok": False, "reason": "project_not_found", "project_id": pid}
    if conn.execute("SELECT 1 FROM vkpi_kol_pool WHERE id = ?", (kid,)).fetchone() is None:
        return {"ok": False, "reason": "kol_not_found", "kol_pool_id": kid}
    conn.execute(
        f"""
        INSERT INTO {_TABLE} (project_id, kol_pool_id, status, requested_by, reason)
        VALUES (?, ?, 'pending', ?, ?)
        ON CONFLICT (project_id, kol_pool_id) DO UPDATE SET
            reason = EXCLUDED.reason, updated_at = NOW()
        """,
        (pid, kid, _actor(staff), str(reason or "")[:500]),
    )
    conn.commit()
    return {"ok": True, "status": "pending", "project_id": pid, "kol_pool_id": kid}


def _decide(project_id: int, kol_pool_id: int, to_status: str, staff: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    if not table_exists(_TABLE):
        return {"ok": False, "reason": "migration_183_not_applied"}
    # 红线:approve/reject 仅管理层。
    if not scope.can_view_all(staff):
        return {"ok": False, "reason": "admin_only"}
    pid, kid = int(project_id or 0), int(kol_pool_id or 0)
    conn = get_conn()
    cur = conn.execute(
        f"""
        UPDATE {_TABLE}
        SET status = ?, approved_by = ?, approved_at = NOW(),
            reason = CASE WHEN ? <> '' THEN ? ELSE reason END, updated_at = NOW()
        WHERE project_id = ? AND kol_pool_id = ?
        """,
        (to_status, _actor(staff), str(reason or ""), str(reason or ""), pid, kid),
    )
    conn.commit()
    if int(getattr(cur, "rowcount", 0) or 0) <= 0:
        # 没有 pending 行 → 先建一条再置态(管理员可直接审批未请求的)。
        conn.execute(
            f"INSERT INTO {_TABLE} (project_id, kol_pool_id, status, approved_by, approved_at, reason) "
            f"VALUES (?, ?, ?, ?, NOW(), ?) ON CONFLICT (project_id, kol_pool_id) DO UPDATE SET "
            f"status=EXCLUDED.status, approved_by=EXCLUDED.approved_by, approved_at=NOW(), updated_at=NOW()",
            (pid, kid, to_status, _actor(staff), str(reason or "")),
        )
        conn.commit()
    return {"ok": True, "status": to_status, "project_id": pid, "kol_pool_id": kid}


def approve(project_id: int, kol_pool_id: int, *, staff: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    """管理层通过发货。"""
    res = _decide(project_id, kol_pool_id, "approved", staff, reason)
    if isinstance(res, dict) and res.get("ok"):  # P1 事件总线:发货获批入流(best-effort)
        try:
            from app.domains.platform import event_ledger

            event_ledger.emit(
                "shipment_approved", entity_type="project", entity_id=int(project_id),
                actor_type="staff", actor_id=str(_actor(staff) or ""), source="shipment_approval",
                payload={"kol_pool_id": int(kol_pool_id)},
                trace_id=event_ledger.new_trace_id("project", project_id, "kol", kol_pool_id),
            )
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
    return res


def reject(project_id: int, kol_pool_id: int, *, staff: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
    """管理层驳回发货。"""
    return _decide(project_id, kol_pool_id, "rejected", staff, reason)


def is_approved(project_id: int, kol_pool_id: int) -> bool:
    """发货前查门槛:该 (project,kol) 是否已审批通过。缺表/无记录 → False(默认拦,保守)。"""
    if not table_exists(_TABLE):
        return False
    try:
        row = get_conn().execute(
            f"SELECT status FROM {_TABLE} WHERE project_id = ? AND kol_pool_id = ?",
            (int(project_id), int(kol_pool_id)),
        ).fetchone()
        return bool(row and str(dict(row).get("status")) == "approved")
    except Exception:
        logger.debug("shipment_approval.is_approved_failed", exc_info=True)
        return False


def list_pending(staff: dict[str, Any] | None = None, *, status: str = "pending", limit: int = 100) -> dict[str, Any]:
    """列审批条目(默认 pending)。管理层全看;成员只看自己请求的。"""
    if not table_exists(_TABLE):
        return {"items": [], "available": False, "reason": "migration_183_not_applied"}
    st = str(status or "pending").strip().lower()
    if st not in {"pending", "approved", "rejected", "all"}:
        st = "pending"
    conn = get_conn()
    where, params = [], []
    if st != "all":
        where.append("status = ?")
        params.append(st)
    if not scope.can_view_all(staff):
        where.append("requested_by = ?")
        params.append(_actor(staff) or 0)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(max(1, min(int(limit or 100), 300)))
    rows = conn.execute(f"SELECT * FROM {_TABLE} {clause} ORDER BY created_at DESC LIMIT ?", tuple(params)).fetchall()
    return {"items": [dict(r) for r in rows], "available": True, "count": len(rows), "status_filter": st}
