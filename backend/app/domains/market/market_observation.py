"""N7 市场观察生成(加性,纯只读合成)—— 从现成 market 引擎合成一批"市场观察"。

把三路真数据合成统一的"市场观察"卡片(observation):
  - market_brain.build_daily_brief()  → 热点(hot_products / rising_channels)+ 机会(opportunities)
  - competitor_radar.get_competitor_radar() → 竞品(competitor)
  - bet_ledger.list_bets() → 风险(open 押注到期前的不确定性)

每条观察统一结构:
  {topic, kind(热点|竞品|机会|风险), source, evidence_refs, confidence, suggested_action}

红线 / 安全:
- 纯只读合成现成 service,绝不写任何表,绝不触 viltrox_fit_score。
- best-effort:任一引擎异常/表缺失 → 跳过该路,绝不让整体崩。
- 无真数据时诚实返回空 observations,绝不臆造内容。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 观察类型(中文口径,与产品语言一致)
KIND_HOT = "热点"
KIND_COMPETITOR = "竞品"
KIND_OPPORTUNITY = "机会"
KIND_RISK = "风险"

_VALID_KINDS = (KIND_HOT, KIND_COMPETITOR, KIND_OPPORTUNITY, KIND_RISK)

# confidence 三档(不臆造数值,按来源数据状态映射)
_CONF_HIGH = "high"
_CONF_MED = "med"
_CONF_LOW = "low"


def _s(value: Any, limit: int = 240) -> str:
    """安全转字符串并截断(防超长 / None)。"""
    return str(value or "").strip()[:limit]


def _make_observation(
    *,
    topic: str,
    kind: str,
    source: str,
    evidence_refs: list[dict[str, Any]] | None = None,
    confidence: str = _CONF_MED,
    suggested_action: str = "",
) -> dict[str, Any] | None:
    """构造一条结构化观察;topic 为空则丢弃(不臆造空壳)。"""
    t = _s(topic, 200)
    if not t:
        return None
    k = kind if kind in _VALID_KINDS else KIND_HOT
    c = confidence if confidence in (_CONF_HIGH, _CONF_MED, _CONF_LOW) else _CONF_MED
    return {
        "topic": t,
        "kind": k,
        "source": _s(source, 60),
        "evidence_refs": list(evidence_refs or []),
        "confidence": c,
        "suggested_action": _s(suggested_action, 240),
    }


def _conf_from_status(data_status: Any) -> str:
    """把引擎的 data_status / confidence_cap 映射到三档(诚实:stale/empty → low)。"""
    s = str(data_status or "").strip().lower()
    if s in ("ready", "real", "ok", "high"):
        return _CONF_HIGH
    if s in ("stale_or_empty", "empty", "error", "low", ""):
        return _CONF_LOW
    return _CONF_MED


def _observe_from_market_brain() -> list[dict[str, Any]]:
    """从 market_brain 日报合成 热点 + 机会 观察。best-effort。"""
    try:
        from app.domains.market import market_brain

        brief = market_brain.build_daily_brief()
    except Exception:
        logger.debug("market_observation.market_brain_failed", exc_info=True)
        return []
    if not isinstance(brief, dict):
        return []
    sections = brief.get("sections") or {}
    if not isinstance(sections, dict):
        return []
    out: list[dict[str, Any]] = []

    # 热点:产品热 + 上升渠道
    for key, label in (("hot_products", "产品热度"), ("rising_channels", "上升渠道")):
        sec = sections.get(key) or {}
        items = sec.get("items") if isinstance(sec, dict) else None
        if not isinstance(items, list):
            continue
        conf = _conf_from_status(sec.get("data_status") if isinstance(sec, dict) else None)
        for it in items[:5]:
            topic = it if isinstance(it, str) else (it.get("title") or it.get("name") or it.get("label")) if isinstance(it, dict) else None
            obs = _make_observation(
                topic=f"{label}:{_s(topic, 120)}" if topic else "",
                kind=KIND_HOT,
                source="market_brain",
                evidence_refs=[{"type": key, "raw": it if isinstance(it, dict) else {"value": _s(it, 120)}}],
                confidence=conf,
                suggested_action=_s(it.get("suggested_action"), 200) if isinstance(it, dict) else "",
            )
            if obs:
                out.append(obs)

    # 机会:opportunities(自带 suggested_action / confidence)
    opp_sec = sections.get("opportunities") or {}
    opps = opp_sec.get("items") if isinstance(opp_sec, dict) else None
    cap = opp_sec.get("confidence_cap") if isinstance(opp_sec, dict) else None
    if isinstance(opps, list):
        for it in opps[:6]:
            d = it if isinstance(it, dict) else {}
            obs = _make_observation(
                topic=_s(d.get("title") or d.get("topic") or it, 160),
                kind=KIND_OPPORTUNITY,
                source="market_brain",
                evidence_refs=[{"type": "market_opportunity", "raw": d or {"value": _s(it, 120)}}],
                confidence=_conf_from_status(d.get("confidence") or cap),
                suggested_action=_s(d.get("suggested_action") or d.get("basis"), 200),
            )
            if obs:
                out.append(obs)
    return out


def _observe_from_competitor_radar() -> list[dict[str, Any]]:
    """从 competitor_radar 合成 竞品 观察。best-effort。"""
    try:
        from app.domains.market import competitor_radar

        radar = competitor_radar.get_competitor_radar()
    except Exception:
        logger.debug("market_observation.competitor_radar_failed", exc_info=True)
        return []
    if not isinstance(radar, dict) or not radar.get("available"):
        return []
    content = radar.get("content") or {}
    items = content.get("items") if isinstance(content, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items[:6]:
        d = it if isinstance(it, dict) else {}
        brand = _s(d.get("brand"), 40)
        title = _s(d.get("title"), 160)
        topic = f"{brand}:{title}" if brand and title else (title or brand)
        if not topic:
            continue
        out.append(
            _make_observation(
                topic=topic,
                kind=KIND_COMPETITOR,
                source="competitor_radar",
                evidence_refs=[{"type": "competitor_radar", "raw": d}],
                confidence=_CONF_HIGH,  # radar 走 Gemini 接地搜索,真实事件
                suggested_action=_s(d.get("impact"), 200),
            )
        )
    return [o for o in out if o]


def _observe_from_bet_ledger() -> list[dict[str, Any]]:
    """从 bet_ledger 的 open 押注合成 风险 观察(未结算押注=待证不确定性)。best-effort。"""
    try:
        from app.domains.market import bet_ledger

        res = bet_ledger.list_bets(outcome="open", limit=20)
    except Exception:
        logger.debug("market_observation.bet_ledger_failed", exc_info=True)
        return []
    if not isinstance(res, dict) or res.get("status") != "ok":
        return []
    bets = res.get("bets") or []
    if not isinstance(bets, list):
        return []
    out: list[dict[str, Any]] = []
    for b in bets[:8]:
        d = b if isinstance(b, dict) else {}
        hyp = _s(d.get("hypothesis"), 160)
        if not hyp:
            continue
        # 风险口径:押注风险等级 high → 观察 confidence low(更不确定);low → high。
        risk = str(d.get("risk_level") or "med").lower()
        conf = {"high": _CONF_LOW, "med": _CONF_MED, "low": _CONF_HIGH}.get(risk, _CONF_MED)
        out.append(
            _make_observation(
                topic=f"未结算押注:{hyp}",
                kind=KIND_RISK,
                source="bet_ledger",
                evidence_refs=[{"type": "bet", "bet_uid": d.get("bet_uid"), "review_at": d.get("review_at")}],
                confidence=conf,
                suggested_action="到期复盘核验该假设,据实结算 won/lost/void。",
            )
        )
    return [o for o in out if o]


def generate_observations(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """N7 市场观察:只读合成 market_brain + competitor_radar + bet_ledger 真数据。

    返回一批结构化观察 {topic, kind, source, evidence_refs, confidence, suggested_action}。
    best-effort:任一来源缺失/异常 → 跳过该路;无任何真数据 → 诚实返回空 observations。
    纯只读,绝不写表,绝不触 viltrox_fit_score。
    """
    del staff  # 当前合成为全局市场视角,不按人过滤(读端 service 已自带 scope)

    observations: list[dict[str, Any]] = []
    sources_used: list[str] = []

    for name, fn in (
        ("market_brain", _observe_from_market_brain),
        ("competitor_radar", _observe_from_competitor_radar),
        ("bet_ledger", _observe_from_bet_ledger),
    ):
        try:
            part = fn()
        except Exception:
            logger.debug("market_observation.source_failed", extra={"source": name}, exc_info=True)
            part = []
        if part:
            sources_used.append(name)
            observations.extend(part)

    # 按 kind 统计(供前端后续分组;诚实计数)
    by_kind: dict[str, int] = {}
    for o in observations:
        by_kind[o["kind"]] = by_kind.get(o["kind"], 0) + 1

    return {
        "status": "ok",
        "count": len(observations),
        "observations": observations,
        "sources_used": sources_used,
        "by_kind": by_kind,
        "note": (
            "N7 市场观察:只读合成 market_brain/competitor_radar/bet_ledger 真数据;"
            "best-effort,无真数据诚实返回空,零臆造,零触 viltrox_fit_score。"
        ),
    }
