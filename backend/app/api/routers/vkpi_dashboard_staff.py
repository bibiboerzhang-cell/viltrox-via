"""V-KPI command center, staff, and dashboard routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.dependencies.perms import require_tab
from app.domains import dashboard as dashboard_domain
from app.domains import staff as staff_domain
from app.domains.access import scope
from app.domains.projects import workflow

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-dashboard"])


def _require_manager_staff(staff: dict) -> None:
    if not staff_domain.is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


def _scope_403(exc: Exception) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc) or "scope denied")


class _MentionBody(BaseModel):
    target_staff_id: int
    message: str = ""


@router.post("/team/mention")
def team_mention(body: _MentionBody, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """#19 团队 @提及:给目标成员写一条通知(复用 vkpi_alerts,staff_id=目标 → 出现在其通知流)。

    发件人由后端从鉴权 staff 取(不信前端);每次提及用 uuid alert_key 保证是新通知而非覆盖。
    """
    import uuid
    from app.domains.alerts import service as alerts_service

    message = (body.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    target_id = int(body.target_staff_id)
    sender = str(staff.get("name") or staff.get("user_name") or staff.get("email") or "同事")
    key = f"mention:{target_id}:{uuid.uuid4().hex[:12]}"
    alert = alerts_service.upsert_alert(
        alert_key=key,
        title=f"{sender} 提及了你",
        body=message[:500],
        severity="info",
        target_type="mention",
        staff_id=target_id,
        rule_key="team_mention",
    )
    return {"status": "success", "alert_id": alert.get("id"), "target_staff_id": target_id}


# ── #18 发布审批(PublishPreviewModal 三按钮 · 按真 source_table+source_id 落 vkpi_publish_approvals)──
class _PublishActionBody(BaseModel):
    source_table: str
    source_id: str
    platform: str = ""
    account_handle: str = ""
    title: str = ""
    scheduled_publish_at: str | None = None


def _publish_require_keys(body: "_PublishActionBody") -> tuple[str, str]:
    st = (body.source_table or "").strip()
    sid = str(body.source_id or "").strip()
    if not st or not sid:
        raise HTTPException(status_code=400, detail="source_table + source_id required")
    return st, sid


def _staff_pk(staff: dict):
    try:
        return int(staff.get("id") or staff.get("staff_id") or 0) or None
    except Exception:
        return None


def _publish_upsert(body, *, status=None, approved_by=None, scheduled=None, reminded_by=None) -> int:
    """按 (source_table, source_id) upsert 一条审批;只动本隔离表(零既有表触点)。"""
    from app.db.connection import get_conn
    from app.domains.alerts.common import utcnow

    st, sid = _publish_require_keys(body)
    now = utcnow()
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM vkpi_publish_approvals WHERE source_table=? AND source_id=?", (st, sid)
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO vkpi_publish_approvals (source_table, source_id, platform, account_handle, title) VALUES (?,?,?,?,?)",
            (st, sid, (body.platform or "")[:80], (body.account_handle or "")[:120], (body.title or "")[:300]),
        )
        row = conn.execute(
            "SELECT id FROM vkpi_publish_approvals WHERE source_table=? AND source_id=?", (st, sid)
        ).fetchone()
    aid = int(row["id"])
    sets = ["updated_at=?"]
    params = [now]
    if status is not None:
        sets.append("status=?"); params.append(status)
    if approved_by is not None:
        sets += ["approved_by=?", "approved_at=?"]; params += [approved_by, now]
    if scheduled is not None:
        sets.append("scheduled_publish_at=?"); params.append(scheduled)
    if reminded_by is not None:
        sets += ["reminder_by=?", "reminder_sent_at=?"]; params += [reminded_by, now]
    params.append(aid)
    conn.execute(f"UPDATE vkpi_publish_approvals SET {', '.join(sets)} WHERE id=?", tuple(params))
    conn.commit()
    return aid


@router.post("/publish/approve")
def publish_approve(body: _PublishActionBody, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """#18 审批通过:把该日历内容条目标记 approved(按真 source_table+source_id 落库)。"""
    aid = _publish_upsert(body, status="approved", approved_by=_staff_pk(staff))
    return {"status": "success", "approval_id": aid, "state": "approved"}


