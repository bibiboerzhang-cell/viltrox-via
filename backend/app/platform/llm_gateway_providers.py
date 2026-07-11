"""Provider HTTP adapters for the LLM gateway (extracted, behavior-unchanged).

Holds the per-provider request/response parsing (openai / google / anthropic),
the shared JSON POST helper, and the provider→caller registry. Imports the
shared config/key/cost helpers from llm_gateway to avoid duplication; the main
module re-exports these names so existing call sites keep working.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from app.platform.llm_gateway import (
    PROVIDER_CONFIG,
    _estimate_cost_cents,
    _estimate_cost_micro_usd,
    _get_api_key,
)


def _request_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={**headers, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - trusted provider endpoints
        return json.loads(response.read().decode("utf-8") or "{}")


def _call_openai(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("openai")
    if not api_key:
        return {"status": "not_configured", "error": "missing OPENAI_API_KEY", "provider": "openai"}
    config = PROVIDER_CONFIG["openai"]
    started = time.monotonic()
    try:
        body = _request_json(
            str(config["endpoint"]),
            {
                "model": config["model"],
                "input": prompt,
                "max_output_tokens": max(1, min(4000, int(max_output_tokens or 800))),
                "temperature": 0.2,
            },
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
            "model": str(body.get("model") or config["model"]),
            "text": (str(body.get("output_text") or "") or "".join(text_parts)).strip(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("openai", input_tokens, output_tokens),
            "cost_micro_usd": _estimate_cost_micro_usd("openai", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "openai", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "openai", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


def _call_google(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("google")
    if not api_key:
        return {"status": "not_configured", "error": "missing GEMINI_API_KEY/GOOGLE_API_KEY", "provider": "google"}
    config = PROVIDER_CONFIG["google"]
    started = time.monotonic()
    try:
        endpoint = str(config["endpoint"]).format(model=config["model"]) + f"?key={api_key}"
        body = _request_json(
            endpoint,
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max(1, min(4000, int(max_output_tokens or 800))),
                    "temperature": 0.2,
                    # gemini-2.5 系默认动态思考,思考 token 计入 maxOutputTokens——
                    # 本网关全是低成本结构化抽取,思考只会烧光预算导致正文截断,关死。
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
            {},
            int(config["timeout"]),
        )
        candidates = body.get("candidates") or []
        parts = (((candidates[0] or {}).get("content") or {}).get("parts") or []) if candidates else []
        text = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        usage = body.get("usageMetadata") or {}
        input_tokens = int(usage.get("promptTokenCount") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or 0)
        return {
            "status": "success",
            "provider": "google",
            "model": str(config["model"]),
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("google", input_tokens, output_tokens),
            "cost_micro_usd": _estimate_cost_micro_usd("google", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "google", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "google", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


def _call_anthropic(prompt: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = _get_api_key("anthropic")
    if not api_key:
        return {"status": "not_configured", "error": "missing ANTHROPIC_API_KEY", "provider": "anthropic"}
    config = PROVIDER_CONFIG["anthropic"]
    started = time.monotonic()
    try:
        body = _request_json(
            str(config["endpoint"]),
            {"model": config["model"], "max_tokens": max(1, min(4000, int(max_output_tokens or 800))), "messages": [{"role": "user", "content": prompt}]},
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
            "model": str(body.get("model") or config["model"]),
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_cents": _estimate_cost_cents("anthropic", input_tokens, output_tokens),
            "cost_micro_usd": _estimate_cost_micro_usd("anthropic", input_tokens, output_tokens),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "failed", "provider": "anthropic", "error": f"http_{exc.code}: {exc.read().decode(errors='replace')[:300]}", "latency_ms": int((time.monotonic() - started) * 1000)}
    except Exception as exc:
        return {"status": "failed", "provider": "anthropic", "error": str(exc)[:500], "latency_ms": int((time.monotonic() - started) * 1000)}


_PROVIDER_CALLERS: dict[str, Callable[[str, int], dict[str, Any]]] = {
    "openai": _call_openai,
    "google": _call_google,
    "anthropic": _call_anthropic,
}
