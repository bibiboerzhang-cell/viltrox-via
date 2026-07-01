"""Recommendation outcome collector for self-learning prebuild."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
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
    # 打通 attribution→outcome:缺键时从已确认的 pool 桥解析 linked_main_kol_id,否则下方 join 全断。
    # 默认 persist=False(只内存生效,不改既有推荐行);persist_linked_kol=True 才落库回填(待审批后批量)。
    _resolve_linked_kol_id(conn, rec_dict, persist=bool(persist_linked_kol))
    ensure_outcome(
        int(recommendation_id),
        kol_pool_id=rec_dict.get("kol_pool_id"),
        launch_id=rec_dict.get("launch_id"),
        feature_snapshot=_loads_safe(rec_dict.get("feature_snapshot_json")),
        scoring_breakdown=_loads_safe(rec_dict.get("scoring_breakdown_json")),
        model_version=str((_loads_safe(rec_dict.get("scoring_breakdown_json")) or {}).get("strategy_version") or "rule_v0"),
        display_position=rec_dict.get("rank"),
        display_context={"rank": rec_dict.get("rank"), "score": rec_dict.get("score"), "status": rec_dict.get("status")},
    )

    kol_id = int(rec_dict.get("linked_main_kol_id") or 0)
    rec_id = int(recommendation_id)
    projects = conn.execute(
        """
        SELECT id, stage, created_at, updated_at
        FROM vkpi_projects
        WHERE COALESCE(stage_status, '') != 'deleted'
          AND (
            metadata_json LIKE ?
            OR metadata_json LIKE ?
            OR (? > 0 AND kol_id=? AND source_type='product_recommendation')
          )
        """,
        (f'%"recommendation_id": {rec_id}%', f'%"recommendation_id":{rec_id}%', kol_id, kol_id),
    ).fetchall()
    project_ids = [int(row["id"]) for row in projects]
    project_clause, project_params = _ids_clause("project_id", project_ids)
    stage_map = {int(row["id"]): str(row["stage"] or "") for row in projects}

    message_where = []
    message_params: list[Any] = []
    if project_ids:
        clause, params = _ids_clause("project_id", project_ids)
        message_where.append(clause)
        message_params.extend(params)
    if kol_id:
        message_where.append("kol_id=?")
        message_params.append(kol_id)
    message_stats = None
    if message_where:
        message_stats = conn.execute(
            f"""
            SELECT
                MIN(captured_at) AS first_message_at,
                MIN(CASE WHEN direction='outbound' THEN captured_at END) AS first_outbound_at,
                MIN(CASE WHEN direction='inbound' THEN captured_at END) AS first_inbound_at
            FROM vkpi_messages
            WHERE {' OR '.join(f'({part})' for part in message_where)}
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
            """,
            tuple(agreement_params),
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
            """,
            tuple(project_params),
        ).fetchone()

    click_stats = None
    if project_ids or kol_id:
        click_where = []
        click_params: list[Any] = []
        if project_ids:
            link_project_clause, link_project_params = _ids_clause("l.project_id", project_ids)
            click_where.append(link_project_clause)
            click_params.extend(link_project_params)
        if kol_id:
            click_where.append("l.kol_id=?")
            click_params.append(kol_id)
        click_stats = conn.execute(
            f"""
            SELECT COUNT(*) AS valid_clicks
            FROM vkpi_link_clicks c
            JOIN vkpi_links l ON l.id = c.link_id
            WHERE COALESCE(c.is_bot, 0)=0
              AND ({' OR '.join(f'({part})' for part in click_where)})
            """,
            tuple(click_params),
        ).fetchone()

    sales_stats = None
    if project_ids or kol_id:
        sales_where = []
        sales_params: list[Any] = []
        if project_ids:
            sales_project_clause, sales_project_params = _ids_clause("project_id", project_ids)
            sales_where.append(sales_project_clause)
            sales_params.extend(sales_project_params)
        if kol_id:
            sales_where.append("kol_id=?")
            sales_params.append(kol_id)
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
            WHERE confidence != 'excluded'
              AND ({' OR '.join(f'({part})' for part in sales_where)})
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
              AND status != 'void'
            """,
            tuple(project_params),
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
            """,
            (kol_id,),
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

    updates: list[str] = [
        "attributed_clicks=?",
        "attributed_orders=?",
        "attributed_gmv_cents=?",
        "attributed_cost_cents=?",
        "computed_roi=?",
    ]
    params: list[Any] = [clicks, orders, gmv_cents, cost_cents, computed_roi]
    first_actions: list[Any] = []
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
    if first_order and orders > 0 and gmv_cents > 0:
        updates.extend(["order_attributed=?", "first_order_at=COALESCE(first_order_at, ?)"])
        params.append(True)
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
    result["aggregates"] = {
        "project_ids": project_ids,
        "kol_id": kol_id,
        "was_claimed": bool(first_claim),
        "outreach_sent": bool(first_outreach),
        "reply_received": bool(first_reply),
        "agreement_reached": bool(first_agreement),
        "content_published": bool(first_content),
        "order_attributed": orders > 0 and gmv_cents > 0,  # 净额>0 才算已归因(退款净化)
        "valid_clicks": clicks,
        "orders": orders,
        "gmv_cents": gmv_cents,  # 净额(含退款负行)
        "cost_cents": cost_cents,
        "computed_roi": computed_roi,
    }
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


def _loads_safe(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value or "{}"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        logger.warning("vkpi outcome collector json parse failed: %s", exc)
        return {}
