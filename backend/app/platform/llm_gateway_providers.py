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


class ProviderConfigUnsupported(ValueError):
    """The exact model cannot be driven with a request shape this transport emits.

    Raised before any network I/O (e.g. gemini-3.7*/…-latest have no
    ``thinkingLevel=minimal``); mapped to ``provider_config_unsupported`` so the
    candidate / fallback chain advances instead of burning a 400 per call.
    """


# ---- per-provider request policies ------------------------------------------
# OpenAI Responses API: ``reasoning.effort`` is injected only for EXACT model
# ids listed here (never by prefix — gpt-5.6 / gpt-5.5 keep provider default).
# gpt-5.6-luna must run with effort='none' (目录实测:不带也能跑但更慢)。
_OPENAI_REASONING_EFFORT: dict[str, str] = {"gpt-5.6-luna": "none"}
_OPENAI_REASONING_EFFORT_ENV = "VKPI_OPENAI_REASONING_EFFORT_JSON"

# Anthropic: thinking defaults to disabled (成本中性,沿用今日行为);
# VKPI_ANTHROPIC_THINKING=adaptive 切自适应思考,可配 VKPI_ANTHROPIC_EFFORT
# (low|medium|high|xhigh|max → output_config.effort)与 VKPI_ANTHROPIC_MAX_TOKENS
# (adaptive 时抬高 4000 上限,思考 token 也吃 max_tokens)。
# 永不发 temperature/top_p/top_k/budget_tokens(Sonnet 5/Opus 5 一律 400)。
_ANTHROPIC_THINKING_POLICY: dict[str, Any] = {"default": "disabled"}
_ANTHROPIC_THINKING_MODES = frozenset({"disabled", "adaptive"})
_ANTHROPIC_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4000


def _openai_reasoning_effort(model: str) -> str | None:
    """Return the reasoning effort for an exact OpenAI model id, or None."""

    table: dict[str, str] = {
        str(key).strip().lower(): str(value).strip().lower()
        for key, value in _OPENAI_REASONING_EFFORT.items()
    }
    raw = str(os.environ.get(_OPENAI_REASONING_EFFORT_ENV) or "").strip()
    if raw:
        try:
            override = json.loads(raw)
        except ValueError:
            override = None
        if isinstance(override, dict):
            for key, value in override.items():
                table[str(key).strip().lower()] = str(value or "").strip().lower()
        else:
            logger.warning(
                "llm provider ignored malformed %s (expected JSON object)",
                _OPENAI_REASONING_EFFORT_ENV,
            )
    effort = table.get(str(model or "").strip().lower(), "")
    return effort or None


def _anthropic_thinking_policy() -> dict[str, Any]:
    """Resolve the Anthropic thinking/effort/max_tokens policy from env."""

    mode = str(
        os.environ.get("VKPI_ANTHROPIC_THINKING")
        or _ANTHROPIC_THINKING_POLICY["default"]
    ).strip().lower()
    if mode not in _ANTHROPIC_THINKING_MODES:
        logger.warning("llm provider ignored unknown VKPI_ANTHROPIC_THINKING=%s", mode)
        mode = str(_ANTHROPIC_THINKING_POLICY["default"])
    effort = str(os.environ.get("VKPI_ANTHROPIC_EFFORT") or "").strip().lower()
    if effort and effort not in _ANTHROPIC_EFFORT_LEVELS:
        logger.warning("llm provider ignored unknown VKPI_ANTHROPIC_EFFORT=%s", effort)
        effort = ""
    max_tokens_cap = _ANTHROPIC_DEFAULT_MAX_TOKENS
    if mode == "adaptive":
        try:
            max_tokens_cap = max(
                _ANTHROPIC_DEFAULT_MAX_TOKENS,
                int(os.environ.get("VKPI_ANTHROPIC_MAX_TOKENS") or 0),
            )
        except ValueError:
            max_tokens_cap = _ANTHROPIC_DEFAULT_MAX_TOKENS
    return {"mode": mode, "effort": effort, "max_tokens_cap": max_tokens_cap}


