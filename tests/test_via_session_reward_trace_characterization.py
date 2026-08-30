"""Characterization lock for record_via_reward_trace_for_session (session_reward).

This function sits on the learning loop (reward trace -> outcome merge ->
routing stat -> recommendation feedback signal). The tests freeze, before the
CC-reduction knife:
- the exact call ORDER of every read and write;
- the exact keyword payload of every write (insert trace / update outcome /
  routing stat upsert / feedback signal / session touch);
- dedupe, invalid-event, and missing-bundle short circuits;
- event-value resolution fallbacks.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.services.via import session_reward


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def sync(self, name: str, result: Any):
        def _fn(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return result

        return _fn

    def names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def kwargs_of(self, name: str) -> dict:
        return next(kwargs for called, _args, kwargs in self.calls if called == name)

    def args_of(self, name: str) -> tuple:
        return next(args for called, args, _kwargs in self.calls if called == name)


_SESSION = {
    "id": 7,
    "user_id": 9,
    "current_surface": "gallery",
    "state": {"seen": 1},
}

_DECISIONS = [
    {
        "decision_id": "d1",
        "decision_type": "reply_mode",
        "chosen_action": {"provider": " Gemini "},
        "latency_ms": 12.5,
        "cost_estimate": 0.002,
        "state_snapshot": {"intent": "Product_QA", "current_surface": ""},
    },
    {
        "decision_id": "d2",
        "decision_type": "intent_route",
        "chosen_action": {},
    },
]

_TRACE_ROW = {"trace_id": "t1", "created_at": "2026-08-29T00:00:00Z"}
_SUMMARY = {"reward_delta": 0.25, "events": 3}


def _wire(monkeypatch, recorder, *, bundle, decisions, existing=None, latest_outcome=None,
          updated_outcome=None, merged=None, touched=None):
    monkeypatch.setattr(session_reward, "get_via_session_bundle", recorder.sync("bundle", bundle))
    monkeypatch.setattr(session_reward, "list_via_decision_records", recorder.sync("decisions", decisions))
    monkeypatch.setattr(
        session_reward, "get_via_reward_trace_by_idempotency", recorder.sync("idempotency", existing)
    )
    monkeypatch.setattr(session_reward, "insert_via_reward_trace", recorder.sync("insert_trace", _TRACE_ROW))
    monkeypatch.setattr(session_reward, "list_via_reward_traces", recorder.sync("list_traces", ["trace"]))
    monkeypatch.setattr(session_reward, "summarize_via_reward_traces", recorder.sync("summarize", _SUMMARY))
    monkeypatch.setattr(
        session_reward, "get_latest_via_outcome_record", recorder.sync("latest_outcome", latest_outcome)
    )
    monkeypatch.setattr(
        session_reward, "merge_via_reward_trace_summary", recorder.sync("merge", merged or {})
    )
    monkeypatch.setattr(
        session_reward, "update_via_outcome_record", recorder.sync("update_outcome", updated_outcome or {})
    )
    monkeypatch.setattr(
        session_reward, "upsert_via_routing_provider_stat", recorder.sync("routing_stat", {"ok": True})
    )
    monkeypatch.setattr(session_reward, "record_feedback_signal", recorder.sync("feedback", None))
    monkeypatch.setattr(
        session_reward, "touch_via_session", recorder.sync("touch", touched or {"id": 7, "touched": True})
    )


def _run(**kwargs):
    return asyncio.run(session_reward.record_via_reward_trace_for_session(**kwargs))


def test_missing_bundle_returns_empty_dict(monkeypatch):
    recorder = _Recorder()
    _wire(monkeypatch, recorder, bundle=None, decisions=[])
    result = _run(session_key="vs-1", event_type="click")
    assert result == {}
    assert recorder.calls == [("bundle", ("vs-1", 12), {})]


def test_invalid_event_type_short_circuits(monkeypatch):
    recorder = _Recorder()
    _wire(monkeypatch, recorder, bundle={"session": _SESSION}, decisions=_DECISIONS)
    result = _run(session_key="vs-1", event_type="page_view")
    assert result == {"error": "invalid_event_type"}
    assert recorder.names() == ["bundle"]


def test_dedupe_hit_returns_existing_trace_without_writes(monkeypatch):
    recorder = _Recorder()
    existing = {"trace_id": "old", "decision_id": "d-old"}
    latest = {"outcome_id": "o1", "reward_score": 0.4}
    _wire(
        monkeypatch,
        recorder,
        bundle={"session": _SESSION},
        decisions=[],
        existing=existing,
        latest_outcome=latest,
    )
    result = _run(
        session_key="vs-1",
        event_type="purchase",
        payload={"order_id": "ord-1"},
    )
    assert result == {
        "trace": existing,
        "summary": _SUMMARY,
        "decision_id": "d-old",
        "outcome": latest,
        "session": _SESSION,
        "deduped": True,
    }
    assert recorder.names() == [
        "bundle",
        "decisions",
        "idempotency",
        "list_traces",
        "summarize",
        "latest_outcome",
    ]
    assert recorder.args_of("idempotency") == ("vs-1", "ord-1")
    assert recorder.args_of("list_traces") == ("vs-1",)
    assert recorder.kwargs_of("list_traces") == {"decision_id": "d-old", "limit": 64}
    assert recorder.args_of("latest_outcome") == ("vs-1", "d-old")


def test_full_path_preserves_every_write_payload_and_order(monkeypatch):
    recorder = _Recorder()
    latest = {"outcome_id": "o1", "reward_score": 0.3}
    merged = {
        "clicked_product": True,
        "added_to_cart": True,
        "purchased": False,
        "thumb_feedback": 1,
        "reward_score": 0.7,
        "outcome_payload": {"merged": True},
    }
    updated = {"outcome_id": "o1", "reward_score": 0.7}
    touched = {"id": 7, "current_surface": "chat"}
    _wire(
        monkeypatch,
        recorder,
        bundle={"session": _SESSION},
        decisions=_DECISIONS,
        latest_outcome=latest,
        merged=merged,
        updated_outcome=updated,
        touched=touched,
    )
    result = _run(
        session_key="vs-1",
        event_type="Add_To_Cart",
        payload={"value": "3.5", "sku": "abc"},
        current_surface="chat",
        source="via-panel",
        origin="widget",
        product_key="lens-26",
        idempotency_key="cart-1",
    )
    assert recorder.names() == [
        "bundle",
        "decisions",
        "idempotency",
        "insert_trace",
        "list_traces",
        "summarize",
        "latest_outcome",
        "merge",
        "update_outcome",
        "routing_stat",
        "feedback",
        "touch",
    ]
    assert recorder.kwargs_of("insert_trace") == {
        "session_key": "vs-1",
        "decision_id": "d1",
        "user_id": 9,
        "event_type": "add_to_cart",
        "surface": "chat",
        "source": "via-panel",
        "origin": "widget",
        "product_key": "lens-26",
        "event_value": 3.5,
        "idempotency_key": "cart-1",
        "event_payload": {"value": "3.5", "sku": "abc"},
    }
    assert recorder.kwargs_of("merge") == {
        "outcome": latest,
        "reward_trace_summary": _SUMMARY,
    }
    assert recorder.args_of("update_outcome") == ("o1",)
    assert recorder.kwargs_of("update_outcome") == {
        "clicked_product": True,
        "added_to_cart": True,
        "purchased": False,
        "thumb_feedback": 1,
        "reward_score": 0.7,
        "outcome_payload": {"merged": True},
    }
    assert recorder.kwargs_of("routing_stat") == {
        "bucket_key": "product_qa:chat",
        "provider": "gemini",
        "exposure_increment": 0,
        "success_increment": 1,
        "reward_delta": 0.7,
        "guard_fail_increment": 0,
        "latency_ms": 12.5,
        "cost_estimate": 0.002,
        "last_outcome_at": "2026-08-29T00:00:00Z",
        "metrics": {"event_type": "add_to_cart", "surface": "chat", "origin": "widget"},
    }
    assert recorder.kwargs_of("feedback") == {
        "source_type": "via_reward_trace",
        "source_id": "vs-1",
        "event_type": "reward_add_to_cart",
        "actor_role": "user",
        "user_id": 9,
        "payload": {
            "decision_id": "d1",
            "event_type": "add_to_cart",
            "event_value": 3.5,
            "product_key": "lens-26",
            "trace_summary": _SUMMARY,
        },
    }
    assert recorder.args_of("touch") == ("vs-1",)
    assert recorder.kwargs_of("touch") == {
        "current_surface": "chat",
        "session_state": {
            "seen": 1,
            "last_reward_trace_type": "add_to_cart",
            "last_reward_trace_at": "2026-08-29T00:00:00Z",
            "last_reward_trace_summary": _SUMMARY,
        },
    }
    assert result == {
        "trace": _TRACE_ROW,
        "summary": _SUMMARY,
        "decision_id": "d1",
        "outcome": updated,
        "session": touched,
    }


def test_thumb_down_without_outcome_uses_summary_reward_and_guard_fail(monkeypatch):
    recorder = _Recorder()
    _wire(
        monkeypatch,
        recorder,
        bundle={"session": _SESSION},
        decisions=_DECISIONS,
        latest_outcome=None,
    )
    result = _run(session_key="vs-1", event_type="thumb_down")
    assert "merge" not in recorder.names()
    assert "update_outcome" not in recorder.names()
    stat = recorder.kwargs_of("routing_stat")
    assert stat["exposure_increment"] == 0
    assert stat["success_increment"] == 0
    assert stat["guard_fail_increment"] == 1
    assert stat["reward_delta"] == 0.25
    assert stat["bucket_key"] == "product_qa:gallery"
    assert recorder.kwargs_of("touch")["current_surface"] == "gallery"
    assert result["outcome"] == {}


def test_click_counts_exposure_and_product_click_does_not_count_success(monkeypatch):
    recorder = _Recorder()
    _wire(monkeypatch, recorder, bundle={"session": _SESSION}, decisions=_DECISIONS)
    _run(session_key="vs-1", event_type="click", current_surface="upload")
    click_stat = recorder.kwargs_of("routing_stat")
    assert click_stat["exposure_increment"] == 1
    assert click_stat["success_increment"] == 1

    recorder2 = _Recorder()
    _wire(monkeypatch, recorder2, bundle={"session": _SESSION}, decisions=_DECISIONS)
    _run(session_key="vs-1", event_type="product_click", current_surface="upload")
    product_stat = recorder2.kwargs_of("routing_stat")
    assert product_stat["exposure_increment"] == 0
    assert product_stat["success_increment"] == 0


def test_no_matching_decision_skips_routing_stat_but_keeps_learning_signal(monkeypatch):
    recorder = _Recorder()
    _wire(
        monkeypatch,
        recorder,
        bundle={"session": _SESSION},
        decisions=[],
    )
    result = _run(session_key="vs-1", event_type="thumb_up", decision_id="ghost")
    assert "routing_stat" not in recorder.names()
    assert recorder.kwargs_of("feedback")["payload"]["decision_id"] == "ghost"
    assert recorder.kwargs_of("insert_trace")["decision_id"] == "ghost"
    assert result["decision_id"] == "ghost"


def test_event_value_resolution_fallbacks(monkeypatch):
    recorder = _Recorder()
    _wire(monkeypatch, recorder, bundle={"session": _SESSION}, decisions=[])
    _run(session_key="vs-1", event_type="purchase", payload={"order_total": "12.5"})
    assert recorder.kwargs_of("insert_trace")["event_value"] == 12.5

    recorder2 = _Recorder()
    _wire(monkeypatch, recorder2, bundle={"session": _SESSION}, decisions=[])
    _run(session_key="vs-1", event_type="purchase", payload={"value": "not-a-number"})
    assert recorder2.kwargs_of("insert_trace")["event_value"] == 0.0

    recorder3 = _Recorder()
    _wire(monkeypatch, recorder3, bundle={"session": _SESSION}, decisions=[])
    _run(session_key="vs-1", event_type="purchase", event_value=9.25, payload={"value": "1"})
    assert recorder3.kwargs_of("insert_trace")["event_value"] == 9.25
