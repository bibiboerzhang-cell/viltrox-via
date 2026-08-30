"""LLM adapter for the optional product-fit recommendation-reason port."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from app.core.model_registry import current_task_model_binding, split_binding
from app.platform import llm_production
from app.shared.product_fit_contracts import (
    ReasonResult,
    RecommendationReasonPort,
    copy_reason_result,
)
from app.shared.product_fit_policy import (
    PRODUCT_FIT_REASON_BUDGET_SCOPE,
    PRODUCT_FIT_SCENARIO,
    deterministic_reason,
    reason_failure_code,
    reason_prompt,
    text,
    valid_reason_payload,
)


REASON_MODEL_TASK = "kol_product_fit_reason"


def reason_model_binding() -> tuple[str, str]:
    return split_binding(current_task_model_binding().get(REASON_MODEL_TASK) or "")


class LlmRecommendationReasonAdapter(RecommendationReasonPort):
    """Bounded adapter whose result cannot influence deterministic ranking."""

    def __init__(
        self,
        *,
        binding_resolver: Callable[[], tuple[str, str]] = reason_model_binding,
        generate_json: Callable[..., dict[str, Any]] = llm_production.generate_json,
    ) -> None:
        self._binding_resolver = binding_resolver
        self._generate_json = generate_json

    def generate_reason(
        self,
        candidate: Mapping[str, Any],
        *,
        binding: Mapping[str, Any],
        token_limit: int,
        budget_scope: str,
    ) -> ReasonResult:
        provider, model = self._binding_resolver()
        target_label = text(
            candidate.get("product_family_name")
            or candidate.get("product_family_uid")
        )
        try:
            response = self._generate_json(
                reason_prompt(binding, candidate),
                provider=provider,
                model=model,
                purpose="p4_recommendation_reasons",
                max_output_tokens=int(token_limit),
                cost_tag=budget_scope,
                triggered_by=REASON_MODEL_TASK,
                required_keys=("short_reason", "pitch_angle", "caution_note"),
                validator=valid_reason_payload,
                metadata={
                    "task_binding": REASON_MODEL_TASK,
                    "scenario": PRODUCT_FIT_SCENARIO,
                    "kol_entity_uid": (binding.get("kol") or {}).get(
                        "kol_entity_uid"
                    ),
                    "product_family_uid": candidate.get("product_family_uid"),
                    "rank": candidate.get("rank"),
                    "phase": "kol_recommendation",
                    "subphase": "product_fit_reason",
                    "attempt_index": 1,
                    "total": 1,
                    "target_label": target_label,
                },
            )
        except Exception as exc:
            response = {
                "status": "unavailable",
                "failure": {"code": type(exc).__name__},
            }
        candidate_json = response.get("json") if isinstance(response, dict) else None
        exact_response = (
            str(response.get("status") or "") == "success"
            and str(response.get("provider") or "").strip().lower() == provider
            and str(response.get("model") or "").strip().startswith(model)
        )
        if exact_response and valid_reason_payload(candidate_json):
            reason = {
                "short_reason": text(candidate_json.get("short_reason")),
                "pitch_angle": text(candidate_json.get("pitch_angle")),
                "caution_note": text(candidate_json.get("caution_note")),
            }
            mode = "llm"
            provenance_provider = provider
            provenance_model = model
            status = "success"
            fallback_reason = ""
        else:
            reason = deterministic_reason(binding, candidate)
            mode = "deterministic_fallback"
            provenance_provider = "rule_v0"
            provenance_model = "rule_v0"
            response_status = str(response.get("status") or "unavailable")
            status = "degraded" if response_status == "success" else response_status
            fallback_reason = (
                "exact_model_or_json_contract_mismatch"
                if response_status == "success"
                else reason_failure_code(response)
            )
        return ReasonResult(
            mode=mode,
            provider=provenance_provider,
            model=provenance_model,
            requested_binding=f"{provider}/{model}",
            status=status,
            fallback_reason=fallback_reason,
            **reason,
        )


def attach_reason(
    payload: dict[str, Any],
    item: dict[str, Any],
    *,
    port: RecommendationReasonPort | None = None,
    binding_resolver: Callable[[], tuple[str, str]] = reason_model_binding,
    generate_json: Callable[..., dict[str, Any]] = llm_production.generate_json,
) -> None:
    adapter = port or LlmRecommendationReasonAdapter(
        binding_resolver=binding_resolver,
        generate_json=generate_json,
    )
    item["recommendation_reason"] = copy_reason_result(
        adapter.generate_reason(
            item,
            binding=payload,
            token_limit=220,
            budget_scope=PRODUCT_FIT_REASON_BUDGET_SCOPE,
        )
    )


__all__ = [
    "LlmRecommendationReasonAdapter",
    "REASON_MODEL_TASK",
    "attach_reason",
    "reason_model_binding",
]
