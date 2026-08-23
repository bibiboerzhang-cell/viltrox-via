"""Shared primitives for strict production LLM entrypoints.

This module intentionally contains only side-effect-free metadata normalization
and the public bounded failure type.  Provider I/O, fleet-breaker transitions,
budget reservations and settlement live in the per-provider siblings
(:mod:`llm_production_anthropic` / :mod:`llm_production_google` /
:mod:`llm_production_openai`) behind the :mod:`llm_production` facade so their
ordering stays explicit and reviewable.
"""
from __future__ import annotations

from typing import Any


# Task bindings are only inferred where ``purpose`` is an unambiguous alias of
# one reviewed registry binding. Generic production calls keep their purpose
# visible without pretending that they use one of the reviewed task bindings.
_PURPOSE_TASK_BINDINGS = {
    "audit_deep_score": "audit_deep_score",
    "audit_video_analysis": "audit_video_analysis",
    "audit_vision_fallback": "audit_vision_fallback",
    "audit_pre_filter": "audit_pre_filter",
    "trust_anomaly": "audit_pre_filter",
    "marketing_advisor": "via_chat",
    "via_dialogue": "via_chat",
}


def progress_metadata(
    purpose: str,
    metadata: dict[str, Any] | None,
    *,
    phase: str,
) -> dict[str, Any]:
    """Return complete, bounded progress correlation for strict calls."""

    clean_purpose = str(purpose or "").strip()
    out = dict(metadata) if isinstance(metadata, dict) else {}
    if not str(out.get("task_binding") or "").strip():
        inferred = _PURPOSE_TASK_BINDINGS.get(clean_purpose)
        if inferred:
            out["task_binding"] = inferred
    if not str(out.get("phase") or "").strip():
        out["phase"] = phase
    if not str(out.get("subphase") or "").strip():
        out["subphase"] = "provider_generation"
    if not isinstance(out.get("attempt_index"), int) or isinstance(
        out.get("attempt_index"), bool
    ) or int(out.get("attempt_index") or 0) <= 0:
        out["attempt_index"] = 1
    if not isinstance(out.get("attempt_total"), int) or isinstance(
        out.get("attempt_total"), bool
    ) or int(out.get("attempt_total") or 0) <= 0:
        legacy_total = out.get("total")
        out["attempt_total"] = (
            int(legacy_total)
            if isinstance(legacy_total, int)
            and not isinstance(legacy_total, bool)
            and legacy_total > 0
            else 1
        )
    if not str(out.get("target_label") or "").strip() and clean_purpose:
        out["target_label"] = clean_purpose[:160]
    return out


def expected_task_binding(task_binding: str) -> str:
    """Resolve the reviewed ``provider/model`` binding for one task name.

    解析故意绕一圈门面 ``app.platform.llm_production``:provider 子模块
    (``llm_production_anthropic/google/openai``)统一从这里取期望绑定,于是
    ``monkeypatch.setattr(llm_production, "current_task_model_binding", ...)``
    这种打在门面上的补丁对所有 provider 路径都生效(2026-08-23 拆分前后契约不变)。
    门面 import 在函数体内,避免 common → facade → provider → common 的环。
    """

    from app.platform import llm_production

    return str(
        llm_production.current_task_model_binding().get(str(task_binding or ""), "")
        or ""
    )


class ProductionLlmUnavailable(RuntimeError):
    """Safe, bounded failure raised when strict production generation degrades."""

    def __init__(self, result: dict[str, Any]) -> None:
        failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
        code = str(
            failure.get("code")
            or result.get("failure_code")
            or result.get("reason")
            or "provider_unavailable"
        )[:120]
        super().__init__(code)
        self.code = code
        self.result = result


def sdk_failure(
    code: str,
    *,
    provider: str,
    model: str,
    purpose: str,
    details: dict[str, Any] | None = None,
) -> ProductionLlmUnavailable:
    """Build the stable blocked result used by strict provider adapters."""

    return ProductionLlmUnavailable(
        {
            "status": "blocked",
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "failure": {"code": str(code or "provider_unavailable")},
            **(details or {}),
        }
    )


__all__ = [
    "ProductionLlmUnavailable",
    "expected_task_binding",
    "progress_metadata",
    "sdk_failure",
]
