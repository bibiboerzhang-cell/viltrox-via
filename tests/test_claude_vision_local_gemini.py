"""本地文件 Gemini 梯子(从 claude_vision 抽出)经 llm_production 严格边界(2026-08-23 C3)。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.ai.analyzers import claude_vision, claude_vision_local_gemini as local


def test_claude_vision_keeps_reexport_and_no_direct_gemini_sdk_call() -> None:
    assert claude_vision.LOCAL_FILE_GEMINI_MODEL == local.LOCAL_FILE_GEMINI_MODEL == "gemini-3.6-flash"
    source = open(claude_vision.__file__, encoding="utf-8").read()
    assert "models.generate_content" not in source
    assert "files.upload" not in source


def test_local_prompt_and_estimate_are_conservative(monkeypatch) -> None:
    monkeypatch.setattr(local, "fetch_youtube_subtitles", lambda _url: "00:01 intro")
    monkeypatch.setattr(local, "get_creator_profile", lambda _h: {"viltrox_lenses": ["AF 50mm", "AF 85mm"]})
    prompt = local.build_local_video_prompt(url="https://youtube.com/watch?v=x", title="", platform="youtube", creator_handle="h")
    assert "字幕时间轴" in prompt and "AF 50mm" in prompt
    assert "标题: https://youtube.com/watch?v=x" in prompt
    assert '"content_genre"' in prompt  # JSON 契约原样保留
    known = local.local_video_input_token_estimate(prompt, 120)
    assert known >= 120 * local.LOCAL_FILE_RESERVE_TOKENS_PER_SECOND
    assert local.local_video_input_token_estimate(prompt, None) == local.llm_production.GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP


def test_generate_local_video_analysis_routes_through_strict_boundary(monkeypatch) -> None:
    captured: dict = {}

    class _Thinking:
        def __init__(self, **kw):
            self.kw = kw

    class _Config:
        def __init__(self, **kw):
            self.kw = kw

    class _Part:
        @staticmethod
        def from_uri(*, file_uri, mime_type):
            return ("uri", file_uri, mime_type)

    monkeypatch.setattr(local, "genai_types", SimpleNamespace(ThinkingConfig=_Thinking, GenerateContentConfig=_Config, Part=_Part))
    monkeypatch.setattr(local, "_gemini_client", object())
    monkeypatch.setattr(local.llm_production, "generate_google_content", lambda **kw: captured.update(kw) or SimpleNamespace(text="{}"))

    local.generate_local_video_analysis(
        gfile=SimpleNamespace(uri="files/abc"),
        prompt="p" * 300,
        model_name=local.LOCAL_FILE_GEMINI_MODEL,
        duration_seconds=30,
        title="T",
        platform="instagram",
    )

    assert captured["model"] == "gemini-3.6-flash"
    assert captured["purpose"] == "local_file_video"
    assert captured["metadata"]["task_binding"] == "local_file_video"
    assert captured["contents"][0] == ("uri", "files/abc", "video/mp4")
    assert captured["config"].kw["thinking_config"].kw == {"thinking_level": "minimal"}
    assert "temperature" not in captured["config"].kw
    assert captured["max_output_tokens"] == local.LOCAL_FILE_GEMINI_MAX_OUTPUT_TOKENS
    assert captured["estimated_input_tokens"] >= 60 * local.LOCAL_FILE_RESERVE_TOKENS_PER_SECOND


def test_file_api_ladder_merges_result_and_cleans_up(monkeypatch) -> None:
    events: list = []
    gfile = SimpleNamespace(name="files/abc", uri="files/abc", state=SimpleNamespace(name="ACTIVE"))
    files = SimpleNamespace(
        upload=lambda **kw: events.append(("upload", kw["file"])) or gfile,
        get=lambda *, name: gfile,
        delete=lambda *, name: events.append(("delete", name)),
    )
    monkeypatch.setattr(local, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(local, "_gemini_client", SimpleNamespace(files=files))
    monkeypatch.setattr(local, "genai_types", object())
    monkeypatch.setattr(local, "build_local_video_prompt", lambda **kw: "prompt")
    payload = '{"content_genre": "review", "quality_scores": {"exposure": 8}, "quality_overall": 8, "brand_exposure_detail": {"logo_on_lens_barrel": true}}'
    monkeypatch.setattr(local, "generate_local_video_analysis", lambda **kw: events.append(("generate", kw["model_name"])) or SimpleNamespace(text=payload))
    monkeypatch.setattr(local, "compute_weighted_scores", lambda *_a: {
        "brand_exposure_score": 7, "storytelling_score": 6, "tech_floor": {"status": "ok"}, "tech_score": 7, "marketing_score": 7,
    })

    result = {"layers_used": []}
    ok = asyncio.run(
        local.analyze_local_video_with_gemini_file_api(
            video_path="/tmp/v.mp4", url="https://x", title="T", platform="instagram", creator_handle="", duration_seconds=12, result=result
        )
    )

    assert ok is True
    assert result["analyzed"] is True
    assert result["method"] == "gemini_fileapi_instagram_gemini-3.6-flash"
    assert result["logo_detected"] == 1 and result["content_genre"] == "review"
    assert events[0] == ("upload", "/tmp/v.mp4") and ("generate", "gemini-3.6-flash") in events and events[-1] == ("delete", "files/abc")


def test_file_api_ladder_returns_false_when_model_call_fails(monkeypatch) -> None:
    gfile = SimpleNamespace(name="f", uri="f", state=SimpleNamespace(name="ACTIVE"))
    monkeypatch.setattr(local, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(local, "_gemini_client", SimpleNamespace(files=SimpleNamespace(upload=lambda **kw: gfile, get=lambda *, name: gfile, delete=lambda *, name: None)))
    monkeypatch.setattr(local, "genai_types", object())
    monkeypatch.setattr(local, "build_local_video_prompt", lambda **kw: "prompt")

    def _blocked(**_kw):
        raise RuntimeError("budget_blocked")

    monkeypatch.setattr(local, "generate_local_video_analysis", _blocked)
    result = {"layers_used": []}
    assert asyncio.run(local.analyze_local_video_with_gemini_file_api(video_path="v", url="u", title="", platform="tiktok", creator_handle="", duration_seconds=None, result=result)) is False
    assert "analyzed" not in result
