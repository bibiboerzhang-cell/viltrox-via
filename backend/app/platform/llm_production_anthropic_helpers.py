"""Pure request identity, input estimation and request policy for strict Anthropic calls.

The SDK call itself (``client.messages.create``) stays in
:mod:`app.platform.llm_production` (the reviewed provider boundary tracked by
the inventory ratchet); this sibling only shapes the kwargs and inspects the
response so the boundary file stays within its line cap.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from typing import Any

# 思考策略与网关传输层(llm_gateway_providers)同一套 env:默认 disabled
# (成本中性,沿用今日行为);VKPI_ANTHROPIC_THINKING=adaptive 开自适应思考,
# 可配 VKPI_ANTHROPIC_EFFORT(low|medium|high|xhigh|max)。永不发
# temperature/top_p/top_k/budget_tokens(Sonnet 5 / Opus 5 一律 400)。
ANTHROPIC_THINKING_MODES = frozenset({"disabled", "adaptive"})
ANTHROPIC_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
_DEFAULT_THINKING_MODE = "disabled"

# 预留估算:Sonnet 5 / Opus 5 高清图上限 4784 token/张(不再给 4-6 特例);
# 4.7+ tokenizer 同文本多 ~30% token → Claude 5 系文本估算除数 3 → 2.3。
_IMAGE_TOKENS = 4784
_TEXT_DIVISOR_LEGACY = 3.0
_TEXT_DIVISOR_CLAUDE5 = 2.3
# PDF document 块:官方口径每页 ≈1500-3000 token(文本+页图),按 3000/页保守估;
# 页数从 PDF 对象表数(/Type /Page),解不出来时按 base64 体积兜底。
_DOCUMENT_TOKENS_PER_PAGE = 3000
_DOCUMENT_TOKENS_FLOOR = 3000
_DOCUMENT_TOKENS_PER_BASE64_KB = 8
_DOCUMENT_TOKENS_CAP = 300_000
_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page(?![s\w])")


class AnthropicRefusal(RuntimeError):
    """The provider returned ``stop_reason=refusal`` (or an empty, non-natural stop).

    Carries ``stop_reason`` and the consumed usage so the strict boundary's
    exception branch can settle it as a provider failure without dropping the
    billed tokens.
    """

    def __init__(
        self,
        stop_reason: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        super().__init__(f"anthropic_refusal:{stop_reason or 'unknown'}")
        self.stop_reason = str(stop_reason or "")
        self.input_tokens = max(0, int(input_tokens or 0))
        self.output_tokens = max(0, int(output_tokens or 0))
        self.reason = "anthropic_refusal"


def anthropic_thinking_policy() -> dict[str, Any]:
    """Resolve thinking mode + effort from env (shared with the gateway transport)."""

    mode = str(os.environ.get("VKPI_ANTHROPIC_THINKING") or _DEFAULT_THINKING_MODE).strip().lower()
    if mode not in ANTHROPIC_THINKING_MODES:
        mode = _DEFAULT_THINKING_MODE
    effort = str(os.environ.get("VKPI_ANTHROPIC_EFFORT") or "").strip().lower()
    if effort not in ANTHROPIC_EFFORT_LEVELS:
        effort = ""
    return {"mode": mode, "effort": effort}


def anthropic_create_kwargs(
    model: str, max_tokens: int, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build ``messages.create`` kwargs: exact model, cap, caller messages, thinking policy."""

    policy = anthropic_thinking_policy()
    kwargs: dict[str, Any] = {
        "model": str(model or "").strip(),
        "max_tokens": int(max_tokens),
        "messages": messages,
        "thinking": {"type": str(policy["mode"])},
    }
    if policy["mode"] == "adaptive" and policy["effort"]:
        kwargs["output_config"] = {"effort": str(policy["effort"])}
    return kwargs


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        if str(getattr(block, "type", "") or "") == "text":
            parts.append(str(getattr(block, "text", "") or ""))
    return "".join(parts).strip()


