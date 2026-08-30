from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REPO = Path(__file__).resolve().parents[1]
for import_path in (REPO / "backend", REPO / "scripts"):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))


def _load_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


weekly_pulse = _load_script(
    "_vkpi_weekly_pulse_security",
    REPO / "backend" / "scripts" / "weekly_pulse.py",
)
youtube_evidence = _load_script(
    "_vkpi_scrape_youtube_evidence_security",
    REPO / "scripts" / "scrape_youtube_evidence.py",
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _headers(request: urllib.request.Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}


def test_weekly_pulse_apify_token_uses_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "apify-secret-that-must-not-enter-the-url"
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> _FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse({"data": {"items": []}})

    monkeypatch.setenv("APIFY_TOKEN", secret)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    monkeypatch.setattr(weekly_pulse.urllib.request, "urlopen", fake_urlopen)

    assert weekly_pulse.apify_usage_recent(60) == 0.0
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert captured["timeout"] == 30
    assert secret not in request.full_url
    assert "token" not in urllib.parse.parse_qs(
        urllib.parse.urlsplit(request.full_url).query
    )
    assert _headers(request)["authorization"] == f"Bearer {secret}"


def test_weekly_pulse_apify_failure_does_not_log_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "apify-secret-that-must-not-enter-errors"

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
        assert secret not in request.full_url
        raise RuntimeError(f"transport failed for token={secret}")

    monkeypatch.setenv("APIFY_TOKEN", secret)
    monkeypatch.setattr(weekly_pulse.urllib.request, "urlopen", fake_urlopen)

    with caplog.at_level(logging.DEBUG):
        assert weekly_pulse.apify_usage_recent(60) == -1.0

    captured_output = capsys.readouterr()
    assert secret not in caplog.text
    assert secret not in captured_output.out
    assert secret not in captured_output.err


def test_weekly_pulse_refuses_enqueue_only_or_deprecated_refresh_receipt() -> None:
    with pytest.raises(RuntimeError, match="durable worker completion evidence"):
        weekly_pulse.require_completed_refresh({"status": "durable_queue_required"})
    with pytest.raises(RuntimeError, match="status=missing"):
        weekly_pulse.require_completed_refresh({})

    weekly_pulse.require_completed_refresh({"status": "completed"})


def test_youtube_evidence_key_uses_google_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "youtube-secret-that-must-not-enter-the-url"
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> _FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeResponse({"items": []})

    monkeypatch.setattr(youtube_evidence.urllib.request, "urlopen", fake_urlopen)
    api = youtube_evidence.YouTubeApi(secret)

    assert api.get("channels", {"part": "id"}) == {"items": []}
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert captured["timeout"] == 45
    assert secret not in request.full_url
    assert "key" not in urllib.parse.parse_qs(
        urllib.parse.urlsplit(request.full_url).query
    )
    assert _headers(request)["x-goog-api-key"] == secret


def test_youtube_evidence_http_error_and_logs_redact_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "youtube-secret-that-must-not-enter-errors"

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
        assert secret not in request.full_url
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(f'{{"message":"invalid key {secret}"}}'.encode()),
        )

    monkeypatch.setattr(youtube_evidence.urllib.request, "urlopen", fake_urlopen)
    api = youtube_evidence.YouTubeApi(secret)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised:
        api.get("channels", {"part": "id"})

    error_chain = f"{raised.value!r} {raised.value.__cause__!r}"
    assert secret not in error_chain
    assert secret not in caplog.text
    assert "[redacted]" in str(raised.value)


def test_youtube_evidence_transport_error_redacts_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "youtube-secret-in-transport-error"

    def fake_urlopen(request: urllib.request.Request, *, timeout: int) -> Any:
        raise OSError(f"socket failed with x-goog-api-key={secret}")

    monkeypatch.setattr(youtube_evidence.urllib.request, "urlopen", fake_urlopen)
    api = youtube_evidence.YouTubeApi(secret)

    with pytest.raises(RuntimeError) as raised:
        api.get("channels", {"part": "id"})

    assert secret not in str(raised.value)
    assert "[redacted]" in str(raised.value)