@router.post("/publish/reschedule")
def publish_reschedule(body: _PublishActionBody, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """#18 编辑时间:更新计划发布时间(ISO 字符串;Postgres 转 TIMESTAMPTZ)。"""
    when = (body.scheduled_publish_at or "").strip()
    if not when:
        raise HTTPException(status_code=400, detail="scheduled_publish_at required")
    aid = _publish_upsert(body, status="scheduled", scheduled=when)
    return {"status": "success", "approval_id": aid, "state": "scheduled", "scheduled_publish_at": when}


@router.post("/publish/remind")
def publish_remind(body: _PublishActionBody, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """#18 提醒 KOL:记一条提醒留痕(reminder_sent_at + 操作人)。"""
    aid = _publish_upsert(body, reminded_by=_staff_pk(staff))
    return {"status": "success", "approval_id": aid, "reminded": True}


@router.get("/publish/pending")
def publish_pending(
    status: str = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """R19 · 列「需审批」发布条目(默认 pending),供 Command Center「待审批」整合视图消费。

    只读隔离表 vkpi_publish_approvals;ActionInbox 兼容形 {items, available, count}。
    红线:全程只读,绝不写;零触 viltrox_fit_score。
    """
    from app.db.connection import get_conn, table_exists

    if not table_exists("vkpi_publish_approvals"):
        return {"items": [], "available": False, "count": 0, "reason": "migration_173_not_applied"}
    st = str(status or "pending").strip().lower()
    if st not in {"pending", "approved", "scheduled", "all"}:
        st = "pending"
    conn = get_conn()
    if st == "all":
        rows = conn.execute(
            "SELECT * FROM vkpi_publish_approvals ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM vkpi_publish_approvals WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (st, int(limit)),
        ).fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "available": True, "count": len(items), "status_filter": st}


# ── #24 协作设置(ShareModal「共同目标 + 提醒规则」· per-resource · vkpi_collab_settings)──
class _CollabBody(BaseModel):
    kind: str
    target_id: str
    shared_goal: str = ""
    reminder_rule: str = ""


@router.get("/collab-settings")
def get_collab_settings(
    kind: str = Query(default=""),
    target_id: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读某 project/event 的协作设置;未设过诚实返回空串。"""
    from app.db.connection import get_conn

    k = (kind or "").strip()
    tid = (target_id or "").strip()
    # C2 共享管理:kind 增补 'kol'(target_id=kol_pool_id 文本),供 KOL 共享行挂协作设置。
    if k not in {"project", "event", "kol"} or not tid:
        return {"shared_goal": "", "reminder_rule": ""}
    row = get_conn().execute(
        "SELECT shared_goal, reminder_rule FROM vkpi_collab_settings WHERE kind=? AND target_id=?", (k, tid)
    ).fetchone()
    if not row:
        return {"shared_goal": "", "reminder_rule": ""}
    return {"shared_goal": row["shared_goal"] or "", "reminder_rule": row["reminder_rule"] or ""}


@router.patch("/collab-settings")
def patch_collab_settings(body: _CollabBody, staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """写某 project/event 的协作设置(upsert by kind+target_id);只动本隔离表。"""
    from app.db.connection import get_conn
    from app.domains.alerts.common import utcnow

    k = (body.kind or "").strip()
    tid = (body.target_id or "").strip()
    # C2 共享管理:kind 增补 'kol'(与 GET 同口径),仅放宽白名单,upsert 逻辑不变。
    if k not in {"project", "event", "kol"} or not tid:
        raise HTTPException(status_code=400, detail="kind(project|event|kol)+target_id required")
    goal = (body.shared_goal or "").strip()[:500]
    rule = (body.reminder_rule or "").strip()[:500]
    sid = _staff_pk(staff)
    now = utcnow()
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM vkpi_collab_settings WHERE kind=? AND target_id=?", (k, tid)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE vkpi_collab_settings SET shared_goal=?, reminder_rule=?, updated_by=?, updated_at=? WHERE kind=? AND target_id=?",
            (goal, rule, sid, now, k, tid),
        )
    else:
        conn.execute(
            "INSERT INTO vkpi_collab_settings (kind, target_id, shared_goal, reminder_rule, updated_by) VALUES (?,?,?,?,?)",
            (k, tid, goal, rule, sid),
        )
    conn.commit()
    return {"status": "success", "shared_goal": goal, "reminder_rule": rule}


@router.get("/architecture")
def architecture(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.architecture_summary()


@router.get("/dashboard")
def dashboard(
    window_days: int = 30,
    scope: str = Query(default="all", pattern="^(all|owned|kol|company|official|owned_matrix)$"),
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        result = dashboard_domain.build_dashboard_summary(
            window_days=window_days,
            metric_scope=scope,
            staff_id=staff_id,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc
    return result


@router.get("/dashboard/revenue-trend")
def dashboard_revenue_trend(
    window_days: int = 7,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_revenue_trend(
            window_days=window_days,
            staff_id=staff_id,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/dashboard/product-performance")
def dashboard_product_performance(
    window_days: int = 30,
    staff_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_product_performance(
            window_days=window_days,
            staff_id=staff_id,
            limit=limit,
            staff=staff,
        )
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


def _map_staff_scope_id(staff: dict) -> int | None:
    """C3 员工轻隔离:地图分布的 staff scope 全靠服务端从鉴权 staff 推导。

    owner/管理层(can_view_all)→ None=全局地图;其余员工 → 强制只看自己的 KOL。
    不接受任何客户端 staff_id/scope 传参决定权限(与 Dashboard summary 的 P1 口径一致)。
    """
    return scope.effective_staff_id(staff, None)


@router.get("/dashboard/kol-distribution")
def dashboard_kol_distribution(
    limit: int = Query(default=200, ge=1, le=250),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return real KOL country distribution for the premium dashboard map."""
    return dashboard_domain.build_dashboard_kol_distribution(
        limit=limit, staff_scope_id=_map_staff_scope_id(staff)
    )


@router.get("/dashboard/kol-distribution-pack")
def dashboard_kol_distribution_pack(
    limit: int = Query(default=250, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return a versioned KOL map pack for cache-first dashboard rendering."""
    return dashboard_domain.build_dashboard_kol_distribution_pack(
        limit=limit, staff_scope_id=_map_staff_scope_id(staff)
    )


@router.get("/dashboard/agents-status")
def dashboard_agents_status(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only dashboard status for existing V-KPI agents."""
    del staff
    return dashboard_domain.build_dashboard_agents_status()


@router.get("/dashboard/copilot-brief")
def dashboard_copilot_brief(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return the latest read-only brief-agent artifact for Dashboard Copilot."""
    del staff
    return dashboard_domain._build_dashboard_copilot_brief()


@router.get("/dashboard/fit-movers")
def dashboard_fit_movers(
    limit: int = Query(default=8, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """V6 Fit Top:最近两份 fit 快照 diff 出的 Top Movers(只读)。不足两天返回 warming_up,绝不编造。"""
    del staff
    from app.domains.dashboard import fit_snapshot

    return fit_snapshot.compute_top_movers(limit=limit)


@router.get("/dashboard/ai-today-hot")
def dashboard_ai_today_hot(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """AI Today 今日热点(LLM 每早生成的拍摄方案/话题/重点决策;只读)。未生成则诚实空。"""
    del staff
    from app.domains.market import ai_today

    return ai_today.get_ai_today_hot()


@router.get("/dashboard/competitor-radar")
def dashboard_competitor_radar(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """竞品新品雷达(Gemini+Google 接地每早查海外竞品新品;只读)。未生成则诚实空。"""
    del staff
    from app.domains.market import competitor_radar

    return competitor_radar.get_competitor_radar()


class _ReportAnalysisBody(BaseModel):
    report_text: str
    period: str = "monthly"
    language: str = "zh"


@router.post("/dashboard/report-analysis")
def dashboard_report_analysis(
    body: _ReportAnalysisBody,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """按需:把「生成报告」拼好的全量真实数据喂 LLM,整理成经营深度分析(预算闸硬限 + 当天缓存)。"""
    del staff
    from app.domains.dashboard import report_analysis

    return report_analysis.analyze(
        report_text=body.report_text,
        period=(body.period or "monthly"),
        language=(body.language or "zh"),
    )


@router.get("/dashboard/tasks")
def dashboard_tasks(
    limit: int = Query(default=6, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return dashboard task candidates from the latest recommendation-agent artifact."""
    del staff
    return dashboard_domain._build_dashboard_tasks(limit=limit)


@router.get("/dashboard/agents/inbox")
def dashboard_agents_inbox(
    limit: int = Query(default=50, ge=1, le=100),
    agent_id: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only inbox items from existing runtime/ops agent artifacts."""
    del staff
    return dashboard_domain._build_dashboard_agents_inbox(limit=limit, agent_id=agent_id)


@router.get("/dashboard/recent-content")
def dashboard_recent_content(
    limit: int = Query(default=12, ge=1, le=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return recent content rows for the glass dashboard content panel."""
    del staff
    return dashboard_domain.build_dashboard_recent_content(limit=limit)


@router.get("/dashboard/system-health")
def dashboard_system_health(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return real, read-only system-health counters for the dashboard health bar.

    One light read per source (apify_jobs queue/blocked/worker, vkpi_llm_calls
    today cost, vkpi_kol_pool freshness). Missing sources surface honestly as
    available=false + 待接入, never fabricated.
    """
    del staff
    return dashboard_domain.build_dashboard_system_health()


@router.get("/dashboard/data-freshness")
def dashboard_data_freshness(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only per-entity data-freshness buckets for the Agent-OS layer.

    One light CASE aggregate per entity (vkpi_kol_pool / vkpi_employee_channels /
    optional vkpi_products), freshness derived via COALESCE over existing timestamps.
    Missing sources surface honestly as available=false + 待接入, never fabricated.
    """
    del staff
    return dashboard_domain.build_data_freshness_snapshot()


@router.get("/staff-directory")
def staff_directory(staff=Depends(require_tab("vkpi", "read"))):
    _require_manager_staff(staff)
    return staff_domain.staff_directory()


@router.get("/staff/{staff_id}/profile")
def staff_profile(
    staff_id: int,
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    limit: int = Query(default=80, ge=1, le=300),
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return staff_domain.build_staff_profile(staff_id, staff=staff, window=window, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/staff-kpi")
def staff_kpi(
    window: str = Query(default="month", pattern="^(today|day|daily|1d|7d|week|weekly|30d|month|monthly)$"),
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return staff_domain.build_staff_kpi(window=window, staff_id=staff_id, staff=staff)
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/employee-workspace")
def employee_workspace(
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    return staff_domain.build_employee_workspace(staff_id=staff_id, staff=staff)


@router.get("/dashboard/view/{view}")
def dashboard_view(
    view: str,
    window_days: int = 30,
    staff_id: int | None = None,
    staff=Depends(require_tab("vkpi", "read")),
):
    try:
        return dashboard_domain.build_dashboard_view_payload(
            view=view,
            window_days=window_days,
            staff_id=staff_id,
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise _scope_403(exc) from exc


@router.get("/workflow/stages")
def stages(staff=Depends(require_tab("vkpi", "read"))):
    return workflow.stage_config()
