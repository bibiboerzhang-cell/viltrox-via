from __future__ import annotations

import asyncio
from argparse import Namespace

from app.domains.market.llm_quality import evaluate_market_llm_output
from scripts import vkpi_market_llm_provider_smoke


def test_quality_gate_rejects_short_gemini_fragment() -> None:
    gate = evaluate_market_llm_output(
        {
            "status": "success",
            "provider": "google",
            "model": "gemini-flash-latest",
            "text": "; hot_topics=",
            "output_tokens": 5,
        }
    )

    assert gate["usable_for_ui"] is False
    assert "too_short" in gate["reasons"]
    assert "missing_required_labels:判断,量化依据,限制" in gate["reasons"]
    assert "trailing_fragment" in gate["reasons"]


def test_quality_gate_accepts_quantified_chinese_summary() -> None:
    text = (
        "判断：当前Reddit舆情机会主要集中在竞品相关讨论；量化依据：competitor_focus 9条、"
        "competitor_launch 2条，合计11/15条信号；限制：仅反映run_id=3样本。\n"
        "判断：DJI是本批次最突出的高热品牌；量化依据：dji 4条/score 140.6，"
        "领先nanlite 3条/110.8；限制：不代表全市场销量。"
    )
    gate = evaluate_market_llm_output(
        {"status": "success", "provider": "openai", "model": "gpt-test", "text": text}
    )

    assert gate["usable_for_ui"] is True
    assert gate["reasons"] == []
    assert gate["checks"]["has_quantitative_evidence"] is True


def test_llm_single_report_includes_quality_gate(monkeypatch) -> None:
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
        lambda *_args: {"provider": "google", "status": "success", "text": "; hot_topics="},
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

    assert report["llm_quality_gate"]["usable_for_ui"] is False
    assert report["summary"]["llm_output_usable_for_ui"] is False
    assert report["checks"]["llm_output_quality_gate_present"] is True
