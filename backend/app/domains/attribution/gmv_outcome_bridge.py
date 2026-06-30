"""GMV 归因事件 → recommendation_outcomes 预备桥(no-op until token)。

Shopify 归因架构(viltroxvia.com 短链 + webhook)已规划,但 shpat_ token 尚未接入。
本模块只做**预备接线**:把"收到 GMV 归因事件 → 促升对应推荐的 order_attributed / GMV"的
代码路径就位,但在缺 Shopify token 配置时安全 no-op —— 不报错、不写脏数据、只记 debug 日志。
补上 token(connection_status().token_configured)后即自动生效,无需改动调用方。

设计要点:
- 不新建表、不新增列 —— 复用既有 outcomes.refresh_business_outcome(只读真实业务行促升标签)。
- 缺 token:_shopify_token_configured() 为 False → 直接 return no-op 结果,绝不抛、绝不烧 LLM。
- 桥本身只读 + 委派,绝不 INSERT/UPDATE viltrox_fit_score,绝不触 rule_v0。
- DB 走 get_conn() + '?' 占位;SQL 串内无裸 %;BOOLEAN 读回当 int 容错。

链路(token 就位后):
  Shopify order webhook → vkpi_sales_attributions(kol_id/project_id 已 match)
    → handle_gmv_attribution_event({kol_id|project_id|order_id})
    → 解析受影响 recommendation_id
    → outcomes.refresh_business_outcome(rec_id)  # 从真实 sales_attributions 促升 order_attributed + GMV
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _shopify_token_configured() -> bool:
    """True only when a live Shopify Admin access token is configured (DB creds or env).

    Reuses commerce.shopify_connect.connection_status() — the single source of truth for
    whether shpat_ material exists. Any failure resolves to "not configured" so the bridge
    degrades to a safe no-op rather than raising into a webhook/scheduler path.
    """
    try:
        from app.domains.commerce import shopify_connect

        status = shopify_connect.connection_status() or {}
    except Exception:
        logger.debug("gmv_outcome_bridge.token_probe_failed", exc_info=True)
        return False
    # connection_status() returns token_configured as a Python bool, but read defensively
    # (BOOLEAN round-trips as int 1/0 under the Postgres compat layer in other call sites).
    return bool(status.get("token_configured"))


def _recommendation_ids_for(kol_id: int = 0, project_id: int = 0, limit: int = 50) -> list[int]:
    """Resolve recommendation ids whose outcome an attributed order could promote.

    A recommendation links to a KOL via vkpi_kol_recommendations.linked_main_kol_id
    (= kols.id, the same id stored on vkpi_sales_attributions.kol_id). When the order
    matched a project, fall back to the recommendation whose product launch spawned the
    project's KOL. Read-only; never fabricates rows.
    """
    conn = get_conn()
    ids: list[int] = []
    safe_limit = max(1, min(int(limit or 50), 500))

    if kol_id > 0:
        rows = conn.execute(
            """
            SELECT id
            FROM vkpi_kol_recommendations
            WHERE linked_main_kol_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(kol_id), safe_limit),
        ).fetchall()
        ids.extend(_as_int(dict(row).get("id")) for row in rows)

    if project_id > 0:
        # vkpi_projects.kol_id is the same kols.id; map project -> kol -> recommendations
        # so a discount/UTM-matched order (which resolves a project, not a KOL directly)
        # still reaches the originating recommendation.
        proj_rows = conn.execute(
            "SELECT kol_id FROM vkpi_projects WHERE id=? AND COALESCE(stage_status,'') != 'deleted'",
            (int(project_id),),
        ).fetchall()
        for proj in proj_rows:
            proj_kol_id = _as_int(dict(proj).get("kol_id"))
            if proj_kol_id > 0 and proj_kol_id != kol_id:
                rows = conn.execute(
                    """
                    SELECT id
                    FROM vkpi_kol_recommendations
                    WHERE linked_main_kol_id=?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (proj_kol_id, safe_limit),
                ).fetchall()
                ids.extend(_as_int(dict(row).get("id")) for row in rows)

    # De-dup, preserve order, drop zeros.
    seen: set[int] = set()
    unique: list[int] = []
    for rec_id in ids:
        if rec_id > 0 and rec_id not in seen:
            seen.add(rec_id)
            unique.append(rec_id)
    return unique


def _outcome_gmv_cents(recommendation_id: int) -> int:
    """Read-only: current attributed_gmv_cents on a recommendation outcome (0 if absent).

    Used only to compute the signed GMV delta a refund recompute produces (for logging /
    the bridge return). Never writes; any error -> 0 so a refund webhook is never disturbed.
    """
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT attributed_gmv_cents FROM vkpi_recommendation_outcomes WHERE recommendation_id=?",
            (int(recommendation_id),),
        ).fetchone()
    except Exception:
        logger.debug("gmv_outcome_bridge.outcome_gmv_read_failed rec_id=%s", recommendation_id, exc_info=True)
        return 0
    if not row:
        return 0
    return _as_int(dict(row).get("attributed_gmv_cents"))


def handle_gmv_attribution_event(event: dict[str, Any] | None = None) -> dict[str, Any]:
    """Promote / re-derive recommendation_outcomes from a GMV attribution event (no-op until token).

    event keys (any subset; resolved from a matched Shopify order / attribution row):
      - kol_id:     kols.id the order was attributed to (preferred linkage key)
      - project_id: vkpi_projects.id the order was attributed to (fallback linkage)
      - order_id / source_ref: carried through for log/debug only
      - refund:     True when this event originates from a refund webhook. The bridge then
                    treats the call as a downward *re-derivation* rather than a promotion —
                    refresh_business_outcome() recomputes attributed_orders / attributed_gmv
                    from the *current* ledger state, which now includes the refund's negative
                    vkpi_sales_attributions row, so the outcome corrects itself. No new column,
                    no manual subtraction: the recompute is the negative correction (重算=负向).

    Behaviour:
      - No Shopify token configured -> safe no-op: returns {ok:False, reason:'not_configured'},
        logs at debug, writes nothing, never raises. Adding shpat_ later flips it live.
      - Token configured but no kol_id/project_id resolvable -> {ok:True, refreshed:0}.
      - Otherwise: delegate to outcomes.refresh_business_outcome(rec_id) for each affected
        recommendation. That helper only promotes labels from real business rows
        (sales_attributions etc.) — it never fabricates platform data and never touches
        viltrox_fit_score / rule_v0.
    """
    event = event or {}
    kol_id = _as_int(event.get("kol_id"))
    project_id = _as_int(event.get("project_id"))
    order_ref = str(event.get("order_id") or event.get("source_ref") or "").strip()
    is_refund = bool(event.get("refund"))

    if not _shopify_token_configured():
        logger.debug(
            "gmv_outcome_bridge.noop_no_token kol_id=%s project_id=%s order=%s",
            kol_id or None,
            project_id or None,
            order_ref or None,
        )
        return {
            "ok": False,
            "reason": "not_configured",
            "refreshed": 0,
            "note": "Shopify shpat_ token 未配置;GMV 归因桥 no-op,补 token 即生效。",
        }

    if kol_id <= 0 and project_id <= 0:
        logger.debug("gmv_outcome_bridge.noop_no_linkage order=%s", order_ref or None)
        return {"ok": True, "refreshed": 0, "reason": "no_linkage", "recommendation_ids": []}

    rec_ids = _recommendation_ids_for(kol_id=kol_id, project_id=project_id)
    if not rec_ids:
        logger.debug(
            "gmv_outcome_bridge.no_recommendations kol_id=%s project_id=%s",
            kol_id or None,
            project_id or None,
        )
        return {"ok": True, "refreshed": 0, "reason": "no_recommendations", "recommendation_ids": []}

    from app.domains.recommendations import outcomes

    refreshed = 0
    promoted_order = 0
    gmv_delta_cents = 0  # signed change in attributed GMV across the recompute (refunds -> negative)
    for rec_id in rec_ids:
        # Read the outcome's attributed GMV *before* recompute so a refund webhook can report
        # the realized downward correction. Read-only; tolerant of a missing row / column.
        before_cents = _outcome_gmv_cents(int(rec_id))
        try:
            agg = (outcomes.refresh_business_outcome(int(rec_id)) or {}).get("aggregates") or {}
        except Exception:
            logger.debug("gmv_outcome_bridge.refresh_failed rec_id=%s", rec_id, exc_info=True)
            continue
        refreshed += 1
        if agg.get("order_attributed"):
            promoted_order += 1
        after_cents = _as_int(agg.get("gmv_cents"))
        gmv_delta_cents += after_cents - before_cents

    logger.info(
        "gmv_outcome_bridge.refreshed count=%s order_attributed=%s gmv_delta_cents=%s refund=%s kol_id=%s project_id=%s order=%s",
        refreshed,
        promoted_order,
        gmv_delta_cents,
        is_refund,
        kol_id or None,
        project_id or None,
        order_ref or None,
    )
    return {
        "ok": True,
        "refreshed": refreshed,
        "order_attributed_promoted": promoted_order,
        "gmv_delta_cents": gmv_delta_cents,
        "refund": is_refund,
        "recommendation_ids": rec_ids,
    }


def handle_attribution_row(attribution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convenience adapter: take a vkpi_sales_attributions row dict and bridge it.

    Lets the Shopify order webhook hand its freshly-created attribution row straight to
    the bridge once a token is configured, without re-querying. Pre-token this is still a
    safe no-op (delegates to handle_gmv_attribution_event).
    """
    attribution = attribution or {}
    return handle_gmv_attribution_event(
        {
            "kol_id": attribution.get("kol_id"),
            "project_id": attribution.get("project_id"),
            "order_id": attribution.get("order_id"),
            "source_ref": attribution.get("source_ref"),
        }
    )


def handle_refund_row(attribution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convenience adapter for a refund's negative vkpi_sales_attributions row.

    The Shopify refund webhook hands the freshly-created *negative* attribution row here so
    the outcome of the affected recommendation is re-derived from the now-refund-inclusive
    ledger (refresh_business_outcome). Identical linkage to handle_attribution_row, but flags
    refund=True so the bridge logs/returns the downward correction. Pre-token this is still a
    safe no-op (delegates to handle_gmv_attribution_event).
    """
    attribution = attribution or {}
    return handle_gmv_attribution_event(
        {
            "kol_id": attribution.get("kol_id"),
            "project_id": attribution.get("project_id"),
            "order_id": attribution.get("order_id"),
            "source_ref": attribution.get("source_ref"),
            "refund": True,
        }
    )


__all__ = [
    "handle_gmv_attribution_event",
    "handle_attribution_row",
    "handle_refund_row",
]
