"""Pure request identity and input estimation for strict Anthropic calls."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def anthropic_messages_fingerprint(messages: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"anthropic_messages_sha256:{digest}"


def anthropic_input_token_estimate(
    messages: list[dict[str, Any]],
    *,
    model: str,
) -> int:
    image_tokens = 1568 if str(model or "").strip() == "claude-sonnet-4-6" else 4784
    total = 256
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total += max(1, len(content) // 3)
            continue
        if not isinstance(content, list):
            raise ValueError("unsupported_anthropic_message_content")
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("unsupported_anthropic_content_block")
            block_type = str(block.get("type") or "").strip().lower()
            if block_type == "text":
                total += max(1, len(str(block.get("text") or "")) // 3)
            elif block_type == "image":
                source = block.get("source")
                if not isinstance(source, dict) or str(source.get("type") or "") not in {
                    "base64",
                    "url",
                }:
                    raise ValueError("unsupported_anthropic_image_source")
                total += image_tokens
            else:
                raise ValueError(
                    f"unsupported_anthropic_block_type:{block_type or 'missing'}"
                )
    return max(1, total)


__all__ = ["anthropic_input_token_estimate", "anthropic_messages_fingerprint"]
