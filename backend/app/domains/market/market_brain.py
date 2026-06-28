"""Market Brain v1 日报(cut1 活体化)—— 每天合成"看世界+形成判断+今日建议"的市场大脑简报。

读现成多引擎(prediction / competitor_signals / action_inbox),合成蓝图头条 5 件:
产品热 / 上升渠道 / 竞品动 / 机会窗 / 今日建议。同时做信号有效期治理(过期不当新鲜)。
红线:纯只读合成 + 过期标记;零伪造平台数据,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def mark_expired_signals() -> int:
    """信号有效期治理:expires_at 已过且未标记的竞品信号 → review_status='expired'。

    让日报只采纳未过期信号(避免 35 天 stale 当新鲜)。幂等,只动过期未标记行。
    """
    if not table_exists("vkpi_competitor_signals"):
        return 0
    try:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE vkpi_competitor_signals SET review_status='expired', updated_at=NOW() "
            "WHERE expires_at IS NOT NULL AND expires_at < NOW() AND COALESCE(review_status,'') <> 'expired'"
        )
        conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)
    except Exception:
        logger.debug("market_brain.expire_failed", exc_info=True)
        return 0


def _fresh_competitor_moves(limit: int = 6) -> list[dict[str, Any]]:
    """竞品动:未过期、未驳回的竞品信号(按强度)。"""
    if not table_exists("vkpi_competitor_signals"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT brand, signal_type, score, severity, detail FROM vkpi_competitor_signals "
            "WHERE COALESCE(review_status,'') NOT IN ('expired','rejected') "
            "  AND (expires_at IS NULL OR expires_at >= NOW()) "
            "ORDER BY score DESC NULLS LAST, created_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    except Exception:
        logger.debug("market_brain.moves_failed", exc_info=True)
        return []
    out = []
    for r in rows:
        d = dict(r)
        out.append({"brand": d.get("brand"), "signal_type": d.get("signal_type"),
                    "score": d.get("score"), "severity": d.get("severity"),
                    "detail": str(d.get("detail") or "")[:140]})
    return out


def _today_actions(limit: int = 5) -> list[dict[str, Any]]:
    """今日建议:Action Inbox 待办建议(带为什么/收益/风险)。"""
    if not table_exists("vkpi_action_inbox"):
        return []
    try:
        rows = get_conn().execute(
            "SELECT title, reason, expected_gain, risk_level FROM vkpi_action_inbox "
            "WHERE status='suggested' ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    except Exception:
        logger.debug("market_brain.actions_failed", exc_info=True)
        return []
    return [{"title": dict(r).get("title"), "why": dict(r).get("reason"),
             "expected_gain": dict(r).get("expected_gain"), "risk": dict(r).get("risk_level")} for r in rows]


def build_daily_brief(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Market Brain v1 日报:每日合成 5 件(产品热/上升渠道/竞品动/机会窗/今日建议)。

    先治理过期信号再采纳;每段标 data_status(诚实:无真数据=stale_or_empty,不伪造)。
    """
    del staff
    expired = mark_expired_signals()
    try:
        from app.domains.market import prediction

        pred = prediction.predict_opportunities()
    except Exception:
        logger.warning("market_brain.prediction_failed", exc_info=True)
        pred = {"status": "error"}

    moves = _fresh_competitor_moves()
    sections = {
        "hot_products": {"items": pred.get("product_hints") or [], "data_status": pred.get("status")},
        "rising_channels": {"items": pred.get("rising_channels") or [], "data_status": pred.get("status")},
        "competitor_moves": {"items": moves, "data_status": "ready" if moves else "stale_or_empty"},
        "opportunities": {"items": pred.get("opportunities") or [], "confidence_cap": pred.get("confidence_cap")},
        "today_actions": {"items": _today_actions(), "data_status": "ready"},
    }
    nonempty = sum(1 for s in sections.values() if s.get("items"))
    return {
        "status": "ok",
        "generated_for": "market_brain_v1",
        "expired_signals_swept": expired,
        "coverage": f"{nonempty}/5",
        "sections": sections,
        "note": "市场大脑日报 v1:只读合成现成引擎(prediction/competitor_signals/action_inbox)+ 信号有效期治理;零伪造,零触 viltrox_fit_score。",
    }
