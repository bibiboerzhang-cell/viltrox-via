"""优化波 B·F1 / C4:输出上限按家族、MAX_TOKENS 截断续写、链只在提供方压力时换模型。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.platform import llm_production_google_helpers as helpers
from app.services.ai.analyzers import gemini_video, gemini_video_recovery as rec, gemini_video_youtube


SIX_LAYERS = {
    "layer1_visual_content": {
        "content_summary": "A creator compares autofocus and flare performance.",
        "scene_timeline": [{"timestamp": "00:04", "what": "Lens close-up."}],
        "evidence": {"timestamps": ["00:04 lens close-up"]},
    },
    "layer6_flags_and_scores": {"final_verdict": "Useful category evidence."},
}
URL = "https://www.youtube.com/watch?v=QfcIpjtZ1s4"


def _resp(text: str, finish: str = "STOP", *, tokens: int = 5) -> Any:
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=tokens, total_token_count=10 + tokens),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
    )


# ── F1: 按家族输出上限 ───────────────────────────────────────────────────────────


def test_hard_cap_raised_and_family_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("GEMINI_VIDEO_MAX_OUTPUT_TOKENS", "APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS", "GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3"):
        monkeypatch.delenv(key, raising=False)
    assert helpers.GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP == 65536
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 24576
    assert rec.gemini_video_max_output_tokens("gemini-3.5-flash-lite") == 24576
    assert rec.gemini_video_max_output_tokens("gemini-2.5-flash") == 8192
    assert rec.gemini_video_max_output_tokens("gemini-2.5-pro") == 8192


def test_env_overrides_keep_working(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3", raising=False)
    # prod 里钉着的旧 8192 不会把 3.x 压回截断区
    monkeypatch.setenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS", "8192")
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 24576
    assert rec.gemini_video_max_output_tokens("gemini-2.5-flash") == 8192
    # 通用 env 抬高则全家族一起抬
    monkeypatch.setenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS", "32768")
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 32768
    assert rec.gemini_video_max_output_tokens("gemini-2.5-flash") == 32768
    # 3.x 精确钉死(可降可升),最终被硬顶钳位
    monkeypatch.setenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3", "12000")
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 12000
    monkeypatch.setenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3", "999999")
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 65536
    monkeypatch.setenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3", "1")
    assert rec.gemini_video_max_output_tokens("gemini-3.6-flash") == 256


def test_strict_generate_content_passes_family_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _resp("{}")

    monkeypatch.setattr(gemini_video.llm_production, "generate_google_content", _fake)
    monkeypatch.delenv("GEMINI_VIDEO_MAX_OUTPUT_TOKENS_GEMINI3", raising=False)
    gemini_video._strict_generate_content(
        model_name="gemini-3.6-flash", contents=["x"], config=None, prompt="p", performance_context=None,
        llm_context=None, subphase="s", attempt_index=1, attempt_total=1, attempt_log=[],
    )
    assert seen["max_output_tokens"] == 24576
    gemini_video._strict_generate_content(
        model_name="gemini-3.6-flash", contents=["x"], config=None, prompt="p", performance_context=None,
        llm_context=None, subphase="s", attempt_index=1, attempt_total=1, attempt_log=[], max_output_tokens=4096,
    )
    assert seen["max_output_tokens"] == 4096


# ── F1: finish_reason / 拼接 / 续写内容 ──────────────────────────────────────────


def test_finish_reason_helpers_tolerate_shapes() -> None:
    assert helpers.google_finish_reason(_resp("x", "MAX_TOKENS")) == "MAX_TOKENS"
    assert helpers.google_response_truncated(_resp("x", "MAX_TOKENS")) is True
    assert helpers.google_response_truncated(_resp("x", "STOP")) is False
    assert helpers.google_finish_reason(SimpleNamespace(candidates=[SimpleNamespace(finish_reason="FinishReason.MAX_TOKENS")])) == "MAX_TOKENS"
    assert helpers.google_finish_reason(SimpleNamespace(candidates=[{"finish_reason": "max_tokens"}])) == "MAX_TOKENS"
    assert helpers.google_finish_reason(SimpleNamespace(text="no candidates")) == ""
    assert helpers.google_finish_reason(SimpleNamespace(candidates=[])) == ""


def test_stitch_strips_fences_and_overlap() -> None:
    prefix = '```json\n{"a": 1, "b": "long text that got cut'
    continuation = '"long text that got cut off", "c": 2}\n```'
    assert rec.stitch_truncated_json(prefix, continuation) == '{"a": 1, "b": "long text that got cut off", "c": 2}'
    assert json.loads(rec.stitch_truncated_json('{"a": 1,', ' "b": 2}')) == {"a": 1, "b": 2}
    assert rec.stitch_truncated_json("", "{}") == "{}"
    assert rec.stitch_truncated_json("{", "") == "{"


def test_continuation_contents_are_three_turns() -> None:
    turns = rec.continuation_contents([{"file": "video"}, "prompt"], '{"a":', genai_types=None)
    assert [t["role"] for t in turns] == ["user", "model", "user"]
    assert turns[0]["parts"][0] == {"file": "video"}
    assert turns[0]["parts"][1] == {"text": "prompt"}
    assert turns[1]["parts"] == [{"text": '{"a":'}]
    assert rec.CONTINUATION_INSTRUCTION in turns[2]["parts"][0]["text"]


def test_merge_usage_sums_token_counts() -> None:
    merged = rec.merge_usage_metadata(
        {"prompt_token_count": 100, "candidates_token_count": 8192, "extra": "a"},
        {"prompt_token_count": 100, "candidates_token_count": 900, "thoughts_token_count": 5},
    )
    assert merged["prompt_token_count"] == 200
    assert merged["candidates_token_count"] == 9092
    assert merged["thoughts_token_count"] == 5
    assert merged["extra"] == "a"
    assert merged["continuation_calls"] == 1


# ── F1: _generate_json_with_recovery 续写一次 ─────────────────────────────────────


def _recovery_call(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    seen: list[dict[str, Any]] = []

    def _fake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        out = responses.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(gemini_video, "_strict_generate_content", _fake)
    diagnostics: dict[str, Any] = {}
    out = gemini_video._generate_json_with_recovery(
        model_name="gemini-3.6-flash", contents=[{"file": "v"}, "prompt"], config=None, prompt="prompt",
        performance_context=None, llm_context=None, subphase="youtube_uri_fast_generation",
        attempt_index=1, attempt_total=2, attempt_log=[], diagnostics=diagnostics,
    )
    return diagnostics, seen, out


def test_truncated_output_is_continued_once_on_same_model(monkeypatch: pytest.MonkeyPatch) -> None:
    full = json.dumps(SIX_LAYERS)
    cut = len(full) // 2
    diagnostics, seen, (parsed, usage) = _recovery_call(
        monkeypatch,
        [_resp(full[:cut], "MAX_TOKENS", tokens=8192), _resp(full[cut:], "STOP", tokens=700)],
    )
    assert parsed == SIX_LAYERS
    assert len(seen) == 2
    assert seen[0]["subphase"] == "youtube_uri_fast_generation"
    assert seen[1]["subphase"] == "youtube_uri_fast_generation_continuation"
    assert seen[1]["model_name"] == "gemini-3.6-flash"
    turns = seen[1]["contents"]
    assert len(turns) == 3 and getattr(turns[1], "role", None) == "model"
    assert usage["candidates_token_count"] == 8892
    assert diagnostics["truncation"]["hit"] is True
    assert diagnostics["truncation"]["recovered"] is True
    assert diagnostics["retries"]["calls"] == 2


def test_not_truncated_output_makes_single_call(monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostics, seen, (parsed, _usage) = _recovery_call(monkeypatch, [_resp(json.dumps(SIX_LAYERS))])
    assert parsed == SIX_LAYERS and len(seen) == 1
    assert diagnostics["truncation"] == {"hit": False, "recovered": False}


def test_continuation_that_restarts_whole_json_still_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    full = json.dumps(SIX_LAYERS)
    _diag, _seen, (parsed, _u) = _recovery_call(
        monkeypatch, [_resp(full[: len(full) // 3], "MAX_TOKENS"), _resp("```json\n" + full + "\n```")]
    )
    assert parsed == SIX_LAYERS


def test_continuation_failure_surfaces_as_json_error_not_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    full = json.dumps(SIX_LAYERS)
    seen: list[Any] = []

    def _fake(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _resp(full[:20], "MAX_TOKENS") if len(seen) == 1 else _resp("still broken", "MAX_TOKENS")

    monkeypatch.setattr(gemini_video, "_strict_generate_content", _fake)
    diagnostics: dict[str, Any] = {}
    with pytest.raises(ValueError):
        gemini_video._generate_json_with_recovery(
            model_name="gemini-3.6-flash", contents=["v", "p"], config=None, prompt="p", performance_context=None,
            llm_context=None, subphase="s", attempt_index=1, attempt_total=1, attempt_log=[], diagnostics=diagnostics,
        )
    gemini_video._mark_attempt_failed(diagnostics)
    assert diagnostics["truncation"]["hit"] is True and diagnostics["truncation"]["recovered"] is False
    assert len(seen) == 2  # 续写只做一次


# ── C4: 链换节判据 ───────────────────────────────────────────────────────────────


class _ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_should_switch_model_only_on_provider_pressure_or_transport() -> None:
    from app.services.ai.analyzers.gemini_video_results import InvalidFinalV1ResultError

    assert rec.should_switch_model(_ApiError(503, "503 UNAVAILABLE")) is True
    assert rec.should_switch_model(_ApiError(429, "429 RESOURCE_EXHAUSTED")) is True
    assert rec.should_switch_model(RuntimeError("model is overloaded")) is True
    assert rec.should_switch_model(ConnectionError("reset")) is True
    assert rec.should_switch_model(RuntimeError("522 decodo")) is True
    assert rec.should_switch_model(json.JSONDecodeError("x", "{", 1)) is False
    assert rec.should_switch_model(ValueError("Gemini response JSON root must be an object")) is False
    assert rec.should_switch_model(InvalidFinalV1ResultError("invalid_result: missing verdict")) is False
    assert rec.should_switch_model(_ApiError(400, "400 INVALID_ARGUMENT: bad uri")) is False


def _youtube_env(monkeypatch: pytest.MonkeyPatch, generate: Any) -> list[str]:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", lambda _url: "")
    monkeypatch.setattr(gemini_video, "_strict_generate_content", generate)
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    slow: list[str] = []
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_args, **_kwargs: slow.append("yt-dlp") or SimpleNamespace(returncode=1, stderr=b"blocked"),
    )
    monkeypatch.setattr(gemini_video_youtube.os.path, "exists", lambda _p: False)
    return slow


def _run(models: list[str]) -> dict[str, Any]:
    return asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(URL, "demo", schema_version="final_v1", models=models)
    )


def test_direct_path_json_failure_does_not_burn_second_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _generate(**kwargs: Any) -> Any:
        calls.append(kwargs["model_name"])
        return _resp("not json at all")

    slow = _youtube_env(monkeypatch, _generate)
    result = _run(["gemini-3.6-flash", "gemini-3.5-flash-lite"])
    assert result["analyzed"] is False
    # 直链只试了主力;慢路再试一次主力(换透传方式而非换模型),lite 一次都没碰
    assert calls == ["gemini-3.6-flash"]
    assert slow == ["yt-dlp"]
    assert result["youtube_direct"]["chain_stop_reason"].startswith("JSONDecodeError")
    assert result["diagnostics"]["subtitles"]["parallel"] is True


def test_direct_path_provider_pressure_switches_model_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _generate(**kwargs: Any) -> Any:
        calls.append(kwargs["model_name"])
        raise _ApiError(503, "503 UNAVAILABLE: high demand")

    slow = _youtube_env(monkeypatch, _generate)
    with pytest.raises(gemini_video.ProviderPressureExhausted):
        _run(["gemini-3.6-flash", "gemini-3.5-flash-lite"])
    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert slow == []


def test_direct_path_pressure_on_primary_succeeds_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _generate(**kwargs: Any) -> Any:
        calls.append(kwargs["model_name"])
        if kwargs["model_name"] == "gemini-3.6-flash":
            raise _ApiError(429, "429 RESOURCE_EXHAUSTED")
        return _resp(json.dumps(SIX_LAYERS))

    _youtube_env(monkeypatch, _generate)
    result = _run(["gemini-3.6-flash", "gemini-3.5-flash-lite"])
    assert result["analyzed"] is True
    assert result["model"] == "gemini-3.5-flash-lite"
    assert calls == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]


def test_direct_path_truncation_recovers_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    full = json.dumps(SIX_LAYERS)
    responses = [_resp(full[:40], "MAX_TOKENS"), _resp(full[40:], "STOP")]
    _youtube_env(monkeypatch, lambda **_kw: responses.pop(0))
    result = _run(["gemini-3.6-flash"])
    assert result["analyzed"] is True
    assert result["diagnostics"]["truncation"] == {
        "hit": True, "recovered": True, "model": "gemini-3.6-flash", "prefix_chars": 40,
        "continuation_chars": len(full) - 40, "continuation_truncated": False,
    }


def test_default_chain_is_primary_then_lite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_FINAL_V1_MODELS", raising=False)
    assert gemini_video.final_v1_gemini_models() == list(gemini_video.DEFAULT_GEMINI_FINAL_V1_MODELS)
    assert gemini_video.DEFAULT_GEMINI_FINAL_V1_MODELS[0] == "gemini-3.6-flash"
    assert gemini_video.DEFAULT_GEMINI_FINAL_V1_MODELS[1:] == ["gemini-3.5-flash-lite"]
    monkeypatch.setenv("GEMINI_FINAL_V1_MODELS", "gemini-2.5-flash")
    assert gemini_video.final_v1_gemini_models() == ["gemini-2.5-flash"]
