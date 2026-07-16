"""R16 · KOL ROI 汇总 + 下次推荐权重(只读展示信号,绝不并入 viltrox_fit_score / rule_v0)。

compute-on-read:不在 vkpi_kol_pool 加列、不写任何表(红线安全,远离 fit 列)。
- get_kol_roi_summary:该 KOL 关联项目(vkpi_project_kol_assignments)的 cost/revenue 聚合 → ROI。
- compute_next_recommendation_weight:据推荐漏斗(vkpi_recommendation_outcomes)算 0-1 权重。
红线:ROI / 权重是独立展示信号,绝不并入 fit;无 revenue → 诚实 awaiting_m5(非假 0)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains import business_truth
from app.domains.metrics import aggregation as metrics_agg

logger = get_logger(__name__)


def _bulk_high_value_signals(conn: Any, kids: list[int]) -> dict[int, dict[str, Any]]:
    """Return ROI and learning signals for a candidate set with constant queries.

    This is deliberately private to the leaderboard.  The single-KOL public
    functions keep their established contracts, while a 50-row leaderboard no
    longer fans out into hundreds of table checks and reads.
    """
    if not kids:
        return {}
    placeholders = ",".join("?" for _ in kids)
    signals: dict[int, dict[str, Any]] = {
        kid: {"cost_cents": None, "revenue_cents": None, "funnel_weight": None, "outcome_weight": None}
        for kid in kids
    }

    if table_exists("vkpi_cost_ledger"):
        try:
            for kid in kids:
                signals[kid]["cost_cents"] = 0
            rows = conn.execute(
                f"""
                SELECT pka.kol_pool_id, COALESCE(SUM(c.amount_cents), 0) AS cost_cents
                FROM vkpi_project_kol_assignments pka
                JOIN vkpi_cost_ledger c ON c.project_id = pka.project_id
                WHERE pka.kol_pool_id IN ({placeholders})
                  AND c.status='actual' AND c.approved_at IS NOT NULL
                GROUP BY pka.kol_pool_id
                """,
                tuple(kids),
            ).fetchall()
            for row in rows:
                data = dict(row)
                signals[int(data["kol_pool_id"])]["cost_cents"] = int(data.get("cost_cents") or 0)
        except Exception:
            logger.debug("roi.high_value_cost_batch_failed", exc_info=True)
            for kid in kids:
                signals[kid]["cost_cents"] = None

    if table_exists("vkpi_sales_attributions"):
        try:
            for kid in kids:
                signals[kid].update({"revenue_cents": 0, "commission_cents": 0, "orders": 0})
            rows = conn.execute(
                f"""
                SELECT pka.kol_pool_id,
                       COALESCE(NULLIF(s.currency, ''), 'USD') AS currency,
                       COALESCE(SUM(s.revenue_cents), 0) AS revenue_cents,
                       COALESCE(SUM(s.commission_cents), 0) AS commission_cents,
                       COUNT(*) AS orders
                FROM vkpi_project_kol_assignments pka
                JOIN vkpi_sales_attributions s ON s.project_id = pka.project_id
                WHERE pka.kol_pool_id IN ({placeholders})
                  AND {business_truth.verified_shopify_attribution_sql('s')}
                GROUP BY pka.kol_pool_id, COALESCE(NULLIF(s.currency, ''), 'USD')
                """,
                tuple(kids),
            ).fetchall()
            currencies: dict[int, list[dict[str, Any]]] = {kid: [] for kid in kids}
            for row in rows:
                data = dict(row)
                currencies[int(data["kol_pool_id"])].append(data)
            for kid, buckets in currencies.items():
                if not buckets:
                    continue
                primary = max(
                    buckets,
                    key=lambda value: (int(value.get("revenue_cents") or 0), int(value.get("orders") or 0)),
                )
                signals[kid].update(
                    {
                        "revenue_cents": int(primary.get("revenue_cents") or 0),
                        "commission_cents": int(primary.get("commission_cents") or 0),
                        "orders": int(primary.get("orders") or 0),
                        "currency": str(primary.get("currency") or "USD"),
                        "mixed_currency": len(buckets) > 1,
                    }
                )
        except Exception:
            logger.debug("roi.high_value_revenue_batch_failed", exc_info=True)
            for kid in kids:
                signals[kid].update({"revenue_cents": None, "commission_cents": None, "orders": None})

    if table_exists("vkpi_recommendation_outcomes"):
        try:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                  SELECT kol_pool_id, was_claimed, agreement_reached, content_published,
                         ROW_NUMBER() OVER (PARTITION BY kol_pool_id ORDER BY id DESC) AS rn
                  FROM vkpi_recommendation_outcomes
                  WHERE kol_pool_id IN ({placeholders})
                )
                SELECT kol_pool_id, COUNT(*) AS total,
                       SUM(CASE WHEN was_claimed THEN 1 ELSE 0 END) AS claimed,
                       SUM(CASE WHEN agreement_reached THEN 1 ELSE 0 END) AS agreed,
                       SUM(CASE WHEN content_published THEN 1 ELSE 0 END) AS published
                FROM ranked WHERE rn <= 50 GROUP BY kol_pool_id
                """,
                tuple(kids),
            ).fetchall()
            for row in rows:
                data = dict(row)
                total = int(data.get("total") or 0)
                if total:
                    signals[int(data["kol_pool_id"])]["funnel_weight"] = min(
                        1.0,
                        max(
                            0.0,
                            (
                                0.2 * int(data.get("claimed") or 0)
                                + 0.3 * int(data.get("agreed") or 0)
                                + 0.5 * int(data.get("published") or 0)
                            )
                            / total,
                        ),
                    )
        except Exception:
            logger.debug("roi.high_value_funnel_batch_failed", exc_info=True)

    if table_exists("vkpi_agent_outcome_evaluations"):
        try:
            string_kids = [str(kid) for kid in kids]
            string_placeholders = ",".join("?" for _ in string_kids)
            rows = conn.execute(
                f"""
                WITH ranked AS (
                  SELECT entity_id, success,
                         ROW_NUMBER() OVER (PARTITION BY entity_id ORDER BY id DESC) AS rn
                  FROM vkpi_agent_outcome_evaluations
                  WHERE entity_type='kol' AND entity_id IN ({string_placeholders})
                )
                SELECT entity_id, COUNT(*) AS total,
                       SUM(CASE WHEN success IS TRUE THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN success IS FALSE THEN 1 ELSE 0 END) AS failures
                FROM ranked WHERE rn <= 20 GROUP BY entity_id
                """,
                tuple(string_kids),
            ).fetchall()
            for row in rows:
                data = dict(row)
                successes = int(data.get("successes") or 0)
                failures = int(data.get("failures") or 0)
                decided = successes + failures
                if decided:
                    signals[int(data["entity_id"])]["outcome_weight"] = successes / decided
        except Exception:
            logger.debug("roi.high_value_outcome_batch_failed", exc_info=True)

    for signal in signals.values():
        funnel_weight = signal.pop("funnel_weight", None)
        outcome_weight = signal.pop("outcome_weight", None)
        if funnel_weight is not None and outcome_weight is not None:
            signal["recommendation_weight"] = round(0.6 * funnel_weight + 0.4 * outcome_weight, 4)
        elif funnel_weight is not None:
            signal["recommendation_weight"] = round(funnel_weight, 4)
        elif outcome_weight is not None:
            signal["recommendation_weight"] = round(outcome_weight, 4)
        else:
            signal["recommendation_weight"] = None
        revenue = signal.get("revenue_cents")
        cost = signal.get("cost_cents")
        signal["roi"] = (
            round((int(revenue) - int(cost)) / int(cost), 4)
            if isinstance(revenue, int) and isinstance(cost, int) and cost > 0
            else None
        )
        signal["status"] = "ready" if isinstance(revenue, int) and revenue > 0 else "awaiting_m5"
    return signals


