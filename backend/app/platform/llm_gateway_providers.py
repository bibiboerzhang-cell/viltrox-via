"""Provider HTTP adapters for the LLM gateway (extracted, behavior-unchanged).

Holds the per-provider request/response parsing (openai / google / anthropic),
the shared JSON POST helper, and the provider→caller registry. The shared
config/key helpers now live in the ``llm_gateway_common`` leaf (2026-08-30 拆
import 期真活环:本模块不再反向 import llm_gateway);the cost estimators are
defined here (their only real call cluster). ``llm_gateway`` re-exports every
one of these names so existing call sites keep working.
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

from app.platform.llm_gateway_common import (
    PROVIDER_CONFIG,
    _get_api_key,
    _micro_usd_to_cents,
)
from app.platform.llm_gateway_model_alias import resolve_model_alias as _resolve_model_alias
from app.platform.models.runtime import ResolvedModelBinding, resolve_model_binding
from app.platform.llm_gateway_invoke_limits import GatewayDeadlineExceeded, bounded_http_timeout


logger = logging.getLogger(__name__)


# 精度修复:旧 _estimate_cost_cents 用整除 // 1_000_000 算 cents,任意 token 量都被截断归零
# (如 google 1000 input tok x 7 cents/M = 7000//1_000_000 = 0),cost_cents 恒 0、月度预算闸
# SUM 永读 $0 失效。新口径以「微美元」(micro_usd,$1 = 1_000_000 micro_usd)为精度源:
#   micro_usd = tokens x cents_per_million / 100   (1 cent = 10_000 micro_usd,故 /1_000_000 x 10_000)
# 用浮点累计再 round 成整数 micro_usd,避免整除归零;cost_cents 从 micro_usd 四舍五入派生
# (真实亚分调用仍可诚实落 0,但精度由 cost_micro_usd 保住,不再把成本钉死在 0)。
def _resolve_gateway_binding(provider: str, model_id: str = "") -> ResolvedModelBinding:
    """Resolve a model through the shared contract, retaining legacy defaults."""
    config = PROVIDER_CONFIG.get(str(provider or "").strip().lower()) or {}
    resolved_model = str(model_id or config.get("model") or "").strip()
    # 显式 model_override / model_fallbacks 里的 *-latest 别名同样映射成精确名,
    # 就绪闸、定价、响应模型比对与台账全部按精确名走。
    resolved_model = _resolve_model_alias(provider, resolved_model)
    binding = resolve_model_binding(provider, resolved_model, gateway_config=config)
    if binding.pricing_known or resolved_model != str(config.get("model") or "").strip():
        return binding
    # Some legacy configured defaults are selectable in the broad core registry
    # but absent from the priced router registry. Keep the existing configured
    # default usable (only after the runtime hard gate) with its explicit gateway
    # rates; exact overrides still require model-specific catalog pricing.
    input_rate = config.get("input_cents_per_million")
    output_rate = config.get("output_cents_per_million")
    if input_rate is None or output_rate is None:
        return binding
    return ResolvedModelBinding(
        provider=binding.provider,
        model_id=binding.model_id,
        model_key=binding.model_key,
        endpoint_family=binding.endpoint_family,
        input_cents_per_million=float(input_rate),
        output_cents_per_million=float(output_rate),
        transport_ready=binding.transport_ready,
        registered=binding.registered,
        runtime_availability=binding.runtime_availability,
        runtime_evidence_source=binding.runtime_evidence_source,
        registry_source=f"{binding.registry_source}+gateway_default_pricing",
        pricing_version="legacy_provider_default",
    )


def _estimate_cost_micro_usd(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    *,
    model_id: str | None = None,
    binding: ResolvedModelBinding | None = None,
) -> int:
    resolved = binding or _resolve_gateway_binding(provider, str(model_id or ""))
    # Low-level adapter calls made outside invoke() retain the old provider-rate
    # fallback. Strict exact-model gateway calls are rejected before this point
    # when their catalog price is unknown.
    config = PROVIDER_CONFIG.get(provider) or {}
    in_rate = (
        float(resolved.input_cents_per_million)
        if resolved.input_cents_per_million is not None
        else float(config.get("input_cents_per_million") or 0)
    )
    out_rate = (
        float(resolved.output_cents_per_million)
        if resolved.output_cents_per_million is not None
        else float(config.get("output_cents_per_million") or 0)
    )
    micro = (int(input_tokens or 0) * in_rate + int(output_tokens or 0) * out_rate) / 100.0
    return int(round(micro))


def _estimate_cost_cents(
    provider: str,
    input_tokens: int,
    output_tokens: int,
    *,
    model_id: str | None = None,
    binding: ResolvedModelBinding | None = None,
) -> int:
    # 向后兼容:返回整数 cents,但改由精度 micro_usd 派生(四舍五入),不再用整除直接归零。
    return _micro_usd_to_cents(
        _estimate_cost_micro_usd(
            provider,
            input_tokens,
            output_tokens,
            model_id=model_id,
            binding=binding,
        )
    )


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
    bounded_http_timeout(timeout)
    client = _get_http_client()
    content = json.dumps(payload).encode("utf-8")
    response = client.post(
        url,
        content=content,
        headers={**headers, "Content-Type": "application/json"},
        timeout=httpx.Timeout(bounded_http_timeout(timeout)),
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
    if isinstance(exc, GatewayDeadlineExceeded):
        return {
            "status": "deadline_exceeded", "provider": provider,
            "error": "gateway deadline exceeded", "provider_io_started": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
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
