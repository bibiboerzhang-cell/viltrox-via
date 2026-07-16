from __future__ import annotations

import inspect

from app.services.ai.analyzers import gpt_prefilter
from app.services.scraping import ytdlp


def test_caption_prefilter_uses_registered_exact_atomic_boundary(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        gpt_prefilter,
        "_audit_prefilter_binding",
        lambda: ("openai", "gpt-5.4-mini"),
    )

    def fake_generate_json(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": {
                "viltrox_likely": True,
                "camera_body": "Sony A7R IV",
                "viltrox_lens": "Viltrox 27mm F1.2",
                "other_lens": None,
                "content_genre": "review",
                "skip_vision": True,
                "confidence": "high",
            },
        }

    monkeypatch.setattr(gpt_prefilter.llm_production, "generate_json", fake_generate_json)
    result = gpt_prefilter.gpt_prefilter_caption(
        "Viltrox test",
        "Shot with Sony",
        "youtube",
    )

    assert result["viltrox_likely"] is True
    assert result["viltrox_lens"] == "Viltrox 27mm F1.2"
    assert result["error"] is None
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["purpose"] == "audit_pre_filter"
    assert captured["cost_tag"] == "single_call"
    assert captured["required_keys"]


def test_prefilter_returns_honest_empty_result_when_readiness_blocks(monkeypatch) -> None:
    monkeypatch.setattr(
        gpt_prefilter,
        "_audit_prefilter_binding",
        lambda: ("openai", "gpt-5.4-mini"),
    )
    monkeypatch.setattr(
        gpt_prefilter.llm_production,
        "generate_json",
        lambda *args, **kwargs: {
            "status": "degraded",
            "failure": {"code": "readiness_not_production_ready"},
            "errors": [{"status": "model_binding_blocked"}],
        },
    )

    result = gpt_prefilter.gpt_prefilter_caption("test", "caption", "instagram")

    assert result["viltrox_likely"] is False
    assert result["skip_vision"] is False
    assert result["error"] == "readiness_not_production_ready"


def test_engagement_anomaly_requires_bounded_structured_result(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        gpt_prefilter,
        "_audit_prefilter_binding",
        lambda: ("openai", "gpt-5.4-mini"),
    )

    def fake_generate_json(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": {"anomaly": True, "risk_delta": 12, "reasons": ["spike"]},
        }

    monkeypatch.setattr(gpt_prefilter.llm_production, "generate_json", fake_generate_json)
    result = gpt_prefilter.gpt_analyze_engagement_anomaly(
        {"likes": 9000, "views": 10000},
        "instagram",
        "creator",
        [{"likes": 10}],
    )

    assert result["anomaly"] is True
    assert result["risk_delta"] == 12
    assert captured["purpose"] == "trust_anomaly"
    assert captured["validator"](
        {"anomaly": True, "risk_delta": 50, "reasons": []}
    ) is True
    assert captured["validator"](
        {"anomaly": True, "risk_delta": 51, "reasons": []}
    ) is False


def test_ytdlp_compatibility_wrappers_do_not_duplicate_provider_sdk_calls() -> None:
    source = inspect.getsource(ytdlp)
    assert ".chat.completions.create(" not in inspect.getsource(gpt_prefilter)
    assert ".chat.completions.create(" not in source
    assert "strict_prefilter" in source
    assert "strict_anomaly" in source
