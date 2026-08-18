"""Project list/create/status/delete operations for V-KPI workflow."""
from __future__ import annotations

import secrets
import logging
from typing import Any

from app.db.connection import PostgresCompatConnection, get_conn
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow_common import (
    PROJECT_STAGES,
    TERMINAL_STAGES,
    _int,
    _json,
    _loads,
    _validate_transition,
    normalize_stage,
    staff_id,
    utcnow,
)

logger = logging.getLogger(__name__)

# Behavior-preserving re-exports: ProjectCard stage constants + enrichment moved
# to workflow_projects_cards.py (function bodies unchanged → behavior unchanged).
from app.domains.projects.workflow_projects_cards import (  # noqa: E402
    ASSIGNMENT_STAGE_TO_CARD_STAGE,
    CARD_STAGE_INDEX,
    CARD_STAGE_LABELS,
    PROJECT_CARD_STAGE_KEYS,
    _clamp,
    _empty_stage_counts,
    _enrich_project_card_fields,
)

# Audit / staff-name / pool-occupancy helpers moved to workflow_projects_occupancy.py.
from app.domains.projects.workflow_projects_occupancy import (  # noqa: E402
    _current_user_id,
    _log_project_audit,
    _pool_claim_occupancy,
    _staff_display_names,
)


def _lock_owned_search_session_for_draft(
    conn: Any,
    *,
    session_id: int,
    owner_id: int,
) -> None:
    """Serialize one owner's draft creation without breaking SQLite tooling.

    Production connections are PostgreSQL compatibility wrappers, so the row
    lock is held until ``create_project`` commits on the same request-scoped
    connection.  SQLite and lightweight test connections use the same
    owner-scoped existence check without unsupported ``FOR UPDATE`` syntax.
    """
    sql = """
        SELECT id
        FROM vkpi_kol_search_sessions
        WHERE id=? AND created_by=?
    """
    if isinstance(conn, PostgresCompatConnection):
        sql += " FOR UPDATE"
    locked = conn.execute(sql, (int(session_id), int(owner_id))).fetchone()
    if not locked:
        # Preserve the public cross-employee contract: another employee's id
        # is indistinguishable from a missing session.
        raise LookupError(f"search session not found: {session_id}")


