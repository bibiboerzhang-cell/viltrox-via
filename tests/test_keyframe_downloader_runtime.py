from __future__ import annotations

import json
from types import SimpleNamespace

from app.workers import apify_jobs_worker_prep as prep
from app.workers.apify_jobs_worker_helpers import _error_category


def test_keyframe_download_uses_current_python_module_without_path_yt_dlp(
    monkeypatch,
    tmp_path,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(prep.sys, "executable", "/runtime/venv/bin/python")
    monkeypatch.setenv("PATH", "/usr/bin")

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        (tmp_path / "youtube_keyframes.mp4").write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(prep.subprocess, "run", fake_run)

    result = prep._download_youtube_for_keyframes(
        "https://www.youtube.com/watch?v=example",
        str(tmp_path),
    )

    assert seen["command"][:3] == [
        "/runtime/venv/bin/python",
        "-m",
        "yt_dlp",
    ]
    assert "yt-dlp" not in seen["command"]
    assert seen["kwargs"] == {"capture_output": True, "text": True, "timeout": 180}
    assert result["success"] is True
    assert result["bytes"] == 5


def test_keyframe_download_redacts_missing_execution_entrypoint(
    monkeypatch,
    tmp_path,
) -> None:
    raw_secret = "https://user:secret@example.invalid/private-python"

    def missing_entrypoint(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", raw_secret)

    monkeypatch.setattr(prep.subprocess, "run", missing_entrypoint)

    result = prep._download_youtube_for_keyframes(
        "https://www.youtube.com/watch?v=example",
        str(tmp_path),
    )

    assert result["success"] is False
    assert result["error"] == "yt_dlp_entrypoint_missing"
    classified = _error_category(f"youtube_keyframe_download_failed: {result['error']}")
    assert classified == "download"
    assert "secret" not in json.dumps(result)


def test_keyframe_download_redacts_other_process_launch_errors(
    monkeypatch,
    tmp_path,
) -> None:
    def denied(*_args, **_kwargs):
        raise PermissionError(13, "denied", "/runtime/venv/bin/python-secret")

    monkeypatch.setattr(prep.subprocess, "run", denied)

    result = prep._download_youtube_for_keyframes(
        "https://www.youtube.com/watch?v=example",
        str(tmp_path),
    )

    assert result["success"] is False
    assert result["error"] == "yt_dlp_execution_failed"
    assert "python-secret" not in json.dumps(result)


def test_keyframe_download_redacts_proxy_credentials_from_tool_error(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        prep.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stderr="proxy=http://user:secret@proxy.example token=abc123",
            stdout="",
        ),
    )

    result = prep._download_youtube_for_keyframes(
        "https://www.youtube.com/watch?v=example",
        str(tmp_path),
    )

    assert result["success"] is False
    assert "secret" not in result["error"] and "abc123" not in result["error"]
    assert "proxy=***" in result["error"]
