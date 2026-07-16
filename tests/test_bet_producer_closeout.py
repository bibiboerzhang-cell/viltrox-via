"""闭环C 收尾测试:bet_producer verification_date(传入 today 锚)+ approve→真下注端点。

覆盖两点:
1. produce_bet_drafts 据传入 today 算出确定的 verification_date(不依赖墙钟);机会窗自带到期优先采信。
2. POST /agents/bets/{id}/approve:仅 'bet' 草案 + approved 成功才 create_bet;双闸强制 cost/gain=0,
   绝不触 fit。非 bet / 未通过审批 / 缺 hypothesis → 据实拒绝,绝不下注。
"""
from __future__ import annotations

from datetime import date

import pytest

from app.domains.market import bet_producer


def _fake_pred(*, window_days: int, limit: int, **_kw) -> dict:
    return {
        "confidence_cap": "high",
        "opportunities": [
            {"title": "35mm 新方向", "basis": "竞品热度", "suggested_action": "试投一轮",
             "confidence": "high", "data_status": "real"},
        ],
    }


def test_verification_date_anchored_on_passed_today(monkeypatch) -> None:
    monkeypatch.setattr(bet_producer, "_load_opportunities", _fake_pred)
    out = bet_producer.produce_bet_drafts(dry_run=True, today="2026-01-01", min_confidence="low")

    assert out["status"] == "ok"
    assert out["today"] == "2026-01-01"
    assert len(out["drafts"]) == 1
    draft = out["drafts"][0]
    # today(2026-01-01) + 默认窗口(21d) = 2026-01-22,确定值,不依赖墙钟。
    assert draft["verification_date"] == "2026-01-22"
    # review_at 与 verification_date 同一天(ISO datetime,00:00 UTC)。
    assert draft["review_at"].startswith("2026-01-22")
    # suggestion payload 把 verification_date 透传给审批端点。
    assert out["suggestions"][0]["payload"]["verification_date"] == "2026-01-22"


def test_verification_date_accepts_date_object(monkeypatch) -> None:
    monkeypatch.setattr(bet_producer, "_load_opportunities", _fake_pred)
    out = bet_producer.produce_bet_drafts(dry_run=True, today=date(2026, 6, 1), min_confidence="low")
    assert out["drafts"][0]["verification_date"] == "2026-06-22"


def test_verification_date_never_empty_even_without_today(monkeypatch) -> None:
    monkeypatch.setattr(bet_producer, "_load_opportunities", _fake_pred)
    out = bet_producer.produce_bet_drafts(dry_run=True, min_confidence="low")
    # 缺省兜底也必须产出非空、可解析的复盘日。
    vd = out["drafts"][0]["verification_date"]
    assert vd and date.fromisoformat(vd)


def test_opportunity_own_deadline_takes_priority(monkeypatch) -> None:
    def _pred(**_kw) -> dict:
        return {
            "confidence_cap": "high",
            "opportunities": [
                {"title": "限时窗", "confidence": "high", "deadline": "2026-03-15"},
            ],
        }

    monkeypatch.setattr(bet_producer, "_load_opportunities", _pred)
    out = bet_producer.produce_bet_drafts(dry_run=True, today="2026-01-01", min_confidence="low")
    # 机会窗自带 deadline → verification_date 采信它,而非 today+窗口。
    assert out["drafts"][0]["verification_date"] == "2026-03-15"


def test_dry_run_persists_nothing(monkeypatch) -> None:
    monkeypatch.setattr(bet_producer, "_load_opportunities", _fake_pred)
    out = bet_producer.produce_bet_drafts(dry_run=True, today="2026-01-01", min_confidence="low")
    assert out["dry_run"] is True
    assert out["persisted"] == 0


# ---------------- approve→真下注 端点 ----------------

