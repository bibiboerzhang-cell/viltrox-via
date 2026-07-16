"""Recommendation outcome collector for self-learning prebuild."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains import business_truth
from app.shared.vkpi_utils import utcnow_iso
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema

NODE_COLUMNS = {
    "shortlisted": ("was_shortlisted", "shortlisted_at"),
    "rejected": ("was_rejected", "rejected_at"),
    "claimed": ("was_claimed", "claimed_at"),
    "project_created": ("project_created", "project_created_at"),
    "outreach_sent": ("outreach_sent", "outreach_sent_at"),
    "reply_received": ("reply_received", "reply_at"),
    "agreement_reached": ("agreement_reached", "agreement_at"),
    "content_published": ("content_published", "content_published_at"),
    "order_attributed": ("order_attributed", "first_order_at"),
}
logger = get_logger(__name__)


def _utcnow() -> str:
    return utcnow_iso()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def ensure_outcome(recommendation_id: int, *, kol_pool_id: int | None = None, launch_id: int | None = None, feature_snapshot: dict[str, Any] | None = None, scoring_breakdown: dict[str, Any] | None = None, model_version: str = "rule_v0", display_position: int | None = None, display_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (int(recommendation_id),)).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO vkpi_recommendation_outcomes
                (recommendation_id, kol_pool_id, launch_id, recommended_at, feature_snapshot_json,
                 scoring_breakdown_json, model_version, display_position, display_context_json)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (int(recommendation_id), kol_pool_id, launch_id, _utcnow(), _json(feature_snapshot), _json(scoring_breakdown), model_version, display_position, _json(display_context)),
        )
        conn.commit()
    return get_outcome(recommendation_id)


def record(recommendation_id: int, node: str, *, context: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    if node not in NODE_COLUMNS:
        raise ValueError(f"unsupported outcome node: {node}")
    ensure_outcome(recommendation_id)
    bool_col, time_col = NODE_COLUMNS[node]
    now = _utcnow()
    extra = ""
    params: list[Any] = [True, now]
    if node == "rejected" and note:
        extra = ", reject_reason=?"
        params.append(note)
    if node == "content_published" and context and context.get("content_url"):
        extra += ", content_url=?"
        params.append(str(context.get("content_url") or ""))
    params.append(int(recommendation_id))
    get_conn().execute(f"UPDATE vkpi_recommendation_outcomes SET {bool_col}=?, {time_col}=?{extra}, first_action_at=COALESCE(first_action_at, ?) WHERE recommendation_id=?", [params[0], params[1], *params[2:-1], now, params[-1]])
    get_conn().commit()
    return get_outcome(recommendation_id)


def get_outcome(recommendation_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (int(recommendation_id),)).fetchone()
    return {"outcome": dict(row) if row else None}


def _ids_clause(column: str, values: list[int]) -> tuple[str, list[int]]:
    ids = [int(value) for value in values if int(value or 0) > 0]
    if not ids:
        return "1=0", []
    return f"{column} IN ({','.join('?' for _ in ids)})", ids


def _first_timestamp(values: list[Any]) -> str | None:
    stamps = [str(value) for value in values if value]
    return min(stamps) if stamps else None


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if not row:
        return default
    try:
        return row[key]
    except Exception:
        return default


def _resolve_linked_kol_id(conn: Any, rec_dict: dict[str, Any], *, persist: bool = False) -> int:
    """打通 attribution→outcome 的连接键 linked_main_kol_id(幂等、只读真业务行、零伪造)。

    最致命断点:outcome 促升靠 rec.linked_main_kol_id join 销售/项目/消息归因,而该推荐多数
    从未被 claim/promote → 该列恒 NULL → join 全断 → outcome 空心。这里在已经存在 *高置信* 的
    真实桥(vkpi_kol_pool.linked_main_kol_id —— 由人工 promote/claim 落地的 kols.id)时,把它
    用作连接键。绝不凭 handle 文本模糊猜测、绝不新建桥:只搬运已被业务确认过的那一个
    kol_pool→kols 链接,所以不会把销售归到错的 KOL(红线安全)。

    persist=False(默认):**只在内存中**解析出 kol_id 供本次 refresh 的 join 用,
        绝不 UPDATE 既有 vkpi_kol_recommendations 行(尊重"批量改既有行先 dry-run 待审")。
    persist=True:把解析出的桥幂等回填到推荐行(`linked_main_kol_id IS NULL` 守卫,只写一次)。
        供审核后批量回填或事件触发的写路径显式开启。

    返回最终生效的 linked_main_kol_id(0 表示仍无桥,诚实不促升)。零触 viltrox_fit_score。
    """
    existing = int(rec_dict.get("linked_main_kol_id") or 0)
    if existing > 0:
        return existing
    pool_id = int(rec_dict.get("kol_pool_id") or 0)
    if pool_id <= 0:
        return 0
    pool_row = conn.execute(
        "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?",
        (pool_id,),
    ).fetchone()
    pool_kol_id = int(_row_get(pool_row, "linked_main_kol_id", 0) or 0)
    if pool_kol_id <= 0:
        return 0
    rec_dict["linked_main_kol_id"] = pool_kol_id  # 内存生效,本次 join 立即用到
    if persist:
        conn.execute(
            "UPDATE vkpi_kol_recommendations SET linked_main_kol_id=?, updated_at=? WHERE id=? AND linked_main_kol_id IS NULL",
            (pool_kol_id, _utcnow(), int(rec_dict.get("id") or 0)),
        )
        conn.commit()
        logger.info(
            "outcomes.backfill_linked_kol rec_id=%s kol_pool_id=%s -> linked_main_kol_id=%s",
            rec_dict.get("id"), pool_id, pool_kol_id,
        )
    return pool_kol_id


def refresh_business_outcome(recommendation_id: int, *, persist_linked_kol: bool = False) -> dict[str, Any]:
    """Refresh outcome labels from real V-KPI business rows.

    This never fabricates platform data. It only promotes labels when matching
    project/message/content/link/sales/cost/claim rows already exist.
    """

    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    rec = conn.execute(
        "SELECT * FROM vkpi_kol_recommendations WHERE id=?",
        (int(recommendation_id),),
    ).fetchone()
    if not rec:
        return {"outcome": None, "aggregates": {"status": "recommendation_not_found"}}

    rec_dict = dict(rec)
    existing_outcome = conn.execute(
        "SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?",
        (int(recommendation_id),),
    ).fetchone()
    recommended_at = (
        _row_get(existing_outcome, "recommended_at")
        or rec_dict.get("created_at")
        or _utcnow()
    )
    launch_sku = ""
    launch_id = int(rec_dict.get("launch_id") or 0)
    if launch_id > 0:
        launch_row = conn.execute(
            "SELECT product_sku FROM vkpi_product_launches WHERE id=?",
            (launch_id,),
        ).fetchone()
        launch_sku = str(_row_get(launch_row, "product_sku", "") or "").strip()
    # 打通 attribution→outcome:缺键时从已确认的 pool 桥解析 linked_main_kol_id,否则下方 join 全断。
    # 默认 persist=False(只内存生效,不改既有推荐行);persist_linked_kol=True 才落库回填(待审批后批量)。
    _resolve_linked_kol_id(conn, rec_dict, persist=bool(persist_linked_kol))
    kol_id = int(rec_dict.get("linked_main_kol_id") or 0)
    rec_id = int(recommendation_id)
    projects = conn.execute(
        """
        SELECT id, stage, created_at, updated_at
        FROM vkpi_projects
        WHERE COALESCE(stage_status, '') != 'deleted'
          AND created_at >= ?
          AND (
            metadata_json LIKE ?
            OR metadata_json LIKE ?
            OR (
              ? > 0 AND kol_id=? AND source_type='product_recommendation'
              AND (? = '' OR product_sku = ?)
            )
          )
        """,
        (
            recommended_at,
            f'%"recommendation_id": {rec_id}%',
            f'%"recommendation_id":{rec_id}%',
            kol_id,
            kol_id,
            launch_sku,
            launch_sku,
        ),
    ).fetchall()
    project_ids = [int(row["id"]) for row in projects]
    project_clause, project_params = _ids_clause("project_id", project_ids)
    stage_map = {int(row["id"]): str(row["stage"] or "") for row in projects}
    first_project = _first_timestamp([row["created_at"] for row in projects])

    message_where = []
    message_params: list[Any] = []
    if project_ids:
        clause, params = _ids_clause("project_id", project_ids)
        message_where.append(clause)
        message_params.extend(params)
    message_stats = None
    if message_where:
        message_params.append(recommended_at)
        message_stats = conn.execute(
            f"""
            SELECT
                MIN(captured_at) AS first_message_at,
                MIN(CASE WHEN direction='outbound' THEN captured_at END) AS first_outbound_at,
                MIN(CASE WHEN direction='inbound' THEN captured_at END) AS first_inbound_at
            FROM vkpi_messages
            WHERE ({' OR '.join(f'({part})' for part in message_where)})
              AND captured_at >= ?
            """,
            tuple(message_params),
        ).fetchone()

    agreement_stage_at = None
    if project_ids:
        agreement_clause, agreement_params = _ids_clause("project_id", project_ids)
        agreement_stage_at = conn.execute(
            f"""
            SELECT MIN(effective_at) AS first_agreement_at
            FROM vkpi_project_stage_events
            WHERE {agreement_clause}
              AND to_stage IN ('agreed','shipped','received','published','measured','closed')
              AND effective_at >= ?
            """,
            (*agreement_params, recommended_at),
        ).fetchone()

    content_stats = None
    if project_ids:
        content_stats = conn.execute(
            f"""
            SELECT
                COUNT(*) AS n,
                MIN(COALESCE(published_at, created_at)) AS first_content_at,
                MIN(post_url) AS content_url
            FROM vkpi_content_posts
            WHERE {project_clause}
              AND COALESCE(published_at, created_at) >= ?
            """,
            (*project_params, recommended_at),
        ).fetchone()

    click_stats = None
    if project_ids:
        click_where = []
        click_params: list[Any] = []
        link_project_clause, link_project_params = _ids_clause("l.project_id", project_ids)
        click_where.append(link_project_clause)
        click_params.extend(link_project_params)
        click_params.append(recommended_at)
        click_stats = conn.execute(
            f"""
            SELECT COUNT(*) AS valid_clicks
            FROM vkpi_link_clicks c
            JOIN vkpi_links l ON l.id = c.link_id
            WHERE COALESCE(c.is_bot, 0)=0
              AND ({' OR '.join(f'({part})' for part in click_where)})
              AND c.clicked_at >= ?
            """,
            tuple(click_params),
        ).fetchone()

    sales_stats = None
    if project_ids:
        sales_where = []
        sales_params: list[Any] = []
        sales_project_clause, sales_project_params = _ids_clause("project_id", project_ids)
        sales_where.append(sales_project_clause)
        sales_params.extend(sales_project_params)
        sales_params.append(recommended_at)
        # 净额口径:退款是 revenue_cents<0 的负归因行,必须计入 GMV 净额,不能被 >0 过滤掉。
        #   - gmv_cents = SUM(revenue_cents):正单 + 负退款 = 净销售额(退款向下修正)。
        #   - orders    = 只数正向真实订单(revenue_cents>0),退款不算"新订单"而是冲减额;
        #                 order_attributed 由净额>0 且有正向订单共同决定(净退成 0/负 → 不促升)。
        #   - first_order_at 取首个 *正向* 订单时间(退款不是首单)。
        # confidence='excluded' 仍排除;'refund' 行天然 revenue_cents<0,被净额自动吸收。
        sales_stats = conn.execute(
            f"""
            SELECT
                COUNT(*) FILTER (WHERE revenue_cents > 0) AS orders,
                COALESCE(SUM(revenue_cents), 0) AS gmv_cents,
                MIN(CASE WHEN revenue_cents > 0 THEN COALESCE(occurred_at, created_at) END) AS first_order_at
            FROM vkpi_sales_attributions
            WHERE {business_truth.verified_shopify_attribution_sql()}
              AND ({' OR '.join(f'({part})' for part in sales_where)})
              AND COALESCE(occurred_at, created_at) >= ?
            """,
            tuple(sales_params),
        ).fetchone()

    cost_stats = None
    if project_ids:
        cost_stats = conn.execute(
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
            FROM vkpi_cost_ledger
            WHERE {project_clause}
              AND {business_truth.approved_actual_cost_sql()}
              AND incurred_at >= ?
            """,
            (*project_params, recommended_at),
        ).fetchone()

    # was_claimed 的真业务行来源:vkpi_kol_claims(kol_id = kols.id = linked_main_kol_id)。
    # 履约闭环的"认领"动作真实落在这张表,不靠人工 record(claimed)。曾被 claim 过(含已 released)
    # 即视为 was_claimed=True;claimed_at 取首个 claim 时间。无桥(kol_id<=0)则诚实不促升。
    claim_stats = None
    if kol_id:
        claim_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                MIN(COALESCE(claimed_at, created_at)) AS first_claim_at
            FROM vkpi_kol_claims
            WHERE kol_id=?
              AND COALESCE(claimed_at, created_at) >= ?
            """,
            (kol_id, recommended_at),
        ).fetchone()

    first_outreach = str(_row_get(message_stats, "first_outbound_at") or _row_get(message_stats, "first_message_at") or "") or None
    first_reply = str(_row_get(message_stats, "first_inbound_at") or "") or None
    first_agreement = str(_row_get(agreement_stage_at, "first_agreement_at") or "") or None
    if not first_agreement and any(stage in {"agreed", "shipped", "received", "published", "measured", "closed"} for stage in stage_map.values()):
        first_agreement = _first_timestamp([row["updated_at"] or row["created_at"] for row in projects])
    first_content = str(_row_get(content_stats, "first_content_at") or "") or None
    content_url = str(_row_get(content_stats, "content_url") or "") or ""
    first_claim = str(_row_get(claim_stats, "first_claim_at") or "") or None
    first_order = str(_row_get(sales_stats, "first_order_at") or "") or None
    clicks = int(_row_get(click_stats, "valid_clicks", 0) or 0)
    orders = int(_row_get(sales_stats, "orders", 0) or 0)
    gmv_cents = int(_row_get(sales_stats, "gmv_cents", 0) or 0)
    cost_cents = int(_row_get(cost_stats, "cost_cents", 0) or 0)
    computed_roi = (float(gmv_cents) / float(cost_cents)) if cost_cents > 0 else None
    aggregates = {
        "status": "ready" if project_ids or first_claim else "no_observed_business_evidence",
        "project_ids": project_ids,
        "kol_id": kol_id,
        "project_created": bool(first_project),
        "was_claimed": bool(first_claim),
        "outreach_sent": bool(first_outreach),
        "reply_received": bool(first_reply),
        "agreement_reached": bool(first_agreement),
        "content_published": bool(first_content),
        "order_attributed": bool(first_order and orders > 0 and gmv_cents > 0),
        "valid_clicks": clicks,
        "orders": orders,
        "gmv_cents": gmv_cents,
        "cost_cents": cost_cents,
        "computed_roi": computed_roi,
    }
    if not project_ids and not first_claim:
        return {
            "outcome": dict(existing_outcome) if existing_outcome else None,
            "aggregates": aggregates,
        }

    if not existing_outcome:
        ensure_outcome(
            int(recommendation_id),
            kol_pool_id=rec_dict.get("kol_pool_id"),
            launch_id=rec_dict.get("launch_id"),
            feature_snapshot=_loads_safe(rec_dict.get("feature_snapshot_json")),
            scoring_breakdown=_loads_safe(rec_dict.get("scoring_breakdown_json")),
            model_version=str(
                (_loads_safe(rec_dict.get("scoring_breakdown_json")) or {}).get("strategy_version")
                or "rule_v0"
            ),
            display_position=rec_dict.get("rank"),
            display_context={
                "rank": rec_dict.get("rank"),
                "score": rec_dict.get("score"),
                "status": rec_dict.get("status"),
            },
        )

    updates: list[str] = [
        "attributed_clicks=?",
        "attributed_orders=?",
        "attributed_gmv_cents=?",
        "attributed_cost_cents=?",
        "computed_roi=?",
        "order_attributed=?",
    ]
    has_net_order = bool(first_order and orders > 0 and gmv_cents > 0)
    params: list[Any] = [clicks, orders, gmv_cents, cost_cents, computed_roi, has_net_order]
    first_actions: list[Any] = []
    if first_project:
        updates.extend(["project_created=?", "project_created_at=COALESCE(project_created_at, ?)"])
        params.extend([True, first_project])
        first_actions.append(first_project)
    if first_claim:
        updates.extend(["was_claimed=?", "claimed_at=COALESCE(claimed_at, ?)"])
        params.append(True)
        params.append(first_claim)
        first_actions.append(first_claim)
    if first_outreach:
        updates.extend(["outreach_sent=?", "outreach_sent_at=COALESCE(outreach_sent_at, ?)"])
        params.append(True)
        params.append(first_outreach)
        first_actions.append(first_outreach)
    if first_reply:
        updates.extend(["reply_received=?", "reply_at=COALESCE(reply_at, ?)", "reply_sentiment=COALESCE(NULLIF(reply_sentiment, ''), 'unknown')"])
        params.append(True)
        params.append(first_reply)
        first_actions.append(first_reply)
    if first_agreement:
        updates.extend(["agreement_reached=?", "agreement_at=COALESCE(agreement_at, ?)"])
        params.append(True)
        params.append(first_agreement)
        first_actions.append(first_agreement)
    if first_content:
        updates.extend(["content_published=?", "content_published_at=COALESCE(content_published_at, ?)"])
        params.append(True)
        params.append(first_content)
        first_actions.append(first_content)
        if content_url:
            updates.append("content_url=COALESCE(NULLIF(content_url, ''), ?)")
            params.append(content_url)
    # 净额口径:有正向订单且净 GMV>0 才促升 order_attributed(全额退款 → 净额<=0 → 不算已归因)。
    if has_net_order:
        updates.append("first_order_at=COALESCE(first_order_at, ?)")
        params.append(first_order)
        first_actions.append(first_order)
    first_action = _first_timestamp(first_actions)
    if first_action:
        updates.append("first_action_at=COALESCE(first_action_at, ?)")
        params.append(first_action)
    params.append(rec_id)
    conn.execute(
        f"UPDATE vkpi_recommendation_outcomes SET {', '.join(updates)} WHERE recommendation_id=?",
        tuple(params),
    )
    conn.commit()
    result = get_outcome(rec_id)
    aggregates["order_attributed"] = has_net_order
    result["aggregates"] = aggregates
    return result


def refresh_open_outcomes(limit: int = 200, *, persist_linked_kol: bool = False) -> dict[str, Any]:
    """批量回填业务标签:遍历近 N 条推荐,从真实业务行刷新 outcome(claimed/published/order/roi)。

    持续学习的"actual_result/business_impact"那一半——把"打分→动作→结果"的结果段自动落地,
    供调度器/事件触发周期性跑。红线:只读真实业务行促升标签,绝不伪造平台数据,零触 viltrox_fit_score。

    persist_linked_kol=False(默认):每条推荐的 linked_main_kol_id 缺键时只内存解析(join 立即可用),
        **不回写**既有推荐行 —— 尊重"批量改既有行先 dry-run 待审"。outcome 表(影子结果)照常落库。
    persist_linked_kol=True:同时把从 pool 桥解析出的连接键幂等回填到推荐行(审批后/事件路径显式开启)。
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM vkpi_kol_recommendations WHERE kol_pool_id IS NOT NULL ORDER BY id DESC LIMIT ?",
        (int(max(1, min(limit, 2000))),),
    ).fetchall()
    refreshed = 0
    linked_backfilled = 0
    promoted = {"was_claimed": 0, "content_published": 0, "order_attributed": 0, "computed_roi": 0}
    for r in rows:
        try:
            agg = (refresh_business_outcome(int(dict(r)["id"]), persist_linked_kol=bool(persist_linked_kol)) or {}).get("aggregates") or {}
        except Exception:
            logger.debug("refresh_open_outcomes.one_failed", exc_info=True)
            continue
        refreshed += 1
        if int(agg.get("kol_id") or 0) > 0:
            linked_backfilled += 1
        if agg.get("was_claimed"):
            promoted["was_claimed"] += 1
        if agg.get("content_published"):
            promoted["content_published"] += 1
        if agg.get("order_attributed"):
            promoted["order_attributed"] += 1
        if agg.get("computed_roi") is not None:
            promoted["computed_roi"] += 1
    return {"status": "ok", "scanned": len(rows), "refreshed": refreshed,
            "linked_kol_present": linked_backfilled, "promoted": promoted,
            "note": "批量回填业务标签(持续学习结果段):缺键先从 pool 桥幂等回填 linked_main_kol_id 打通"
                    " attribution→outcome,再从真实业务行(claim/消息/内容/销售净额)促升;退款计入 GMV 净额;"
                    "只读真实业务行促升,零伪造、零触 viltrox_fit_score。"}


