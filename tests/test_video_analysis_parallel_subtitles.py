"""优化波 B·F2(字幕侧):字幕抓取与 Gemini 直链并行,晚到不等;慢路仍尽量带字幕。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video, gemini_video_youtube


URL = "https://www.youtube.com/watch?v=QfcIpjtZ1s4"
SIX_LAYERS = {
    "layer1_visual_content": {
        "content_summary": "summary",
        "scene_timeline": [{"timestamp": "00:04", "what": "x"}],
        "evidence": {"timestamps": ["00:04 x"]},
    },
    "layer6_flags_and_scores": {"final_verdict": "verdict"},
}


def _resp() -> Any:
    return SimpleNamespace(
        text=json.dumps(SIX_LAYERS),
        usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1, total_token_count=2),
    )


def _env(monkeypatch: pytest.MonkeyPatch, *, subtitle_delay: float, grace: float, prompts: list[str]) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_VIDEO_SUBTITLE_GRACE_SECONDS", grace)

    def _slow_subtitles(_url: str) -> str:
        time.sleep(subtitle_delay)
        return "[00:01] hello from subtitles"

    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", _slow_subtitles)
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)

    def _generate(**kwargs: Any) -> Any:
        prompts.append(str(kwargs["prompt"]))
        return _resp()

    monkeypatch.setattr(gemini_video, "_strict_generate_content", _generate)
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("direct path must succeed")),
    )


def _run() -> dict[str, Any]:
    return asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(URL, "demo", schema_version="final_v1", models=["gemini-3.6-flash"])
    )


def test_late_subtitles_do_not_block_direct_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    _env(monkeypatch, subtitle_delay=0.8, grace=0.05, prompts=prompts)
    started = time.monotonic()
    result = _run()
    elapsed = time.monotonic() - started
    assert result["analyzed"] is True
    assert elapsed < 0.6, elapsed  # 没有等 0.8s 的字幕
    assert "hello from subtitles" not in prompts[0]
    assert result["subtitle_chars"] == 0
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is False
    diag = result["diagnostics"]["subtitles"]
    assert diag["parallel"] is True and diag["used"] is False
    assert "subtitles" in result["stage_timings_ms"]


def test_fast_subtitles_are_still_used_within_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    _env(monkeypatch, subtitle_delay=0.05, grace=2.0, prompts=prompts)
    result = _run()
    assert result["analyzed"] is True
    assert "hello from subtitles" in prompts[0]
    assert result["subtitle_chars"] == len("[00:01] hello from subtitles")
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is True
    diag = result["diagnostics"]["subtitles"]
    assert diag["used"] is True and diag["done"] is True and diag["chars"] > 0
    assert result["stage_timings_ms"]["subtitles"] >= 40


def test_subtitle_fetch_runs_on_daemon_thread_and_tolerates_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    names: list[str] = []

    def _boom(_url: str) -> str:
        names.append(threading.current_thread().name)
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", _boom)
    fetch = gemini_video_youtube._SubtitleFetch(URL)
    assert fetch.collect(1.0) == ""
    assert fetch.done is True
    assert fetch.error.startswith("RuntimeError")
    assert names == ["gemini-youtube-subtitles"]
    assert fetch.diagnostics(used=False)["error"].startswith("RuntimeError")


def test_slow_path_waits_longer_for_subtitles(monkeypatch: pytest.MonkeyPatch) -> None:
    """直链失败后走慢路:字幕此时多半已到,File API 生成要带上。"""

    prompts: list[str] = []
    _env(monkeypatch, subtitle_delay=0.3, grace=0.0, prompts=prompts)
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_VIDEO_SUBTITLE_SLOW_PATH_WAIT_SECONDS", 5.0)
    calls = {"n": 0}

    def _generate(**kwargs: Any) -> Any:
        calls["n"] += 1
        prompts.append(str(kwargs["prompt"]))
        if calls["n"] == 1:
            raise ValueError("Gemini response JSON root must be an object")
        return _resp()

    monkeypatch.setattr(gemini_video, "_strict_generate_content", _generate)
    monkeypatch.setattr(gemini_video_youtube.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(gemini_video_youtube.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(gemini_video_youtube.os.path, "getsize", lambda _p: 5_000_000)
    active = SimpleNamespace(name="files/abc", uri="https://generativelanguage/files/abc", state=SimpleNamespace(name="ACTIVE"))
    client = SimpleNamespace(
        files=SimpleNamespace(upload=lambda **_k: active, get=lambda **_k: active, delete=lambda **_k: None)
    )
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", client)
    result = _run()
    assert result["analyzed"] is True
    assert result["method"] == "gemini_fileapi_gemini-3.6-flash"
    assert "hello from subtitles" not in prompts[0]  # 直链那次没等
    assert "hello from subtitles" in prompts[1]  # 慢路那次带上了
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is True
