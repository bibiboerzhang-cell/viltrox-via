"""KOL 桥修复单测:bet subject(entity_type='kol')→ materialize payload 顶层 kol_pool_id
→ verdict_flow._bet_context 读到 int(vkpi_gtm_outcomes.kol_pool_id 不再落 NULL)。

不连真 DB:_suggestion_for_bet 与 _bet_context 均为纯函数(make_suggestion 是纯 dict 构造)。
覆盖:新链路顶层键 + 旧数据(payload 无顶层键)entity_type='kol' 兜底 + 'kol_pool' 旧口径回归
+ 非数字 entity_id 不加不炸。
"""
from __future__ import annotations

from app.domains.market_brain import materialize, verdict_flow


def _kol_bet(kid: int = 4321) -> dict:
    """带 kol subject 的七要素 bet(与 gtm_bets._kol_outreach_items 产出结构对齐)。"""
    return {
        "bet_key": f"kol:{kid}",
        "action_type": "kol_outreach",
        "gtm_plan_id": "gtmp-test",
        "why": {"statement": "候选池排名靠前且可估价"},
        "what": "外联候选 KOL @tester(youtube)并寄样 SKU-X",
        "who": f"@tester(kol_pool_id={kid},youtube)",
        "expected": {"statement": "7 天内收到有效回复并进入排期", "metrics": []},
        "cost": {"statement": "预估报价 p50 $200", "usd_p50": 200},
        "risk": "无标签",
        "risk_level": "low",
        "escalate_if": "回复率超预期加码",
        "retreat_if": "两周无回复撤退",
        "review_at": "2026-07-14",
        "review_days": 7,
        "requires_approval": True,
        "subject": {"entity_type": "kol", "entity_id": str(kid)},
    }


def _suggestion(bet: dict) -> dict:
    return materialize._suggestion_for_bet(
        item={"action": bet["what"], "reason": "候选池排名靠前"},
        bet=bet,
        gtm_plan_id="gtmp-test",
        sku="SKU-X",
        country="US",
        goal="exposure",
        budget_usd=3000.0,
        window_days=30,
        plan_generated_at="2026-07-07T00:00:00Z",
        actor_staff_id=None,
    )


def test_materialize_payload_carries_kol_pool_id_and_context_reads_int():
    """新链路:kol subject → payload 顶层 kol_pool_id(int)→ _bet_context 直取非 None。"""
    sug = _suggestion(_kol_bet(kid=4321))
    payload = sug["payload"]
    assert payload["kol_pool_id"] == 4321
    assert isinstance(payload["kol_pool_id"], int)

    row = {
        "id": 1,
        "entity_type": sug["entity_type"],  # 'kol'(subject 契约)
        "entity_id": sug["entity_id"],  # '4321'(make_suggestion 已 str 化)
        "payload": payload,
    }
    ctx = verdict_flow._bet_context(row)
    assert ctx["kol_pool_id"] is not None
    assert isinstance(ctx["kol_pool_id"], int)
    assert ctx["kol_pool_id"] == 4321


def test_legacy_payload_without_top_key_falls_back_on_entity_type_kol():
    """旧数据回归:payload 无顶层 kol_pool_id,行 entity_type='kol' → 兜底从 entity_id 取到 int。"""
    row = {
        "id": 2,
        "entity_type": "kol",
        "entity_id": "987",
        "payload": {"gtm_plan_id": "gtmp-old", "bet": _kol_bet(kid=987)},
    }
    ctx = verdict_flow._bet_context(row)
    assert ctx["kol_pool_id"] == 987
    assert isinstance(ctx["kol_pool_id"], int)


def test_entity_type_kol_pool_fallback_still_works():
    """旧口径 entity_type='kol_pool' 兜底不回退。"""
    row = {"id": 3, "entity_type": "kol_pool", "entity_id": "55", "payload": {"bet": {}}}
    ctx = verdict_flow._bet_context(row)
    assert ctx["kol_pool_id"] == 55


def test_non_numeric_entity_id_does_not_add_key_or_crash():
    """int 化失败:payload 不加顶层键、不炸;_bet_context 诚实回 None。"""
    bet = _kol_bet()
    bet["subject"] = {"entity_type": "kol", "entity_id": "not-a-number"}
    sug = _suggestion(bet)
    assert "kol_pool_id" not in sug["payload"]

    row = {"id": 4, "entity_type": "kol", "entity_id": "not-a-number", "payload": sug["payload"]}
    ctx = verdict_flow._bet_context(row)
    assert ctx["kol_pool_id"] is None
