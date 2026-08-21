"""P3: single additive MY KOL aggregate read-endpoint.

GET /api/admin/vkpi/my-kol/aggregate?staff_id=&window_days=

One read-only call that returns a staff member's full MY KOL payload (staff row,
official channel matrix, pool favorites, projects, claims, kpi summary). Additive:
the frontend MyKolPage keeps its current calls until it adopts this later.

RBAC: require_tab("vkpi","read"). Employees (non can_view_all) only ever get
their own aggregate; managers may pass ?staff_id= to view a specific member,
defaulting to themselves.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.core.release_validation import release_validation_active
from app.db.connection import get_conn
from app.domains import business_truth
from app.domains.access import scope
from app.domains.audit.decorator import audit_action
from app.domains.kol import (
    my_kol_aggregate,
    my_kol_board_ext,
    risk_index,
    video_tracking,
)
from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-my-kol"])
logger = get_logger(__name__)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _staff_name_without_email(value: Any, *, default: str = "Staff") -> str:
    """Return a display label without using an email-shaped fallback."""
    name = str(value or "").strip()
    return default if not name or "@" in name else name


def _rollback_quietly(conn: Any) -> None:
    try:
        conn.rollback()
    except Exception as exc:
        logger.warning("MY KOL video tracking rollback failed: %s", type(exc).__name__)


def _raise_video_tracking_error(conn: Any, exc: video_tracking.VideoTrackingError) -> None:
    _rollback_quietly(conn)
    raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def _content_monitoring_error(exc: Exception) -> HTTPException:
    status_code = int(getattr(exc, "status_code", 400) or 400)
    code = str(getattr(exc, "code", "content_monitoring_failed") or "content_monitoring_failed")
    return HTTPException(status_code=status_code, detail=code)


@router.get("/my-kol/{kol_pool_id}/content-monitoring")
def my_kol_content_monitoring_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Pure read of explicit recent-content monitoring state; never enqueues."""

    from app.domains.kol import content_monitoring

    try:
        return content_monitoring.get_content_monitoring(
            int(kol_pool_id),
            staff=staff,
        )
    except (content_monitoring.ContentMonitoringError, MyKolPaidActionError) as exc:
        raise _content_monitoring_error(exc) from exc


