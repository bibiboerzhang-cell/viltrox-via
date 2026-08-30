"""Pure shaping/verification stages for the strict Gemini generation boundary.

2026-08-30 CC 车道:generate_google_content(原 CC 57)五段拆分时,把不含
provider I/O / 预留 / 台账副作用的纯组装与校验段搬到本兄弟文件——token 钳位、
进度元数据、任务绑定校验、usage/正文验证、实耗定价。预算围栏顺序、预留/结算
与台账写入仍留在 :mod:`llm_production_google`,保证花钱路径的顺序单文件可审。

调用方 / 测试只认门面 ``app.platform.llm_production``(monkeypatch 也打在门面上);
本模块不得被业务代码直接 import。
"""
from __future__ import annotations

from typing import Any

from app.platform import llm_gateway
from app.platform.llm_production_common import (
    allowed_task_bindings as _allowed_task_bindings,
    progress_metadata as _progress_metadata,
    sdk_failure as _sdk_failure,
)
from app.platform.llm_production_google_helpers import (
    GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
    GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    google_usage_cost_micro_usd as _google_usage_cost_micro_usd,
    google_usage_metadata as _google_usage_metadata,
    usage_int as _usage_int,
)


def google_token_limits(
    max_output_tokens: int, estimated_input_tokens: int
) -> tuple[int, int]:
    """Clamp caller token limits to the platform hard caps (invalid → raise)."""

    try:
        output_limit = min(
            GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
            max(1, int(max_output_tokens)),
        )
        input_estimate = min(
            GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
            max(1, int(estimated_input_tokens)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("valid token limits are required") from exc
    return output_limit, input_estimate


def google_progress_metadata(
    exact_purpose: str,
    metadata: dict[str, Any] | None,
    execution_class: str,
) -> dict[str, Any]:
    """Build bounded progress correlation for one strict Gemini attempt."""

    progress_metadata = _progress_metadata(
        exact_purpose,
        metadata,
        phase="video_analysis",
    )
    progress_metadata["execution_class"] = str(
        execution_class or llm_gateway.PRODUCTION_EXECUTION_CLASS
    )
    return progress_metadata


def validate_google_task_binding(
    progress_metadata: dict[str, Any],
    *,
    provider: str,
    exact_model: str,
    exact_purpose: str,
) -> tuple[str, bool]:
    """Enforce the reviewed task binding chain; annotate primary/fallback role."""

    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    actual_binding = f"{provider}/{exact_model}"
    progress_metadata["task_binding_actual"] = actual_binding
    if task_binding:
        # 2026-08-23 波 C·C1:绑定校验认整条链(主 + 回退);台账仍按实际请求的精确模型
        # 记(record_call model=exact_model),并标注本次是主力还是回退节。
        allowed_bindings = _allowed_task_bindings(task_binding)
        expected_binding = allowed_bindings[0] if allowed_bindings else ""
        if actual_binding not in allowed_bindings:
            raise _sdk_failure(
                "task_binding_model_mismatch",
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
                details={
                    "task_binding": task_binding,
                    "expected_binding": expected_binding,
                    "allowed_bindings": list(allowed_bindings),
                    "actual_binding": actual_binding,
                },
            )
        progress_metadata["task_binding_role"] = (
            "primary" if actual_binding == expected_binding else "fallback"
        )
        progress_metadata["task_binding_primary"] = expected_binding
    task_binding_fallback = bool(
        task_binding
        and progress_metadata.get("task_binding_role") == "fallback"
        and actual_binding != progress_metadata.get("task_binding_primary")
    )
    progress_metadata["fallback_semantics"] = "task_binding_role_v1"
    return actual_binding, task_binding_fallback


def google_usage_and_status(response: Any, binding: Any) -> tuple[
    dict[str, Any], int, int, str, str
]:
    """Verify usage/body evidence and classify the confirmed response."""

    usage_metadata = _google_usage_metadata(response)
    # 2026-07-18 $150 对账修:接地检索灌入的 toolUsePromptTokenCount 此前
    # 从未计入 input(真实计费 input 是 prompt 的数倍;台账 $27 vs 实扣 $150)。
    input_tokens = _usage_int(
        usage_metadata, "prompt_token_count", "promptTokenCount"
    ) + _usage_int(
        usage_metadata, "tool_use_prompt_token_count", "toolUsePromptTokenCount"
    )
    output_tokens = _usage_int(
        usage_metadata, "candidates_token_count", "candidatesTokenCount"
    ) + _usage_int(
        usage_metadata, "thoughts_token_count", "thoughtsTokenCount"
    )
    response_model = str(
        getattr(response, "model_version", "")
        or getattr(response, "model", "")
        or ""
    ).strip()
    status = "success"
    if not binding.matches_response_model(response_model):
        status = "model_mismatch"
    elif input_tokens <= 0 or output_tokens <= 0:
        status = "usage_missing"
    return usage_metadata, input_tokens, output_tokens, response_model, status


def google_actual_cost_micro(
    *,
    exact_model: str,
    binding: Any,
    usage_metadata: dict[str, Any],
    input_tokens: int,
    output_tokens: int,
    status: str,
    estimated_micro: int,
    progress_metadata: dict[str, Any],
) -> int:
    """Price the confirmed usage conservatively (grounding surcharge included)."""

    actual_micro = (
        _google_usage_cost_micro_usd(
            model=exact_model,
            binding=binding,
            usage_metadata=usage_metadata,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if input_tokens > 0 and output_tokens > 0
        else 0
    )
    if status == "model_mismatch":
        actual_micro = max(actual_micro, estimated_micro)
    # 2026-07-18 $150 对账修:Grounding with Google Search 按请求另收
    # $35/1,000(与 token 费无关),此前从未入账。调用方带
    # grounding_tool=google_search 元数据即计附加费。
    if str(progress_metadata.get("grounding_tool") or "") == "google_search":
        actual_micro += 35_000
    return actual_micro


__all__ = [
    "google_actual_cost_micro",
    "google_progress_metadata",
    "google_token_limits",
    "google_usage_and_status",
    "validate_google_task_binding",
]
