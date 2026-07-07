"""V-KPI GTM 90 天北极星三指标路由(U3 · 会呼吸的指挥室)。

- GET /api/admin/vkpi/gtm/northstar
  → 三表盘数据,前端 NorthStarGauges 挂 GTM Command 顶部:
    ① launch_briefs  · launch brief 数(目标 30)   ← vkpi_product_launches 未删行
    ② dealers        · Dealer 行数(目标 300)      ← vkpi_dealers 全行
    ③ verdict_rate   · GTM 裁决率 %(目标 30%)     ← vkpi_gtm_outcomes decided 比例
                       (闭环波迁移 217;decided = decided_at 非空 或 decision 非 open)

诚实纪律:三指标全部真库现查;表未建 → value=0 + status="table_missing"(绝不编数,
展示欠账正是目的);单指标查询失败 → 该指标 status="error" 不拖垮整卡;整端点异常
不 500,回 {status:"error", reason}(前端安静降级)。
红线:纯读零写库零 LLM 零采集;不触 viltrox_fit_score / rule_v0;响应无 private 字段。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-gtm-northstar"])

# 90 天北极星目标(作战地图口径:brief→30 / Dealer→300 / 裁决率→30%)。
_TARGET_BRIEFS = 30
_TARGET_DEALERS = 300
_TARGET_VERDICT_PCT = 30.0


def _count_metric(table: str, sql: str, *, target: float, unit: str, label: str) -> dict[str, Any]:
    """单计数指标:表缺 → 诚实 0 + table_missing;查询炸 → error(局部,不拖垮整卡)。"""
    from app.db.connection import get_conn, table_exists

    base = {"label": label, "target": target, "unit": unit, "note": ""}
    try:
        if not table_exists(table):
            return {**base, "value": 0, "status": "table_missing", "note": f"{table} 未建,诚实 0"}
        row = get_conn().execute(sql).fetchone()
        value = int(row["n"] if row and row["n"] is not None else 0)
        return {**base, "value": value, "status": "ok"}
    except Exception as exc:  # noqa: BLE001 — 单指标失败局部诚实,不炸整卡
        logger.warning("northstar count metric failed (%s): %s", table, exc)
        return {**base, "value": 0, "status": "error", "note": str(exc)[:200]}


def _verdict_metric() -> dict[str, Any]:
    """GTM 裁决率:decided/total(vkpi_gtm_outcomes,闭环波迁移 217)。表缺 → 0。"""
    from app.db.connection import get_conn, table_exists

    base = {
        "label": "GTM 裁决率",
        "target": _TARGET_VERDICT_PCT,
        "unit": "%",
        "note": "",
        "decided": 0,
        "total": 0,
    }
    try:
        if not table_exists("vkpi_gtm_outcomes"):
            return {
                **base,
                "value": 0.0,
                "status": "table_missing",
                "note": "vkpi_gtm_outcomes 未建(GTM-Loop 迁移 217 未落),裁决账本 0,诚实 0%",
            }
        # SUM(CASE) 而非 FILTER:PG / sqlite compat 双跑。decided 口径:已写裁决时间,
        # 或 decision 已非 open/空(GTM-Loop 规格:decided 即 finalized)。
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE
                       WHEN decided_at IS NOT NULL THEN 1
                       WHEN COALESCE(NULLIF(decision, ''), 'open') <> 'open' THEN 1
                       ELSE 0
                   END), 0) AS decided
            FROM vkpi_gtm_outcomes
            """
        ).fetchone()
        total = int(row["total"] if row and row["total"] is not None else 0)
        decided = int(row["decided"] if row and row["decided"] is not None else 0)
        rate = round(decided * 100.0 / total, 1) if total > 0 else 0.0
        return {**base, "value": rate, "status": "ok", "decided": decided, "total": total}
    except Exception as exc:  # noqa: BLE001 — 表在但列形状漂移等,局部诚实
        logger.warning("northstar verdict metric failed: %s", exc)
        return {**base, "value": 0.0, "status": "error", "note": str(exc)[:200]}


def _build_northstar() -> dict[str, Any]:
    return {
        "status": "ok",
        "window_days": 90,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "launch_briefs": _count_metric(
                "vkpi_product_launches",
                "SELECT COUNT(*) AS n FROM vkpi_product_launches WHERE deleted_at IS NULL",
                target=_TARGET_BRIEFS,
                unit="份",
                label="Launch Brief",
            ),
            "dealers": _count_metric(
                "vkpi_dealers",
                "SELECT COUNT(*) AS n FROM vkpi_dealers",
                target=_TARGET_DEALERS,
                unit="行",
                label="Dealer",
            ),
            "verdict_rate": _verdict_metric(),
        },
    }


@router.get("/gtm/northstar")
def get_gtm_northstar(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """90 天北极星三指标(全只读,真库现查,不写库)。"""
    del staff  # 权限已由 require_tab 校验;全局口径无 scope 二次过滤。
    try:
        return _build_northstar()
    except Exception as exc:  # noqa: BLE001 — 聚合失败不炸接口,诚实回原因
        logger.warning("gtm northstar failed: %s", exc, exc_info=True)
        return {"status": "error", "reason": str(exc)[:300]}