def ensure_outcomes_for_display(
    recs: list[dict[str, Any]],
    *,
    display_context: dict[str, Any] | None = None,
    create_missing: bool = False,
) -> dict[str, Any]:
    """Audit outcome coverage for displayed recommendations.

    Display/read paths are read-only by default: missing outcome rows are reported
    but not created. Explicit recommendation materialization or outcome refresh may
    opt in with create_missing=True. Empty placeholders are never business results.
    """
    ensured = 0
    skipped_existing = 0
    missing_not_created = 0
    try:
        ids = list(dict.fromkeys(
            int(rec.get("id") or 0)
            for rec in (recs or [])
            if int(rec.get("id") or 0) > 0
        ))
        if not ids:
            return {"ensured": 0, "existing": 0, "missing": 0,
                    "create_missing": bool(create_missing), "writes": False}
        if create_missing:
            ensure_vkpi_product_industry_schema()
        conn = get_conn()
        clause, params = _ids_clause("recommendation_id", ids)
        existing_rows = conn.execute(
            f"SELECT recommendation_id FROM vkpi_recommendation_outcomes WHERE {clause}",
            tuple(params),
        ).fetchall()
        existing = {int(_row_get(row, "recommendation_id", 0) or 0) for row in existing_rows}
        seen: set[int] = set()
        for rec in recs or []:
            rec_id = int(rec.get("id") or 0)
            if rec_id <= 0 or rec_id in seen:
                continue
            seen.add(rec_id)
            if rec_id in existing:
                skipped_existing += 1
                continue
            if not create_missing:
                missing_not_created += 1
                continue
            try:
                breakdown = _loads_safe(rec.get("scoring_breakdown_json"))
                context = dict(display_context or {})
                context.setdefault("run_id", rec.get("run_id"))
                context.setdefault("rank", rec.get("rank"))
                context.setdefault("score", rec.get("score"))
                context.setdefault("status", rec.get("status"))
                ensure_outcome(
                    rec_id,
                    kol_pool_id=rec.get("kol_pool_id"),
                    launch_id=rec.get("launch_id"),
                    feature_snapshot=_loads_safe(rec.get("feature_snapshot_json")),
                    scoring_breakdown=breakdown,
                    model_version=str(breakdown.get("strategy_version") or "rule_v0"),
                    display_position=rec.get("rank"),
                    display_context=context,
                )
                existing.add(rec_id)
                ensured += 1
            except Exception:
                logger.warning("ensure_outcomes_for_display.one_failed rec_id=%s", rec_id, exc_info=True)
    except Exception:
        logger.warning("ensure_outcomes_for_display.failed", exc_info=True)
    return {
        "ensured": ensured,
        "existing": skipped_existing,
        "missing": missing_not_created,
        "create_missing": bool(create_missing),
        "writes": bool(create_missing and ensured),
    }


