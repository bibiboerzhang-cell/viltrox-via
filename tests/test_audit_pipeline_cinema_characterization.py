from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services.audit import pipeline


@asynccontextmanager
async def _db_scope_stub():
    yield None


async def _provider_passthrough(_provider, callback):
    value = callback()
    if inspect.isawaitable(value):
        return await value
    return value


def _job() -> SimpleNamespace:
    return SimpleNamespace(
        submission_id=101,
        platform="Uploaded Video",
        url="",
        handle="",
        uploaded_video=None,
        title="",
        caption="",
        scraped_text="local fixture",
        metrics={},
        hints={},
    )


def _install_pipeline_stubs(monkeypatch: pytest.MonkeyPatch, *, classify_product):
    from app.services.ai import runtime_guards
    from app.services.ai.analyzers import claude_text
    from app.services.audit import similarity
    from app.services.scoring import benchmark, campaign, creator, risk

    monkeypatch.setattr(pipeline, "db_connection_scope", _db_scope_stub)
    monkeypatch.setattr(runtime_guards, "guarded_provider_call", _provider_passthrough)
    monkeypatch.setattr(
        claude_text,
        "analyze_text_content",
        lambda *_args, **_kwargs: {
            "analyzed": True,
            "camera_brand": "ARRI",
            "products_detected": ["Viltrox EPIC 65mm"],
            "viltrox_products_all": [],
            "viltrox_detected": True,
            "confidence": "high",
            "content_types": [],
        },
    )
    monkeypatch.setattr(similarity, "classify_product", classify_product)
    monkeypatch.setattr(similarity, "detect_gear_mentions", lambda _text: [])
    monkeypatch.setattr(
        similarity,
        "detect_viltrox",
        lambda _text, _hints: {
            "status": "confirmed",
            "confirmed": True,
            "evidence": [],
            "content_types": [],
        },
    )
    monkeypatch.setattr(similarity, "analyze_comments_for_spam", lambda _comments: {})
    monkeypatch.setattr(risk, "compute_risk", lambda *_args, **_kwargs: {"risk_score": 0, "penalty": 0})
    monkeypatch.setattr(campaign, "compute_creator_score", lambda *_args, **_kwargs: 50)
    monkeypatch.setattr(
        campaign,
        "compute_campaign_score",
        lambda *_args, **_kwargs: {
            "raw_score": 100,
            "content_score": 50,
            "campaign_interaction_score": 10,
        },
    )
    monkeypatch.setattr(campaign, "compute_ratios", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(creator, "update_creator_profile", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(benchmark, "update_genre_benchmark", lambda *_args, **_kwargs: {})


def test_cinema_camera_with_already_specific_series_keeps_initial_match(monkeypatch):
    calls: list[str] = []

    def classify_product(text: str):
        calls.append(text)
        return {"series": "EPIC", "label": "EPIC 65mm", "confidence": "high"}

    _install_pipeline_stubs(monkeypatch, classify_product=classify_product)

    result = asyncio.run(pipeline.perform_full_audit(_job()))

    assert result["product_match"] == {
        "series": "EPIC",
        "label": "EPIC 65mm",
        "confidence": "high",
    }
    assert len(calls) == 1


def test_cinema_camera_reclassifies_generic_series_from_detected_product(monkeypatch):
    calls: list[str] = []

    def classify_product(text: str):
        calls.append(text)
        if len(calls) == 1:
            return {"series": "AIR", "label": "AIR", "confidence": "medium"}
        return {"series": "EPIC", "label": "EPIC 65mm", "confidence": "high"}

    _install_pipeline_stubs(monkeypatch, classify_product=classify_product)

    result = asyncio.run(pipeline.perform_full_audit(_job()))

    assert result["product_match"]["series"] == "EPIC"
    assert len(calls) == 2
