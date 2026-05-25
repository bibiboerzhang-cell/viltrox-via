from __future__ import annotations

import asyncio
from argparse import Namespace

from app.domains.market import provider_preflight as market_provider_preflight
from scripts import vkpi_market_llm_provider_smoke


def _clear_provider_env(monkeypatch) -> None:
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "YOUTUBE_API_KEY",
        "GOOGLE_YOUTUBE_API_KEY",
        "GOOGLE_CSE_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_CSE_CX",
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "APIFY_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _fake_llm_preflight(*_args, **_kwargs):
    return {
        "provider_calls_allowed": False,
        "provider_gate_reason": "monthly_env_budget_disabled",
        "providers": [
            {
                "provider": "google",
                "configured": True,
                "provider_calls_allowed": False,
                "scopes": ["monthly_total", "single_call", "provider:gemini", "cron:market_provider_smoke"],
            }
        ],
    }


def test_market_provider_preflight_is_read_only_by_default(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(market_provider_preflight, "_env_file_values", lambda: {})
    monkeypatch.setattr(market_provider_preflight.llm_gateway, "budget_preflight", _fake_llm_preflight)

    report = market_provider_preflight.build_provider_preflight()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["external_http_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["task_enqueued"] is False
    assert report["checks"]["secrets_not_exposed"] is True
    assert report["checks"]["provider_calls_blocked_by_default"] is True
    assert report["checks"]["writes_blocked_by_default"] is True


def test_market_provider_preflight_detects_google_and_market_sources(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(market_provider_preflight, "_env_file_values", lambda: {"GEMINI_API_KEY": "secret-value"})
    monkeypatch.setattr(market_provider_preflight.llm_gateway, "budget_preflight", _fake_llm_preflight)

    report = market_provider_preflight.build_provider_preflight()
    sources = {source["source_key"]: source for source in report["sources"]}

    assert sources["google_gemini_llm"]["configured"] is True
    assert sources["google_gemini_llm"]["configured_env_keys"] == ["GEMINI_API_KEY"]
    assert sources["google_gemini_llm"]["env_status"]["GEMINI_API_KEY"]["value_exposed"] is False
    assert sources["google_youtube_data"]["readiness"] == "not_configured"
    assert sources["google_news_rss"]["readiness"] == "configured"
    assert sources["reddit_community"]["readiness"] == "not_configured"
    assert sources["rss_industry_news"]["readiness"] == "configured"
    assert report["summary"]["configured_sources"] >= 2
    assert report["summary"]["live_probe_candidate_count"] == 1
    assert report["next_live_tests"][0]["source_key"] == "google_gemini_llm"


def test_source_status_requires_any_and_all_env_groups(monkeypatch) -> None:
    _clear_provider_env(monkeypatch)
    monkeypatch.setattr(
        market_provider_preflight,
        "_env_file_values",
        lambda: {"REDDIT_CLIENT_ID": "id", "REDDIT_CLIENT_SECRET": "secret"},
    )
    monkeypatch.setattr(market_provider_preflight.llm_gateway, "budget_preflight", _fake_llm_preflight)

    report = market_provider_preflight.build_provider_preflight()
    reddit = {source["source_key"]: source for source in report["sources"]}["reddit_community"]

    assert reddit["readiness"] == "partial"
    assert reddit["configured"] is False
    assert "REDDIT_USER_AGENT" in reddit["env_status"]


def test_llm_single_report_marks_provider_http_call(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_market_llm_provider_smoke.market_provider_preflight,
        "build_provider_preflight",
        lambda **_kwargs: {
            "mode": "vkpi_market_provider_preflight_v0",
            "provider_calls": False,
            "llm_calls": False,
            "external_http_calls": False,
            "write_db": False,
            "passed": True,
            "summary": {},
            "sources": [],
            "checks": {},
        },
    )
    monkeypatch.setattr(
        vkpi_market_llm_provider_smoke,
        "_execute_llm_single",
        lambda *_args: {"provider": "google", "status": "success", "text": "ok"},
    )

    report = asyncio.run(
        vkpi_market_llm_provider_smoke.build_report(
            Namespace(
                execute_live_probe=False,
                live_source_key="google_gemini_llm",
                execute_llm_single=True,
                prompt="smoke",
                preferred_provider="google",
                max_output_tokens=16,
            )
        )
    )

    assert report["llm_calls"] is True
    assert report["provider_calls"] is True
    assert report["external_http_calls"] is True
    assert report["write_db"] is True
