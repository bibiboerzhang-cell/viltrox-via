"""Complete-return and side-effect-order freeze for the YouTube analyzer split."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.analyzers import gemini_video, gemini_video_youtube


URL = "https://www.youtube.com/watch?v=abcdefghijk"
LEGACY_OUTPUT_SHA256 = {
    "direct": "da716f9e9e4573afcfd2d44b5c6de64928f8aea68fb3f12d98ef59f5b696c08b",
    "file_api": "5b7d41785d9f382dafeac6f9babfaee1b4c6461b1648a9e6e24845bb4bfbbd0f",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class _Part:
    def __init__(self, *, file_data: Any = None) -> None:
        self.file_data = file_data

    @classmethod
    def from_uri(cls, *, file_uri: str, mime_type: str) -> Any:
        return SimpleNamespace(file_uri=file_uri, mime_type=mime_type)


class _FileData:
    def __init__(self, *, file_uri: str) -> None:
        self.file_uri = file_uri


class _Subtitle:
    def __init__(self, url: str, calls: list[str]) -> None:
        calls.append(f"subtitles:start:{url}")
        self.calls = calls
        self.done = True
        self.text = "[00:01] frozen subtitle"
        self.elapsed_ms = 7

    def collect(self, timeout_seconds: float) -> str:
        self.calls.append(f"subtitles:collect:{timeout_seconds}")
        return self.text

    def diagnostics(self, *, used: bool) -> dict[str, Any]:
        return {
            "parallel": True,
            "done": True,
            "elapsed_ms": 7,
            "chars": len(self.text),
            "used": used,
            "error": "",
        }


def _install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    direct_fails: bool,
) -> None:
    monkeypatch.setattr(gemini_video_youtube, "GEMINI_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_AVAILABLE", True)
    monkeypatch.setattr(gemini_video_youtube, "YTDLP_PROXY", "")
    monkeypatch.setattr(gemini_video_youtube, "genai_types", SimpleNamespace(Part=_Part, FileData=_FileData))
    monkeypatch.setattr(gemini_video_youtube, "get_creator_profile", lambda _handle: {"viltrox_lenses": ["AF 16mm"]})
    monkeypatch.setattr(
        gemini_video_youtube,
        "_SubtitleFetch",
        lambda url: _Subtitle(url, calls),
    )
    monkeypatch.setattr(gemini_video_youtube, "_ytdlp_max_height", lambda: 720)
    monkeypatch.setattr(gemini_video_youtube, "_ytdlp_cookies_args", lambda: [])
    monkeypatch.setattr(gemini_video_youtube.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(gemini_video_youtube.os.path, "getsize", lambda _path: 4096)

    def subprocess_run(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("download")
        return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

    monkeypatch.setattr(gemini_video_youtube.subprocess, "run", subprocess_run)
    monkeypatch.setattr(
        gemini_video_youtube,
        "_build_prompts",
        lambda schema, **kwargs: (
            calls.append(f"prompts:{schema}:{bool(kwargs['subtitle_raw'])}")
            or ("dynamic prompt", "full prompt")
        ),
    )

    active_file = SimpleNamespace(
        name="files/frozen",
        uri="https://files.test/frozen",
        state=SimpleNamespace(name="ACTIVE"),
    )

    class Files:
        def upload(self, **_kwargs: Any) -> Any:
            calls.append("upload")
            return active_file

        def get(self, *, name: str) -> Any:
            calls.append(f"poll:{name}")
            return active_file

        def delete(self, *, name: str) -> None:
            calls.append(f"delete:{name}")

    monkeypatch.setattr(gemini_video_youtube, "gemini_client", SimpleNamespace(files=Files()))
    monkeypatch.setattr(gemini_video, "final_v1_gemini_models", lambda _models: ["gemini-frozen"])
    monkeypatch.setattr(
        gemini_video,
        "_final_v1_cache_config",
        lambda model: calls.append(f"cache:{model}") or (None, {"enabled": False}),
    )
    monkeypatch.setattr(gemini_video, "_video_generate_config", lambda *_args: None)

    attempts = {"count": 0}

    def generate_json(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        attempts["count"] += 1
        calls.append(f"generate:{kwargs['subphase']}:{kwargs['model_name']}")
        if direct_fails and attempts["count"] == 1:
            raise ValueError("direct transport rejected")
        return {"frozen": True}, {"total_token_count": 2}

    monkeypatch.setattr(gemini_video, "_generate_json_with_recovery", generate_json)
    monkeypatch.setattr(
        gemini_video,
        "_mark_attempt_failed",
        lambda diagnostics: calls.append("attempt:failed") or diagnostics.update(attempt_failed=True),
    )
    monkeypatch.setattr(gemini_video, "_retry_after_context_cache_error", lambda *_args: False)
    monkeypatch.setattr(gemini_video, "_is_provider_pressure_error", lambda _error: False)

    def stage_add(result: dict[str, Any], stage: str, _started: float) -> int:
        calls.append(f"stage:{stage}")
        timings = result.setdefault("stage_timings_ms", {})
        timings[stage] = int(timings.get(stage) or 0) + 1
        return 1

    monkeypatch.setattr(gemini_video, "_stage_add", stage_add)
    monkeypatch.setattr(
        gemini_video,
        "_stamp_analyzer_model_identity",
        lambda result, chain, selected, diagnostics: (
            calls.append(f"stamp:{selected}"),
            result.update(
                requested_model_chain=list(chain),
                selected_model=selected,
                provider_reported_model=selected,
            ),
            diagnostics.update(selected_model=selected),
        ),
    )

    def apply_final(
        result: dict[str, Any],
        parsed: dict[str, Any],
        *,
        method: str,
        model: str,
        usage_metadata: dict[str, Any],
        subtitle_used: bool,
    ) -> None:
        calls.append(f"apply:{method}")
        result.update(
            analyzed=True,
            method=method,
            model=model,
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
            parsed=parsed,
        )

    monkeypatch.setattr(gemini_video_youtube, "_apply_final_v1_result", apply_final)


def _run(calls: list[str]) -> dict[str, Any]:
    def checkpoint(stage: str) -> None:
        calls.append(f"auth:{stage}")

    return asyncio.run(
        gemini_video_youtube.analyze_youtube_with_gemini(
            URL,
            "Frozen title",
            creator_handle="creator",
            schema_version="final_v1",
            models=["gemini-frozen"],
            authorization_checkpoint=checkpoint,
        )
    )


def test_direct_success_complete_output_and_call_order_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_runtime(monkeypatch, calls, direct_fails=False)
    result = _run(calls)

    assert _digest(result) == LEGACY_OUTPUT_SHA256["direct"]
    assert calls == [
        "auth:youtube_subtitles",
        f"subtitles:start:{URL}",
        "auth:youtube_direct_attempt",
        "cache:gemini-frozen",
        "stage:cache_setup",
        "prompts:final_v1:True",
        "generate:youtube_uri_fast_generation:gemini-frozen",
        "stage:youtube_direct",
        "stamp:gemini-frozen",
        "apply:gemini_direct_gemini-frozen",
    ]


def test_file_api_fallback_complete_output_and_call_order_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_runtime(monkeypatch, calls, direct_fails=True)
    result = _run(calls)

    assert _digest(result) == LEGACY_OUTPUT_SHA256["file_api"]
    assert calls == [
        "auth:youtube_subtitles",
        f"subtitles:start:{URL}",
        "auth:youtube_direct_attempt",
        "cache:gemini-frozen",
        "stage:cache_setup",
        "prompts:final_v1:True",
        "generate:youtube_uri_fast_generation:gemini-frozen",
        "attempt:failed",
        "stage:youtube_direct",
        "auth:youtube_download",
        "download",
        "stage:download",
        "auth:file_api_upload",
        "upload",
        "stage:upload",
        "poll:files/frozen",
        "stage:file_active_wait",
        "auth:file_api_attempt",
        "cache:gemini-frozen",
        "stage:cache_setup",
        "prompts:final_v1:True",
        "generate:youtube_file_fallback_generation:gemini-frozen",
        "stage:generation",
        "stamp:gemini-frozen",
        "apply:gemini_fileapi_gemini-frozen",
        "delete:files/frozen",
        "stage:cleanup",
    ]
