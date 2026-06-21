"""R15 · 自建短链归因 GMV 聚合(只读:短链 → 点击 → 订单 → GMV → 佣金)。

读 vkpi_links + vkpi_link_clicks(点击,排除 bot)+ vkpi_sales_attributions(订单/GMV/佣金,
按 link_id 关联)→ 每条短链的 clicks/orders/revenue/commission/conversion + 总计。
红线:全程只读;confidence/转化为独立展示信号,绝不并入 viltrox_fit_score / rule_v0;
goaffpro 另计(本聚合不 merge,分 source 报）。RBAC:非管理层只见自己 staff 的短链。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.access import scope

logger = get_logger(__name__)


def aggregate_link_gmv(*, staff: dict[str, Any] | None = None, limit: int = 200) -> dict[str, Any]:
    """每条自建短链的点击/订单/GMV/佣金/转化聚合 + 总计(只读)。表缺 → available=False。"""
    if not table_exists("vkpi_links"):
        return {"items": [], "available": False, "totals": {}, "reason": "links_table_absent"}
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 200), 500))

    where = ""
    params: list[Any] = []
    if not scope.can_view_all(staff):
        aid = int(scope.actor_staff_id(staff))
        where = "WHERE l.staff_id = ? OR l.created_by_staff_id = ?"
        params = [aid, aid]

    try:
        rows = conn.execute(
            f"""
            SELECT l.id AS link_id, l.slug, l.platform, l.kol_id, l.project_id, l.campaign_name,
              COALESCE((SELECT COUNT(*) FROM vkpi_link_clicks ck
                        WHERE ck.link_id = l.id AND COALESCE(ck.is_bot, 0) = 0), 0) AS clicks,
              COALESCE((SELECT COUNT(*) FROM vkpi_sales_attributions s WHERE s.link_id = l.id), 0) AS orders,
              COALESCE((SELECT SUM(s.revenue_cents) FROM vkpi_sales_attributions s WHERE s.link_id = l.id), 0) AS revenue_cents,
              COALESCE((SELECT SUM(s.commission_cents) FROM vkpi_sales_attributions s WHERE s.link_id = l.id), 0) AS commission_cents
            FROM vkpi_links l
            {where}
            ORDER BY revenue_cents DESC, clicks DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    except Exception:
        logger.warning("gmv_aggregation.read_failed", exc_info=True)
        return {"items": [], "available": False, "totals": {}, "reason": "aggregation_error"}

    items: list[dict[str, Any]] = []
    for r in rows:
        it = dict(r)
        clk = int(it.get("clicks") or 0)
        ords = int(it.get("orders") or 0)
        # SUM 返回 Decimal → 统一 int(分),避免下游 JSON/计算的类型歧义。
        it["clicks"] = clk
        it["orders"] = ords
        it["revenue_cents"] = int(it.get("revenue_cents") or 0)
        it["commission_cents"] = int(it.get("commission_cents") or 0)
        it["conversion_rate"] = round(ords / clk, 4) if clk > 0 else None
        items.append(it)

    totals = {
        "links": len(items),
        "clicks": sum(int(i.get("clicks") or 0) for i in items),
        "orders": sum(int(i.get("orders") or 0) for i in items),
        "revenue_cents": sum(int(i.get("revenue_cents") or 0) for i in items),
        "commission_cents": sum(int(i.get("commission_cents") or 0) for i in items),
    }
    return {
        "items": items,
        "available": True,
        "totals": totals,
        "scope": "all" if scope.can_view_all(staff) else "own",
        "note": "自建短链归因(点击→订单→GMV→佣金);转化/confidence 为独立展示信号绝不并入 fit;goaffpro 另计未合并。",
    }