def refresh_unfinalized_outcomes(limit: int = 500) -> dict[str, Any]:
    """每日刷新:遍历未 finalize 且 recommendation_id 非空的 outcome 行,逐条从真实业务行回流。

    与 refresh_open_outcomes(按最近 N 条推荐行遍历)互补:本函数按 outcomes 表自身遍历,
    覆盖「展示路径落了底座、但推荐行较老不在最近 N 条里」的行,保证已展示的推荐都有结果回流。
    单条失败吞掉不拖垮整批。红线同 refresh_business_outcome:只读真实业务行促升,零伪造。
    """
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT recommendation_id
        FROM vkpi_recommendation_outcomes
        WHERE outcome_finalized_at IS NULL
          AND recommendation_id IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 500), 2000))),),
    ).fetchall()
    refreshed = 0
    failed = 0
    for row in rows:
        rec_id = int(_row_get(row, "recommendation_id", 0) or 0)
        if rec_id <= 0:
            continue
        try:
            refresh_business_outcome(rec_id)
            refreshed += 1
        except Exception:
            failed += 1
            logger.debug("refresh_unfinalized_outcomes.one_failed rec_id=%s", rec_id, exc_info=True)
    return {"status": "ok", "scanned": len(rows), "refreshed": refreshed, "failed": failed,
            "note": "按 outcomes 表遍历未 finalize 行回流业务事件(与 refresh_open_outcomes 按推荐行遍历互补)。"}