def _anthropic_request_body(
    model: str, max_output_tokens: int, prompt: str
) -> dict[str, Any]:
    policy = _anthropic_thinking_policy()
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max(
            1, min(int(policy["max_tokens_cap"]), int(max_output_tokens or 800))
        ),
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": str(policy["mode"])},
    }
    if policy["mode"] == "adaptive" and policy["effort"]:
        body["output_config"] = {"effort": str(policy["effort"])}
    return body


def _google_thinking_config(model_key: str) -> dict[str, Any] | None:
    """Explicit per-family Gemini thinking control (实测矩阵 2026-08-22).

    - gemini-3.x 非 pro:``thinkingLevel=minimal``(``thinkingBudget=0`` 会 400);
    - gemini-3.x pro:不注入(证据不足,保留 provider 默认);
    - gemini-3.7* / *-latest:无 minimal 档,默认每次烧思考 token → 直接拒绝;
    - gemini-2.5-pro:``thinkingBudget=128``(不允许 0);其余 2.5:``thinkingBudget=0``;
    - 其他 id:不注入。
    """

    key = str(model_key or "").strip().lower()
    if key.startswith("gemini-3.7") or key.endswith("-latest"):
        raise ProviderConfigUnsupported(f"no_minimal_thinking_level:{key}")
    if key.startswith("gemini-3"):
        return None if "pro" in key else {"thinkingLevel": "minimal"}
    if key.startswith("gemini-2.5-pro"):
        return {"thinkingBudget": 128}
    if key.startswith("gemini-2.5"):
        return {"thinkingBudget": 0}
    return None


def _provider_failure(provider: str, exc: Exception, *, started: float) -> dict[str, Any]:
    """Map failures without exposing response bodies, prompts, URLs, or keys."""

    status = "provider_exception"
    error = type(exc).__name__
    if isinstance(exc, ProviderConfigUnsupported):
        status = "provider_config_unsupported"
        error = _redact_provider_error(provider, exc)
    elif isinstance(exc, httpx.HTTPStatusError):
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
        reasoning_effort = _openai_reasoning_effort(model)
        if reasoning_effort:
            request_payload["reasoning"] = {"effort": reasoning_effort}
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
    started = time.monotonic()
    try:
        # Google exposes two incompatible thinking controls across the
        # reviewed catalog (thinkingBudget for 2.5, thinkingLevel for 3.x);
        # the mapping lives in _google_thinking_config.  Sampling knobs
        # (temperature/top_p/top_k) are deprecated on Gemini 3.x and are never
        # sent: generationConfig carries only maxOutputTokens (+thinkingConfig).
        thinking_config = _google_thinking_config(model_key)
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max(1, min(4000, int(max_output_tokens or 800))),
        }
        if thinking_config is not None:
            generation_config["thinkingConfig"] = thinking_config
        # Google documents ``x-goog-api-key`` as the REST authentication
        # header.  Keeping the credential out of the query string also keeps it
        # out of httpx access logs, reverse-proxy logs and exception URLs.
        endpoint = str(config["endpoint"]).format(model=model)
        body = _request_json(
            endpoint,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
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
            _anthropic_request_body(model, max_output_tokens, prompt),
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            int(config["timeout"]),
        )
        content = body.get("content") or []
        text = "".join(str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text").strip()
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        stop_reason = str(body.get("stop_reason") or "").strip().lower()
        result: dict[str, Any] = {
            "status": "success",
            "provider": "anthropic",
            "model": str(body.get("model") or model),
            "text": text,
            "stop_reason": stop_reason,
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
        # A refusal (or an empty body that did not end naturally) is a provider
        # failure, not a successful empty answer: keep the consumed usage for
        # the ledger but let the provider/model fallback chain engage.
        if stop_reason == "refusal" or (not text and stop_reason not in {"", "end_turn"}):
            result["status"] = "provider_error"
            result["error"] = (
                "anthropic_refusal" if stop_reason == "refusal" else f"anthropic_empty_{stop_reason}"
            )
        elif stop_reason == "max_tokens":
            result["truncated"] = True
        return result
    except Exception as exc:
        return _provider_failure("anthropic", exc, started=started)


_PROVIDER_CALLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "openai": _call_openai,
    "google": _call_google,
    "anthropic": _call_anthropic,
}
