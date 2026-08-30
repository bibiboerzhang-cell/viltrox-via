"""Characterization and structural guards for smart URL analysis orchestration."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.services.ai.analyzers import claude_vision
from scripts.vkpi_engineering_health_collect import collect_complexity


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class Harness:
    events: list[tuple[Any, ...]] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    gpt: dict[str, Any] = field(default_factory=dict)
    youtube: dict[str, Any] = field(default_factory=dict)
    youtube_error: BaseException | None = None
    images: list[str] = field(default_factory=list)
    image_analysis: dict[str, Any] = field(default_factory=dict)
    direct: dict[str, Any] = field(
        default_factory=lambda: {
            "success": False,
            "path": None,
            "duration": 0,
            "error": "direct failed",
        }
    )
    ytdlp: dict[str, Any] = field(
        default_factory=lambda: {
            "success": False,
            "path": None,
            "duration": 0,
            "error": "download failed",
        }
    )
    local_ok: bool = False
    local_patch: dict[str, Any] = field(default_factory=dict)
    claude: dict[str, Any] = field(default_factory=dict)
    caption: dict[str, Any] = field(default_factory=dict)
    text: dict[str, Any] = field(default_factory=dict)
    unlink_error: BaseException | None = None

    def install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        anthropic: bool,
        gemini: bool,
        openai: bool,
        ytdlp: bool,
    ) -> None:
        monkeypatch.setattr(claude_vision, "ANTHROPIC_AVAILABLE", anthropic)
        monkeypatch.setattr(claude_vision, "GEMINI_AVAILABLE", gemini)
        monkeypatch.setattr(claude_vision, "OPENAI_AVAILABLE", openai)
        monkeypatch.setattr(claude_vision, "YTDLP_AVAILABLE", ytdlp)
        monkeypatch.setattr(claude_vision, "get_creator_profile", self.get_profile)
        monkeypatch.setattr(claude_vision, "gpt_prefilter_caption", self.gpt_prefilter)
        monkeypatch.setattr(
            claude_vision,
            "analyze_youtube_with_gemini",
            self.youtube_analysis,
        )
        monkeypatch.setattr(
            claude_vision,
            "fetch_all_images_from_post",
            self.fetch_images,
        )
        monkeypatch.setattr(claude_vision, "_analyze_images_batch", self.analyze_images)
        monkeypatch.setattr(
            claude_vision,
            "_download_direct_video_url",
            self.download_direct,
        )
        monkeypatch.setattr(claude_vision, "download_video_ytdlp", self.download_ytdlp)
        monkeypatch.setattr(
            claude_vision,
            "analyze_local_video_with_gemini_file_api",
            self.analyze_local,
        )
        monkeypatch.setattr(claude_vision, "analyze_video_with_claude", self.analyze_claude)
        monkeypatch.setattr(claude_vision, "parse_gear_from_caption", self.parse_caption)
        monkeypatch.setattr(claude_vision, "analyze_text_content", self.analyze_text)
        monkeypatch.setattr(
            claude_vision,
            "compute_weighted_scores",
            self.weighted_scores,
        )
        monkeypatch.setattr(claude_vision.os, "unlink", self.unlink)

    def get_profile(self, handle: str) -> dict[str, Any]:
        self.events.append(("profile", handle))
        return deepcopy(self.profile)

    def gpt_prefilter(self, title: str, caption: str, platform: str) -> dict[str, Any]:
        self.events.append(("gpt", title, caption, platform))
        return deepcopy(self.gpt)

    async def youtube_analysis(
        self, url: str, title: str, creator_handle: str
    ) -> dict[str, Any]:
        self.events.append(("youtube", url, title, creator_handle))
        if self.youtube_error is not None:
            raise self.youtube_error
        return deepcopy(self.youtube)

    def fetch_images(self, url: str, og_image: str) -> list[str]:
        self.events.append(("images", url, og_image))
        return list(self.images)

    def analyze_images(
        self, images: list[str], title: str, platform: str, profile_hint: str
    ) -> dict[str, Any]:
        self.events.append(
            ("image_analysis", tuple(images), title, platform, profile_hint)
        )
        return deepcopy(self.image_analysis)

    def download_direct(self, url: str, _tmpdir: str) -> dict[str, Any]:
        self.events.append(("direct", url))
        return deepcopy(self.direct)

    def download_ytdlp(self, url: str, _tmpdir: str) -> dict[str, Any]:
        self.events.append(("ytdlp", url))
        return deepcopy(self.ytdlp)

    async def analyze_local(self, **kwargs: Any) -> bool:
        self.events.append(
            (
                "local_gemini",
                kwargs["video_path"],
                kwargs["platform"],
                kwargs["duration_seconds"],
            )
        )
        if self.local_ok:
            kwargs["result"].update(deepcopy(self.local_patch))
            kwargs["result"]["layers_used"].append("gemini_stub")
        return self.local_ok

    def analyze_claude(
        self, video_path: str, filename: str, *, creator_handle: str
    ) -> dict[str, Any]:
        self.events.append(("claude_video", video_path, filename, creator_handle))
        return deepcopy(self.claude)

    def parse_caption(self, text: str) -> dict[str, Any]:
        self.events.append(("caption", text))
        return deepcopy(self.caption)

    def analyze_text(
        self,
        title: str,
        caption: str,
        url: str,
        platform: str,
        scraped_text: str,
        *,
        og_image: str,
    ) -> dict[str, Any]:
        self.events.append(
            ("text", title, caption, url, platform, scraped_text, og_image)
        )
        return deepcopy(self.text)

    def weighted_scores(
        self, quality_scores: dict[str, Any], genre: str
    ) -> dict[str, Any]:
        self.events.append(("weights", tuple(quality_scores.items()), genre))
        return {
            "tech_score": 7.25,
            "marketing_score": 6.5,
            "quality_overall": 7.0,
            "weighted_overall": 7.0,
        }

    def unlink(self, path: str) -> None:
        self.events.append(("unlink", path))
        if self.unlink_error is not None:
            raise self.unlink_error


def _run(**overrides: Any) -> dict[str, Any]:
    payload = {
        "url": "https://example.test/post/1",
        "title": "Field Review",
        "caption": "Caption text",
        "scraped_text": "Scraped text",
        "og_image": "https://example.test/cover.jpg",
        "platform": "Pinterest",
        "creator_handle": "creator_1",
        "direct_video_url": "",
    }
    payload.update(overrides)
    return asyncio.run(claude_vision.analyze_url_content_smart(**payload))


def test_smart_analysis_provider_unavailable_is_an_ordered_noop(monkeypatch) -> None:
    harness = Harness()
    harness.install(
        monkeypatch,
        anthropic=False,
        gemini=False,
        openai=True,
        ytdlp=True,
    )

    result = _run()

    assert _digest(result) == "15e6a0b21faacab15e619093ce6171462c98bbb2de4a6df2729a68140b7a6413"
    assert harness.events == []
    assert list(result) == [
        "analyzed", "method", "camera_body", "camera_brand", "viltrox_lens",
        "other_lens", "flash", "adapter", "accessories", "gear_combo",
        "brand_elements", "products_detected", "viltrox_products_all",
        "competitor_products", "competitor_brands", "content_genre",
        "content_topic", "content_summary", "production_quality", "audience_fit",
        "content_types", "notes", "layers_used", "error", "quality_scores",
        "quality_overall", "quality_summary", "reference_value",
        "reference_reasons", "improvements", "marketing_potential",
        "marketing_notes", "timestamps", "video_source",
    ]


def test_smart_analysis_image_route_skips_download_when_evidence_is_complete(
    monkeypatch,
) -> None:
    harness = Harness(
        profile={"cameras": ["Sony A7 IV"], "viltrox_lenses": ["AF 50mm"]},
        gpt={
            "camera_body": "Sony A7 IV",
            "viltrox_lens": "AF 50mm F1.8",
            "content_genre": "review",
            "confidence": "medium",
        },
        images=["img-a", "img-b"],
        image_analysis={
            "camera_body": "Sony A7 IV",
            "viltrox_lens": "AF 50mm F1.8",
            "confidence": "high",
            "content_genre": "review",
            "content_summary": "Image review summary",
            "quality_scores": {"exposure": 8},
            "brand_elements": ["barrel logo"],
        },
        caption={},
        text={
            "quality_scores": {"exposure": 8, "focus": 7},
            "quality_summary": "Good exposure",
            "competitor_brands": ["Sigma"],
            "notes": "text checked",
        },
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=False,
        openai=True,
        ytdlp=True,
    )

    result = _run()

    assert _digest(result) == "7b648a5d14551955de30d69cf5b6b7270ea7751aaebdf429ccc8a28adf08ebdb"
    assert result["method"] == "image_vision_2imgs"
    assert result["layers_used"] == ["gpt_prefilter", "images(2)", "text_claude"]
    assert not any(event[0] in {"direct", "ytdlp", "local_gemini", "claude_video"} for event in harness.events)
    assert [event[0] for event in harness.events] == [
        "profile", "gpt", "images", "image_analysis", "caption", "text", "weights",
    ]


def test_youtube_complete_gemini_skips_media_download_but_keeps_text_scoring(
    monkeypatch,
) -> None:
    harness = Harness(
        youtube={
            "analyzed": True,
            "camera_body": "Sony FX3",
            "viltrox_lens": "AF 16mm F1.8",
            "confidence": "high",
            "content_genre": "review",
            "content_summary": "Full video read",
            "quality_scores": {"exposure": 9},
            "timestamps": [{"time": "00:12", "event": "lens"}],
            "token_usage": 999,
            "cost_usd": 12.5,
        },
        text={"notes": "text checked"},
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=True,
        openai=False,
        ytdlp=True,
    )

    result = _run(
        url="https://youtube.com/watch?v=abc",
        platform="YouTube",
        creator_handle="",
    )

    assert _digest(result) == "7c701efa68d65165ef2dcb5b5b7b01781a8f4738a59cc729f41de992bb14ae60"
    assert result["method"] == "gemini_youtube"
    assert result["layers_used"] == ["gemini_youtube", "text_claude"]
    assert "token_usage" not in result and "cost_usd" not in result
    assert [event[0] for event in harness.events] == [
        "youtube", "images", "caption", "text", "weights",
    ]


def test_non_youtube_video_keeps_direct_ytdlp_gemini_claude_fallback_order(
    monkeypatch,
) -> None:
    harness = Harness(
        direct={
            "success": False,
            "path": None,
            "duration": 0,
            "error": "signed URL expired",
        },
        ytdlp={
            "success": True,
            "path": "/tmp/stub-video.mp4",
            "duration": 12.4,
            "error": "",
        },
        local_ok=False,
        claude={
            "analyzed": True,
            "camera_body": "Sony FX30",
            "viltrox_lens": "AF 27mm F1.2",
            "confidence": "medium",
            "content_genre": "cinematic",
            "quality_scores": {"focus": 7},
        },
        text={
            "quality_scores": {"focus": 7, "exposure": 6},
            "content_summary": "Fallback summary",
        },
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=True,
        openai=False,
        ytdlp=True,
    )

    result = _run(
        platform="Instagram",
        direct_video_url="https://cdn.example.test/video.mp4",
    )

    assert _digest(result) == "145eb837cd490915e90610ad6d12b7fbf4947ac2f9b904c53d0371de8f4aa268"
    assert result["video_source"] == "ytdlp"
    assert result["method"] == "ytdlp_claude_Instagram"
    assert result["layers_used"] == [
        "direct_video_failed", "video(12s)", "text_claude",
    ]
    assert [event[0] for event in harness.events] == [
        "profile", "images", "direct", "ytdlp", "local_gemini",
        "claude_video", "unlink", "caption", "text", "weights",
    ]


def test_local_gemini_success_prevents_claude_fallback(monkeypatch) -> None:
    harness = Harness(
        direct={
            "success": True,
            "path": "/tmp/direct-video.mp4",
            "duration": 8,
            "error": "",
        },
        local_ok=True,
        local_patch={
            "analyzed": True,
            "method": "gemini_fileapi_Instagram_stub",
            "camera_body": "Sony A7C II",
            "viltrox_lens": "AF 40mm F2.5",
            "quality_scores": {"exposure": 7},
            "content_genre": "vlog",
        },
        text={},
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=True,
        openai=False,
        ytdlp=True,
    )

    result = _run(
        platform="Instagram",
        direct_video_url="https://cdn.example.test/direct.mp4",
    )

    assert result["video_source"] == "direct_url"
    assert result["method"] == "gemini_fileapi_Instagram_stub"
    assert "gemini_stub" in result["layers_used"]
    assert not any(event[0] in {"ytdlp", "claude_video"} for event in harness.events)
    assert ("unlink", "/tmp/direct-video.mp4") in harness.events


@pytest.mark.parametrize(
    ("platform", "should_download"),
    [
        ("Instagram", True), ("TikTok", True), ("Douyin", True),
        ("Facebook", True), ("Bilibili", True), ("Xiaohongshu", True),
        ("Reddit", True), ("Unknown", True), ("Pinterest", False),
    ],
)
def test_video_platform_classification_is_exact(
    monkeypatch, platform: str, should_download: bool
) -> None:
    harness = Harness(
        gpt={"camera_body": "Body", "viltrox_lens": "Lens"},
        image_analysis={"quality_scores": {"exposure": 7}, "confidence": "high"},
        images=["image"],
        text={},
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=False,
        openai=True,
        ytdlp=True,
    )

    _run(platform=platform, creator_handle="")

    assert any(event[0] == "ytdlp" for event in harness.events) is should_download


def test_empty_provider_results_preserve_empty_output_and_failed_download_layer(
    monkeypatch,
) -> None:
    harness = Harness()
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=False,
        openai=True,
        ytdlp=True,
    )

    result = _run(platform="Unknown", creator_handle="")

    assert _digest(result) == "b04a522a854364281bc79784047189b95693a92e02114c2f4fa25c49c4a68454"
    assert result["analyzed"] is False
    assert result["layers_used"] == ["gpt_prefilter", "ytdlp_failed"]
    assert [event[0] for event in harness.events] == [
        "gpt", "images", "ytdlp", "caption", "text",
    ]


def test_provider_timeout_propagates_without_starting_later_layers(monkeypatch) -> None:
    expected = TimeoutError("gemini deadline")
    harness = Harness(youtube_error=expected)
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=True,
        openai=False,
        ytdlp=True,
    )

    with pytest.raises(TimeoutError) as captured:
        _run(
            url="https://youtube.com/watch?v=timeout",
            platform="YouTube",
            creator_handle="",
        )

    assert captured.value is expected
    assert harness.events == [
        ("youtube", "https://youtube.com/watch?v=timeout", "Field Review", "")
    ]


def test_video_cleanup_oserror_is_suppressed_before_text_fallback(monkeypatch) -> None:
    harness = Harness(
        ytdlp={
            "success": True,
            "path": "/tmp/cleanup.mp4",
            "duration": 1,
            "error": "",
        },
        claude={},
        text={},
        unlink_error=OSError("already removed"),
    )
    harness.install(
        monkeypatch,
        anthropic=True,
        gemini=False,
        openai=False,
        ytdlp=True,
    )

    result = _run(platform="Instagram", creator_handle="")

    assert result["layers_used"] == ["video(1s)"]
    assert [event[0] for event in harness.events][-3:] == ["unlink", "caption", "text"]


def test_smart_analysis_family_complexity_size_and_dependency_are_bounded() -> None:
    runtime = getattr(claude_vision, "_smart_runtime", None)
    assert runtime is not None
    modules = (claude_vision, runtime)
    rows = []
    for module in modules:
        path = Path(module.__file__)
        source = path.read_text(encoding="utf-8")
        rows.extend(collect_complexity({str(path): ast.parse(source)}))
        assert len(source.splitlines()) < 800
    public = next(
        row for row in rows
        if row.path == str(Path(claude_vision.__file__))
        and row.qualified_name == "analyze_url_content_smart"
    )
    assert public.cc <= 10
    assert max(row.cc for row in rows) <= 30
    runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
    assert "claude_vision import" not in runtime_source