def _resolve_missing_recommendation_id(conn: Any, out_row: dict[str, Any]) -> tuple[int, str]:
    """对无 recommendation_id 的老 outcome 行做严格反推。返回 (rec_id, rule);推不出返回 (0, 原因)。

    只认唯一匹配(exactly-one),宁缺勿错:
      规则1: display_context_json / feature_snapshot_json 里带显式 recommendation_id 且该推荐行存在;
      规则2: display_context 里 run_id + rank 在 vkpi_kol_recommendations 恰好命中 1 行;
      规则3: kol_pool_id + 同日 created_at(+launch_id/display_position 若有)恰好命中 1 行。
    """
    context = _loads_safe(out_row.get("display_context_json"))
    snapshot = _loads_safe(out_row.get("feature_snapshot_json"))
    # 规则1:显式 id(历史写入方若带过)。
    try:
        explicit = int(context.get("recommendation_id") or snapshot.get("recommendation_id") or 0)
    except Exception:
        explicit = 0
    if explicit > 0:
        hit = conn.execute("SELECT id FROM vkpi_kol_recommendations WHERE id=?", (explicit,)).fetchone()
        if hit:
            return explicit, "explicit_id"
    # 规则2:run_id + rank 唯一命中。
    try:
        run_id = int(context.get("run_id") or 0)
    except Exception:
        run_id = 0
    rank = context.get("rank")
    if rank is None:
        rank = out_row.get("display_position")
    if run_id > 0 and rank is not None:
        try:
            hits = conn.execute(
                "SELECT id FROM vkpi_kol_recommendations WHERE run_id=? AND rank=?",
                (run_id, int(rank)),
            ).fetchall()
            if len(hits) == 1:
                return int(_row_get(hits[0], "id", 0) or 0), "run_id_rank"
        except Exception:
            logger.debug("backfill.resolve_run_rank_failed", exc_info=True)
    # 规则3:kol_pool_id + 同日(recommended_at vs created_at 前 10 位日期)唯一命中。
    pool_id = int(out_row.get("kol_pool_id") or 0)
    day = str(out_row.get("recommended_at") or "")[:10]
    if pool_id > 0 and day:
        try:
            candidates = conn.execute(
                "SELECT id, launch_id, rank, created_at FROM vkpi_kol_recommendations WHERE kol_pool_id=? ORDER BY id DESC LIMIT 100",
                (pool_id,),
            ).fetchall()
        except Exception:
            logger.debug("backfill.resolve_pool_candidates_failed", exc_info=True)
            candidates = []
        launch_id = int(out_row.get("launch_id") or 0)
        display_position = out_row.get("display_position")
        matches: list[int] = []
        for cand in candidates:
            cand_dict = dict(cand)
            if str(cand_dict.get("created_at") or "")[:10] != day:
                continue
            if launch_id and int(cand_dict.get("launch_id") or 0) != launch_id:
                continue
            if display_position is not None and cand_dict.get("rank") is not None and int(cand_dict.get("rank") or -1) != int(display_position):
                continue
            matches.append(int(cand_dict.get("id") or 0))
        if len(matches) == 1 and matches[0] > 0:
            return matches[0], "pool_day"
        if len(matches) > 1:
            return 0, "ambiguous"
    return 0, "no_match"


