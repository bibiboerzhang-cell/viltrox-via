from __future__ import annotations

import json

from app.domains.market import (
    build_market_intelligence_cards,
    latest_external_signal_smoke_report,
    latest_reviewed_market_run_id,
    latest_usable_market_llm_report,
)


def _market_report():
    return {
        "mode": "p5_69_market_intelligence_v0",
        "generated_at": "2026-05-24T17:10:31Z",
        "passed": True,
        "summary": {
            "signals_loaded": 15,
            "launch_candidates": 12,
            "comment_opportunities": 3,
            "high_priority": 9,
            "run_id": 3,
        },
        "distributions": {"review_status": {"pending_review": 15}},
        "hot_brands": [
            {"brand": "dji", "count": 4, "score": 140.6},
            {"brand": "nanlite", "count": 3, "score": 110.8},
        ],
    }


def _llm_report(text: str):
    return {
        "mode": "vkpi_market_provider_preflight_v0",
        "generated_at": "2026-05-24T17:32:10Z",
        "llm_single_result": {
            "status": "success",
            "provider": "openai",
            "model": "gpt-test",
            "text": text,
            "input_tokens": 200,
            "output_tokens": 180,
            "cost_cents": 0,
        },
    }


def _external_smoke_report():
    return {
        "mode": "market_external_signal_smoke_v0",
        "generated_at": "2026-05-24T18:10:00Z",
        "passed": True,
        "write_db": False,
        "llm_calls": False,
        "sync_triggered": False,
        "summary": {
            "items_loaded": 2,
            "business_signal_items": 1,
            "tier1_mentions": 2,
            "viltrox_product_mentions": 1,
        },
        "top_candidates": [
            {
                "source_uid": "external:1",
                "provider": "google_news",
                "source_key": "google_news_viltrox",
                "source_type": "google_news_rss",
                "source_url": "https://news.google.com/rss/articles/example",
                "title": "Viltrox and DJI creator signal",
                "summary": "Viltrox lens and DJI creator kit are discussed in the same sample.",
                "published_at": "2026-05-24T18:00:00Z",
                "score": 0.74,
                "keyword_hits": ["viltrox", "dji"],
            }
        ],
    }


def test_cards_include_market_summary_and_brand_cards() -> None:
    payload = build_market_intelligence_cards(_market_report(), brand_limit=2)

    assert payload["passed"] is True
    assert payload["summary"]["source_run_id"] == 3
    assert payload["summary"]["card_count"] == 3
    assert payload["cards"][0]["entityType"] == "market_signal_run"
    assert payload["cards"][1]["entityType"] == "competitor_brand"
    assert all(card["evidence"] for card in payload["cards"])


def test_cards_include_only_usable_llm_summary() -> None:
    text = (
        "判断：当前Reddit舆情机会主要集中在竞品相关讨论；量化依据：competitor_focus 9条、"
        "competitor_launch 2条，合计11/15条信号；限制：仅反映run_id=3样本。\n"
        "判断：DJI是本批次最突出的高热品牌；量化依据：dji 4条/score 140.6；限制：不代表销量。"
    )
    payload = build_market_intelligence_cards(_market_report(), llm_report=_llm_report(text), brand_limit=1)

    assert payload["summary"]["llm_card_included"] is True
    assert payload["cards"][1]["type"] == "brief"
    assert payload["cards"][1]["quality_gate"]["usable_for_ui"] is True


def test_cards_hide_unusable_llm_summary() -> None:
    payload = build_market_intelligence_cards(_market_report(), llm_report=_llm_report("; hot_topics="), brand_limit=1)

    assert payload["summary"]["llm_card_included"] is False
    assert all(card["type"] != "brief" for card in payload["cards"])
    assert payload["checks"]["llm_card_requires_quality_gate"] is True


def test_cards_include_external_signal_smoke_cards() -> None:
    payload = build_market_intelligence_cards(
        _market_report(),
        external_smoke_report=_external_smoke_report(),
        brand_limit=1,
    )

    assert payload["summary"]["external_smoke_card_count"] == 2
    assert payload["cards"][1]["entityType"] == "external_signal_smoke"
    assert payload["cards"][2]["entityType"] == "external_signal_item"
    assert payload["cards"][2]["evidence"][3]["url"].startswith("https://news.google.com/")


def test_latest_usable_market_llm_report_skips_bad_outputs(tmp_path) -> None:
    (tmp_path / "001-market-llm-single-smoke-google.json").write_text(
        json.dumps(_llm_report("; hot_topics=")),
        encoding="utf-8",
    )
    usable_text = (
        "判断：当前Reddit舆情机会主要集中在竞品相关讨论；量化依据：competitor_focus 9条、"
        "competitor_launch 2条；限制：仅反映run_id=3样本。"
    )
    good_path = tmp_path / "002-market-llm-single-smoke-openai.json"
    good_path.write_text(json.dumps(_llm_report(usable_text)), encoding="utf-8")

    report = latest_usable_market_llm_report(ops_dir=tmp_path)

    assert report is not None
    assert report["_artifact_path"].endswith("002-market-llm-single-smoke-openai.json")
    assert report["_quality_gate"]["usable_for_ui"] is True


def test_latest_external_signal_smoke_report_requires_items(tmp_path) -> None:
    (tmp_path / "001-market-external-signal-smoke-v0.json").write_text(
        json.dumps({"passed": True, "summary": {"items_loaded": 0}}),
        encoding="utf-8",
    )
    good_path = tmp_path / "002-market-external-signal-smoke-v0.json"
    good_path.write_text(json.dumps(_external_smoke_report()), encoding="utf-8")

    report = latest_external_signal_smoke_report(ops_dir=tmp_path)

    assert report is not None
    assert report["_artifact_path"].endswith("002-market-external-signal-smoke-v0.json")


def test_latest_reviewed_market_run_id_handles_db_errors(monkeypatch) -> None:
    class BrokenConn:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr("app.domains.market.intelligence_card_repository.get_conn", lambda: BrokenConn())

    assert latest_reviewed_market_run_id() is None
