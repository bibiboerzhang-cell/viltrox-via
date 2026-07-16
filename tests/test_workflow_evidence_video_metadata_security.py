from __future__ import annotations

import io
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domains.projects import workflow_evidence_video_metadata as video_metadata  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_youtube_metadata_sends_key_in_header_not_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "youtube-secret-that-must-not-enter-the-url"
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> _FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse(
            {
                "items": [
                    {
                        "snippet": {"title": "Offline fixture"},
                        "statistics": {"viewCount": "12"},
                        "contentDetails": {"duration": "PT1M"},
                    }
                ]
            }
        )

    monkeypatch.setenv("YOUTUBE_API_KEY", secret)
    monkeypatch.setattr(video_metadata.urllib.request, "urlopen", fake_urlopen)

    result = video_metadata._youtube_api_metadata(
        "https://www.youtube.com/watch?v=abc123"
    )

    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert captured["timeout"] == 25
    assert secret not in request.full_url
    assert "key" not in urllib.parse.parse_qs(
        urllib.parse.urlsplit(request.full_url).query
    )
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-goog-api-key"] == secret
    assert result["scrape_source"] == "youtube_api"
    assert result["view_count"] == 12


def test_youtube_metadata_http_error_and_logs_do_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "youtube-secret-that-must-not-enter-errors"

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
        assert secret not in request.full_url
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(f'{{"message":"rejected key {secret}"}}'.encode()),
        )

    monkeypatch.setenv("YOUTUBE_API_KEY", secret)
    monkeypatch.setattr(video_metadata.urllib.request, "urlopen", fake_urlopen)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised:
        video_metadata._youtube_api_metadata(
            "https://youtu.be/abc123"
        )

    error_chain = f"{raised.value!r} {raised.value.__cause__!r}"
    assert secret not in error_chain
    assert secret not in caplog.text
    assert "[redacted]" in str(raised.value)
