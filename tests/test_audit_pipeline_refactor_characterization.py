"""Frozen behavior and structural bounds for the full audit pipeline split."""
from __future__ import annotations

import ast
import asyncio
import inspect
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.audit import pipeline, pipeline_contract, pipeline_execution, pipeline_result
from app.services.audit.pipeline_contract import AuditDependencies
from scripts.vkpi_engineering_health_collect import collect_complexity


class _Logger:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def info(self, message: str, *_args) -> None:
        self.events.append(f"log:info:{message.split(' | ')[0]}")

    def warning(self, message: str, *_args) -> None:
        self.events.append(f"log:warning:{message.split(' | ')[0]}")


def _job(**overrides):
    values = {
        "submission_id": 701,
        "platform": "Uploaded Video",
        "url": "",
        "handle": "creator_one",
        "uploaded_video": None,
        "title": "Hands-on lens review",
        "caption": "A compact prime for street work",
        "scraped_text": "local transcript",
        "metrics": {"views": 100, "likes": 10},
        "hints": {"logo": True},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _dependencies(
    events: list[str],
    *,
    text_result=None,
    classify_error: Exception | None = None,
) -> AuditDependencies:
    @asynccontextmanager
    async def db_scope():
        events.append("db:enter")
        try:
            yield None
        finally:
            events.append("db:exit")

    async def provider_guard(provider, callback):
        events.append(f"provider:{provider}:start")
        value = callback()
        if inspect.isawaitable(value):
            value = await value
        events.append(f"provider:{provider}:end")
        return value

    async def scrape_url(_url):
        raise AssertionError("URL scraping is outside this local fixture")

    def analyze_text(*_args, **_kwargs):
        events.append("analysis:text")
        if isinstance(text_result, Exception):
            raise text_result
        return text_result or {
            "analyzed": True,
            "viltrox_detected": True,
            "confidence": "high",
            "brand_elements": [],
            "products_detected": ["Viltrox 27mm f/1.2"],
            "viltrox_products_all": [],
            "content_types": ["review"],
            "content_genre": "review",
            "tech_score": 70,
            "marketing_score": 60,
            "quality_scores": {"sharpness": 9},
            "brand_score_bonus": 8,
        }

    def classify_product(_text):
        events.append("detect:classify")
        if classify_error is not None:
            raise classify_error
        return {"series": "PRO", "label": "27mm F1.2 Pro", "confidence": "high"}

    def detect_viltrox(_text, _hints):
        events.append("detect:brand")
        return {
            "status": "confirmed",
            "confirmed": True,
            "evidence": ["caption"],
            "content_types": ["short"],
        }

    def comment_spam(_comments):
        events.append("score:spam")
        return {"spam_ratio": 0.0}

    def compute_risk(*_args, **_kwargs):
        events.append("score:risk")
        return {"risk_score": 2, "penalty": 3}

    def creator_score(*_args, **_kwargs):
        events.append("score:creator")
        return 40

    def campaign_score(*_args, **_kwargs):
        events.append("score:campaign")
        return {"raw_score": 80, "content_score": 55, "campaign_interaction_score": 21}

    def update_profile(*_args, **_kwargs):
        events.append("profile:update")

    def update_benchmark(*_args, **_kwargs):
        events.append("benchmark:update")
        return {"percentile_tech": 91, "percentile_mkt": 82}

    return AuditDependencies(
        db_connection_scope=db_scope,
        logger=_Logger(events),
        valid_url=lambda _url: False,
        detect_platform=lambda _url: "Unknown",
        scrape_url=scrape_url,
        analyze_video_with_claude=lambda *_args, **_kwargs: {},
        analyze_url_content_smart=lambda *_args, **_kwargs: {},
        analyze_text_content=analyze_text,
        gpt_prefilter_caption=lambda *_args: {"intent": "review"},
        analyze_youtube_with_gemini=lambda *_args, **_kwargs: {},
        gemini_available=False,
        anthropic_available=False,
        guarded_provider_call=provider_guard,
        classify_product=classify_product,
        detect_gear_mentions=lambda _text: ["27mm"],
        detect_viltrox=detect_viltrox,
        analyze_comments_for_spam=comment_spam,
        compute_risk=compute_risk,
        compute_campaign_score=campaign_score,
        compute_creator_score=creator_score,
        update_creator_profile=update_profile,
        update_genre_benchmark=update_benchmark,
    )


def _context(events: list[str]) -> pipeline.AuditContext:
    def get_vertical(_genre):
        events.append("weights:vertical")
        return "review"

    def apply_weights(_vertical):
        events.append("weights:apply")

    def compute_weighted(_quality, _genre, _vertical):
        events.append("weights:compute")
        return {
            "tech_score": 88,
            "marketing_score": 77,
            "quality_overall": 84,
            "tech_status": "verified",
        }

    return pipeline.AuditContext(
        compute_weighted_fn=compute_weighted,
        get_vertical_fn=get_vertical,
        apply_learned_weights_fn=apply_weights,
    )


def test_split_pipeline_preserves_normal_result_shape_and_phase_order() -> None:
    events: list[str] = []
    result = asyncio.run(
        pipeline_execution.execute_full_audit(
            _job(),
            _context(events),
            _dependencies(events),
        )
    )

    assert result["detection_status"] == "confirmed"
    assert result["product_match"] == {
        "series": "PRO",
        "label": "27mm F1.2 Pro",
        "confidence": "high",
    }
    assert result["content_types"] == ["short", "review"]
    assert result["metrics"] == {
        "views": 100,
        "likes": 10,
        "comments": 0,
        "shares": 0,
        "favorites": 0,
    }
    assert result["scores"] == {
        "content_score": 55,
        "campaign_interaction_score": 21,
        "creator_score": 40,
        "overall_score": 30,
        "risk_score": 2,
        "raw_score": 80,
        "final_score": 100,
    }
    assert result["tech_score"] == 88
    assert result["marketing_score"] == 77
    assert result["percentile_tech"] == 91
    assert result["percentile_mkt"] == 82
    assert result["video_analysis"]["prefilter"] == {"intent": "review"}
    assert result["video_analysis"]["layers_used"] == ["gpt_prefilter"]
    assert set(result) == {
        "submission_id", "platform", "extracted_handle", "title", "detection_status",
        "product_match", "content_types", "metrics", "metrics_available", "scores",
        "risk", "recommendation", "memo", "evidence", "scraped_ok", "scrape_snapshot",
        "video_analysis", "tech_score", "marketing_score", "content_genre",
        "percentile_tech", "percentile_mkt", "vertical_category", "vertical_tech_score",
        "vertical_mkt_score", "community_value", "product_showcase_score",
        "brand_exposure_score", "storytelling_score", "tech_status", "logo_detected",
        "product_closeup_count", "comment_spam", "gear_mentions",
    }
    assert events.index("detect:classify") < events.index("score:risk")
    assert events.index("score:campaign") < events.index("profile:update")
    assert events.index("profile:update") < events.index("weights:compute")
    assert events.index("weights:compute") < events.index("benchmark:update")


def test_provider_failure_is_captured_before_detection_and_keeps_result_contract() -> None:
    events: list[str] = []
    result = asyncio.run(
        pipeline_execution.execute_full_audit(
            _job(handle="", hints={}),
            None,
            _dependencies(events, text_result=RuntimeError("provider exploded")),
        )
    )

    assert result["video_analysis"]["analyzed"] is False
    assert result["video_analysis"]["provider"] == "claude"
    assert result["video_analysis"]["error"] == "provider exploded"
    assert events.index("analysis:text") < events.index("detect:classify")
    assert "profile:update" not in events
    assert result["submission_id"] == 701
    assert result["scrape_snapshot"]["source_url"] == ""


def test_detection_exception_propagates_after_db_scope_exit_without_scoring() -> None:
    events: list[str] = []
    expected = ValueError("catalog unavailable")

    with pytest.raises(ValueError) as captured:
        asyncio.run(
            pipeline_execution.execute_full_audit(
                _job(),
                None,
                _dependencies(events, classify_error=expected),
            )
        )

    assert captured.value is expected
    assert events.index("analysis:text") < events.index("db:enter")
    assert events[-3:] == ["db:enter", "detect:classify", "db:exit"]
    assert "score:risk" not in events
    assert "score:campaign" not in events


def test_audit_pipeline_family_has_bounded_complexity_size_and_dependency_direction() -> None:
    modules = (pipeline, pipeline_contract, pipeline_execution, pipeline_result)
    rows = []
    for module in modules:
        module_path = Path(module.__file__)
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rows.extend(collect_complexity({str(module_path): tree}))
        assert len(source.splitlines()) <= 800

    assert max(row.cc for row in rows) <= 30
    facade = next(
        row
        for row in rows
        if row.path.endswith("/pipeline.py") and row.qualified_name == "perform_full_audit"
    )
    assert facade.cc <= 5
    execution_source = Path(pipeline_execution.__file__).read_text(encoding="utf-8")
    assert "from app.services.audit.pipeline import" not in execution_source
