"""Provider HTTP adapters for the LLM gateway (extracted, behavior-unchanged).

Holds the per-provider request/response parsing (openai / google / anthropic),
the shared JSON POST helper, and the provider→caller registry. Imports the
shared config/key/cost helpers from llm_gateway to avoid duplication; the main
module re-exports these names so existing call sites keep working.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable

import httpx

from app.platform.llm_gateway import (
    PROVIDER_CONFIG,
    _estimate_cost_cents,
    _estimate_cost_micro_usd,
    _get_api_key,
)


logger = logging.getLogger(__name__)


_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_PID: int | None = None
_HTTP_CLIENT_LOCK = threading.Lock()
_HTTP_LIMITS = httpx.Limits(
    max_connections=32,
    max_keepalive_connections=16,
    keepalive_expiry=30.0,
)
_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[-_]?key|key|token|access_token)=)[^&\s]+"
)
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-goog-request-id",
    "anthropic-request-id",
)


def _redact_provider_error(provider: str, value: Any) -> str:
    """Return a bounded transport detail without credentials or secret URLs."""

    text = str(value or "")
    try:
        secret = _get_api_key(provider)
    except Exception:
        secret = ""
    if secret:
        text = text.replace(secret, "[redacted]")
    return _SECRET_QUERY_RE.sub(r"\1[redacted]", text)[:300]


def _get_http_client() -> httpx.Client:
    """Return one TLS-verifying, env-proxy-aware pool per process.

    ``urllib`` trusted ``HTTP(S)_PROXY``/``NO_PROXY`` and the system CA store by
    default.  ``trust_env=True`` and ``verify=True`` preserve those semantics.
    PID tracking prevents a pre-fork parent pool from being reused by a child.
    """

    global _HTTP_CLIENT, _HTTP_CLIENT_PID
    pid = os.getpid()
    if _HTTP_CLIENT is not None and _HTTP_CLIENT_PID == pid:
        return _HTTP_CLIENT
    with _HTTP_CLIENT_LOCK:
        if _HTTP_CLIENT is not None and _HTTP_CLIENT_PID == pid:
            return _HTTP_CLIENT
        if _HTTP_CLIENT is not None:
            try:
                _HTTP_CLIENT.close()
            except Exception:
                logger.warning("llm provider inherited HTTP pool close failed", exc_info=True)
        _HTTP_CLIENT = httpx.Client(
            trust_env=True,
            verify=True,
            follow_redirects=True,
            limits=_HTTP_LIMITS,
            headers={
                "Accept": "application/json",
                "User-Agent": "ViltroxMarketing/1.0",
            },
        )
        _HTTP_CLIENT_PID = pid
        return _HTTP_CLIENT


def _close_http_client() -> None:
    """Close the process-local pool; used by lifecycle hooks and offline tests."""

    global _HTTP_CLIENT, _HTTP_CLIENT_PID
    with _HTTP_CLIENT_LOCK:
        client = _HTTP_CLIENT
        _HTTP_CLIENT = None
        _HTTP_CLIENT_PID = None
    if client is not None:
        try:
            client.close()
        except Exception:
            logger.warning("llm provider HTTP pool close failed", exc_info=True)


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    response = _get_http_client().post(
        url,
        content=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        timeout=httpx.Timeout(float(timeout)),
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("provider response root must be an object")
    return body


def _provider_failure(provider: str, exc: Exception, *, started: float) -> dict[str, Any]:
    """Map failures without exposing response bodies, prompts, URLs, or keys."""

    status = "provider_exception"
    error = type(exc).__name__
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = int(exc.response.status_code)
        status = (
            "provider_429"
            if status_code == 429
            else "provider_5xx"
            if status_code >= 500
            else "provider_http_error"
        )
        request_id = next(
            (
                str(exc.response.headers.get(name) or "").strip()
                for name in _REQUEST_ID_HEADERS
                if _SAFE_REQUEST_ID_RE.fullmatch(
                    str(exc.response.headers.get(name) or "").strip()
                )
            ),
            "",
        )
        error = f"http_{status_code}"
        if request_id:
            error += f" request_id={request_id}"
    elif isinstance(exc, httpx.TimeoutException):
        status = "timeout"
    elif isinstance(exc, httpx.RequestError):
        status = "transport_error"
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        status = "invalid_response"
        error = _redact_provider_error(provider, exc)
    return {
        "status": status,
        "provider": provider,
        "error": error,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


atexit.register(_close_http_client)


def _call_openai(
    prompt: str,
    max_output_tokens: int,
    *,
    model_override: str | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key("openai")
    if not api_key:
        return {"status": "not_configured", "error": "missing OPENAI_API_KEY", "provider": "openai"}
    config = PROVIDER_CONFIG["openai"]
    model = str(model_override or config["model"]).strip()
    started = time.monotonic()
    try:
        request_payload: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max(1, min(4000, int(max_output_tokens or 800))),
        }
        # Reasoning-capable GPT-5 revisions do not share one sampling-parameter
        # contract.  In particular, account-visible gpt-5.5 rejects temperature
        # on the Responses endpoint.  Omitting the optional knob preserves the
        # provider default and keeps one transport valid across exact GPT-5
        # bindings; legacy/non-reasoning families retain the historical value.
        if not model.lower().startswith("gpt-5"):
            request_payload["temperature"] = 0.2
        body = _request_json(
            str(config["endpoint"]),
            request_payload,
            {"Authorization": f"Bearer {api_key}"},
            int(config["timeout"]),
        )
        output = body.get("output") or []
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    text_parts.append(str(part.get("text") or ""))
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return {
            "status": "success",
            "provider": "openai",
            "model": str(body.get("model") or model),
            "text": (str(body.get("output_text") or "") or "".join(text_parts)).strip(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents(
                "openai", input_tokens, output_tokens, model_id=model
            ),
            "cost_micro_usd": _estimate_cost_micro_usd(
                "openai", input_tokens, output_tokens, model_id=model
            ),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return _provider_failure("openai", exc, started=started)


def _call_google(
    prompt: str,
    max_output_tokens: int,
    *,
    model_override: str | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key("google")
    if not api_key:
        return {"status": "not_configured", "error": "missing GEMINI_API_KEY/GOOGLE_API_KEY", "provider": "google"}
    config = PROVIDER_CONFIG["google"]
    model = str(model_override or config["model"]).strip()
    model_key = model.lower()
    # Google exposes two incompatible controls across the currently reviewed
    # exact-model catalog.  Gemini 2.5 Pro rejects thinkingBudget=0 with
    # HTTP 400 (its documented minimum is 128), while Gemini 3 models use
    # thinkingLevel.  Keep Flash 2.5 on the historical zero-thinking path used
    # by the low-cost structured extractors.
    thinking_config: dict[str, Any]
    if model_key.startswith("gemini-3"):
        thinking_config = {"thinkingLevel": "minimal"}
    elif model_key.startswith("gemini-2.5-pro"):
        thinking_config = {"thinkingBudget": 128}
    else:
        thinking_config = {"thinkingBudget": 0}
    started = time.monotonic()
    try:
        # Google documents ``x-goog-api-key`` as the REST authentication
        # header.  Keeping the credential out of the query string also keeps it
        # out of httpx access logs, reverse-proxy logs and exception URLs.
        endpoint = str(config["endpoint"]).format(model=model)
        body = _request_json(
            endpoint,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max(1, min(4000, int(max_output_tokens or 800))),
                    "temperature": 0.2,
                    "thinkingConfig": thinking_config,
                },
            },
            {"x-goog-api-key": api_key},
            int(config["timeout"]),
        )
        candidates = body.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        usage = body.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        visible_output_tokens = int(usage.get("candidatesTokenCount") or 0)
        thinking_tokens = int(usage.get("thoughtsTokenCount") or 0)
        # Gemini bills generated thinking tokens in addition to visible
        # candidate tokens.  Keep ``output_tokens`` as the billable total so
        # the shared reservation/ledger path never understates Pro/3.x spend;
        # expose the split as additive telemetry for later reconciliation.
        output_tokens = visible_output_tokens + thinking_tokens
        return {
            "status": "success",
            "provider": "google",
            "model": model,
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "visible_output_tokens": visible_output_tokens,
            "thinking_tokens": thinking_tokens,
            "cost_cents": _estimate_cost_cents(
                "google", input_tokens, output_tokens, model_id=model
            ),
            "cost_micro_usd": _estimate_cost_micro_usd(
                "google", input_tokens, output_tokens, model_id=model
            ),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return _provider_failure("google", exc, started=started)


def _call_anthropic(
    prompt: str,
    max_output_tokens: int,
    *,
    model_override: str | None = None,
) -> dict[str, Any]:
    api_key = _get_api_key("anthropic")
    if not api_key:
        return {"status": "not_configured", "error": "missing ANTHROPIC_API_KEY", "provider": "anthropic"}
    config = PROVIDER_CONFIG["anthropic"]
    model = str(model_override or config["model"]).strip()
    started = time.monotonic()
    try:
        body = _request_json(
            str(config["endpoint"]),
            {"model": model, "max_tokens": max(1, min(4000, int(max_output_tokens or 800))), "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            int(config["timeout"]),
        )
        content = body.get("content") or []
        text = "".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text").strip()
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        return {
            "status": "success",
            "provider": "anthropic",
            "model": str(body.get("model") or model),
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents(
                "anthropic", input_tokens, output_tokens, model_id=model
            ),
            "cost_micro_usd": _estimate_cost_micro_usd(
                "anthropic", input_tokens, output_tokens, model_id=model
            ),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return _provider_failure("anthropic", exc, started=started)


_PROVIDER_CALLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "openai": _call_openai,
    "google": _call_google,
    "anthropic": _call_anthropic,
}
