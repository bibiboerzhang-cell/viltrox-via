"""Recommendation outcome collector for self-learning prebuild."""
from __future__ import annotations

import json
import os
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

# pool→kols 桥回填写开关:默认 0 = 只读影子(内存解析供 join,不回写推荐行);
# 置 1 才把已确认的 pool.linked_main_kol_id 幂等回填到 vkpi_kol_recommendations。
PERSIST_LINKED_KOL_ENV = "VKPI_RECO_PERSIST_LINKED_KOL"


def _utcnow() -> str:
    return utcnow_iso()


def persist_linked_kol_enabled() -> bool:
    return str(os.environ.get(PERSIST_LINKED_KOL_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    """compat 读回 BOOLEAN 可能是 int 1/0 或 't':统一判真。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "t", "true", "yes", "on"}


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
    # 幂等:重复动作不挪首次时间戳(COALESCE),布尔位只会从 false 变 true。
    get_conn().execute(f"UPDATE vkpi_recommendation_outcomes SET {bool_col}=?, {time_col}=COALESCE({time_col}, ?){extra}, first_action_at=COALESCE(first_action_at, ?) WHERE recommendation_id=?", [params[0], params[1], *params[2:-1], now, params[-1]])
    get_conn().commit()
    return get_outcome(recommendation_id)


def record_if_missing(recommendation_id: int, node: str, *, at: str | None = None, context: dict[str, Any] | None = None) -> bool:
    """同步路径专用的幂等置位:节点已为真 → 零写入返回 False;否则按事件自身时间戳置位返回 True。

    与 record() 的区别:时间取真实业务事件时间(派单 updated_at / 触达 touched_at / 反馈 created_at)
    而非「现在」,且已置位时不产生任何 UPDATE(批量同步反复跑零噪声)。零触 viltrox_fit_score。
    """
    ensure_vkpi_product_industry_schema()
    if node not in NODE_COLUMNS:
        raise ValueError(f"unsupported outcome node: {node}")
    rec_id = int(recommendation_id)
    bool_col, time_col = NODE_COLUMNS[node]
    conn = get_conn()
    row = conn.execute(
        f"SELECT {bool_col} AS flag FROM vkpi_recommendation_outcomes WHERE recommendation_id=?", (rec_id,)
    ).fetchone()
    if row is None:
        ensure_outcome(rec_id)
    elif _truthy(_row_get(row, "flag")):
        return False
    stamp = str(at or "").strip() or _utcnow()
    extra = ""
    params: list[Any] = [True, stamp]
    if node == "content_published" and context and context.get("content_url"):
        extra = ", content_url=COALESCE(NULLIF(content_url, ''), ?)"
        params.append(str(context.get("content_url") or ""))
    params.extend([stamp, rec_id])
    conn.execute(
        f"UPDATE vkpi_recommendation_outcomes SET {bool_col}=?, {time_col}=COALESCE({time_col}, ?){extra}, "
        f"first_action_at=COALESCE(first_action_at, ?) WHERE recommendation_id=?",
        params,
    )
    conn.commit()
    return True


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


def _resolve_linked_kol_id(conn: Any, rec_dict: dict[str, Any], *, persist: bool = False) -> tuple[int, str]:
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

    返回 (linked_main_kol_id, source):0 表示仍无桥,诚实不促升;source 供批量统计
    (existing / pool_bridge_shadow / pool_bridge_persisted / no_pool / no_bridge)。零触 viltrox_fit_score。
    """
    existing = int(rec_dict.get("linked_main_kol_id") or 0)
    if existing > 0:
        return existing, "existing"
    pool_id = int(rec_dict.get("kol_pool_id") or 0)
    if pool_id <= 0:
        return 0, "no_pool"
    pool_row = conn.execute(
        "SELECT linked_main_kol_id FROM vkpi_kol_pool WHERE id=?",
        (pool_id,),
    ).fetchone()
    pool_kol_id = int(_row_get(pool_row, "linked_main_kol_id", 0) or 0)
    if pool_kol_id <= 0:
        return 0, "no_bridge"
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
        return pool_kol_id, "pool_bridge_persisted"
    return pool_kol_id, "pool_bridge_shadow"


def _load_refresh_context(conn: Any, recommendation_id: int, persist_linked_kol: bool | None) -> dict[str, Any] | None:
    rec = conn.execute(
        "SELECT * FROM vkpi_kol_recommendations WHERE id=?", (int(recommendation_id),)
    ).fetchone()
    if not rec:
        return None
    rec_dict = dict(rec)
    existing_outcome = conn.execute(
        "SELECT * FROM vkpi_recommendation_outcomes WHERE recommendation_id=?",
        (int(recommendation_id),),
    ).fetchone()
    recommended_at = _row_get(existing_outcome, "recommended_at") or rec_dict.get("created_at") or _utcnow()
    launch_sku = ""
    launch_id = int(rec_dict.get("launch_id") or 0)
    if launch_id > 0:
        launch_row = conn.execute(
            "SELECT product_sku FROM vkpi_product_launches WHERE id=?", (launch_id,)
        ).fetchone()
        launch_sku = str(_row_get(launch_row, "product_sku", "") or "").strip()
    if persist_linked_kol is None:
        persist_linked_kol = persist_linked_kol_enabled()
    kol_id, source = _resolve_linked_kol_id(conn, rec_dict, persist=bool(persist_linked_kol))
    return {"rec_id": int(recommendation_id), "rec": rec_dict, "existing": existing_outcome,
            "recommended_at": recommended_at, "launch_sku": launch_sku, "kol_id": kol_id, "linked_kol_source": source}


def _load_refresh_projects(conn: Any, context: dict[str, Any]) -> dict[str, Any]:
    rec_id = context["rec_id"]
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
        (context["recommended_at"], f'%"recommendation_id": {rec_id}%',
         f'%"recommendation_id":{rec_id}%', context["kol_id"], context["kol_id"],
         context["launch_sku"], context["launch_sku"]),
    ).fetchall()
    project_ids = [int(row["id"]) for row in projects]
    project_clause, project_params = _ids_clause("project_id", project_ids)
    return {"rows": projects, "ids": project_ids, "clause": project_clause, "params": project_params,
            "stage_map": {int(row["id"]): str(row["stage"] or "") for row in projects},
            "first_project": _first_timestamp([row["created_at"] for row in projects])}


def _load_project_evidence(conn: Any, context: dict[str, Any], projects: dict[str, Any]) -> dict[str, Any]:
    project_ids = projects["ids"]
    if not project_ids:
        return {"message": None, "agreement": None, "content": None,
                "click": None, "sales": None, "cost": None}
    recommended_at = context["recommended_at"]
    clause, message_params = _ids_clause("project_id", project_ids)
    message_where = [clause]
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
        """, tuple(message_params),
    ).fetchone()
    agreement_clause, agreement_params = _ids_clause("project_id", project_ids)
    agreement_stage_at = conn.execute(
        f"""
        SELECT MIN(effective_at) AS first_agreement_at
        FROM vkpi_project_stage_events
        WHERE {agreement_clause}
          AND to_stage IN ('agreed','shipped','received','published','measured','closed')
          AND effective_at >= ?
        """, (*agreement_params, recommended_at),
    ).fetchone()
    content_stats = conn.execute(
        f"""
        SELECT
            COUNT(*) AS n,
            MIN(COALESCE(published_at, created_at)) AS first_content_at,
            MIN(post_url) AS content_url
        FROM vkpi_content_posts
        WHERE {projects['clause']}
          AND COALESCE(published_at, created_at) >= ?
        """, (*projects["params"], recommended_at),
    ).fetchone()
    link_project_clause, click_params = _ids_clause("l.project_id", project_ids)
    click_where = [link_project_clause]
    click_params.append(recommended_at)
    click_stats = conn.execute(
        f"""
        SELECT COUNT(*) AS valid_clicks
        FROM vkpi_link_clicks c
        JOIN vkpi_links l ON l.id = c.link_id
        WHERE COALESCE(c.is_bot, 0)=0
          AND ({' OR '.join(f'({part})' for part in click_where)})
          AND c.clicked_at >= ?
        """, tuple(click_params),
    ).fetchone()
    sales_project_clause, sales_params = _ids_clause("project_id", project_ids)
    sales_where = [sales_project_clause]
    sales_params.append(recommended_at)
    # 退款负行进入 GMV 净额;orders/first_order_at 只认正向订单。
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
        """, tuple(sales_params),
    ).fetchone()
    cost_stats = conn.execute(
        f"""
        SELECT COALESCE(SUM(amount_cents), 0) AS cost_cents
        FROM vkpi_cost_ledger
        WHERE {projects['clause']}
          AND {business_truth.approved_actual_cost_sql()}
          AND incurred_at >= ?
        """, (*projects["params"], recommended_at),
    ).fetchone()
    return {"message": message_stats, "agreement": agreement_stage_at, "content": content_stats,
            "click": click_stats, "sales": sales_stats, "cost": cost_stats}