def _load_vkpi_agents():
    """直接按文件路径加载 vkpi_agents,绕过 routers/__init__ barrel。

    barrel 会连带 import 全部 sibling 路由;并行开发期某个 sibling 可能临时语法不全,
    不该拖垮本模块独立测试(本测试只关心 vkpi_agents 自身的端点)。
    """
    import importlib.util
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "backend", "app", "api", "routers", "vkpi_agents.py")
    spec = importlib.util.spec_from_file_location("vkpi_agents_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vkpi_agents = _load_vkpi_agents()  # noqa: E402


class _HTTPExc(Exception):
    def __init__(self, status_code: int, detail) -> None:
        self.status_code = status_code
        self.detail = detail


def _patch_http(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_agents, "HTTPException", _HTTPExc)


def _bet_item(**over) -> dict:
    base = {
        "id": 7,
        "category": "bet",
        "requires_approval": 1,  # compat BOOLEAN 读回 int 1
        "detail": "押注:35mm 新方向",
        "payload_json": {
            "hypothesis": "押注:35mm 新方向 命中",
            "probability": 0.7,
            "verification_date": "2026-01-22",
            "review_at": "2026-01-22T00:00:00+00:00",
            "risk_level": "medium",
            "expected_gain_cents": 0,
            "cost_cents": 0,
            "evidence": {"refs": [{"type": "market_opportunity"}]},
        },
    }
    base.update(over)
    return base


def test_approve_endpoint_creates_bet_with_zero_cost(monkeypatch) -> None:
    _patch_http(monkeypatch)
    captured: dict = {}

    class _Inbox:
        @staticmethod
        def get_action(action_id, staff):
            return _bet_item()

        @staticmethod
        def approve_action(action_id, staff, reason=""):
            captured["approved_reason"] = reason
            return {"ok": True, "status": "approved", "action_id": action_id}

    class _Ledger:
        @staticmethod
        def create_bet(**kw):
            captured["create_bet"] = kw
            return {"status": "ok", "bet_id": 99, "bet_uid": "bet_x"}

    # 隔离健壮:端点 `from app.domains.actions import inbox` 读包属性(真模块),不读 sys.modules
    # 覆盖;全量套件里真模块已被前序测试 import → setitem 被绕过返回真 get_action→404。改 patch
    # 真模块的函数属性(端点 resolve 到的是同一对象),顺序无关。
    import app.domains.actions.inbox as _real_inbox
    import app.domains.market.bet_ledger as _real_ledger
    monkeypatch.setattr(_real_inbox, "get_action", _Inbox.get_action)
    monkeypatch.setattr(_real_inbox, "approve_action", _Inbox.approve_action)
    monkeypatch.setattr(_real_ledger, "create_bet", _Ledger.create_bet)

    res = vkpi_agents.bet_approve_from_inbox(7, {"reason": "ship it"}, staff={"id": 1})

    assert res["status"] == "ok"
    assert res["bet"]["bet_id"] == 99
    assert res["verification_date"] == "2026-01-22"
    # 双闸:强制不烧钱、不许诺收益。
    assert captured["create_bet"]["cost_cents"] == 0
    assert captured["create_bet"]["expected_gain_cents"] == 0
    assert captured["create_bet"]["source_action_inbox_id"] == 7
    assert captured["create_bet"]["review_at"] == "2026-01-22T00:00:00+00:00"
    assert captured["approved_reason"] == "ship it"


def test_approve_rejects_non_bet_category(monkeypatch) -> None:
    _patch_http(monkeypatch)

    class _Inbox:
        @staticmethod
        def get_action(action_id, staff):
            return _bet_item(category="kol_profile")

        approve_action = staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not approve")))

    import app.domains.actions.inbox as _real_inbox
    monkeypatch.setattr(_real_inbox, "get_action", _Inbox.get_action)
    monkeypatch.setattr(_real_inbox, "approve_action", _Inbox.approve_action)
    with pytest.raises(_HTTPExc) as ei:
        vkpi_agents.bet_approve_from_inbox(7, {}, staff={"id": 1})
    assert ei.value.status_code == 400


def test_approve_blocks_when_state_machine_rejects(monkeypatch) -> None:
    _patch_http(monkeypatch)
    sentinel: dict = {"created": False}

    class _Inbox:
        @staticmethod
        def get_action(action_id, staff):
            return _bet_item()

        @staticmethod
        def approve_action(action_id, staff, reason=""):
            return {"ok": False, "reason": "illegal_state_transition"}

    class _Ledger:
        @staticmethod
        def create_bet(**kw):
            sentinel["created"] = True
            return {"status": "ok"}

    # 隔离健壮:端点 `from app.domains.actions import inbox` 读包属性(真模块),不读 sys.modules
    # 覆盖;全量套件里真模块已被前序测试 import → setitem 被绕过返回真 get_action→404。改 patch
    # 真模块的函数属性(端点 resolve 到的是同一对象),顺序无关。
    import app.domains.actions.inbox as _real_inbox
    import app.domains.market.bet_ledger as _real_ledger
    monkeypatch.setattr(_real_inbox, "get_action", _Inbox.get_action)
    monkeypatch.setattr(_real_inbox, "approve_action", _Inbox.approve_action)
    monkeypatch.setattr(_real_ledger, "create_bet", _Ledger.create_bet)

    with pytest.raises(_HTTPExc) as ei:
        vkpi_agents.bet_approve_from_inbox(7, {}, staff={"id": 1})
    assert ei.value.status_code == 409
    # 仅 approved 才下注:审批失败时绝不 create_bet。
    assert sentinel["created"] is False


def test_approve_404_when_out_of_scope(monkeypatch) -> None:
    _patch_http(monkeypatch)

    class _Inbox:
        @staticmethod
        def get_action(action_id, staff):
            return None

    import app.domains.actions.inbox as _real_inbox
    monkeypatch.setattr(_real_inbox, "get_action", _Inbox.get_action)
    with pytest.raises(_HTTPExc) as ei:
        vkpi_agents.bet_approve_from_inbox(7, {}, staff={"id": 1})
    assert ei.value.status_code == 404


def test_truthy_handles_int_boolean_readback() -> None:
    # compat BOOLEAN 读回 int 1/0;requires_approval=1 必须判真。
    assert vkpi_agents._truthy(1) is True
    assert vkpi_agents._truthy(0) is False
    assert vkpi_agents._truthy(True) is True
