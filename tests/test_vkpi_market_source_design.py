from __future__ import annotations

from app.domains.market import source_design_use_case as market_source_design


def test_source_registry_blocks_external_collection() -> None:
    sources = [market_source_design._source_readiness(source) for source in market_source_design.SOURCE_REGISTRY]

    assert sources
    assert all(source["external_calls_allowed"] is False for source in sources)
    assert all(source["write_db_allowed"] is False for source in sources)
    assert all(source["contract_complete"] for source in sources)
    assert any(source["source_key"] == "reddit_community" and source["execution_gate"].startswith("P5.67") for source in sources)
    assert any(source["source_key"] == "x_public_posts" and source["execution_gate"].startswith("P5.68") for source in sources)


def test_market_source_design_report_uses_existing_tables_and_stays_read_only(monkeypatch) -> None:
    monkeypatch.setattr(market_source_design, "_table_exists", lambda table: True)
    monkeypatch.setattr(market_source_design, "_count", lambda table: 0)

    report = market_source_design.build_market_source_design_report()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["external_http_calls"] is False
    assert report["summary"]["source_count"] >= 5
    assert report["checks"]["external_calls_blocked"] is True
    assert report["checks"]["writes_blocked"] is True


def test_market_source_design_report_fails_when_core_tables_missing(monkeypatch) -> None:
    monkeypatch.setattr(market_source_design, "_table_exists", lambda table: table == "vkpi_market_sources")
    monkeypatch.setattr(market_source_design, "_count", lambda table: 0)

    report = market_source_design.build_market_source_design_report()

    assert report["passed"] is False
    assert report["checks"]["market_scan_tables_present"] is False
    assert report["checks"]["competitor_signal_tables_present"] is False