def _load_claim_evidence(conn: Any, kol_id: int, recommended_at: Any) -> Any:
    if not kol_id:
        return None
    return conn.execute(
        """
        SELECT
            COUNT(*) AS n,
            MIN(COALESCE(claimed_at, created_at)) AS first_claim_at
        FROM vkpi_kol_claims
        WHERE kol_id=?
          AND COALESCE(claimed_at, created_at) >= ?
        """, (kol_id, recommended_at),
    ).fetchone()


def _string_or_none(value: Any) -> str | None:
    return str(value or "") or None


def _summarize_refresh(context: dict[str, Any], projects: dict[str, Any], evidence: dict[str, Any], claim: Any) -> dict[str, Any]:
    first_outreach = _string_or_none(_row_get(evidence["message"], "first_outbound_at") or _row_get(evidence["message"], "first_message_at"))
    first_reply = _string_or_none(_row_get(evidence["message"], "first_inbound_at"))
    first_agreement = _string_or_none(_row_get(evidence["agreement"], "first_agreement_at"))
    closed_stages = {"agreed", "shipped", "received", "published", "measured", "closed"}
    if not first_agreement and any(stage in closed_stages for stage in projects["stage_map"].values()):
        first_agreement = _first_timestamp([row["updated_at"] or row["created_at"] for row in projects["rows"]])
    first_content = _string_or_none(_row_get(evidence["content"], "first_content_at"))
    first_claim = _string_or_none(_row_get(claim, "first_claim_at"))
    first_order = _string_or_none(_row_get(evidence["sales"], "first_order_at"))
    orders = int(_row_get(evidence["sales"], "orders", 0) or 0)
    gmv_cents = int(_row_get(evidence["sales"], "gmv_cents", 0) or 0)
    cost_cents = int(_row_get(evidence["cost"], "cost_cents", 0) or 0)
    has_net_order = bool(first_order and orders > 0 and gmv_cents > 0)
    values = {"first_project": projects["first_project"], "first_claim": first_claim,
              "first_outreach": first_outreach, "first_reply": first_reply,
              "first_agreement": first_agreement, "first_content": first_content,
              "content_url": str(_row_get(evidence["content"], "content_url") or "") or "",
              "first_order": first_order, "clicks": int(_row_get(evidence["click"], "valid_clicks", 0) or 0),
              "orders": orders, "gmv_cents": gmv_cents, "cost_cents": cost_cents,
              "computed_roi": (float(gmv_cents) / float(cost_cents)) if cost_cents > 0 else None,
              "has_net_order": has_net_order}
    values["aggregates"] = {
        "status": "ready" if projects["ids"] or first_claim else "no_observed_business_evidence",
        "project_ids": projects["ids"], "kol_id": context["kol_id"],
        "linked_kol_source": context["linked_kol_source"],
        "project_created": bool(values["first_project"]), "was_claimed": bool(first_claim),
        "outreach_sent": bool(first_outreach), "reply_received": bool(first_reply),
        "agreement_reached": bool(first_agreement), "content_published": bool(first_content),
        "order_attributed": has_net_order, "valid_clicks": values["clicks"],
        "orders": orders, "gmv_cents": gmv_cents, "cost_cents": cost_cents,
        "computed_roi": values["computed_roi"],
    }
    return values


