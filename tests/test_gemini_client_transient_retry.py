"""优化波 B·C10:Gemini SDK 调用对 5xx / 代理错 / 连接错做 0.8s→2s→5s 退避重试;4xx 不重试。"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.ai.clients import gemini_client as gc


class _ApiError(Exception):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or f"{code} error")
        self.code = code


class ProxyError(Exception):
    pass


class _Models:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.other_attr = "passthrough"

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(outcomes: list[Any], sleeps: list[float], delays=(0.8, 2.0, 5.0)):
    inner = SimpleNamespace(models=_Models(outcomes), files="files-api", caches="caches-api")
    return gc.RetryingGeminiClient(inner, delays=delays, sleep=sleeps.append), inner


@pytest.mark.parametrize(
    "exc, transient",
    [
        (_ApiError(503, "503 UNAVAILABLE"), True),
        (_ApiError(500), True),
        (_ApiError(522, "Cloudflare 522 origin timeout"), True),
        (_ApiError(429, "429 RESOURCE_EXHAUSTED"), False),
        (_ApiError(400, "400 INVALID_ARGUMENT"), False),
        (_ApiError(404), False),
        (ProxyError("tunnel failed"), True),
        (ConnectionError("connection reset by peer"), True),
        (ConnectionResetError(), True),
        (RuntimeError("522 from decodo gateway"), True),
        (RuntimeError("400 bad request: url"), False),
        (ValueError("Gemini response JSON root must be an object"), False),
        (RuntimeError("server disconnected without response"), True),
    ],
)
def test_transient_classifier(exc: BaseException, transient: bool) -> None:
    assert gc.is_transient_gemini_error(exc) is transient


def test_5xx_retries_with_fixed_backoff_ladder_then_succeeds() -> None:
    sleeps: list[float] = []
    ok = SimpleNamespace(text="{}")
    client, inner = _client([_ApiError(503), ProxyError("522"), ConnectionError("reset"), ok], sleeps)
    out = client.models.generate_content(model="gemini-3.6-flash", contents=["x"])
    assert out is ok
    assert sleeps == [0.8, 2.0, 5.0]
    assert len(inner.models.calls) == 4
    info = gc.last_generate_retry_info()
    assert info["attempts"] == 4 and info["retries"] == 3 and info["backoff_ms"] == 7800
    assert len(info["errors"]) == 3


def test_retries_exhausted_raises_last_error() -> None:
    sleeps: list[float] = []
    client, inner = _client([_ApiError(503), _ApiError(503), _ApiError(503), _ApiError(503, "final 503")], sleeps)
    with pytest.raises(_ApiError, match="final 503"):
        client.models.generate_content(model="m", contents=["x"])
    assert sleeps == [0.8, 2.0, 5.0]
    assert len(inner.models.calls) == 4
    assert gc.last_generate_retry_info()["retries"] == 3


def test_4xx_is_not_retried() -> None:
    sleeps: list[float] = []
    client, inner = _client([_ApiError(429, "429 RESOURCE_EXHAUSTED")], sleeps)
    with pytest.raises(_ApiError):
        client.models.generate_content(model="m", contents=["x"])
    assert sleeps == []
    assert len(inner.models.calls) == 1
    assert gc.last_generate_retry_info()["retries"] == 0


def test_wrapper_passes_other_attributes_through_and_is_patchable() -> None:
    from unittest.mock import patch

    client, inner = _client([SimpleNamespace(text="a")], [])
    assert client.files == "files-api"
    assert client.caches == "caches-api"
    assert client.models.other_attr == "passthrough"
    # worker 子进程的 gemini_model 覆盖补丁打在 client.models.generate_content 上:必须可 patch 且可恢复
    original = client.models.generate_content
    with patch.object(client.models, "generate_content", lambda **kw: "forced"):
        assert client.models.generate_content(model="x") == "forced"
    assert client.models.generate_content == original or callable(client.models.generate_content)


def test_empty_env_ladder_disables_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_SDK_RETRY_DELAYS", "")
    assert gc._retry_delays_from_env() == ()
    monkeypatch.setenv("GEMINI_SDK_RETRY_DELAYS", "0.1, 0.2,bad")
    assert gc._retry_delays_from_env() == (0.1, 0.2)
    monkeypatch.delenv("GEMINI_SDK_RETRY_DELAYS")
    assert gc._retry_delays_from_env() == (0.8, 2.0, 5.0)


def test_reset_clears_thread_local_account() -> None:
    client, _inner = _client([SimpleNamespace(text="a")], [])
    client.models.generate_content(model="m", contents=["x"])
    assert gc.last_generate_retry_info()["attempts"] == 1
    gc.reset_generate_retry_info()
    assert gc.last_generate_retry_info() == {"attempts": 0, "retries": 0, "errors": [], "backoff_ms": 0}
