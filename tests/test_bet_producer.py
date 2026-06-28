"""闭环C · bet_producer 单测:dry_run 产草案结构 + 不写库 + 红线(不烧钱/需人审/不真下注)。

不连真 DB:monkeypatch _load_opportunities 注入假机会窗;断言 dry_run 产出结构 & 零落库。
"""
from __future__ import annotations

from app.domains.market import bet_producer


_FAKE_PRED = {
    "status": "ok",
    "confidence_cap": "medium",
    "opportunities": [
        {
            "title": "竞品热区:competitor_focus",
            "basis": "竞品信号 4 条 / 累计强度 12",
            "suggested_action": "评估该方向内容/产品投放,匹配上升渠道试投一轮观察",
            "confidence": "medium",
            "data_status": "awaiting_sales",
        },
        {
            "title": "竞品热区:voc_issue",
            "basis": "竞品信号 1 条 / 累计强度 2",
            "suggested_action": "试投观察",
            "confidence": "low",  # 应被 min_confidence=medium 滤掉
            "data_status": "awaiting_sales",
        },
    ],
}


def _inject(monkeypatch, pred=_FAKE_PRED):
    monkeypatch.setattr(bet_producer, "_load_opportunities",
                        lambda *, window_days, limit: dict(pred))


def test_dry_run_returns_drafts_without_db_write(monkeypatch):
    _inject(monkeypatch)
    # 红线哨兵:dry_run 路径绝不触 inbox.persist_suggestions。
    import app.domains.actions.inbox as inbox

    def _boom(*_a, **_k):
        raise AssertionError("dry_run must not persist to DB")

    monkeypatch.setattr(inbox, "persist_suggestions", _boom)

    res = bet_producer.produce_bet_drafts(dry_run=True)

    assert res["status"] == "ok"
    assert res["dry_run"] is True
    assert res["persisted"] == 0
    # min_confidence 默认 medium → low 机会被滤,只剩 1 条草案。
    assert len(res["drafts"]) == 1
    assert res["skipped_low_confidence"] == 1
    assert res["candidates_seen"] == 2


def test_draft_structure_has_bet_ledger_contract(monkeypatch):
    _inject(monkeypatch)
    res = bet_producer.produce_bet_drafts(dry_run=True)
    draft = res["drafts"][0]
    # create_bet 契约字段齐备。
    for key in ("dedupe_key", "hypothesis", "probability", "review_at",
                "risk_level", "evidence_refs"):
        assert key in draft, f"draft missing {key}"
    assert draft["hypothesis"].strip()
    # 概率封顶:cap=medium → ≤0.60(无真销量不浮夸)。
    assert 0.0 < draft["probability"] <= 0.60
    # review_at 是未来 ISO 时间戳。
    assert "T" in draft["review_at"]
    assert draft["dedupe_key"].startswith("bet:opportunity:")


def test_suggestion_is_bet_category_no_burn_needs_approval(monkeypatch):
    _inject(monkeypatch)
    res = bet_producer.produce_bet_drafts(dry_run=True)
    sug = res["suggestions"][0]
    assert sug["category"] == "bet"
    # 不烧钱红线。
    assert sug["estimated_cost_cents"] == 0
    assert sug["uses_llm"] is False
    # 草案本身不写业务表;真下注走人审闸。
    assert sug["writes_business_data"] is False
    assert sug["requires_approval"] is True
    # payload 携带 create_bet 契约,供审批端点真下注。
    pl = sug["payload"]
    assert pl["hypothesis"] and pl["probability"] is not None and pl["review_at"]
    assert "vkpi_bet_ledger" in sug["affected_tables"]


def test_empty_opportunities_yields_empty_drafts(monkeypatch):
    _inject(monkeypatch, pred={"status": "ok", "confidence_cap": "medium", "opportunities": []})
    res = bet_producer.produce_bet_drafts(dry_run=True)
    assert res["status"] == "ok"
    assert res["drafts"] == []
    assert res["suggestions"] == []
    assert res["persisted"] == 0


def test_prediction_failure_is_isolated(monkeypatch):
    # 引擎抛异常 → _load_opportunities 已兜空;produce 仍返 ok 空草案(容错隔离)。
    monkeypatch.setattr(bet_producer, "_load_opportunities",
                        lambda *, window_days, limit: {})
    res = bet_producer.produce_bet_drafts(dry_run=True)
    assert res["status"] == "ok"
    assert res["drafts"] == []
    assert res["candidates_seen"] == 0


def test_non_dry_run_persists_via_inbox_not_create_bet(monkeypatch):
    _inject(monkeypatch)
    calls = {"persist": 0, "create_bet": 0}

    import app.domains.actions.inbox as inbox
    import app.domains.market.bet_ledger as bet_ledger

    def _fake_persist(suggestions):
        calls["persist"] += 1
        assert isinstance(suggestions, list) and suggestions
        return len(suggestions)

    def _fake_create_bet(*_a, **_k):  # 红线:绝不被自动调用
        calls["create_bet"] += 1
        raise AssertionError("producer must never auto create_bet")

    monkeypatch.setattr(inbox, "persist_suggestions", _fake_persist)
    monkeypatch.setattr(bet_ledger, "create_bet", _fake_create_bet)

    res = bet_producer.produce_bet_drafts(dry_run=False)
    assert res["dry_run"] is False
    assert res["persisted"] == 1
    assert calls["persist"] == 1
    assert calls["create_bet"] == 0  # 永不自动真下注
