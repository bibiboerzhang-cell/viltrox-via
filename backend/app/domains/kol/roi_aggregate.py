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
from app.domains.metrics import aggregation as metrics_agg

logger = get_logger(__name__)


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


def compute_next_recommendation_weight(kol_pool_id: int, *, lookback: int = 50) -> float | None:
    """据该 KOL 推荐漏斗成功度算 0-1 展示权重(独立信号,绝不并入 fit)。无 outcome → None。

    漏斗加权:认领 0.2 + 达成合作 0.3 + 内容发布 0.5(发布最重),按样本均值,clamp[0,1]。
    """
    kid = int(kol_pool_id or 0)
    if kid <= 0 or not table_exists("vkpi_recommendation_outcomes"):
        return None
    try:
        rows = get_conn().execute(
            """
            SELECT was_claimed, agreement_reached, content_published
            FROM vkpi_recommendation_outcomes
            WHERE kol_pool_id = ?
            ORDER BY id DESC
            LIMIT ?
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
    weight = (0.2 * claimed + 0.3 * agreed + 0.5 * published) / n
    return round(min(1.0, max(0.0, weight)), 4)