def _ensure_refresh_outcome(context: dict[str, Any]) -> None:
    if context["existing"]:
        return
    rec = context["rec"]
    ensure_outcome(
        context["rec_id"], kol_pool_id=rec.get("kol_pool_id"), launch_id=rec.get("launch_id"),
        feature_snapshot=_loads_safe(rec.get("feature_snapshot_json")),
        scoring_breakdown=_loads_safe(rec.get("scoring_breakdown_json")),
        model_version=str((_loads_safe(rec.get("scoring_breakdown_json")) or {}).get("strategy_version") or "rule_v0"),
        display_position=rec.get("rank"),
        display_context={"rank": rec.get("rank"), "score": rec.get("score"), "status": rec.get("status")},
    )


def _refresh_update_plan(values: dict[str, Any]) -> tuple[list[str], list[Any]]:
    updates = ["attributed_clicks=?", "attributed_orders=?", "attributed_gmv_cents=?",
               "attributed_cost_cents=?", "computed_roi=?", "order_attributed=?"]
    params: list[Any] = [values["clicks"], values["orders"], values["gmv_cents"],
                         values["cost_cents"], values["computed_roi"], values["has_net_order"]]
    first_actions: list[Any] = []
    nodes = (
        ("first_project", ("project_created=?", "project_created_at=COALESCE(project_created_at, ?)")),
        ("first_claim", ("was_claimed=?", "claimed_at=COALESCE(claimed_at, ?)")),
        ("first_outreach", ("outreach_sent=?", "outreach_sent_at=COALESCE(outreach_sent_at, ?)")),
        ("first_reply", ("reply_received=?", "reply_at=COALESCE(reply_at, ?)",
                         "reply_sentiment=COALESCE(NULLIF(reply_sentiment, ''), 'unknown')")),
        ("first_agreement", ("agreement_reached=?", "agreement_at=COALESCE(agreement_at, ?)")),
    )
    for key, columns in nodes:
        if values[key]:
            updates.extend(columns)
            params.extend([True, values[key]])
            first_actions.append(values[key])
    if values["first_content"]:
        updates.extend(["content_published=?", "content_published_at=COALESCE(content_published_at, ?)"])
        params.extend([True, values["first_content"]])
        first_actions.append(values["first_content"])
        if values["content_url"]:
            updates.append("content_url=COALESCE(NULLIF(content_url, ''), ?)")
            params.append(values["content_url"])
    if values["has_net_order"]:
        updates.append("first_order_at=COALESCE(first_order_at, ?)")
        params.append(values["first_order"])
        first_actions.append(values["first_order"])
    first_action = _first_timestamp(first_actions)
    if first_action:
        updates.append("first_action_at=COALESCE(first_action_at, ?)")
        params.append(first_action)
    return updates, params