def list_projects(limit: int = 50, stage: str = "", *, staff: dict[str, Any] | None = None, staff_id_filter: int | None = None, starred_only: bool = False) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(200, int(limit or 50)))
    conn = get_conn()
    params: list[Any] = []
    current_user_id = _current_user_id(staff)
    # 项目看板必须是项目事实的统一入口。来源只用于溯源,不能把 cockpit UI、Launch、
    # manual 或 smart-search 已成功写入的项目从列表中静默隐藏。可见范围仍由下方
    # project_filter 统一收口,因此去掉来源白名单不会扩大员工的项目权限。
    where = "WHERE p.stage_status <> 'deleted'"
    if starred_only:
        where += " AND EXISTS (SELECT 1 FROM vkpi_project_stars ps_filter WHERE ps_filter.project_id = p.id AND ps_filter.user_id = ?)"
        params.append(current_user_id)
    if stage:
        where += " AND p.stage = ?"
        params.append(stage)
    scope_clause, scope_params = scope.project_filter("p", staff, staff_id_filter)
    if scope_clause:
        where += f" AND {scope_clause}"
        params.extend(scope_params)
    rows = conn.execute(
        f"""
        SELECT p.*,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN CAST(pa.kol_count AS TEXT) || ' KOL'
                   ELSE COALESCE(pk.display_name, '')
               END AS kol_name,
               CASE
                   WHEN COALESCE(pa.kol_count, 0) > 1 THEN 'multi'
                   ELSE COALESCE(pk.platform, '')
               END AS kol_platform,
               pk.handle AS handle,
               pk.avatar_url AS kol_avatar,
               pa.primary_kol_pool_id AS kol_pool_id,
               COALESCE(pa.kol_count, 0) AS kol_count,
               COALESCE(pa.kol_with_evidence, 0) AS kol_with_evidence,
               COALESCE(ev.evidence_count, 0) AS evidence_count,
               COALESCE(ev.evidence_kol_count, 0) AS evidence_kol_count,
               ev.latest_publish_date AS latest_publish_date,
               COALESCE(ev.total_views, 0) AS total_views,
               COALESCE(s.name, assignment_owner.name) AS staff_name,
               CASE WHEN ps.user_id IS NULL THEN FALSE ELSE TRUE END AS is_starred,
               ps.created_at AS starred_at,
               (
                   SELECT MIN(e.effective_at)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id
               ) AS first_event_at,
               (
                   SELECT MAX(e.effective_at)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id AND e.to_stage = p.stage
               ) AS current_stage_started_at,
               (
                   SELECT COUNT(*)
                   FROM vkpi_project_stage_events e
                   WHERE e.project_id = p.id
               ) AS stage_event_count
        FROM vkpi_projects p
        LEFT JOIN (
            SELECT
                a.project_id,
                COUNT(DISTINCT a.kol_pool_id) AS kol_count,
                COUNT(DISTINCT CASE WHEN kp.has_video_evidence THEN a.kol_pool_id END) AS kol_with_evidence,
                MIN(a.kol_pool_id) AS primary_kol_pool_id
            FROM vkpi_project_kol_assignments a
            LEFT JOIN vkpi_kol_pool kp ON kp.id = a.kol_pool_id
            GROUP BY a.project_id
        ) pa ON pa.project_id = p.id
        LEFT JOIN (
            SELECT project_id, assigned_staff_id
            FROM (
                SELECT
                    project_id,
                    assigned_staff_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id
                        ORDER BY COUNT(*) DESC, assigned_staff_id ASC
                    ) AS rn
                FROM vkpi_project_kol_assignments
                WHERE assigned_staff_id IS NOT NULL
                GROUP BY project_id, assigned_staff_id
            ) ranked_assignment_staff
            WHERE rn = 1
        ) assignment_owner_pick ON assignment_owner_pick.project_id = p.id
        LEFT JOIN vkpi_kol_pool pk ON pk.id = pa.primary_kol_pool_id
        LEFT JOIN (
            SELECT
                project_id,
                COUNT(*) AS evidence_count,
                COUNT(DISTINCT kol_pool_id) AS evidence_kol_count,
                MAX(publish_date) AS latest_publish_date,
                COALESCE(SUM(COALESCE(view_count, 0)), 0) AS total_views
            FROM vkpi_kol_video_evidence
            WHERE project_id IS NOT NULL
            GROUP BY project_id
        ) ev ON ev.project_id = p.id
        LEFT JOIN staff st ON st.id = p.assigned_staff_id
        LEFT JOIN users s ON s.id = st.user_id
        LEFT JOIN staff assignment_st ON assignment_st.id = assignment_owner_pick.assigned_staff_id
        LEFT JOIN users assignment_owner ON assignment_owner.id = assignment_st.user_id
        LEFT JOIN vkpi_project_stars ps ON ps.project_id = p.id AND ps.user_id = ?
        {where}
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT ?
        """,
        (current_user_id, *params, limit_i),
    ).fetchall()
    projects = [dict(row) for row in rows]
    _enrich_project_card_fields(conn, projects)
    return {"projects": projects, "scope": scope.scope_context(staff, staff_id_filter)}


def set_project_star(project_id: int, starred: bool, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=False)
    user_id = _current_user_id(staff)
    if not user_id:
        raise ValueError("user_id required")
    conn = get_conn()
    if starred:
        conn.execute(
            """
            INSERT INTO vkpi_project_stars (user_id, project_id)
            VALUES (?, ?)
            ON CONFLICT (user_id, project_id) DO NOTHING
            """,
            (user_id, int(project_id)),
        )
    else:
        conn.execute("DELETE FROM vkpi_project_stars WHERE user_id=? AND project_id=?", (user_id, int(project_id)))
    conn.commit()
    return {"project_id": int(project_id), "is_starred": bool(starred)}


