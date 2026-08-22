"""Shared W-L1 hooks for ``invoke`` / ``invoke_json``: result cache + deferred outcome.

Both orchestration bodies call these at the same three points — before the
candidate loop (cache lookup), after a genuine provider success (cache store)
and before the final ``rule_v0`` fallback (deferred-instead-of-placeholder).
Keeping them here keeps both orchestration modules under the 1000-line guard
and guarantees the two entrypoints share one cache/deferral contract.
"""
from __future__ import annotations

from typing import Any

from app.platform import llm_gateway_deferred as _deferred
from app.platform import llm_gateway_result_cache as _result_cache
from app.platform.llm_gateway_model_alias import resolve_model_alias as _resolve_model_alias


def cache_model_label(candidates: list[tuple[str, str, bool]]) -> str:
    """Primary route label (exact model) that keys the result cache."""

    for provider, model_id, _explicit in candidates:
        provider_key = str(provider or "").strip().lower()
        exact = _resolve_model_alias(provider_key, model_id)
        if provider_key or exact:
            return f"{provider_key}/{exact}"
    return ""


def serve_cached_result(
    *,
    plan: Any,
    purpose: str,
    prompt: str,
    contract: str,
    record_call: Any,
    triggered_by: Any,
    metadata: dict[str, Any] | None,
    staff: dict[str, Any] | None,
    cost_scope: str,
) -> dict[str, Any] | None:
    """Return a zero-cost hit (and ledger it) when the result cache has one."""

    if plan is None:
        return None
    cached = _result_cache.lookup(plan)
    if cached is None:
        return None
    # cost_tag 故意不传:命中零成本,不往 vkpi_ai_cost_ledger 镜像 $0 行;
    # vkpi_llm_calls 仍落一行(cache_hit=true)供命中率埋点。
    record_call(
        provider=str(cached.get("provider") or ""),
        model=str(cached.get("model") or ""),
        purpose=purpose,
        prompt=prompt,
        input_tokens=0,
        output_tokens=0,
        cost_micro_usd=0,
        status="success",
        fallback_used=False,
        cost_tag=None,
        triggered_by=triggered_by,
        metadata={
            **_result_cache.hit_ledger_metadata(cached, plan, metadata=metadata),
            "cost_scope": cost_scope,
            "json_contract": contract == "json",
        },
        staff=staff,
    )
    return _result_cache.hit_result(cached, plan, purpose=purpose)


def store_cached_result(plan: Any, result: dict[str, Any], audit: Any) -> None:
    """Best-effort cache write after a genuine provider success.

    可追溯性硬规则:缓存条目必须指回一条已落库的 vkpi_llm_calls 行
    (``origin_call_uid``)。台账没确认 call_uid(写失败 / 被替身)就不缓存——
    回放一个没有审计锚点的结果,比多打一次 provider 更危险。
    """

    if plan is None:
        return
    call_uid = ""
    if isinstance(audit, dict):
        call_row = audit.get("call")
        if isinstance(call_row, dict):
            call_uid = str(call_row.get("call_uid") or "")
    if not call_uid:
        return
    _result_cache.store(plan, result, call_uid=call_uid)


def deferred_or_none(
    *,
    prompt: str,
    purpose: str,
    errors: list[dict[str, Any]],
    normalise_error: Any,
    record_call: Any,
    cost_scope: str,
    triggered_by: Any,
    metadata: dict[str, Any] | None,
    staff: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """For deferred purposes blocked purely by budget/readiness gates: defer, not rule_v0."""

    if not _deferred.is_deferred_purpose(purpose):
        return None
    reason = _deferred.deferral_reason(errors)
    if not reason:
        return None
    result = _deferred.build_deferred_result(
        prompt,
        purpose=purpose,
        reason=reason,
        errors=errors,
        normalise_error=normalise_error,
    )
    if extra:
        result.update(extra)
    record_call(
        provider=str(result.get("provider") or "") or "gateway",
        model=str(result.get("model") or ""),
        purpose=purpose,
        prompt=prompt,
        status="deferred",
        fallback_used=False,
        cost_tag=cost_scope,
        triggered_by=triggered_by,
        metadata=_deferred.deferred_ledger_metadata(result, metadata=metadata, errors=errors),
        staff=staff,
    )
    return result


__all__ = [
    "cache_model_label",
    "deferred_or_none",
    "serve_cached_result",
    "store_cached_result",
]
