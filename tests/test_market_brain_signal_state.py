"""Offline regression for unavailable sources incorrectly becoming empty data."""
from __future__ import annotations

import pytest

from app.domains.market_brain import summary
from app.domains.market_brain.read_cache import cacheable_payload
from app.domains.market_brain.summary_signal_state import SIGNAL_SOURCES, signal_read_state


@pytest.mark.parametrize(
    ("statuses", "has_items", "expected"),
    [
        (("ok", "ready", "ready"), True, "ok"),
        (("no_data_in_window", "empty", "empty"), False, "empty"),
        (("no_brand_signal", "ready", "ready"), False, "empty"),
        (("error", "error", "error"), False, "error"),
        (("error", "empty", "empty"), False, "partial"),
        (("ok", "error", "ready"), True, "partial"),
        (("ok", "partial", "ready"), True, "partial"),
        (("unknown", "stale", "unavailable"), False, "error"),
        (("", "", ""), False, "error"),
        ((" READY ", "empty", "ok"), True, "ok"),
    ],
)
def test_coverage_classification(statuses, has_items, expected):
    sources = {name: {"status": status} for name, status in zip(SIGNAL_SOURCES, statuses)}
    assert signal_read_state(sources, has_items=has_items) == expected


def test_missing_and_malformed_source_states_are_not_complete():
    assert signal_read_state({}, has_items=False) == "error"
    assert signal_read_state({"brand_pulse": {"status": "ok"}}, has_items=True) == "partial"
    assert signal_read_state({name: None for name in SIGNAL_SOURCES}, has_items=False) == "error"


@pytest.mark.parametrize("with_signal", [False, True])
def test_card_preserves_available_items_and_reports_a_failed_source(monkeypatch, with_signal):
    def brand(items, sources):
        sources["brand_pulse"] = {"status": "ok"}
        if with_signal:
            items.append({"signal": "fixture observation", "kind": "brand_pulse"})

    def voice(items, sources):
        sources["market_voice"] = {"status": "empty"}

    monkeypatch.setattr(summary, "_brand_pulse_signals", brand)
    monkeypatch.setattr(summary, "_market_voice_signals", voice)
    card = summary._weekly_signals_card(None, "fixture read unavailable")
    assert card["status"] == "partial"
    assert len(card["items"]) == int(with_signal)
    assert card["sources"]["category_tracks"]["status"] == "error"
    assert not cacheable_payload({"weekly_signals": card})


def test_card_all_sources_fail_without_claiming_an_empty_market(monkeypatch):
    def fail(*_args):
        raise RuntimeError("fixture read unavailable")

    monkeypatch.setattr(summary, "_brand_pulse_signals", fail)
    monkeypatch.setattr(summary, "_market_voice_signals", fail)
    card = summary._weekly_signals_card(None, "fixture read unavailable")
    assert card["status"] == "error"
    assert card["items"] == []
    assert set(card["sources"]) == set(SIGNAL_SOURCES)


def test_partial_payload_without_nested_error_is_not_cached_as_complete():
    assert not cacheable_payload({"weekly_signals": {"status": "partial", "items": []}})
    assert cacheable_payload({"weekly_signals": {"status": "empty", "items": []}})