@router.post("/my-kol/{kol_pool_id}/content-monitoring", status_code=202)
@audit_action(
    action_type="kol_content_monitoring_enable",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"content monitoring status={(result or {}).get('status')}",
)
def my_kol_content_monitoring_enable_endpoint(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Explicitly enable/resume one caller-owned subscription; queue remains separate."""

    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")
    from app.domains.kol import content_monitoring

    try:
        return content_monitoring.enable_content_monitoring(
            int(kol_pool_id),
            cadence_hours=body.get("cadence_hours"),
            staff=staff,
        )
    except (content_monitoring.ContentMonitoringError, MyKolPaidActionError) as exc:
        raise _content_monitoring_error(exc) from exc


@router.delete("/my-kol/{kol_pool_id}/content-monitoring")
@audit_action(
    action_type="kol_content_monitoring_pause",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"content monitoring status={(result or {}).get('status')}",
)
def my_kol_content_monitoring_pause_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Pause the caller's subscription and invalidate any queued generation."""

    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")
    from app.domains.kol import content_monitoring

    try:
        return content_monitoring.pause_content_monitoring(
            int(kol_pool_id),
            staff=staff,
        )
    except (content_monitoring.ContentMonitoringError, MyKolPaidActionError) as exc:
        raise _content_monitoring_error(exc) from exc


@router.post("/my-kol/{kol_pool_id}/videos", status_code=202)
def my_kol_track_video_endpoint(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Link SKUs and queue metrics for an existing MY KOL video evidence row.

    The HTTP request is queue-only: it never resolves media or calls a
    provider.  Unseen URLs return an explicit next-slice error until a targeted
    resolver can prove that the remote author is this path's ``kol_pool_id``.
    """

    conn = get_conn()
    try:
        result = video_tracking.queue_tracked_video(
            conn,
            kol_pool_id=int(kol_pool_id),
            content_url=body.get("content_url") or body.get("url"),
            product_skus=body.get("product_skus"),
            staff=staff,
        )
        conn.commit()
    except video_tracking.VideoTrackingError as exc:
        _raise_video_tracking_error(conn, exc)
    except Exception:
        _rollback_quietly(conn)
        raise
    return result


@router.post(
    "/my-kol/{kol_pool_id}/videos/{evidence_id}/refresh",
    status_code=202,
)
def my_kol_refresh_video_endpoint(
    kol_pool_id: int,
    evidence_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue one existing evidence metric refresh; provider work stays in worker."""

    conn = get_conn()
    try:
        result = video_tracking.queue_evidence_refresh(
            conn,
            kol_pool_id=int(kol_pool_id),
            evidence_id=int(evidence_id),
            staff=staff,
            # Card-level "刷新指标" is one-shot.  Only the explicit
            # "追踪已有视频" flow creates/reactivates a durable subscription.
            register_tracking=False,
        )
        conn.commit()
    except video_tracking.VideoTrackingError as exc:
        _raise_video_tracking_error(conn, exc)
    except Exception:
        _rollback_quietly(conn)
        raise
    return result


@router.get("/my-kol/aggregate")
def my_kol_aggregate_endpoint(
    staff_id: int | None = Query(default=None, ge=1),
    window_days: int = Query(default=30, ge=1, le=365),
    scope_mode: str = Query(default="", alias="scope"),
    mode: str = Query(default="full", pattern="^(full|summary)$"),
    favorites_limit: int = Query(default=50, ge=1, le=100),
    favorites_cursor: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return the single MY KOL aggregate bundle for the scoped staff member."""
    # 修(2026-06-16):owner/manager(can_view_all=True)自查(未显式传 staff_id)时
    # effective_staff_id 返回 None('全部'语义)→ 旧逻辑 403,导致 owner 收藏写进库却永远读不到。
    # 自查场景回落到本人 actor id(显式传 staff_id 仍走 manager 跨看)。
    # 增量(2026-07-12)?scope=team:管理层(can_view_all)全团队收藏集口径 ——
    # 与 board-ext 管理层缺省同集合(收藏 ∪ 共享,去重)。权限判定同源 can_view_all:
    # 员工传了也只拿自己的(team 恒 False,后端硬闸);显式 ?staff_id= 优先(跨看单人)。
    team = scope_mode == "team" and staff_id is None and scope.can_view_all(staff)
    target = scope.effective_staff_id(staff, staff_id) or scope.actor_staff_id(staff)
    if not target:
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    try:
        paging = (
            {
                "favorites_limit": int(favorites_limit),
                "favorites_cursor": favorites_cursor,
            }
            if mode == "summary"
            else {}
        )
        return my_kol_aggregate.build_my_kol_aggregate(
            get_conn(),
            int(target),
            window_days=int(window_days),
            actor=staff,
            team_scope=bool(team),
            **paging,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/my-kol/board-ext")
def my_kol_board_ext_endpoint(
    days: int = Query(default=30, ge=1, le=365),
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """【M2】MY KOL 改版看板聚合(七组读数,一次调用全给齐;纯 SELECT 零副作用)。

    kpi_series(收藏集粉丝/新视频/官号粉丝/官号播放时序 + KOL 播放点时读数)、
    funnel(13 真阶段归并 8 段)、platform_dist、fit_dist(只出桶计数)、
    contact_coverage(只出类型计数,绝不出明文联系方式)、views_top、v_content
    (三档派生)。口径细节全在 kol/my_kol_board_ext.py 各组 basis。

    scope 与本路由家族同款 viewer/own-only 口径:员工经 scope.effective_staff_id
    恒被压回本人(own-only);管理层缺省 None=全团队收藏集看板,显式 ?staff_id=
    看指定成员(与 dashboard/summary 聚合的 effective_staff_id 语义一致——本端点
    是团队看板聚合而非个人收藏清单,故管理层自查不回落 actor,与 aggregate 有别)。
    无 staff 身份的非管理层拒 403(防 scope 漏成全量)。
    红线:纯读;零写 viltrox_fit_score / 不碰 rule_v0;零 LLM;全程 ? 占位。
    """
    target = scope.effective_staff_id(staff, staff_id)
    if target is None and not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    body = my_kol_board_ext.build_board_ext(
        get_conn(), staff_scope_id=target, days=int(days)
    )
    body["scope"] = scope.scope_context(staff, staff_id)
    return body


@router.get("/my-kol/risk-index")
def my_kol_risk_index_endpoint(
    staff_id: int | None = Query(default=None, ge=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C6 风险指数(候选 A·实时聚合,零新 LLM)。

    对该 staff 的 MY KOL 集(收藏 + P-GROUP-7 共享进来的池 KOL,口径完全复用
    my_kol_aggregate._pool_favorites)逐 KOL 算风险分 + 维度拆解。数据源唯一:
    vkpi_kol_llm_deep_analysis_results.llm_dimensions_11(Gemini final_v1,
    analysis_kind='video_final_v1' status='ready')的结构化深析信号(非关键词 SQL)。

    风险 = w1*内容无深度((100-content_quality)/100) + w2*素材复用(asset_reuse,
    方向未确认) + w3*竞品露出惩罚(否定感知文本判定,口径同 dashboard/summary.py)。

    **只覆盖已深析 KOL**;未深析 KOL 返回 analyzed=false / risk_index=null
    (前端显「未分析」灰态),绝不当 0 风险。

    纯只读 SELECT(? 占位),不触 viltrox_fit_score / rule_v0 / 评分指纹,不调 LLM。

    RBAC 与 aggregate 端点同口径:require_tab('vkpi','read') 先 gate;owner/manager
    自查(未显式传 staff_id)回落本人 actor id,显式传 staff_id 走 manager 跨看;
    非管理层只能看自己(effective_staff_id 已强制)。
    """
    target = scope.effective_staff_id(staff, staff_id) or scope.actor_staff_id(staff)
    if not target:
        raise HTTPException(status_code=403, detail="no staff identity in scope")
    conn = get_conn()
    try:
        # 复用 aggregate 的 MY KOL 集解析(收藏 + 共享),保证「谁的 KOL」口径完全一致。
        kols = my_kol_aggregate._pool_favorites(conn, int(target))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    payload = risk_index.compute_risk_index(conn, kols)
    payload["staff_id"] = int(target)
    payload["scope"] = scope.scope_context(staff)
    return payload


@router.get("/my-kol/contribution-rollup")
def my_kol_contribution_rollup_endpoint(
    window_days: int = Query(default=90, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """P-KOL-5 管理层 KOL 贡献度聚合(每个负责人一行,各指标分列)。

    只读 SELECT。按 vkpi_kol_claims.staff_id 分组,分列输出每个负责人的:
      - staff_id / staff_name(staff→users.name)
      - active_kol_count:在管 KOL 数(status='active' 的 claim 计数)
      - published_count:已发布内容数(JOIN vkpi_content_posts 到该负责人在管的 KOL,
        过去 window_days 内 published_at 命中;有真数据就出真数,无则 0)
      - attributed_sales_usd:归因销售(SUM vkpi_sales_attributions.revenue_cents/100,
        按 KOL 关联到该负责人在管 KOL;无归因数据则 0)

    口径:active 归属由 idx_vkpi_kol_claims_one_active(WHERE status='active')保证「一个
    KOL 一个 active 归属」,故按 claim.staff_id 聚合即等于「该负责人在管 KOL 集」,无重复计。

    RBAC:require_tab('vkpi','read') 先 gate tab;管理层语义再用 scope.can_view_all
    (owner / vkpi=admin / manager 集)二次 gate——这是「管理层 KOL 贡献度」面板,
    普通成员看不到全员排布,返回 403。

    日期 cutoff 在 Python 算后作参数传(避开 % / INTERVAL 翻译层脆弱);全程 ? 占位。
    """
    # 管理层 gate:复用既有 can_view_all(不引入新角色判定),非管理层拒。
    if not scope.can_view_all(staff, domain="general"):
        raise HTTPException(status_code=403, detail="manager scope required")

    days = max(1, min(_int(window_days, 90), 365))
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 2026-07-18 审计修:无Z裸isoformat被PG按会话时区解析
    conn = get_conn()

    # 主聚合:一个负责人一行。
    #   active_kol_count = 该负责人 active claim 数(=在管 KOL 数,one_active 保证不重复)。
    #   published_count  = 这些 KOL 在 window 内的已发布内容数(LEFT JOIN,无内容则 0)。
    # staff_name 走 staff→users.name(与 claim_listing.list_claims 同一 JOIN 口径)。
    rows = conn.execute(
        """
        SELECT
            c.staff_id AS staff_id,
            u.name AS staff_name,
            u.email AS staff_email,
            COUNT(DISTINCT c.kol_id) AS active_kol_count,
            COUNT(cp.id) AS published_count
        FROM vkpi_kol_claims c
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        LEFT JOIN vkpi_content_posts cp
            ON cp.kol_id = c.kol_id
            AND cp.published_at IS NOT NULL
            AND cp.published_at >= ?
        WHERE c.status = 'active'
        GROUP BY c.staff_id, u.name, u.email
        ORDER BY active_kol_count DESC, c.staff_id ASC
        """,
        (cutoff,),
    ).fetchall()

    # 归因销售单独一支(避免与 content_posts 同表 JOIN 造成笛卡尔放大计数)。
    #   按「该负责人在管 active KOL」关联 vkpi_sales_attributions.kol_id,SUM revenue_cents。
    sales_rows = conn.execute(
        f"""
        SELECT
            c.staff_id AS staff_id,
            COALESCE(SUM(sa.revenue_cents), 0) AS revenue_cents,
            COUNT(sa.id) AS attribution_count
        FROM vkpi_kol_claims c
        JOIN vkpi_sales_attributions sa ON sa.kol_id = c.kol_id
        WHERE c.status = 'active'
          AND {business_truth.verified_shopify_attribution_sql('sa')}
        GROUP BY c.staff_id
        """,
    ).fetchall()
    sales_by_staff = {
        _int(dict(r).get("staff_id")): dict(r) for r in sales_rows
    }

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        sid = _int(item.get("staff_id"))
        sale = sales_by_staff.get(sid)
        revenue_cents = _int(sale.get("revenue_cents")) if sale else 0
        attribution_count = _int(sale.get("attribution_count")) if sale else 0
        items.append(
            {
                "staff_id": sid or None,
                "staff_name": item.get("staff_name") or None,
                "staff_email": item.get("staff_email") or None,
                "active_kol_count": _int(item.get("active_kol_count")),
                # 已发布数:有 content_posts 关联就给真数;否则 0(诚实,无 null 模糊)。
                "published_count": _int(item.get("published_count")),
                # 归因销售:有 attribution 行就出 USD;完全无归因数据时 amount=0 且
                # has_attribution=False,前端据此显示「— / 待接入」。
                "attributed_sales_usd": round(revenue_cents / 100.0, 2),
                "attribution_count": attribution_count,
                "has_attribution": attribution_count > 0,
            }
        )

    return {
        "items": items,
        "window_days": days,
        "scope": scope.scope_context(staff),
        # 数据可得性标注:让前端诚实区分「真有 0」与「该指标尚未接入数据源」。
        "data_notes": {
            "published_count": "来自 vkpi_content_posts.published_at(window 内);无内容记录则为 0",
            "attributed_sales_usd": "来自 vkpi_sales_attributions(按 KOL 关联);无归因数据时为 0 且 has_attribution=false",
        },
    }


# ---------------------------------------------------------------------------
# P-GROUP-7 共享 KOL 池写端点(ADDITIVE,2026-06-16)。
# 表 vkpi_kol_pool_members(迁移 159):把某 kol_pool_id 显式共享给某 staff_id(只读授予)。
# scope.member_shared_kol_ids + my_kol_aggregate._pool_favorites 已据本表把「共享进来的池
# KOL」自动并入接收方 MY KOL 视图(只读,is_shared=true)。这里只补「写/撤销/列出已共享给谁」。
# 红线:只触 vkpi_kol_pool_members 这张隔离表;绝不读写 viltrox_fit_score / rule_v0 / 归属。
# 全程 '?' 占位(方言层翻译);commit + 错误处理对齐 project_members。
# ---------------------------------------------------------------------------


def _kol_pool_claim_owner(conn, kol_pool_id: int) -> int | None:
    """该 kol_pool_id 的 active claim 归属人 staff_id(经 linked_main_kol_id),无则 None。"""
    row = conn.execute(
        """
        SELECT c.staff_id AS sid
        FROM vkpi_kol_pool p
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        WHERE p.id = ?
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return _int(dict(row).get("sid")) if row else None


def _assert_can_share_kol(conn, staff, kol_pool_id: int) -> None:
    """归属校验:仅该 KOL 的 active claim 归属人 或 管理层(can_view_all)可共享/撤销——
    与项目侧 assert_can_manage_members 同口径,避免任意 vkpi:write 转授/撤销他人 KOL。"""
    if scope.can_view_all(staff):
        return
    actor = scope.actor_staff_id(staff)
    owner = _kol_pool_claim_owner(conn, kol_pool_id)
    if not actor or owner != actor:
        raise HTTPException(status_code=403, detail="仅该 KOL 的负责人或管理层可共享/撤销")


@router.post("/my-kol/{kol_pool_id}/share")
def my_kol_share_endpoint(
    kol_pool_id: int,
    body: dict = Body(default={}),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """把某 kol_pool_id 共享给 body.staff_id(只读授予)。幂等:UNIQUE 冲突即 DO NOTHING。

    归属校验:仅该 KOL 负责人/管理层可共享。shared_by 取当前操作者 staff_id。
    """
    pid = _int(kol_pool_id)
    target_staff_id = _int(body.get("staff_id"))
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")
    if target_staff_id <= 0:
        raise HTTPException(status_code=400, detail="staff_id required")

    conn = get_conn()
    _assert_can_share_kol(conn, staff, pid)
    shared_by = scope.actor_staff_id(staff) or None
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id, shared_by)
        VALUES (?, ?, ?)
        ON CONFLICT (kol_pool_id, staff_id) DO NOTHING
        """,
        (pid, target_staff_id, shared_by),
    )
    conn.commit()
    return {
        "status": "shared",
        "kol_pool_id": pid,
        "staff_id": target_staff_id,
        "shared_by": shared_by,
    }


@router.delete("/my-kol/{kol_pool_id}/share/{staff_id}")
def my_kol_unshare_endpoint(
    kol_pool_id: int,
    staff_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """撤销共享:删 (kol_pool_id, staff_id) 一行。仅触本表,不改归属/收藏/认领。"""
    pid = _int(kol_pool_id)
    target_staff_id = _int(staff_id)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")
    if target_staff_id <= 0:
        raise HTTPException(status_code=400, detail="staff_id required")

    conn = get_conn()
    _assert_can_share_kol(conn, staff, pid)
    conn.execute(
        "DELETE FROM vkpi_kol_pool_members WHERE kol_pool_id = ? AND staff_id = ?",
        (pid, target_staff_id),
    )
    conn.commit()
    return {"status": "removed", "kol_pool_id": pid, "staff_id": target_staff_id}


# ---------------------------------------------------------------------------
# C2 团队共享管理(集中管控,2026-07-02):看全部共享关系 + 按 share id 撤销。
# 与上面的 per-KOL 弹窗端点(/{kol_pool_id}/share*)互补:那组按单个 KOL 增删,
# 这组给 TeamModal「共享管理」区一个全局视图(谁把哪个 KOL 分享给了谁)。
# 红线:只触 vkpi_kol_pool_members / vkpi_collab_settings(读)两张隔离表;
# 绝不读写 viltrox_fit_score / rule_v0 / 归属。全程 '?' 占位(方言层翻译)。
# ---------------------------------------------------------------------------


@router.get("/my-kol/shares")
def my_kol_shares_list_endpoint(
    limit: int = Query(default=200, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """团队 KOL 共享关系总列表(集中管控)。

    - 管理层(can_view_all)看全部;普通成员只看「自己发出 + 自己收到」的行。
    - JOIN staff→users 两次只取分享人/接收人展示名，不返回邮箱;
      JOIN vkpi_kol_pool 取 KOL 展示名/平台/handle(display_name 空串回落 handle)。
    - 协作设置:LEFT JOIN vkpi_collab_settings(kind='kol',target_id=kol_pool_id 文本),
      未设过诚实回空串(不伪造)。
    - shared_via_group_id 非 NULL = 该行由分组共享展开产生(迁移 161;改组即重算,
      单行撤销后组重算可能恢复,前端据此提示)。
    - can_revoke:管理层恒可撤;普通成员仅可撤自己发出的行(与 DELETE 权限同口径)。
    纯 SELECT,无副作用。
    """
    conn = get_conn()
    view_all = scope.can_view_all(staff)
    actor = scope.actor_staff_id(staff)
    if not view_all and not actor:
        raise HTTPException(status_code=403, detail="no staff identity in scope")

    base_sql = """
        SELECT m.id,
               m.kol_pool_id,
               m.staff_id AS to_staff_id,
               m.shared_by AS from_staff_id,
               m.shared_via_group_id,
               m.created_at,
               COALESCE(NULLIF(ut.name, ''), 'Staff') AS to_name,
               CASE WHEN m.shared_by IS NULL THEN ''
                    ELSE COALESCE(NULLIF(uf.name, ''), 'Staff') END AS from_name,
               COALESCE(NULLIF(p.display_name, ''), p.handle, '') AS kol_name,
               COALESCE(p.platform, '') AS platform,
               COALESCE(p.handle, '') AS handle,
               COALESCE(cs.shared_goal, '') AS shared_goal,
               COALESCE(cs.reminder_rule, '') AS reminder_rule
        FROM vkpi_kol_pool_members m
        LEFT JOIN staff st_to ON st_to.id = m.staff_id
        LEFT JOIN users ut ON ut.id = st_to.user_id
        LEFT JOIN staff st_from ON st_from.id = m.shared_by
        LEFT JOIN users uf ON uf.id = st_from.user_id
        LEFT JOIN vkpi_kol_pool p ON p.id = m.kol_pool_id
        LEFT JOIN vkpi_collab_settings cs
            ON cs.kind = 'kol' AND cs.target_id = CAST(m.kol_pool_id AS TEXT)
    """
    if view_all:
        rows = conn.execute(
            base_sql + " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    else:
        rows = conn.execute(
            base_sql
            + " WHERE m.shared_by = ? OR m.staff_id = ?"
            + " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
            (int(actor), int(actor), int(limit)),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        created_at = item.get("created_at")
        if created_at is not None and not isinstance(created_at, str):
            created_at = str(created_at)
        from_staff_id = _int(item.get("from_staff_id")) or None
        items.append(
            {
                "id": _int(item.get("id")) or None,
                "kol_pool_id": _int(item.get("kol_pool_id")) or None,
                "to_staff_id": _int(item.get("to_staff_id")) or None,
                "to_name": _staff_name_without_email(item.get("to_name")),
                "from_staff_id": from_staff_id,
                # shared_by 可空容旧(159 注释):空时诚实回空串,前端显「未知」。
                "from_name": _staff_name_without_email(
                    item.get("from_name"),
                    default="" if from_staff_id is None else "Staff",
                ),
                "kol_name": item.get("kol_name") or "",
                "platform": item.get("platform") or "",
                "handle": item.get("handle") or "",
                "created_at": created_at,
                "shared_via_group_id": _int(item.get("shared_via_group_id")) or None,
                "shared_goal": item.get("shared_goal") or "",
                "reminder_rule": item.get("reminder_rule") or "",
                "can_revoke": bool(view_all or (actor and from_staff_id == actor)),
            }
        )

    return {"items": items, "count": len(items), "scope_all": bool(view_all)}


@router.delete("/my-kol/shares/{share_id}")
def my_kol_share_revoke_endpoint(
    share_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """按 share id 撤销一条共享(集中管控用)。权限=分享人本人 或 管理层(can_view_all)。

    与 per-KOL 的 DELETE /{kol_pool_id}/share/{staff_id}(负责人口径)互补:这里按行 id 删,
    供「共享管理」列表一键撤销。只删 vkpi_kol_pool_members 一行,不改归属/收藏/认领。
    读路径(my_kol_aggregate / scope.member_shared_kol_ids)均为实时 SELECT 无缓存层,
    删后无需失效缓存。
    """
    sid = _int(share_id)
    if sid <= 0:
        raise HTTPException(status_code=400, detail="share_id required")

    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, kol_pool_id, staff_id, shared_by, shared_via_group_id
        FROM vkpi_kol_pool_members
        WHERE id = ?
        """,
        (sid,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="share not found")
    item = dict(row)

    if not scope.can_view_all(staff):
        actor = scope.actor_staff_id(staff)
        if not actor or _int(item.get("shared_by")) != actor:
            raise HTTPException(status_code=403, detail="仅分享人本人或管理层可撤销该共享")

    conn.execute("DELETE FROM vkpi_kol_pool_members WHERE id = ?", (sid,))
    conn.commit()

    via_group = _int(item.get("shared_via_group_id")) or None
    return {
        "status": "revoked",
        "id": sid,
        "kol_pool_id": _int(item.get("kol_pool_id")) or None,
        "staff_id": _int(item.get("staff_id")) or None,
        "shared_via_group_id": via_group,
        # 诚实提示:分组展开行删掉后,该分组下次重算(编辑组/共享配置)会重建此行。
        "note": "该行由分组共享产生,分组重算后可能恢复;永久移除请编辑分组共享配置。" if via_group else "",
    }


@router.get("/my-kol/{kol_pool_id}/share/members")
def my_kol_share_members_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """列「该 kol_pool_id 已共享给的成员」，仅负责人或管理层可读。

    纯 SELECT,无副作用；响应只含展示名，不读取或返回邮箱。
    """
    pid = _int(kol_pool_id)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")

    conn = get_conn()
    _assert_can_share_kol(conn, staff, pid)
    rows = conn.execute(
        """
        SELECT m.id,
               m.kol_pool_id,
               m.staff_id,
               m.shared_by,
               m.created_at,
               COALESCE(NULLIF(u.name, ''), 'Staff') AS staff_name
        FROM vkpi_kol_pool_members m
        LEFT JOIN staff st ON st.id = m.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE m.kol_pool_id = ?
        ORDER BY m.created_at ASC, m.id ASC
        """,
        (pid,),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        created_at = item.get("created_at")
        if created_at is not None and not isinstance(created_at, str):
            created_at = str(created_at)
        items.append(
            {
                "id": _int(item.get("id")) or None,
                "kol_pool_id": _int(item.get("kol_pool_id")) or None,
                "staff_id": _int(item.get("staff_id")) or None,
                "shared_by": _int(item.get("shared_by")) or None,
                "created_at": created_at,
                "name": _staff_name_without_email(item.get("staff_name")),
            }
        )

    return {"kol_pool_id": pid, "count": len(items), "items": items}


@router.get("/my-kol/{kol_pool_id}/viewer-context")
def my_kol_viewer_context_endpoint(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """【M3/M5】KOL 详情抽屉的观看者上下文(纯 SELECT,零副作用)。

    - share_origin:该 kol_pool_id 是否经 vkpi_kol_pool_members(迁移 159)共享给「当前操作者」,
      带共享人展示名(staff→users,与 shares 列表同口径)——前端据此标「来自 XX 的共享」。
    - claim:该 KOL(经 linked_main_kol_id)当前 active 认领 + 认领人展示名;can_release =
      本人认领 或 管理层(can_view_all)——与 claims release 端点(claim_lifecycle.release)
      的放行口径一致,前端据此显示「释放」按钮。
    红线:只读 vkpi_kol_pool_members / vkpi_kol_claims / vkpi_kol_pool / staff / users;
    绝不读写 viltrox_fit_score / rule_v0,绝不改归属。全程 '?' 占位(方言层翻译)。
    """
    pid = _int(kol_pool_id)
    if pid <= 0:
        raise HTTPException(status_code=400, detail="kol_pool_id required")
    conn = get_conn()
    actor = scope.actor_staff_id(staff)
    from app.domains.kol.my_kol_paid_action_access import (
        MyKolPaidActionError,
        assert_target_readable,
        target_write_context,
    )

    try:
        assert_target_readable(conn, kol_pool_id=pid, staff=staff)
    except MyKolPaidActionError as exc:
        # Do not leak claim/staff identity for arbitrary pool ids.  Explicit
        # team membership remains a read-only grant.
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    paid_action = target_write_context(conn, kol_pool_id=pid, staff=staff)

    share_origin: dict[str, Any] | None = None
    if actor:
        row = conn.execute(
            """
            SELECT m.shared_by, m.created_at,
                   COALESCE(u.name, u.email, '') AS shared_by_name
            FROM vkpi_kol_pool_members m
            LEFT JOIN staff st ON st.id = m.shared_by
            LEFT JOIN users u ON u.id = st.user_id
            WHERE m.kol_pool_id = ? AND m.staff_id = ?
            LIMIT 1
            """,
            (pid, int(actor)),
        ).fetchone()
        if row:
            item = dict(row)
            created_at = item.get("created_at")
            if created_at is not None and not isinstance(created_at, str):
                created_at = str(created_at)
            share_origin = {
                "shared_by": _int(item.get("shared_by")) or None,
                # shared_by 可空容旧(159 注释):空时诚实回空串,前端显「未知成员」。
                "shared_by_name": item.get("shared_by_name") or "",
                "created_at": created_at,
            }

    claim: dict[str, Any] | None = None
    claim_row = conn.execute(
        """
        SELECT c.id, c.staff_id, c.claimed_at, c.expires_at,
               COALESCE(u.name, u.email, '') AS staff_name
        FROM vkpi_kol_pool p
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE p.id = ?
        LIMIT 1
        """,
        (pid,),
    ).fetchone()
    if claim_row:
        item = dict(claim_row)
        owner = _int(item.get("staff_id")) or None
        is_mine = bool(actor and owner and int(actor) == owner)
        claimed_at = item.get("claimed_at")
        expires_at = item.get("expires_at")
        if claimed_at is not None and not isinstance(claimed_at, str):
            claimed_at = str(claimed_at)
        if expires_at is not None and not isinstance(expires_at, str):
            expires_at = str(expires_at)
        claim = {
            "id": _int(item.get("id")) or None,
            "staff_id": owner,
            "staff_name": item.get("staff_name") or "",
            "claimed_at": claimed_at,
            "expires_at": expires_at,
            "is_mine": is_mine,
            "can_release": bool(is_mine or scope.can_view_all(staff)),
        }

    return {
        "kol_pool_id": pid,
        "share_origin": share_origin,
        "claim": claim,
        "paid_actions": paid_action,
    }
