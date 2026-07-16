from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.system import provider_health  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    calls: list[dict[str, Any]] = []

    def __init__(self, status_code: int = 200, *args: Any, **kwargs: Any) -> None:
        self.status_code = status_code

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, "kwargs": kwargs})
        return _FakeResponse(self.status_code)


class _RaisingAsyncClient(_FakeAsyncClient):
    def __init__(self, secret: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.secret = secret

    async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        raise RuntimeError(
            "probe exploded "
            f"https://example.invalid/models?key={self.secret}&page=1 "
            f"Authorization: Bearer {self.secret}"
        )


class _FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _FakeProviderStatusConn:
    def __init__(self) -> None:
        self.write_params: tuple[Any, ...] | None = None
        self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        if sql.lstrip().startswith("SELECT"):
            return _FakeCursor({"consecutive_failures": 0, "alert_sent_at": None})
        self.write_params = params
        return _FakeCursor()

    def commit(self) -> None:
        self.committed = True


class ProviderHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeAsyncClient.calls = []

    def test_probe_provider_calls_real_openai_models_endpoint(self) -> None:
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(200)  # type: ignore[assignment]
            result = asyncio.run(provider_health.probe_provider("openai", api_key="sk-test-valid"))
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(_FakeAsyncClient.calls[0]["url"], "https://api.openai.com/v1/models")
        self.assertIn("Authorization", _FakeAsyncClient.calls[0]["kwargs"]["headers"])

    def test_probe_provider_rejects_invalid_credentials(self) -> None:
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(401)  # type: ignore[assignment]
            result = asyncio.run(provider_health.probe_provider("resend", api_key="bad-key"))
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid credentials")

    def test_google_probe_keeps_key_out_of_url_and_query_params(self) -> None:
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(200)  # type: ignore[assignment]
            result = asyncio.run(
                provider_health.probe_provider("google", api_key="google-test-key")
            )
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertTrue(result["ok"])
        call = _FakeAsyncClient.calls[0]
        self.assertNotIn("google-test-key", call["url"])
        self.assertNotIn("params", call["kwargs"])
        self.assertEqual(
            call["kwargs"]["headers"], {"x-goog-api-key": "google-test-key"}
        )

    def test_youtube_probe_keeps_key_out_of_url_and_query_params(self) -> None:
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(200)  # type: ignore[assignment]
            result = asyncio.run(
                provider_health.probe_provider("youtube", api_key="youtube-test-key")
            )
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertTrue(result["ok"])
        call = _FakeAsyncClient.calls[0]
        self.assertNotIn("youtube-test-key", call["url"])
        self.assertNotIn("key", call["kwargs"]["params"])
        self.assertEqual(
            call["kwargs"]["headers"], {"x-goog-api-key": "youtube-test-key"}
        )

    def test_apify_probe_uses_bearer_header_not_url_token(self) -> None:
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = lambda *args, **kwargs: _FakeAsyncClient(200)  # type: ignore[assignment]
            result = asyncio.run(
                provider_health.probe_provider("apify", api_key="apify-test-token")
            )
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertTrue(result["ok"])
        call = _FakeAsyncClient.calls[0]
        self.assertNotIn("apify-test-token", call["url"])
        self.assertNotIn("params", call["kwargs"])
        self.assertEqual(
            call["kwargs"]["headers"], {"Authorization": "Bearer apify-test-token"}
        )

    def test_probe_error_redaction_removes_exact_and_query_secrets(self) -> None:
        secret = "probe-test-secret"
        value = (
            f"request failed https://example.invalid/?key={secret}&x=1 "
            f"x-goog-api-key: {secret} Authorization: Bearer {secret}"
        )
        cleaned = provider_health._redact_probe_error(value, secret)
        self.assertNotIn(secret, cleaned)
        self.assertIn("key=[redacted]", cleaned)
        self.assertIn("Bearer [redacted]", cleaned)

    def test_probe_runtime_error_log_never_contains_provider_secret(self) -> None:
        secret = "google-secret-that-must-not-be-logged"
        original = provider_health.httpx.AsyncClient
        try:
            provider_health.httpx.AsyncClient = (  # type: ignore[assignment]
                lambda *args, **kwargs: _RaisingAsyncClient(secret)
            )
            with self.assertLogs(provider_health.logger, level="WARNING") as captured:
                result = asyncio.run(
                    provider_health.probe_provider("google", api_key=secret)
                )
        finally:
            provider_health.httpx.AsyncClient = original  # type: ignore[assignment]
        log_text = "\n".join(
            f"{record.getMessage()} {getattr(record, 'error', '')}"
            for record in captured.records
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn(secret, log_text)
        self.assertIn("[redacted]", log_text)

    def test_record_provider_probe_redacts_error_before_database_write(self) -> None:
        secret = "db-error-secret"
        conn = _FakeProviderStatusConn()
        with (
            mock.patch.object(provider_health, "ensure_provider_status_schema"),
            mock.patch.object(provider_health, "get_conn", return_value=conn),
        ):
            provider_health.record_provider_probe(
                "google", False, f"request failed?access_token={secret}"
            )
        self.assertTrue(conn.committed)
        self.assertIsNotNone(conn.write_params)
        stored_error = str(conn.write_params[3])  # type: ignore[index]
        self.assertNotIn(secret, stored_error)
        self.assertIn("access_token=[redacted]", stored_error)

    def test_down_alert_redacts_and_html_escapes_error(self) -> None:
        secret = "alert-error-secret"
        with (
            mock.patch.object(provider_health, "should_send_down_alert", return_value=True),
            mock.patch.object(provider_health, "send_email") as send_email,
            mock.patch.object(provider_health, "mark_alert_sent"),
        ):
            provider_health._send_down_alert(
                "google", f"<script>?token={secret}</script>"
            )
        body = str(send_email.call_args.args[2])
        self.assertNotIn(secret, body)
        self.assertNotIn("<script>", body)
        self.assertIn("token=[redacted]", body)

    def test_probe_provider_rejects_empty_key_before_http(self) -> None:
        result = asyncio.run(provider_health.probe_provider("google", api_key=""))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing key")
        self.assertEqual(_FakeAsyncClient.calls, [])


if __name__ == "__main__":
    unittest.main()