def _project_ids_for_kol(kol_pool_id: int) -> list[int]:
    if not table_exists("vkpi_project_kol_assignments"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT DISTINCT project_id FROM vkpi_project_kol_assignments WHERE kol_pool_id = ?",
            (int(kol_pool_id),),
        ).fetchall()
        return [int(dict(r)["project_id"]) for r in rows if dict(r).get("project_id")]
    except Exception:
        logger.debug("roi.project_ids_failed", exc_info=True)
        return []


def get_kol_roi_summary(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """该 KOL 关联项目的 ROI 汇总(只读)。无项目→no_projects;无 revenue→awaiting_m5。"""
    del staff  # ROI 读公共业务聚合,不做按人 scope(KOL 维度口径全局一致)。
    kid = int(kol_pool_id or 0)
    if kid <= 0:
        return {"status": "not_found"}
    pids = _project_ids_for_kol(kid)
    if not pids:
        return {
            "kol_pool_id": kid,
            "total_projects": 0,
            "status": "no_projects",
            "roi": None,
            "revenue_cents": None,
            "cost_cents": None,
            "note": "该 KOL 暂无关联项目;ROI 待项目 + 商业数据接入。",
        }
    placeholders = ",".join("?" for _ in pids)
    clause = f"AND project_id IN ({placeholders})"
    cost = metrics_agg._sum_cost(clause, list(pids))
    revenue = metrics_agg._sum_revenue(clause, list(pids))
    rev = revenue.get("revenue_cents")
    roi = None
    net = None
    if isinstance(rev, int) and isinstance(cost, int):
        net = rev - cost
        if cost > 0:
            roi = round((rev - cost) / cost, 4)
    return {
        "kol_pool_id": kid,
        "total_projects": len(pids),
        "roi": roi,
        "net_cents": net,
        "revenue_cents": rev,
        "cost_cents": cost,
        "commission_cents": revenue.get("commission_cents"),
        "orders": revenue.get("orders"),
        "status": "ready" if (isinstance(rev, int) and rev > 0) else "awaiting_m5",
        "note": "ROI 为独立展示信号,绝不并入 viltrox_fit_score;无 revenue 时 awaiting_m5(非假 0)。",
    }


def list_high_value_kols(*, limit: int = 10, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """高价值红人榜:按合作项目数取候选,附 ROI + 下次推荐权重,按权重→项目数排序(只读)。

    喂个人工作台。复用 get_kol_roi_summary + compute_next_recommendation_weight。零触 fit。
    """
    del staff
    if not table_exists("vkpi_project_kol_assignments"):
        return {"items": [], "available": False, "reason": "assignments_table_absent"}
    n = max(1, min(int(limit or 10), 50))
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT kol_pool_id, COUNT(DISTINCT project_id) AS projects "
            "FROM vkpi_project_kol_assignments GROUP BY kol_pool_id ORDER BY projects DESC LIMIT ?",
            (n,),
        ).fetchall()
    except Exception:
        logger.warning("roi.high_value_read_failed", exc_info=True)
        return {"items": [], "available": False, "reason": "read_error"}
    kids = [int(dict(r)["kol_pool_id"]) for r in rows if dict(r).get("kol_pool_id")]
    proj = {int(dict(r)["kol_pool_id"]): int(dict(r)["projects"]) for r in rows if dict(r).get("kol_pool_id")}
    names: dict[int, str] = {}
    if kids and table_exists("vkpi_kol_pool"):
        try:
            ph = ",".join("?" for _ in kids)
            for nr in conn.execute(f"SELECT id, COALESCE(display_name, handle, '') AS label FROM vkpi_kol_pool WHERE id IN ({ph})", kids).fetchall():
                names[int(dict(nr)["id"])] = str(dict(nr).get("label") or "")
        except Exception:
            logger.debug("roi.high_value_names_failed", exc_info=True)
    signals = _bulk_high_value_signals(conn, kids)
    items: list[dict[str, Any]] = []
    for kid in kids:
        signal = signals.get(kid, {})
        items.append({
            "kol_pool_id": kid,
            "name": names.get(kid, f"KOL #{kid}"),
            "projects": proj.get(kid, 0),
            "roi": signal.get("roi"),
            "revenue_cents": signal.get("revenue_cents"),
            "status": signal.get("status", "awaiting_m5"),
            "recommendation_weight": signal.get("recommendation_weight"),
        })
    items.sort(key=lambda x: (-(x["recommendation_weight"] or 0), -x["projects"]))
    return {"items": items, "available": True, "count": len(items),
            "note": "高价值红人榜:权重/ROI 为独立展示信号,绝不并入 viltrox_fit_score。"}


def _funnel_weight(kid: int, lookback: int) -> float | None:
    """漏斗加权:认领 0.2 + 达成合作 0.3 + 内容发布 0.5(发布最重),样本均值,clamp[0,1]。"""
    if kid <= 0 or not table_exists("vkpi_recommendation_outcomes"):
        return None
    try:
        rows = get_conn().execute(
            """
            SELECT was_claimed, agreement_reached, content_published
            FROM vkpi_recommendation_outcomes
            WHERE kol_pool_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (kid, max(1, min(int(lookback or 50), 200))),
        ).fetchall()
    except Exception:
        logger.debug("roi.weight_read_failed", exc_info=True)
        return None
    if not rows:
        return None
    n = len(rows)
    claimed = sum(1 for r in rows if dict(r).get("was_claimed"))
    agreed = sum(1 for r in rows if dict(r).get("agreement_reached"))
    published = sum(1 for r in rows if dict(r).get("content_published"))
    return min(1.0, max(0.0, (0.2 * claimed + 0.3 * agreed + 0.5 * published) / n))


def compute_next_recommendation_weight(kol_pool_id: int, *, lookback: int = 50) -> float | None:
    """0-1 展示权重(独立信号,绝不并入 fit)。漏斗成功度 + Agent 结果回写融合。

    B5/H4 学习闭环:除推荐漏斗外,读 vkpi_agent_outcome_evaluations 的近期成功/失败,
    让执行结果回流到下次推荐(失败模式降权、成功模式升权)。两源都无 → None。
    """
    kid = int(kol_pool_id or 0)
    if kid <= 0:
        return None
    base = _funnel_weight(kid, lookback)
    # Agent 结果回写信号(成功率)。
    try:
        from app.domains.memory import agent_memory_writer

        oc = agent_memory_writer.recent_outcome_stats("kol", kid, lookback=20)
    except Exception:
        oc = {"total": 0}
    outcome_weight = None
    if int(oc.get("total") or 0) > 0:
        decided = int(oc.get("success") or 0) + int(oc.get("fail") or 0)
        if decided > 0:
            outcome_weight = int(oc.get("success") or 0) / decided
    # 融合:两源都有 → 0.6 漏斗 + 0.4 结果回写;只一源 → 该源;都无 → None。
    if base is not None and outcome_weight is not None:
        weight = 0.6 * base + 0.4 * outcome_weight
    elif base is not None:
        weight = base
    elif outcome_weight is not None:
        weight = outcome_weight
    else:
        return None
    return round(min(1.0, max(0.0, weight)), 4)