def backfill_missing_recommendation_ids(limit: int = 200, *, dry_run: bool = True) -> dict[str, Any]:
    """老行修复:对 recommendation_id 为空的 outcome 行做严格反推回填(反推不出保持原样,绝不删行)。

    幂等 + 保守:只在唯一匹配且目标 id 未被其他 outcome 行占用(一行一推荐的不变量)时才回填;
    UPDATE 带 `recommendation_id IS NULL` 守卫,重复跑安全。dry_run=True 只报告不写。
    红线:只补连接键,零触业务标签 / viltrox_fit_score。
    """
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM vkpi_recommendation_outcomes
        WHERE recommendation_id IS NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (int(max(1, min(int(limit or 200), 1000))),),
    ).fetchall()
    claimed_rows = conn.execute(
        "SELECT recommendation_id FROM vkpi_recommendation_outcomes WHERE recommendation_id IS NOT NULL"
    ).fetchall()
    claimed = {int(_row_get(row, "recommendation_id", 0) or 0) for row in claimed_rows}
    backfilled: list[dict[str, Any]] = []
    conflicts = 0
    unresolved = 0
    for raw in rows:
        out_row = dict(raw)
        try:
            rec_id, rule = _resolve_missing_recommendation_id(conn, out_row)
        except Exception:
            logger.debug("backfill.resolve_one_failed outcome_id=%s", out_row.get("id"), exc_info=True)
            unresolved += 1
            continue
        if rec_id <= 0:
            unresolved += 1
            continue
        if rec_id in claimed:
            # 目标推荐已有 outcome 行(一行一推荐不变量)→ 不回填、不合并、不删,保持原样。
            conflicts += 1
            continue
        if not dry_run:
            conn.execute(
                "UPDATE vkpi_recommendation_outcomes SET recommendation_id=? WHERE id=? AND recommendation_id IS NULL",
                (rec_id, int(out_row.get("id") or 0)),
            )
            conn.commit()
        claimed.add(rec_id)
        backfilled.append({"outcome_id": out_row.get("id"), "recommendation_id": rec_id, "rule": rule})
    if backfilled and not dry_run:
        logger.info("outcomes.backfill_missing_recommendation_ids", extra={"backfilled_count": len(backfilled)})
    return {"status": "ok", "dry_run": bool(dry_run), "scanned": len(rows),
            "backfilled": len(backfilled), "conflicts": conflicts, "unresolved": unresolved,
            "details": backfilled[:50],
            "note": "只回填唯一匹配的连接键;ambiguous/no_match 保持原样(诚实不猜),绝不删行。"}


def _loads_safe(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value or "{}"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logger.warning("vkpi outcome collector json parse failed: %s", exc)
        return {}
