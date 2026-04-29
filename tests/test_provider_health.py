from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any


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

    def test_probe_provider_rejects_empty_key_before_http(self) -> None:
        result = asyncio.run(provider_health.probe_provider("google", api_key=""))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "missing key")
        self.assertEqual(_FakeAsyncClient.calls, [])


if __name__ == "__main__":
    unittest.main()
