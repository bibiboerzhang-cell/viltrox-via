"""③ 预测 · 机会窗 —— 基于现有信号 surface "该关注什么"(诚实,非假销量预测)。

合成:竞品活跃热区(competitor_signals)+ 渠道动能(temporal_movers risers)→ 机会窗 + 建议动作。
红线:全程只读;无真销量(GMV)时 confidence 封顶 + 诚实标 data_status=awaiting_sales,绝不编造预测数字;
零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains import business_truth

logger = get_logger(__name__)


def _competitor_hotspots(window_days: int, limit: int = 6) -> list[dict[str, Any]]:
    if not table_exists("vkpi_competitor_signals"):
        return []
    try:
        rows = get_conn().execute(
            """
            SELECT signal_type, COUNT(*) AS cnt, COALESCE(SUM(score),0) AS total_score
            FROM vkpi_competitor_signals
            GROUP BY signal_type
            ORDER BY total_score DESC, cnt DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        logger.debug("prediction.hotspots_failed", exc_info=True)
        return []
    out = []
    for r in rows:
        d = dict(r)
        out.append({
            "signal_type": str(d.get("signal_type") or "unknown"),
            "count": int(d.get("cnt") or 0),
            "total_score": round(float(d.get("total_score") or 0), 2),
        })
    return out


def _product_hints(limit: int = 8) -> list[str]:
    if not table_exists("vkpi_competitor_signals"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT product_hints_json FROM vkpi_competitor_signals WHERE product_hints_json IS NOT NULL ORDER BY score DESC LIMIT 60"
        ).fetchall()
    except Exception:
        return []
    counts: dict[str, int] = {}
    for r in rows:
        raw = dict(r).get("product_hints_json")
        try:
            hints = raw if isinstance(raw, list) else json.loads(raw or "[]")
        except Exception:
            hints = []
        for h in hints if isinstance(hints, list) else []:
            key = str(h).strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]]


def predict_opportunities(*, window_days: int = 30, limit: int = 10, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """机会窗预测(signal-based)。无真销量 → confidence 封顶 medium,诚实标 awaiting_sales。"""
    del staff
    hotspots = _competitor_hotspots(window_days)
    hints = _product_hints()
    risers: list[dict[str, Any]] = []
    try:
        from app.domains.market import temporal_movers

        mv = temporal_movers.compute_movers(window_days=window_days, metric="total_views", limit=5)
        if mv.get("status") == "ok":
            risers = mv.get("risers", [])
    except Exception:
        logger.debug("prediction.movers_failed", exc_info=True)

    has_sales = table_exists("vkpi_sales_attributions") and bool(
        get_conn().execute(
            f"SELECT 1 FROM vkpi_sales_attributions WHERE {business_truth.verified_shopify_attribution_sql()} LIMIT 1"
        ).fetchone()
    )
    cap = "high" if has_sales else "medium"  # 无真销量 → confidence 封顶 medium

    opportunities: list[dict[str, Any]] = []
    for h in hotspots[:5]:
        conf = cap if h["total_score"] >= 3 else "low"
        opportunities.append({
            "title": f"竞品热区:{h['signal_type']}",
            "basis": f"竞品信号 {h['count']} 条 / 累计强度 {h['total_score']}" + (f";渠道动能 {len(risers)} 个上升频道" if risers else ""),
            "suggested_action": "评估该方向内容/产品投放,匹配上升渠道试投一轮观察",
            "confidence": conf,
            "data_status": "ready" if has_sales else "awaiting_sales",
        })

    return {
        "status": "ok",
        "window_days": int(window_days),
        "competitor_hotspots": hotspots,
        "product_hints": hints,
        "rising_channels": [{"name": r.get("name"), "pct_change": r.get("pct_change")} for r in risers],
        "opportunities": opportunities[: max(1, min(int(limit or 10), 20))],
        "confidence_cap": cap,
        "note": "机会窗基于竞品活跃 + 渠道动能 surface(非假销量预测);无真 GMV 时 confidence 封顶 medium、标 awaiting_sales;零触 viltrox_fit_score。",
    }
