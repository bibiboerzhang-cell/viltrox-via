from __future__ import annotations

from app.services.vkpi import market_intelligence_v0


def test_market_intelligence_v0_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr(market_intelligence_v0, "_table_exists", lambda table: True)
    monkeypatch.setattr(market_intelligence_v0, "_count", lambda table: 2)
    monkeypatch.setattr(
        market_intelligence_v0,
        "_competitor_rows",
        lambda limit, run_id=None: [
            {
                "id": 1,
                "signal_uid": "sig-1",
                "brand": "Sigma",
                "normalized_brand": "sigma",
                "signal_type": "competitor_focus",
                "severity": "high",
                "score": 32,
                "product_hints": ["35mm f/1.4"],
                "review_status": "ready",
                "platform": "youtube",
                "detail": "New Sigma launch mentioned against Viltrox.",
                "evidence": {"detail": "New Sigma launch mentioned against Viltrox."},
            },
            {
                "id": 2,
                "signal_uid": "sig-2",
                "brand": "Tamron",
                "normalized_brand": "tamron",
                "signal_type": "voc_issue",
                "severity": "medium",
                "score": 21,
                "product_hints": [],
                "review_status": "pending_review",
                "platform": "reddit",
                "detail": "Users complain about price and ask for Viltrox alternative.",
                "evidence": {"detail": "Users complain about price and ask for Viltrox alternative."},
            },
        ],
    )

    report = market_intelligence_v0.build_market_intelligence_v0()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["external_http_calls"] is False
    assert report["summary"]["signals_loaded"] == 2
    assert report["summary"]["launch_candidates"] == 1
    assert report["summary"]["comment_opportunities"] == 1
    assert report["hot_brands"][0]["brand"] == "sigma"


def test_market_intelligence_v0_fails_without_storage(monkeypatch) -> None:
    monkeypatch.setattr(market_intelligence_v0, "_table_exists", lambda table: False)
    monkeypatch.setattr(market_intelligence_v0, "_count", lambda table: 0)
    monkeypatch.setattr(market_intelligence_v0, "_competitor_rows", lambda limit, run_id=None: [])

    report = market_intelligence_v0.build_market_intelligence_v0()

    assert report["passed"] is False
    assert report["checks"]["market_storage_ready"] is False
    assert report["checks"]["competitor_signals_ready"] is False


def test_market_intelligence_v0_passes_run_scope(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(market_intelligence_v0, "_table_exists", lambda table: True)
    monkeypatch.setattr(market_intelligence_v0, "_count", lambda table: 2)

    def fake_rows(limit, run_id=None):
        captured["limit"] = limit
        captured["run_id"] = run_id
        return []

    monkeypatch.setattr(market_intelligence_v0, "_competitor_rows", fake_rows)

    report = market_intelligence_v0.build_market_intelligence_v0(limit=7, run_id=3)

    assert captured == {"limit": 7, "run_id": 3}
    assert report["summary"]["run_id"] == 3
