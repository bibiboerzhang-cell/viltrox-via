"""No-network tests for the shared HTTPX LLM transport."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

import app.platform.llm_gateway  # noqa: F401 - initialise the circular facade first
from app.platform import llm_gateway_providers as providers


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse({"ok": True})

    def close(self) -> None:
        self.closed = True


def test_request_json_reuses_one_process_pool_and_preserves_transport_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []
    client = _FakeClient()

    def factory(**kwargs: Any) -> _FakeClient:
        created.append(kwargs)
        return client

    monkeypatch.setattr(providers, "_HTTP_CLIENT", None)
    monkeypatch.setattr(providers, "_HTTP_CLIENT_PID", None)
    monkeypatch.setattr(providers.httpx, "Client", factory)

    first = providers._request_json(
        "https://provider.invalid/v1",
        {"prompt": "one"},
        {"Authorization": "Bearer test"},
        17,
    )
    second = providers._request_json(
        "https://provider.invalid/v1",
        {"prompt": "two"},
        {},
        17,
    )

    assert first == {"ok": True} and second == {"ok": True}
    assert len(created) == 1
    assert len(client.posts) == 2
    assert created[0]["trust_env"] is True
    assert created[0]["verify"] is True
    assert created[0]["follow_redirects"] is True
    assert isinstance(created[0]["limits"], httpx.Limits)
    assert client.posts[0]["headers"]["Content-Type"] == "application/json"
    timeout = client.posts[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 17.0
    assert timeout.read == 17.0


def test_pool_is_rebuilt_after_process_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited = _FakeClient()
    replacement = _FakeClient()
    monkeypatch.setattr(providers, "_HTTP_CLIENT", inherited)
    monkeypatch.setattr(providers, "_HTTP_CLIENT_PID", 111)
    monkeypatch.setattr(providers.os, "getpid", lambda: 222)
    monkeypatch.setattr(providers.httpx, "Client", lambda **_kwargs: replacement)

    selected = providers._get_http_client()

    assert selected is replacement
    assert inherited.closed is True
    assert providers._HTTP_CLIENT_PID == 222


def test_transport_timeout_is_structured_and_does_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        providers,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            httpx.ReadTimeout("timed out", request=request)
        ),
    )

    result = providers._call_google("hello", 20, model_override="gemini-2.5-flash")

    assert result["status"] == "timeout"
    assert result["provider"] == "google"
    assert result["error"] == "ReadTimeout"
    assert "test-key" not in str(result)


def test_google_api_key_is_sent_in_header_not_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")

    def request(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int):
        captured.update(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        return {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }

    monkeypatch.setattr(providers, "_request_json", request)

    result = providers._call_google(
        "hello", 20, model_override="gemini-2.5-flash"
    )

    assert result["status"] == "success"
    assert "test-key" not in captured["url"]
    assert "?key=" not in captured["url"]
    assert captured["headers"] == {"x-goog-api-key": "test-key"}


def test_provider_5xx_is_structured_without_returning_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(
        503,
        request=request,
        text="sensitive provider response body",
        headers={"x-request-id": "req_safe-123"},
    )
    error = httpx.HTTPStatusError(
        "upstream unavailable",
        request=request,
        response=response,
    )
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(
        providers,
        "_request_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    result = providers._call_openai("hello", 20, model_override="gpt-5.6")

    assert result["status"] == "provider_5xx"
    assert result["error"] == "http_503 request_id=req_safe-123"
    assert "sensitive provider response body" not in str(result)


def test_provider_http_error_never_returns_body_or_query_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-provider-secret"
    request = httpx.Request(
        "POST", f"https://provider.invalid/v1?key={secret}&mode=test"
    )
    response = httpx.Response(
        503,
        request=request,
        text=f"upstream echoed ?key={secret}&mode=test",
    )
    error = httpx.HTTPStatusError(
        f"failed {request.url}", request=request, response=response
    )
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: secret)

    result = providers._provider_failure("google", error, started=0.0)

    assert secret not in str(result)
    assert result["error"] == "http_503"
    assert "upstream echoed" not in str(result)


def test_transport_detail_redactor_still_removes_exact_key_and_query_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "provider-key-must-not-leak"
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: secret)

    cleaned = providers._redact_provider_error(
        "google",
        f"failed https://provider.invalid/v1?key={secret}&mode=test",
    )

    assert secret not in cleaned
    assert "key=[redacted]" in cleaned


def test_provider_http_error_body_cannot_echo_business_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_prompt = "CONFIDENTIAL_KOL_STRATEGY_DO_NOT_LOG"
    request = httpx.Request("POST", "https://provider.invalid/v1")
    response = httpx.Response(
        400,
        request=request,
        text=f'{{"error":{{"message":"{sensitive_prompt}","code":"unsafe"}}}}',
        headers={
            "x-request-id": "req_visible_456",
            # Invalid request identifiers are ignored rather than reflected.
            "request-id": f"bad {sensitive_prompt}",
        },
    )
    error = httpx.HTTPStatusError("provider rejected request", request=request, response=response)
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")

    result = providers._provider_failure("openai", error, started=0.0)

    assert result["status"] == "provider_http_error"
    assert result["error"] == "http_400 request_id=req_visible_456"
    assert sensitive_prompt not in str(result)
