"""
services/ai/clients/gemini_client.py — Google Gemini SDK 初始化 + 瞬态错误指数退避

优化波 B·C10:decodo 代理阶段性 522 / 连接复位会让一次 generateContent 直接炸掉,
分析器随即掉进「换模型 → 再失败 → yt-dlp 下载慢路」。本模块把真 SDK client 包一层
``RetryingGeminiClient``:``client.models.generate_content`` 对 5xx / 代理错 /
ConnectionError 做 0.8s → 2s → 5s 三次退避重试;4xx(含 429,由模型链层处理)不重试。
其余属性(files / caches / aio ...)原样透传,调用点零改动。

重试次数按线程记录(``last_generate_retry_info()``),分析器把它写进
``result["diagnostics"]["retries"]``;平台层 llm_production 看到的仍是「一次」调用
(预算预约只下一次,不会因为重试漏记或多记)。
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable

from app.core.logging import get_logger


logger = get_logger(__name__)

# 退避梯(秒);可用 GEMINI_SDK_RETRY_DELAYS="0.8,2,5" 覆盖,空串=关闭重试。
_DEFAULT_RETRY_DELAYS = (0.8, 2.0, 5.0)


def _retry_delays_from_env() -> tuple[float, ...]:
    raw = os.environ.get("GEMINI_SDK_RETRY_DELAYS")
    if raw is None:
        return _DEFAULT_RETRY_DELAYS
    delays: list[float] = []
    for item in str(raw).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            delays.append(max(0.0, float(item)))
        except ValueError:
            continue
    return tuple(delays)


GEMINI_SDK_RETRY_DELAYS = _retry_delays_from_env()

# Cloudflare 52x(代理侧)+ 标准 5xx 都算瞬态;4xx 一律不重试。
_RETRYABLE_HTTP_STATUS = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 529, 530})
_TRANSIENT_TYPE_NAMES = frozenset(
    {
        "ProxyError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
        "ReadError",
        "WriteError",
        "NetworkError",
        "ConnectionError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "BrokenPipeError",
        "ServerError",
        "ServiceUnavailable",
        "InternalServerError",
        "DeadlineExceeded",
    }
)
_TRANSIENT_TEXT_MARKERS = (
    "proxyerror",
    "proxy error",
    "connection reset",
    "connection aborted",
    "connection error",
    "remote end closed",
    "server disconnected",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "high demand",
)
_STATUS_IN_TEXT_RE = re.compile(r"(?<![0-9])(4[0-9]{2}|5[0-9]{2})(?![0-9])")


def _exception_status_code(exc: BaseException) -> int | None:
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def is_transient_gemini_error(exc: BaseException) -> bool:
    """5xx / Cloudflare 52x / 代理与连接层错误 → True;任何 4xx(含 429)→ False。"""

    status = _exception_status_code(exc)
    if status is not None:
        if 400 <= status < 500:
            return False
        return status in _RETRYABLE_HTTP_STATUS or 500 <= status < 600
    names = {type(exc).__name__, *(base.__name__ for base in type(exc).__mro__)}
    if names & _TRANSIENT_TYPE_NAMES:
        return True
    text = str(exc or "")
    low = text.lower()
    found = _STATUS_IN_TEXT_RE.findall(text)
    if found:
        codes = {int(item) for item in found}
        if any(code in _RETRYABLE_HTTP_STATUS for code in codes):
            return True
        if any(400 <= code < 500 for code in codes):
            return False
    return any(marker in low for marker in _TRANSIENT_TEXT_MARKERS)


_retry_state = threading.local()


def last_generate_retry_info() -> dict[str, Any]:
    """最近一次(本线程)generate_content 的重试账:{attempts, retries, errors, backoff_ms}。"""

    info = getattr(_retry_state, "last", None)
    return dict(info) if isinstance(info, dict) else {"attempts": 0, "retries": 0, "errors": [], "backoff_ms": 0}


def _remember(info: dict[str, Any]) -> None:
    _retry_state.last = info


def reset_generate_retry_info() -> None:
    """调用前清零,避免线程池里上一次调用的账被误读。"""

    _retry_state.last = None


class _RetryingModels:
    """``client.models`` 代理:只包 generate_content,其余属性透传。"""

    def __init__(self, inner: Any, *, delays: tuple[float, ...], sleep: Callable[[float], None] = time.sleep) -> None:
        self._inner = inner
        self._delays = tuple(delays)
        self._sleep = sleep

    def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        info: dict[str, Any] = {"attempts": 0, "retries": 0, "errors": [], "backoff_ms": 0}
        model = str(kwargs.get("model") or "")
        while True:
            info["attempts"] += 1
            try:
                response = self._inner.generate_content(*args, **kwargs)
            except Exception as exc:
                retry_index = info["retries"]
                if retry_index >= len(self._delays) or not is_transient_gemini_error(exc):
                    _remember(info)
                    raise
                delay = float(self._delays[retry_index])
                info["retries"] += 1
                info["errors"].append(f"{type(exc).__name__}: {str(exc)[:160]}")
                info["backoff_ms"] += int(delay * 1000)
                logger.warning(
                    "gemini_sdk_transient_retry",
                    extra={
                        "model": model,
                        "retry": info["retries"],
                        "max_retries": len(self._delays),
                        "delay_seconds": delay,
                        "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                    },
                )
                self._sleep(delay)
                continue
            _remember(info)
            return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class RetryingGeminiClient:
    """真 SDK client 的薄代理;``models.generate_content`` 带瞬态退避,其余透传。"""

    def __init__(
        self,
        inner: Any,
        *,
        delays: tuple[float, ...] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self.models = _RetryingModels(
            inner.models,
            delays=GEMINI_SDK_RETRY_DELAYS if delays is None else delays,
            sleep=sleep,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    _gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if _gemini_key:
        gemini_client = RetryingGeminiClient(google_genai.Client(api_key=_gemini_key))
        GEMINI_AVAILABLE = True
        logger.info("ai.gemini.ready")
    else:
        gemini_client = None
        GEMINI_AVAILABLE = False
        logger.warning("ai.gemini.disabled_missing_key")
except ImportError:
    google_genai = None
    gemini_client = None
    GEMINI_AVAILABLE = False
    logger.warning("ai.gemini.sdk_missing")