def _normalize_project_products(body: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize selected products from UI without adding a new relation table yet."""
    products: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_product(sku: Any, name: Any = "") -> None:
        product_sku = str(sku or "").strip()
        product_name = str(name or "").strip()
        if not product_sku or product_sku in seen:
            return
        seen.add(product_sku)
        products.append({"product_sku": product_sku, "product_name": product_name})

    for item in body.get("products") or []:
        if not isinstance(item, dict):
            continue
        add_product(item.get("product_sku") or item.get("productSku") or item.get("sku"), item.get("product_name") or item.get("productName") or item.get("name"))

    for sku in body.get("product_skus") or body.get("productSkus") or []:
        add_product(sku)

    add_product(body.get("product_sku") or body.get("productSku"), body.get("product_name") or body.get("productName"))
    return products


def create_project(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    name = str(body.get("project_name") or body.get("name") or "").strip()
    if not name:
        raise ValueError("project_name required")
    stage = normalize_stage(str(body.get("stage") or "discovery"))
    if stage not in PROJECT_STAGES:
        raise ValueError("unsupported stage")
    actor_staff_id = staff_id(staff)
    assigned_staff_id = _int(body.get("assigned_staff_id"), actor_staff_id)
    if not scope.can_view_all(staff):
        assigned_staff_id = actor_staff_id
    now = utcnow()
    project_uid = str(body.get("project_uid") or f"VKPI-{secrets.token_hex(5).upper()}").strip()
    products = _normalize_project_products(body)
    primary_product = products[0] if products else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if products:
        metadata = {**metadata, "products": products, "product_skus": [item["product_sku"] for item in products]}
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_projects (
            project_uid, project_name, kol_id, assigned_staff_id, created_by_staff_id,
            product_sku, product_name, platform, marketplace, stage, stage_status,
            priority, source_type, shopify_discount_code, shopify_link, amazon_asin,
            amazon_attribution_link, amazon_associates_link, sample_status, tracking_number,
            target_post_date, due_at, started_at, last_activity_at, metadata_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            project_uid,
            name,
            _int(body.get("kol_id")) or None,
            assigned_staff_id or None,
            actor_staff_id or None,
            str(primary_product.get("product_sku") or body.get("product_sku") or ""),
            str(primary_product.get("product_name") or body.get("product_name") or ""),
            str(body.get("platform") or ""),
            str(body.get("marketplace") or ""),
            stage,
            "closed" if stage in TERMINAL_STAGES else str(body.get("stage_status") or "active"),
            str(body.get("priority") or "normal"),
            str(body.get("source_type") or "manual"),
            str(body.get("shopify_discount_code") or ""),
            str(body.get("shopify_link") or ""),
            str(body.get("amazon_asin") or ""),
            str(body.get("amazon_attribution_link") or ""),
            str(body.get("amazon_associates_link") or ""),
            str(body.get("sample_status") or "not_required"),
            str(body.get("tracking_number") or ""),
            body.get("target_post_date"),
            body.get("due_at"),
            now,
            now,
            _json(metadata),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM vkpi_projects WHERE project_uid=?", (project_uid,)).fetchone()
    project_id = int(row["id"]) if row else 0
    if project_id:
        conn.execute(
            """
            INSERT INTO vkpi_project_stage_events
                (project_id, from_stage, to_stage, event_type, actor_staff_id, note, effective_at, metadata_json, created_at)
            VALUES (?, '', ?, 'created', ?, ?, ?, '{}', ?)
            """,
            (project_id, stage, actor_staff_id or None, str(body.get("note") or ""), now, now),
        )
        if _int(body.get("kol_id")) and assigned_staff_id:
            existing_claim = conn.execute(
                "SELECT id FROM vkpi_kol_claims WHERE kol_id=? AND status='active' LIMIT 1",
                (_int(body.get("kol_id")),),
            ).fetchone()
            if not existing_claim:
                conn.execute(
                    """
                    INSERT INTO vkpi_kol_claims (
                        kol_id, staff_id, project_id, status, claimed_at, last_effective_touch_at,
                        metadata_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        _int(body.get("kol_id")),
                        assigned_staff_id,
                        project_id,
                        "active",
                        now,
                        now,
                        _json({"source": "project_create"}),
                        now,
                        now,
                    ),
                )
    conn.commit()
    if project_id:
        _log_project_audit(
            staff=staff,
            action_type="project_create",
            project_id=project_id,
            detail=name,
            metadata={
                "project_uid": project_uid,
                "stage": stage,
                "kol_id": _int(body.get("kol_id")) or None,
                "assigned_staff_id": assigned_staff_id or None,
                "product_skus": [item["product_sku"] for item in products],
                "source_type": str(body.get("source_type") or "manual"),
            },
        )
    return {"id": project_id, "project_uid": project_uid, "project_name": name, "stage": stage}


