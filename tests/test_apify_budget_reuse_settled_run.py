"""已结算预约 + run id 的同 task 重试:复用既有 run(免费)而不是 reservation_settled 拒绝(2026-08-23 搜索 0 结果三连击之一)。"""
from __future__ import annotations

from app.platform import apify_budget as ab
from app.platform.apify_budget_contracts import ApifyBudgetDecision, attach_reservation, provider_run_id


def test_provider_run_id_and_attach_reservation_helpers():
    assert provider_run_id({"id": "r1"}) == "r1"
    assert provider_run_id({"data": {"id": "r2"}}) == "r2"
    assert provider_run_id("nope") == ""
    d = ApifyBudgetDecision(allowed=True, scope="provider:apify", estimated_cost_usd=0.0, estimate_source="x", reason="reuse_settled_run", operation="op", actor_id="a", platform="p", source="s", reservation_key="k", payload_hash="h")
    out = attach_reservation({"ok": 1}, d)
    assert out["_vkpi_budget_reservation_key"] == "k" and out["ok"] == 1
    assert attach_reservation("raw", d) == "raw"


def test_wrapper_reuses_settled_run_without_starting_actor(monkeypatch):
    calls = {"start": 0, "wait": 0, "outcome": 0}
    decision = ApifyBudgetDecision(allowed=True, scope="provider:apify", estimated_cost_usd=0.0, estimate_source="reuse_settled_run", reason="reuse_settled_run", operation="op", actor_id="a", platform="p", source="s", reservation_key="k", payload_hash="h", apify_run_id="run-9")
    monkeypatch.setattr(ab, "_reject_release_validation_provider_work", lambda: None)
    monkeypatch.setattr(ab, "require_apify_budget", lambda **kw: decision)
    monkeypatch.setattr(ab, "_wait_for_apify_run", lambda client, run_id, wait_secs=None: calls.__setitem__("wait", calls["wait"] + 1) or {"id": run_id, "status": "SUCCEEDED"})
    monkeypatch.setattr(ab, "_mark_provider_outcome", lambda *a, **k: calls.__setitem__("outcome", calls["outcome"] + 1))
    monkeypatch.setattr(ab, "_mark_provider_started", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start a new run")))

    class _Actor:
        def start(self, **kw):
            calls["start"] += 1
            raise AssertionError("must not start a new run")

    class _Client:
        def actor(self, actor_id):
            return _Actor()

    result = ab.call_apify_actor(_Client(), "a", platform="p", operation="op", source="s", estimated_cost_usd=None, run_input={"q": 1})
    assert result["id"] == "run-9" and result["_vkpi_budget_reservation_key"] == "k"
    assert calls == {"start": 0, "wait": 1, "outcome": 0}
