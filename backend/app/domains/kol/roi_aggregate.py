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
    items: list[dict[str, Any]] = []
    for kid in kids:
        roi = get_kol_roi_summary(kid)
        items.append({
            "kol_pool_id": kid,
            "name": names.get(kid, f"KOL #{kid}"),
            "projects": proj.get(kid, 0),
            "roi": roi.get("roi"),
            "revenue_cents": roi.get("revenue_cents"),
            "status": roi.get("status"),
            "recommendation_weight": compute_next_recommendation_weight(kid),
        })
    items.sort(key=lambda x: (-(x["recommendation_weight"] or 0), -x["projects"]))
    return {"items": items, "available": True, "count": len(items),
            "note": "高价值红人榜:权重/ROI 为独立展示信号,绝不并入 viltrox_fit_score。"}


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
