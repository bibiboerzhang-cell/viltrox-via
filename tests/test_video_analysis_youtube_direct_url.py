"""刀①:YouTube 直链只喂规范化 watch?v= 链接(带参 URL 是直链秒拒的头号真因)。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video, gemini_video_youtube
from app.services.ai.analyzers.gemini_video_youtube import canonical_youtube_url


CANON = "https://www.youtube.com/watch?v=QfcIpjtZ1s4"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=QfcIpjtZ1s4&t=314s",
        "https://www.youtube.com/watch?v=QfcIpjtZ1s4&pp=ygUHdmlsdHJveA%3D%3D",
        "https://www.youtube.com/watch?feature=share&v=QfcIpjtZ1s4",
        "https://youtu.be/QfcIpjtZ1s4?si=H0XZh-q0T8sPh2yF",
        "https://youtu.be/QfcIpjtZ1s4",
        "https://m.youtube.com/watch?v=QfcIpjtZ1s4",
        "https://www.youtube.com/shorts/QfcIpjtZ1s4",
        "https://www.youtube.com/embed/QfcIpjtZ1s4?autoplay=1",
        "https://www.youtube.com/live/QfcIpjtZ1s4",
        "  https://www.youtube.com/watch?v=QfcIpjtZ1s4  ",
        CANON,
    ],
)
def test_canonical_youtube_url_strips_params_and_unifies_hosts(url: str) -> None:
    assert canonical_youtube_url(url) == (CANON, "QfcIpjtZ1s4")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/@channel/videos",
        "https://www.youtube.com/playlist?list=PL123",
        "https://vimeo.com/123456",
        "https://notyoutube.com/watch?v=QfcIpjtZ1s4",
    ],
)
def test_canonical_youtube_url_falls_back_to_raw_when_no_video_id(url: str) -> None:
    assert canonical_youtube_url(url) == (url, "")


SIX_LAYERS = {
    "layer1_visual_content": {
        "content_summary": "A creator compares autofocus and flare performance.",
        "scene_timeline": [{"timestamp": "00:04", "what": "Lens close-up."}],
        "evidence": {"timestamps": ["00:04 lens close-up"]},
    },
    "layer6_flags_and_scores": {"final_verdict": "Useful category evidence."},
}


class _Resp:
    text = json.dumps(SIX_LAYERS)
    usage_metadata = SimpleNamespace(prompt_token_count=1, candidates_token_count=1, total_token_count=2)


def _env(monkeypatch: pytest.MonkeyPatch, seen: list[dict[str, Any]], subtitle_urls: list[str]) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", lambda url: subtitle_urls.append(url) or "")
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    monkeypatch.setattr(
        gemini_video_youtube.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("direct path must succeed; no yt-dlp")),
    )

    def _generate(**kwargs: Any) -> Any:
        seen.append(kwargs)
        return _Resp()

    monkeypatch.setattr(gemini_video, "_strict_generate_content", _generate)


def test_direct_path_passes_canonical_uri_to_gemini_and_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    subtitle_urls: list[str] = []
    _env(monkeypatch, seen, subtitle_urls)
    original = "https://www.youtube.com/watch?v=QfcIpjtZ1s4&t=314s"
    result = asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(
            original, "demo", schema_version="final_v1", models=["gemini-2.5-flash"]
        )
    )
    assert result["analyzed"] is True
    assert result["method"] == "gemini_direct_gemini-2.5-flash"
    assert len(seen) == 1
    file_part = seen[0]["contents"][0]
    assert file_part.file_data.file_uri == CANON
    assert result["youtube_direct"]["url"] == CANON
    assert result["youtube_direct"]["url_canonicalized"] is True
    # 字幕抓取仍用原链(yt-dlp 对附加参数无感,行为不动)
    assert subtitle_urls == [original]


def test_direct_path_keeps_raw_url_when_not_canonicalizable(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    _env(monkeypatch, seen, [])
    odd = "https://www.youtube.com/watch?v=short"
    result = asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(
            odd, "demo", schema_version="final_v1", models=["gemini-2.5-flash"]
        )
    )
    assert result["analyzed"] is True
    assert seen[0]["contents"][0].file_data.file_uri == odd
    assert result["youtube_direct"]["url_canonicalized"] is False


# ── 刀③:慢路(yt-dlp)可调参——默认行为不变,运维用 env 降分辨率 / 透传 cookies ──────────


def _slow_path_env(monkeypatch: pytest.MonkeyPatch, commands: list[list[str]]) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "gemini_client", object())
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_BIN", "yt-dlp")
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_PROXY", "")
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {})
    monkeypatch.setattr(gemini_video_youtube, "fetch_youtube_subtitles", lambda _url: "")
    monkeypatch.setattr(gemini_video, "_final_v1_cache_config", lambda _model: (None, {"enabled": False}))
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)
    monkeypatch.setattr(
        gemini_video,
        "_strict_generate_content",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("400 direct rejected")),
    )

    def _run(cmd: list[str], **_kwargs: Any) -> Any:
        commands.append(list(cmd))
        return SimpleNamespace(returncode=1, stderr=b"ERROR: Sign in to confirm you're not a bot", stdout=b"")

    monkeypatch.setattr(gemini_video_youtube.subprocess, "run", _run)


def test_slow_path_defaults_keep_720p_and_no_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    commands: list[list[str]] = []
    _slow_path_env(monkeypatch, commands)
    result = asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(CANON, "demo", schema_version="final_v1", models=["gemini-2.5-flash"])
    )
    assert result["analyzed"] is False
    assert len(commands) == 1
    cmd = commands[0]
    assert cmd[cmd.index("-f") + 1] == "best[ext=mp4][height<=720]/18/best[height<=720]/best"
    assert "--cookies" not in cmd
    assert result["download_diagnostics"]["max_height"] == 720
    assert result["download_diagnostics"]["cookies"] is False


def test_slow_path_env_levers_lower_resolution_and_pass_cookies(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", "480")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(cookies))
    commands: list[list[str]] = []
    _slow_path_env(monkeypatch, commands)
    result = asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(CANON, "demo", schema_version="final_v1", models=["gemini-2.5-flash"])
    )
    cmd = commands[0]
    assert cmd[cmd.index("-f") + 1] == "best[ext=mp4][height<=480]/18/best[height<=480]/best"
    assert cmd[cmd.index("--cookies") + 1] == str(cookies)
    assert result["download_diagnostics"]["max_height"] == 480
    assert result["download_diagnostics"]["cookies"] is True


def test_slow_path_ignores_missing_cookie_file_and_bad_height(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", "lots")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", "/nonexistent/cookies.txt")
    assert gemini_video_youtube._ytdlp_max_height() == 720
    assert gemini_video_youtube._ytdlp_cookies_args() == []
    monkeypatch.setenv("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", "99999")
    assert gemini_video_youtube._ytdlp_max_height() == 1080