def _write_refresh_outcome(conn: Any, rec_id: int, values: dict[str, Any]) -> dict[str, Any]:
    updates, params = _refresh_update_plan(values)
    params.append(rec_id)
    conn.execute(
        f"UPDATE vkpi_recommendation_outcomes SET {', '.join(updates)} WHERE recommendation_id=?",
        tuple(params),
    )
    conn.commit()
    result = get_outcome(rec_id)
    values["aggregates"]["order_attributed"] = values["has_net_order"]
    result["aggregates"] = values["aggregates"]
    return result


def refresh_business_outcome(recommendation_id: int, *, persist_linked_kol: bool | None = None) -> dict[str, Any]:
    """Promote outcome labels only from existing real V-KPI business rows."""
    ensure_vkpi_schema()
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    context = _load_refresh_context(conn, recommendation_id, persist_linked_kol)
    if context is None:
        return {"outcome": None, "aggregates": {"status": "recommendation_not_found"}}
    projects = _load_refresh_projects(conn, context)
    evidence = _load_project_evidence(conn, context, projects)
    claim = _load_claim_evidence(conn, context["kol_id"], context["recommended_at"])
    values = _summarize_refresh(context, projects, evidence, claim)
    if not projects["ids"] and not values["first_claim"]:
        existing = context["existing"]
        return {"outcome": dict(existing) if existing else None, "aggregates": values["aggregates"]}
    _ensure_refresh_outcome(context)
    return _write_refresh_outcome(conn, context["rec_id"], values)