def anthropic_checked_response(response: Any) -> Any:
    """Return ``response`` unchanged unless it is a refusal / empty abnormal stop.

    ``stop_reason == "refusal"`` (HTTP 200 + empty content) or an empty body
    whose stop reason is not ``end_turn`` raises :class:`AnthropicRefusal`.
    ``max_tokens`` truncation is left to callers (the response carries it).
    """

    stop_reason = str(getattr(response, "stop_reason", "") or "").strip().lower()
    if stop_reason == "refusal" or (
        stop_reason not in {"", "end_turn"} and not _response_text(response)
    ):
        usage = getattr(response, "usage", None)
        raise AnthropicRefusal(
            stop_reason,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )
    return response


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


def _text_divisor(model: str) -> float:
    key = str(model or "").strip().lower()
    # claude-sonnet-5 / claude-opus-5 / claude-fable-5 share the 4.7+ tokenizer;
    # claude-haiku-4-5 / claude-sonnet-4-6 keep the legacy heuristic.
    if key.startswith("claude-") and "-5" in key and "-4-" not in key:
        return _TEXT_DIVISOR_CLAUDE5
    return _TEXT_DIVISOR_LEGACY


def _text_tokens(text: str, divisor: float) -> int:
    return max(1, int(len(text) / divisor))


def _pdf_page_count(base64_data: str) -> int | None:
    try:
        raw = base64.b64decode(base64_data, validate=False)
    except (binascii.Error, ValueError, TypeError):
        return None
    if not raw.startswith(b"%PDF"):
        return None
    return len(_PDF_PAGE_RE.findall(raw)) or None


def _document_tokens(block: dict[str, Any]) -> int:
    source = block.get("source")
    source_type = str(source.get("type") or "") if isinstance(source, dict) else ""
    if source_type not in {"base64", "url", "file", "text"}:
        raise ValueError("unsupported_anthropic_document_source")
    if source_type == "text":
        return _text_tokens(str(source.get("data") or ""), _TEXT_DIVISOR_LEGACY)
    data = source.get("data")
    estimate = _DOCUMENT_TOKENS_FLOOR
    if isinstance(data, str) and data:
        pages = _pdf_page_count(data)
        if pages:
            estimate = pages * _DOCUMENT_TOKENS_PER_PAGE
        else:
            estimate = int(len(data) / 1024 * _DOCUMENT_TOKENS_PER_BASE64_KB)
    return min(_DOCUMENT_TOKENS_CAP, max(_DOCUMENT_TOKENS_FLOOR, estimate))


def anthropic_input_token_estimate(
    messages: list[dict[str, Any]],
    *,
    model: str,
) -> int:
    divisor = _text_divisor(model)
    total = 256
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            total += _text_tokens(content, divisor)
            continue
        if not isinstance(content, list):
            raise ValueError("unsupported_anthropic_message_content")
        for block in content:
            if not isinstance(block, dict):
                raise ValueError("unsupported_anthropic_content_block")
            block_type = str(block.get("type") or "").strip().lower()
            if block_type == "text":
                total += _text_tokens(str(block.get("text") or ""), divisor)
            elif block_type == "image":
                source = block.get("source")
                if not isinstance(source, dict) or str(source.get("type") or "") not in {
                    "base64",
                    "url",
                }:
                    raise ValueError("unsupported_anthropic_image_source")
                total += _IMAGE_TOKENS
            elif block_type == "document":
                # contract_pdf_extract / invoice_extract 真传 document 块
                # (claude_contract_extract.py / contract_assist.py);此前这里
                # 直接 raise,合同/发票提取在预留前就炸 → 按页数/体积保守估算。
                total += _document_tokens(block)
            else:
                raise ValueError(
                    f"unsupported_anthropic_block_type:{block_type or 'missing'}"
                )
    return max(1, total)


__all__ = [
    "ANTHROPIC_EFFORT_LEVELS",
    "ANTHROPIC_THINKING_MODES",
    "AnthropicRefusal",
    "anthropic_checked_response",
    "anthropic_create_kwargs",
    "anthropic_input_token_estimate",
    "anthropic_messages_fingerprint",
    "anthropic_thinking_policy",
]
