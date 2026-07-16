"""Pure helpers for the strict Gemini generation boundary.

Provider I/O remains in :mod:`app.platform.llm_production`; this sibling keeps
request hashing, bounded config shaping, usage parsing and attempt summaries
small enough for the canonical source line guard.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.platform import llm_gateway


GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP = 8192
GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP = 1_000_000


def google_contents_fingerprint(contents: list[Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            contents,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"google_contents_sha256:{digest}"


def google_config_with_output_limit(config: Any, output_limit: int) -> Any:
    if config is None:
        return {"max_output_tokens": output_limit}
    if isinstance(config, dict):
        return {**config, "max_output_tokens": output_limit}
    model_copy = getattr(config, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"max_output_tokens": output_limit})
    copy_method = getattr(config, "copy", None)
    if callable(copy_method):
        try:
            return copy_method(update={"max_output_tokens": output_limit})
        except TypeError:
            pass
    raise ValueError("unsupported_google_generate_config")


def google_usage_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    model_dump = getattr(usage, "model_dump", None)
    if callable(model_dump):
        try:
            value = model_dump(mode="json", exclude_none=True)
        except Exception:
            value = None
        if isinstance(value, dict):
            return value
    out: dict[str, Any] = {}
    for key in (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "cached_content_token_count",
    ):
        value = getattr(usage, key, None)
        if value is not None:
            out[key] = value
    return out


def usage_int(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    return 0


def google_usage_cost_micro_usd(
    *,
    model: str,
    binding: Any,
    usage_metadata: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
) -> int:
    """Price confirmed Google usage conservatively, including audio premium."""

    micro = llm_gateway._estimate_cost_micro_usd(
        "google",
        input_tokens,
        output_tokens,
        binding=binding,
    )
    details = usage_metadata.get("prompt_tokens_details")
    if not isinstance(details, list):
        details = usage_metadata.get("promptTokensDetails")
    audio_tokens = 0
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("modality") or "").strip().upper() != "AUDIO":
            continue
        audio_tokens += usage_int(item, "token_count", "tokenCount")
    model_key = str(model or "").strip().lower()
    # Audio is $1/M on these Flash tiers; add the premium over normal input.
    # Cached-input discounts are intentionally not subtracted at this boundary.
    if "gemini-2.5-flash" in model_key:
        micro += int(round(audio_tokens * 0.70))
    elif "gemini-3-flash" in model_key:
        micro += int(round(audio_tokens * 0.50))
    return max(0, int(micro))


def append_google_attempt(
    attempt_log: list[dict[str, Any]] | None,
    *,
    model: str,
    metadata: dict[str, Any],
    state: str,
    estimated_cost_usd: float,
    actual_cost_usd: float | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    if not isinstance(attempt_log, list):
        return
    attempt_log.append(
        {
            "authority": "llm_production_google_generate_content_v1",
            "model": model,
            "state": state,
            "phase": metadata.get("phase"),
            "subphase": metadata.get("subphase"),
            "attempt_index": metadata.get("attempt_index"),
            "attempt_total": metadata.get("attempt_total"),
            "estimated_cost_usd": round(max(0.0, estimated_cost_usd), 8),
            "actual_cost_usd": (
                round(max(0.0, float(actual_cost_usd)), 8)
                if actual_cost_usd is not None
                else None
            ),
            "input_tokens": max(0, int(input_tokens or 0)),
            "output_tokens": max(0, int(output_tokens or 0)),
        }
    )


__all__ = [
    "GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP",
    "GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP",
    "append_google_attempt",
    "google_config_with_output_limit",
    "google_contents_fingerprint",
    "google_usage_cost_micro_usd",
    "google_usage_metadata",
    "usage_int",
]
