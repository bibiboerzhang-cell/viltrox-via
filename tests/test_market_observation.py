"""N7 市场观察生成测试 —— 合成结构正确 + 无真数据时诚实空返回。

只 monkeypatch 三路上游 service(market_brain / competitor_radar / bet_ledger)的纯函数,
不碰 DB;验证:
  1. 每条观察含全部约定字段 {topic, kind, source, evidence_refs, confidence, suggested_action}。
  2. 三路真数据各自映射到正确 kind(热点/竞品/机会/风险)。
  3. 任一来源异常 best-effort 跳过,不崩。
  4. 全部来源空 → observations 空、count=0,绝不臆造。
"""
from __future__ import annotations

from app.domains.market import market_observation as mo


# ---------------- 假上游 ----------------

def _fake_brief() -> dict:
    return {
        "status": "ok",
        "sections": {
            "hot_products": {"items": ["35mm F1.4 询问量上涨", {"title": "AF 16-35"}], "data_status": "ready"},
            "rising_channels": {"items": ["YouTube 评测"], "data_status": "stale_or_empty"},
            "competitor_moves": {"items": [], "data_status": "stale_or_empty"},
            "opportunities": {
                "items": [{"title": "试投 35mm 新方向", "suggested_action": "试投一轮", "confidence": "high"}],
                "confidence_cap": "high",
            },
            "today_actions": {"items": [], "data_status": "ready"},
        },
    }


def _fake_radar() -> dict:
    # 2026-07-18 超龄闸后:未过就绪门或快照超 72h 的雷达不再产观察——
    # 测试样本必须自带新鲜就绪态(与真实 getter 的顶层字段同形)。
    return {
        "available": True,
        "is_ready": True,
        "age_hours": 2.0,
        "model": "gemini",
        "content": {
            "items": [
                {"brand": "Sony", "title": "新 50mm 发布", "summary": "...", "impact": "价格压力,关注我方 50mm 站位"},
            ]
        },
    }


def _fake_bets(*, outcome: str = "", limit: int = 50, **_kw) -> dict:
    return {
        "status": "ok",
        "bets": [
            {"hypothesis": "押注:35mm 命中", "bet_uid": "bet_a", "review_at": "2026-07-01", "risk_level": "high"},
        ],
        "hit_rate": None,
        "settled": 0,
    }


def _patch_all(monkeypatch) -> None:
    from app.domains.market import market_brain, competitor_radar, bet_ledger
    monkeypatch.setattr(market_brain, "build_daily_brief", lambda *a, **k: _fake_brief())
    monkeypatch.setattr(competitor_radar, "get_competitor_radar", lambda *a, **k: _fake_radar())
    monkeypatch.setattr(bet_ledger, "list_bets", _fake_bets)


# ---------------- 测试 ----------------

def test_synthesis_structure_and_kinds(monkeypatch) -> None:
    _patch_all(monkeypatch)
    out = mo.generate_observations()

    assert out["status"] == "ok"
    assert out["count"] == len(out["observations"]) > 0
    assert set(out["sources_used"]) == {"market_brain", "competitor_radar", "bet_ledger"}

    required = {"topic", "kind", "source", "evidence_refs", "confidence", "suggested_action"}
    for o in out["observations"]:
        assert required <= set(o.keys())
        assert o["topic"]  # 绝不空壳
        assert o["kind"] in (mo.KIND_HOT, mo.KIND_COMPETITOR, mo.KIND_OPPORTUNITY, mo.KIND_RISK)
        assert o["confidence"] in ("high", "med", "low")
        assert isinstance(o["evidence_refs"], list)

    kinds = {o["kind"] for o in out["observations"]}
    assert mo.KIND_HOT in kinds        # 来自 hot_products / rising_channels
    assert mo.KIND_OPPORTUNITY in kinds  # 来自 opportunities
    assert mo.KIND_COMPETITOR in kinds   # 来自 competitor_radar
    assert mo.KIND_RISK in kinds         # 来自 open 押注

    # by_kind 计数与 observations 一致
    assert sum(out["by_kind"].values()) == out["count"]


def test_competitor_observation_has_brand_and_impact(monkeypatch) -> None:
    _patch_all(monkeypatch)
    out = mo.generate_observations()
    comp = [o for o in out["observations"] if o["kind"] == mo.KIND_COMPETITOR]
    assert comp
    o = comp[0]
    assert "Sony" in o["topic"]
    assert o["source"] == "competitor_radar"
    assert o["suggested_action"]  # impact → suggested_action
    assert o["confidence"] == "high"


def test_high_risk_bet_lowers_confidence(monkeypatch) -> None:
    _patch_all(monkeypatch)
    out = mo.generate_observations()
    risk = [o for o in out["observations"] if o["kind"] == mo.KIND_RISK]
    assert risk
    # risk_level=high → 观察更不确定 → confidence low
    assert risk[0]["confidence"] == "low"


def test_empty_when_no_data(monkeypatch) -> None:
    from app.domains.market import market_brain, competitor_radar, bet_ledger
    monkeypatch.setattr(market_brain, "build_daily_brief",
                        lambda *a, **k: {"status": "ok", "sections": {}})
    monkeypatch.setattr(competitor_radar, "get_competitor_radar",
                        lambda *a, **k: {"available": False, "reason": "not_generated_yet"})
    monkeypatch.setattr(bet_ledger, "list_bets",
                        lambda *a, **k: {"status": "unavailable", "bets": []})

    out = mo.generate_observations()
    assert out["status"] == "ok"
    assert out["count"] == 0
    assert out["observations"] == []
    assert out["sources_used"] == []
    assert out["by_kind"] == {}


def test_best_effort_survives_source_exception(monkeypatch) -> None:
    from app.domains.market import market_brain, competitor_radar, bet_ledger

    def _boom(*a, **k):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(market_brain, "build_daily_brief", _boom)
    monkeypatch.setattr(competitor_radar, "get_competitor_radar", lambda *a, **k: _fake_radar())
    monkeypatch.setattr(bet_ledger, "list_bets", _boom)

    out = mo.generate_observations()
    # market_brain / bet_ledger 炸 → 跳过;competitor_radar 仍产出
    assert out["status"] == "ok"
    assert out["sources_used"] == ["competitor_radar"]
    assert all(o["kind"] == mo.KIND_COMPETITOR for o in out["observations"])