def create_project_draft_from_session(
    session_id: int,
    body: dict[str, Any] | None = None,
    *,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """R2:从已批准的 smart-search 会话一键建项目草案(discovery 阶段)+ 挂选中 KOL。

    选人来源只取会话 approved_kol_ids(R1 锁定),请求体不得覆盖。
    brief:用 planner 的 product_positioning / target_persona(优先 body,次 会话内可得,
    末 query_text 兜底)写进 project.metadata.brief(表无 brief 列,落 metadata_json)。
    复用 create_project + add_project_kols;占用冲突(KOL 被他人认领)诚实降级为 warning,
    草案照建(已 commit),KOL 可在项目详情再加。绝不触 viltrox_fit_score / rule_v0。
    """
    body = body or {}
    # 懒导入:避免 projects ←→ kol 模块装载期相互牵连。
    from app.domains.kol import search_sessions as kol_search_sessions

    session = kol_search_sessions.get_session(
        int(session_id),
        staff=staff,
        scope_to_staff=True,
    )  # 缺失/越权统一 → LookupError

    approved_ids: list[int] = []
    approved_seen: set[int] = set()
    for value in session.get("approved_kol_ids") or []:
        kid = _int(value)
        if kid and kid not in approved_seen:
            approved_seen.add(kid)
            approved_ids.append(kid)

    # 服务端审批集是唯一候选来源,避免请求体注入任意/跨会话 KOL。
    raw_ids = approved_ids
    kol_pool_ids: list[int] = []
    seen: set[int] = set()
    for value in raw_ids:
        kid = _int(value)
        if kid and kid not in seen:
            seen.add(kid)
            kol_pool_ids.append(kid)
    if not kol_pool_ids:
        raise ValueError("no approved KOLs on this session; approve candidates first")
    unapproved_ids = [kid for kid in kol_pool_ids if kid not in approved_seen]
    if unapproved_ids:
        raise ValueError(
            "kol_pool_ids must be a subset of approved session candidates: "
            + ",".join(str(kid) for kid in unapproved_ids)
        )

    # planner 定位/人设:body 优先 → 会话内可得 → query_text 兜底。
    # 不在此同步跑 LLM(沿用搜索流「planner 推迟到 worker」的决策;前端建草案时已带上 plan)。
    input_payload = session.get("input_payload") if isinstance(session.get("input_payload"), dict) else {}
    result_summary = session.get("result_summary") if isinstance(session.get("result_summary"), dict) else {}
    session_plan: dict[str, Any] = {}
    for src in (result_summary.get("llm_query_plan"), input_payload.get("llm_query_plan"), input_payload):
        if isinstance(src, dict) and (src.get("product_positioning") or src.get("target_persona")):
            session_plan = src
            break
    query_text = str(session.get("query_text") or "").strip()
    positioning = str(body.get("product_positioning") or session_plan.get("product_positioning") or "").strip()
    persona = str(body.get("target_persona") or session_plan.get("target_persona") or query_text).strip()

    # 项目名:body 优先 → 产品名/query 派生。
    product_name = str(body.get("product_name") or "").strip()
    project_name = str(body.get("project_name") or "").strip()
    if not project_name:
        base = product_name or query_text or f"Smart Search #{int(session_id)}"
        project_name = f"{base} · 合作草案"[:200]

    brief = {
        "product_positioning": positioning,
        "target_persona": persona,
        "query_text": query_text,
        "source": "smart_search",
        "search_session_id": int(session_id),
        "approved_kol_count": len(kol_pool_ids),
    }

    # 幂等恢复:优先复用会话已记录的草案;若上次在“项目已提交、会话未回写”间失败,
    # 再按不可伪造的 metadata.search_session_id + 当前项目 owner 找回。无 schema 迁移。
    actor_staff_id = staff_id(staff)
    session_owner_id = _int(session.get("created_by"))
    if not actor_staff_id or (
        session_owner_id and session_owner_id != actor_staff_id
    ):
        raise LookupError(f"search session not found: {session_id}")
    # get_session(..., scope_to_staff=True) is the primary owner boundary.
    # Persisted rows include created_by and therefore take the row-lock/
    # idempotency path below.  Keeping the projection tolerant of an omitted
    # created_by supports older DTOs and isolated callers without trusting a
    # body-supplied owner or candidate set.
    conn = get_conn() if session_owner_id else None
    # PostgreSQL:同一 owner/session 的并发请求在这里排队。首请求随后通过
    # create_project 在同一 request-scoped connection 上提交项目并释放行锁;
    # 第二请求醒来后才执行下方复用查询,因此会找到并复用首个项目。
    if conn is not None and session_owner_id:
        _lock_owned_search_session_for_draft(
            conn,
            session_id=int(session_id),
            owner_id=session_owner_id,
        )
    draft_summary = result_summary.get("draft_project") if isinstance(result_summary.get("draft_project"), dict) else {}
    recorded_project_id = _int(draft_summary.get("project_id"))
    reusable_row = None
    if recorded_project_id and actor_staff_id and conn is not None:
        reusable_row = conn.execute(
            """
            SELECT * FROM vkpi_projects
            WHERE id=? AND stage_status <> 'deleted' AND source_type='smart_search'
              AND (created_by_staff_id=? OR assigned_staff_id=?)
            """,
            (recorded_project_id, actor_staff_id, actor_staff_id),
        ).fetchone()
    if not reusable_row and actor_staff_id and conn is not None:
        reusable_row = conn.execute(
            """
            SELECT * FROM vkpi_projects
            WHERE stage_status <> 'deleted' AND source_type='smart_search'
              AND metadata_json->>'search_session_id'=?
              AND (created_by_staff_id=? OR assigned_staff_id=?)
            ORDER BY id ASC
            LIMIT 1
            """,
            (str(int(session_id)), actor_staff_id, actor_staff_id),
        ).fetchone()
    if reusable_row:
        reusable = dict(reusable_row)
        project_id = _int(reusable.get("id"))
        metadata = _loads(reusable.get("metadata_json"))
        if not isinstance(metadata, dict) or _int(metadata.get("search_session_id")) != int(session_id):
            reusable = {}
        else:
            attached = 0
            kol_attach_warning = ""
            missing_kol_pool_ids: list[int] = []
            try:
                attach_result = add_project_kols(
                    project_id,
                    {"kol_pool_ids": kol_pool_ids},
                    staff=staff,
                )
                attached = _int(attach_result.get("inserted")) + _int(attach_result.get("skipped_existing"))
                missing_kol_pool_ids = [
                    _int(value)
                    for value in attach_result.get("missing_kol_pool_ids") or []
                    if _int(value)
                ]
                if missing_kol_pool_ids:
                    kol_attach_warning = (
                        "KOL pool items no longer exist: "
                        + ",".join(str(value) for value in missing_kol_pool_ids)
                    )
            except ValueError as exc:
                kol_attach_warning = str(exc)
            kol_search_sessions.update_session_result_summary(
                int(session_id),
                status=str(session.get("status") or "ready"),
                summary_patch={
                    "draft_project": {
                        "project_id": project_id,
                        "project_uid": reusable.get("project_uid"),
                        "attached_kol_count": attached,
                        "requested_kol_count": len(kol_pool_ids),
                        "missing_kol_pool_ids": missing_kol_pool_ids,
                        "kol_attach_warning": kol_attach_warning,
                        "reused": True,
                    }
                },
            )
            return {
                "ok": True,
                "reused": True,
                "project_id": project_id,
                "project_uid": reusable.get("project_uid"),
                "project_name": reusable.get("project_name"),
                "stage": reusable.get("stage"),
                "attached_kol_count": attached,
                "requested_kol_count": len(kol_pool_ids),
                "missing_kol_pool_ids": missing_kol_pool_ids,
                "kol_attach_warning": kol_attach_warning,
                "brief": metadata.get("brief") if isinstance(metadata.get("brief"), dict) else brief,
                "cost_estimate": metadata.get("cost_estimate") if isinstance(metadata.get("cost_estimate"), dict) else {},
            }

    # R3:成本估算 + 风险合成(确定性,零 LLM,零触 fit_score)→ 写进草案 metadata,草案即带预算。
    cost_estimate: dict[str, Any] = {}
    try:
        from app.domains.projects import cost_estimate as cost_estimate_engine

        cost_estimate = cost_estimate_engine.estimate_cost_for_kols(kol_pool_ids, staff=staff)
    except Exception:
        logger.warning("create_project_draft: cost estimate skipped", exc_info=True)
        cost_estimate = {}

    create_body = {
        "project_name": project_name,
        "stage": "discovery",
        # 来源是系统事实,不可由请求 body 改写成 manual/excel 后躲过来源追溯。
        "source_type": "smart_search",
        "product_sku": body.get("product_sku") or session_plan.get("product_sku") or "",
        "product_name": product_name,
        "platform": str(body.get("platform") or ""),
        "metadata": {
            "brief": brief,
            "search_session_id": int(session_id),
            "cost_estimate": cost_estimate,
            "source": {
                "type": "smart_search_session",
                "search_session_id": int(session_id),
                "session_owner_id": session_owner_id or actor_staff_id,
                "query_type": session.get("query_type"),
                "query_text": query_text,
                "approved_kol_pool_ids": kol_pool_ids,
            },
        },
        "note": "draft from smart-search session",
    }
    created = create_project(create_body, staff=staff)
    project_id = _int(created.get("id"))

    attached = 0
    kol_attach_warning = ""
    missing_kol_pool_ids: list[int] = []
    if project_id:
        try:
            res = add_project_kols(project_id, {"kol_pool_ids": kol_pool_ids}, staff=staff)
            if isinstance(res, dict):
                # A concurrent retry may have attached the same KOLs after
                # create_project committed and released the session row lock.
                # Existing assignments still count as truthfully attached.
                attached = _int(res.get("inserted")) + _int(res.get("skipped_existing"))
                missing_kol_pool_ids = [
                    _int(value)
                    for value in res.get("missing_kol_pool_ids") or []
                    if _int(value)
                ]
                if missing_kol_pool_ids:
                    kol_attach_warning = (
                        "KOL pool items no longer exist: "
                        + ",".join(str(value) for value in missing_kol_pool_ids)
                    )
        except ValueError as exc:
            # 占用冲突等 → 诚实降级:草案已建,KOL 未挂,带回原因供前端提示。
            kol_attach_warning = str(exc)

    # 回写会话:链到草案项目(让会话显示已转草案);沿用既有 summary 合并,会话状态不动。
    try:
        kol_search_sessions.update_session_result_summary(
            int(session_id),
            status=str(session.get("status") or "ready"),
            summary_patch={
                "draft_project": {
                    "project_id": project_id,
                    "project_uid": created.get("project_uid"),
                    "attached_kol_count": attached,
                    "requested_kol_count": len(kol_pool_ids),
                    "missing_kol_pool_ids": missing_kol_pool_ids,
                    "kol_attach_warning": kol_attach_warning,
                }
            },
        )
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass

    return {
        "ok": bool(project_id),
        "reused": False,
        "project_id": project_id,
        "project_uid": created.get("project_uid"),
        "project_name": project_name,
        "stage": created.get("stage"),
        "attached_kol_count": attached,
        "requested_kol_count": len(kol_pool_ids),
        "missing_kol_pool_ids": missing_kol_pool_ids,
        "kol_attach_warning": kol_attach_warning,
        "brief": brief,
        "cost_estimate": cost_estimate,
    }


def update_project(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Update editable project profile fields without touching stage transitions."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    now = utcnow()
    updates: dict[str, Any] = {}

    field_map = {
        "project_name": ("project_name", "name", "projectName"),
        "product_sku": ("product_sku", "productSku"),
        "product_name": ("product_name", "productName"),
        "platform": ("platform",),
        "marketplace": ("marketplace",),
        "priority": ("priority",),
        "source_type": ("source_type", "sourceType"),
        "shopify_discount_code": ("shopify_discount_code", "shopifyDiscountCode"),
        "shopify_link": ("shopify_link", "shopifyLink", "shopify_url"),
        "amazon_asin": ("amazon_asin", "amazonAsin"),
        "amazon_attribution_link": ("amazon_attribution_link", "amazonAttributionLink"),
        "amazon_associates_link": ("amazon_associates_link", "amazonAssociatesLink"),
        "sample_status": ("sample_status", "sampleStatus"),
        "tracking_number": ("tracking_number", "trackingNumber"),
        "target_post_date": ("target_post_date", "targetPostDate"),
        "due_at": ("due_at", "dueAt"),
        "follow_status": ("follow_status", "followStatus"),
    }
    for column, keys in field_map.items():
        for key in keys:
            if key in body:
                updates[column] = str(body.get(key) or "").strip()
                break

    if "project_name" in updates and not updates["project_name"]:
        raise ValueError("project_name required")
    if "follow_status" in updates and updates["follow_status"] not in {"active", "paused"}:
        raise ValueError("follow_status must be active or paused")

    if "assigned_staff_id" in body or "assignedStaffId" in body:
        assigned_staff_id = _int(body.get("assigned_staff_id") or body.get("assignedStaffId"))
        if scope.can_view_all(staff):
            updates["assigned_staff_id"] = assigned_staff_id or None

    products = _normalize_project_products(body)
    metadata = _loads(row["metadata_json"])
    metadata_changed = False
    if products:
        primary_product = products[0]
        updates["product_sku"] = primary_product.get("product_sku") or updates.get("product_sku") or ""
        updates["product_name"] = primary_product.get("product_name") or updates.get("product_name") or ""
        metadata["products"] = products
        metadata["product_skus"] = [item["product_sku"] for item in products]
        metadata_changed = True
    if isinstance(body.get("metadata"), dict):
        metadata.update(body["metadata"])
        metadata_changed = True
    if metadata_changed:
        updates["metadata_json"] = _json(metadata)

    # P2:note 是契约里的死字段(vkpi_projects 无对应列)。收到时落一条业务 audit,
    # 不再静默丢弃;不阻塞主更新流程(_log_project_audit 自身 best-effort)。
    note = str(body.get("note") or "").strip()
    if note:
        _log_project_audit(
            staff=staff,
            action_type="project_update_note",
            project_id=int(project_id),
            detail=note[:240],
            metadata={"note": note[:2000]},
        )

    if not updates:
        return {"id": int(project_id), "status": "unchanged"}

    updates["updated_at"] = now
    assignments = ", ".join(f"{column}=?" for column in updates)
    conn.execute(
        f"UPDATE vkpi_projects SET {assignments} WHERE id=?",
        (*updates.values(), int(project_id)),
    )
    conn.commit()
    _log_project_audit(
        staff=staff,
        action_type="project_update",
        project_id=int(project_id),
        detail=str(updates.get("project_name") or row["project_name"] or "")[:240],
        metadata={
            "updated_fields": sorted(updates.keys()),
            "product_skus": [item["product_sku"] for item in products],
        },
    )
    result = {"id": int(project_id), "status": "updated", "updated_fields": sorted(updates.keys())}
    if "follow_status" in updates:
        result["follow_status"] = updates["follow_status"]
    return result


# KOL availability + attach operations moved to workflow_projects_kols.py
# (function bodies unchanged → behavior unchanged). create_project_draft_from_session
# above calls add_project_kols via this re-exported name.
from app.domains.projects.workflow_projects_kols import (  # noqa: E402
    add_project_kols,
    list_available_project_kols,
)


def transition_project(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    to_stage = normalize_stage(str(body.get("to_stage") or body.get("stage") or ""))
    if to_stage not in PROJECT_STAGES:
        raise ValueError("unsupported stage")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    now = utcnow()
    from_stage = str(row["stage"] or "")
    _validate_transition(from_stage, to_stage, body)
    actor_staff_id = staff_id(staff)
    stage_status = "closed" if to_stage in TERMINAL_STAGES else str(body.get("stage_status") or row["stage_status"] or "active")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    tracking_number = str(body.get("tracking_number") or metadata.get("tracking_number") or row["tracking_number"] or "")
    sample_status = str(body.get("sample_status") or metadata.get("sample_status") or row["sample_status"] or "")
    if to_stage == "shipped":
        sample_status = sample_status or "shipped"
    closed_at = now if to_stage in TERMINAL_STAGES else row["closed_at"]
    conn.execute(
        """
        UPDATE vkpi_projects
        SET stage=?, stage_status=?, sample_status=?, tracking_number=?,
            closed_at=?, last_activity_at=?, updated_at=?
        WHERE id=?
        """,
        (to_stage, stage_status, sample_status, tracking_number, closed_at, now, now, int(project_id)),
    )
    conn.execute(
        """
        INSERT INTO vkpi_project_stage_events
            (project_id, from_stage, to_stage, event_type, actor_staff_id, note,
             source_ref_type, source_ref_id, effective_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            from_stage,
            to_stage,
            str(body.get("event_type") or "stage_change"),
            actor_staff_id or None,
            str(body.get("note") or ""),
            str(body.get("source_ref_type") or ""),
            str(body.get("source_ref_id") or ""),
            now,
            _json(body.get("metadata")),
            now,
        ),
    )
    auto_cost_result: dict[str, Any] | None = None
    if to_stage == "shipped":
        from app.domains import costs

        try:
            auto_cost_result = costs.record_shipped_product_cost(int(project_id), staff=staff)
        except Exception as exc:  # cost catalog issues must not block workflow progress
            auto_cost_result = {"status": "error", "reason": str(exc)}
    conn.commit()
    # 乙案④(释放双轨之自动轨):项目推进入终态时,认领人=assignment 负责人的 active claim
    # 自动释放(claims.auto_release_claims_for_project 内部 audit 留痕);失败不阻塞推进。
    claim_auto_release: dict[str, Any] | None = None
    if to_stage in TERMINAL_STAGES:
        try:
            from app.domains.kol import claims as kol_claims

            claim_auto_release = kol_claims.auto_release_claims_for_project(
                int(project_id), to_stage=to_stage, actor_staff_id=actor_staff_id
            )
        except Exception as exc:  # claim release must not block workflow progress
            logger.warning("vkpi.claim_auto_release_failed", extra={"project_id": project_id, "error": str(exc)})
            claim_auto_release = {"status": "error", "reason": str(exc)}
    _log_project_audit(
        staff=staff,
        action_type="project_stage_transition",
        project_id=int(project_id),
        detail=f"{from_stage} -> {to_stage}",
        metadata={
            "from_stage": from_stage,
            "to_stage": to_stage,
            "stage_status": stage_status,
            "event_type": str(body.get("event_type") or "stage_change"),
            "sample_status": sample_status,
            "tracking_number": tracking_number,
            "source_ref_type": str(body.get("source_ref_type") or ""),
            "source_ref_id": str(body.get("source_ref_id") or ""),
            "auto_product_cost": auto_cost_result,
            "claim_auto_release": claim_auto_release,
        },
    )
    return {
        "id": int(project_id),
        "from_stage": from_stage,
        "to_stage": to_stage,
        "auto_product_cost": auto_cost_result,
        "claim_auto_release": claim_auto_release,
    }

def delete_project(project_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Soft-delete a project while preserving attribution, cost, and audit history."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    payload = body or {}
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_projects WHERE id=?", (int(project_id),)).fetchone()
    if not row:
        raise LookupError("project not found")
    project = dict(row)
    if str(project.get("stage_status") or "") == "deleted":
        return {"id": int(project_id), "status": "already_deleted"}
    now = utcnow()
    actor_staff_id = staff_id(staff)
    from_stage = str(project.get("stage") or "")
    reason = str(payload.get("reason") or payload.get("note") or "项目删除").strip()
    metadata = _loads(project.get("metadata_json"))
    metadata["deleted"] = {
        "deleted_at": now,
        "deleted_by_staff_id": actor_staff_id or None,
        "reason": reason,
        "previous_stage": from_stage,
        "previous_stage_status": str(project.get("stage_status") or ""),
    }
    conn.execute(
        """
        UPDATE vkpi_projects
        SET stage='cancelled', stage_status='deleted', closed_at=?, last_activity_at=?,
            metadata_json=?, updated_at=?
        WHERE id=?
        """,
        (now, now, _json(metadata), now, int(project_id)),
    )
    conn.execute(
        """
        INSERT INTO vkpi_project_stage_events
            (project_id, from_stage, to_stage, event_type, actor_staff_id, note,
             source_ref_type, source_ref_id, effective_at, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(project_id),
            from_stage,
            "cancelled",
            "deleted",
            actor_staff_id or None,
            reason,
            "project",
            str(project_id),
            now,
            _json({"delete": True, "previous_stage_status": project.get("stage_status")}),
            now,
        ),
    )
    conn.execute(
        "UPDATE vkpi_links SET status='paused', updated_at=? WHERE project_id=? AND status='live'",
        (now, int(project_id)),
    )
    conn.commit()
    # 乙案④:软删=进 cancelled 终态,同样触发认领自动释放(失败不阻塞删除)。
    claim_auto_release: dict[str, Any] | None = None
    try:
        from app.domains.kol import claims as kol_claims

        claim_auto_release = kol_claims.auto_release_claims_for_project(
            int(project_id), to_stage="cancelled", actor_staff_id=actor_staff_id
        )
    except Exception as exc:
        logger.warning("vkpi.claim_auto_release_failed", extra={"project_id": project_id, "error": str(exc)})
        claim_auto_release = {"status": "error", "reason": str(exc)}
    _log_project_audit(
        staff=staff,
        action_type="project_delete",
        project_id=int(project_id),
        detail=reason,
        metadata={
            "previous_stage": from_stage,
            "previous_stage_status": project.get("stage_status"),
            "reason": reason,
            "paused_live_links": True,
            "claim_auto_release": claim_auto_release,
        },
    )
    return {"id": int(project_id), "status": "deleted", "previous_stage": from_stage, "claim_auto_release": claim_auto_release}