def refresh_open_outcomes(limit: int = 200, *, persist_linked_kol: bool | None = None, run_sync: bool = True, run_fit: bool = True) -> dict[str, Any]:
    """批量回填业务标签:遍历近 N 条推荐,从真实业务行刷新 outcome(claimed/published/order/roi)。

    持续学习的"actual_result/business_impact"那一半——把"打分→动作→结果"的结果段自动落地,
    供调度器/事件触发周期性跑。红线:只读真实业务行促升标签,绝不伪造平台数据,零触 viltrox_fit_score。

    三段式(W-L2 接通):
      0) run_sync:先把 pool 动作 / 派单阶段 / 触达记录幂等同步成 outcome 节点(outcome_sync);
      1) 逐条 refresh_business_outcome:缺键时经 pool→kols 桥解析 linked_main_kol_id;
         persist_linked_kol=None → 读 VKPI_RECO_PERSIST_LINKED_KOL(默认 0 = 只读影子,不回写推荐行);
         桥缺失(pool 无 linked_main_kol_id)诚实计数 bridge.missing_skipped,不猜不造;
      2) run_fit:尾随触发影子重排序周拟合(rerank_fit.maybe_weekly_fit,7 天内已拟合即跳过)。
    """
    if persist_linked_kol is None:
        persist_linked_kol = persist_linked_kol_enabled()
    sync_result: dict[str, Any] = {"status": "skipped"}
    if run_sync:
        try:
            from app.domains.recommendations import outcome_sync

            sync_result = outcome_sync.sync_action_outcomes()
        except Exception as exc:
            logger.warning("refresh_open_outcomes.sync_failed: %s", exc, exc_info=True)
            sync_result = {"status": "failed", "error": str(exc), "changed": 0}
    conn = get_conn()
    rows = conn.execute(
        "SELECT id FROM vkpi_kol_recommendations WHERE kol_pool_id IS NOT NULL ORDER BY id DESC LIMIT ?",
        (int(max(1, min(limit, 2000))),),
    ).fetchall()
    refreshed = 0
    failed = 0
    linked_backfilled = 0
    bridge = {"flag": PERSIST_LINKED_KOL_ENV, "persist": bool(persist_linked_kol),
              "existing": 0, "pool_bridge_shadow": 0, "pool_bridge_persisted": 0, "missing_skipped": 0}
    promoted = {"was_claimed": 0, "content_published": 0, "order_attributed": 0, "computed_roi": 0}
    for r in rows:
        try:
            agg = (refresh_business_outcome(int(dict(r)["id"]), persist_linked_kol=bool(persist_linked_kol)) or {}).get("aggregates") or {}
        except Exception:
            failed += 1
            logger.debug("refresh_open_outcomes.one_failed", exc_info=True)
            continue
        refreshed += 1
        source = str(agg.get("linked_kol_source") or "")
        if source in bridge:
            bridge[source] += 1
        elif source in {"no_pool", "no_bridge"}:
            bridge["missing_skipped"] += 1
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
    fit_result: dict[str, Any] = {"status": "skipped"}
    if run_fit:
        try:
            from app.domains.recommendations import rerank_fit

            fit_result = rerank_fit.maybe_weekly_fit()
        except Exception as exc:
            logger.warning("refresh_open_outcomes.fit_failed: %s", exc, exc_info=True)
            fit_result = {"status": "failed", "error": str(exc)}
    return {"status": "ok", "scanned": len(rows), "refreshed": refreshed, "failed": failed,
            "linked_kol_present": linked_backfilled, "bridge": bridge, "promoted": promoted,
            "action_sync": sync_result, "rerank_fit": fit_result,
            "note": "批量回填业务标签(持续学习结果段):先同步动作/阶段/触达为 outcome 节点,缺键再从 pool 桥解析"
                    " linked_main_kol_id(默认只读影子,VKPI_RECO_PERSIST_LINKED_KOL=1 才回写)打通 attribution→outcome,"
                    "再从真实业务行(claim/消息/内容/销售净额)促升;退款计入 GMV 净额;尾随周拟合影子重排序;"
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
