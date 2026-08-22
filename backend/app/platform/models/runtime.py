"""Single offline resolver for exact model execution metadata.

This module deliberately performs no provider I/O.  Legacy runtime environment
lists remain visible as compatibility telemetry, but they are not production
authorization.  The gateway and model switch use the independent dual-signed
readiness gate.  Registration alone never means that an account can call a
model.

Legacy gateway calls remain supported: when no exact model was requested the
gateway may supply its existing provider config as a compatibility fallback.
That fallback is visibly labelled ``legacy_gateway_default`` and is never
accepted by strict exact-model routes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from app.core.model_registry import is_selectable_model

from . import registry


RUNTIME_NOT_CHECKED = "not_checked"
RUNTIME_VERIFIED = "verified"
RUNTIME_UNAVAILABLE = "unavailable"
_RUNTIME_STATES = {RUNTIME_NOT_CHECKED, RUNTIME_VERIFIED, RUNTIME_UNAVAILABLE}

_ENDPOINT_FAMILIES = {
    "openai": "openai_responses",
    "google": "google_generate_content",
    "anthropic": "anthropic_messages",
}

# Provider response revisions are exact by default.  A provider-specific
# revision may be admitted only by a reviewed code change here; runtime input,
# environment variables and evidence documents cannot widen this policy.
MODEL_RESPONSE_ALIASES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        # OpenAI may return the pinned snapshot id when the stable alias was
        # requested.  Keep this allowlist code-reviewed and one-way: accepting
        # a known snapshot never admits another family or future revision.
        "gpt-5.4-mini": frozenset({"gpt-5.4-mini-2026-03-17"}),
        "gpt-5.5": frozenset({"gpt-5.5-2026-04-23"}),
        # 2026-08-22 模型升级刀:gpt-5.6-luna / claude-sonnet-5 / claude-opus-5 /
        # gemini-3.6-flash 的回显快照 id 待 E 车道活探针(stage1 canary)报告后
        # 再经代码评审补入;在此之前严格路由要求回显与请求 id 完全一致。
    }
)


def response_model_matches(expected_model_id: str, observed_model_id: str) -> bool:
    expected = str(expected_model_id or "").strip().lower()
    observed = str(observed_model_id or "").strip().lower()
    if not expected or not observed:
        return False
    if observed == expected:
        return True
    return observed in MODEL_RESPONSE_ALIASES.get(expected, frozenset())


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    provider: str
    model_id: str
    input_cents_per_million: float
    output_cents_per_million: float
    endpoint_family: str
    pricing_version: str


# Exact candidate pricing from provider documentation.  Keeping these entries
# here means the report policy, preflight, actual-call accounting and ledger all
# resolve the same rates instead of falling back to a provider-wide estimate.
_EXACT_CATALOG: dict[tuple[str, str], _CatalogEntry] = {
    ("openai", "gpt-5.4-mini"): _CatalogEntry(
        provider="openai",
        model_id="gpt-5.4-mini",
        input_cents_per_million=75,
        output_cents_per_million=450,
        endpoint_family="openai_responses",
        pricing_version="openai_models_2026-07-15",
    ),
    ("openai", "gpt-5.5"): _CatalogEntry(
        provider="openai",
        model_id="gpt-5.5",
        input_cents_per_million=500,
        output_cents_per_million=3000,
        endpoint_family="openai_responses",
        pricing_version="openai_models_2026-07-15",
    ),
    ("google", "gemini-3.5-flash"): _CatalogEntry(
        provider="google",
        model_id="gemini-3.5-flash",
        input_cents_per_million=150,
        output_cents_per_million=900,
        endpoint_family="google_generate_content",
        pricing_version="google_models_2026-07-15",
    ),
    ("google", "gemini-2.5-pro"): _CatalogEntry(
        provider="google",
        model_id="gemini-2.5-pro",
        input_cents_per_million=125,
        output_cents_per_million=1000,
        endpoint_family="google_generate_content",
        pricing_version="google_models_2026-07-15",
    ),
    # Conservative multimodal rates used by the video worker.  These match the
    # worker's usage-metadata cost calculator (non-audio input/output); audio
    # input is reconciled from provider usage after the call.
    ("google", "gemini-2.5-flash"): _CatalogEntry(
        provider="google",
        model_id="gemini-2.5-flash",
        input_cents_per_million=30,
        output_cents_per_million=250,
        endpoint_family="google_generate_content",
        pricing_version="google_gemini_video_2026-07-14",
    ),
    ("anthropic", "claude-sonnet-4-6"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        input_cents_per_million=300,
        output_cents_per_million=1500,
        endpoint_family="anthropic_messages",
        pricing_version="anthropic_models_2026-07-15",
    ),
    ("anthropic", "claude-opus-4-7"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-opus-4-7",
        input_cents_per_million=500,
        output_cents_per_million=2500,
        endpoint_family="anthropic_messages",
        pricing_version="anthropic_models_2026-07-15",
    ),
    ("anthropic", "claude-haiku-4-5"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-haiku-4-5",
        input_cents_per_million=100,
        output_cents_per_million=500,
        endpoint_family="anthropic_messages",
        pricing_version="anthropic_models_2026-07-15",
    ),
    ("anthropic", "claude-haiku-4-5-20251001"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-haiku-4-5-20251001",
        input_cents_per_million=100,
        output_cents_per_million=500,
        endpoint_family="anthropic_messages",
        pricing_version="anthropic_models_2026-07-15",
    ),
    ("openai", "gpt-5.6"): _CatalogEntry(
        provider="openai",
        model_id="gpt-5.6",
        input_cents_per_million=500,
        output_cents_per_million=3000,
        endpoint_family="openai_responses",
        pricing_version="openai_models_2026-07-13",
    ),
    ("anthropic", "claude-fable-5"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-fable-5",
        input_cents_per_million=1000,
        output_cents_per_million=5000,
        endpoint_family="anthropic_messages",
        pricing_version="anthropic_models_2026-07-13",
    ),
    # ── 2026-08-22 模型升级刀(价格表冻结于官方页当日核实;与
    # core/model_pricing.PRICING_USD_PER_1M_TOKENS 数值一致,单位为分)──
    # gemini-3.6-flash:促销价至 2026-12-31(之后 150/750);缓存 7.5;音频同价。
    ("google", "gemini-3.6-flash"): _CatalogEntry(
        provider="google",
        model_id="gemini-3.6-flash",
        input_cents_per_million=75,
        output_cents_per_million=375,
        endpoint_family="google_generate_content",
        pricing_version="model_upgrade_2026-08-22",
    ),
    # gemini-3.5-flash-lite:文本/图/视频/音频同价,无缓存折扣(关键帧裁判候选)。
    ("google", "gemini-3.5-flash-lite"): _CatalogEntry(
        provider="google",
        model_id="gemini-3.5-flash-lite",
        input_cents_per_million=30,
        output_cents_per_million=250,
        endpoint_family="google_generate_content",
        pricing_version="model_upgrade_2026-08-22",
    ),
    # claude-sonnet-5:$2/$10 正式价;上下文 1M。
    ("anthropic", "claude-sonnet-5"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-sonnet-5",
        input_cents_per_million=200,
        output_cents_per_million=1000,
        endpoint_family="anthropic_messages",
        pricing_version="model_upgrade_2026-08-22",
    ),
    ("anthropic", "claude-opus-5"): _CatalogEntry(
        provider="anthropic",
        model_id="claude-opus-5",
        input_cents_per_million=500,
        output_cents_per_million=2500,
        endpoint_family="anthropic_messages",
        pricing_version="model_upgrade_2026-08-22",
    ),
    # gpt-5.6-luna:缓存输入 2 分;调用须 reasoning.effort='none'。
    ("openai", "gpt-5.6-luna"): _CatalogEntry(
        provider="openai",
        model_id="gpt-5.6-luna",
        input_cents_per_million=20,
        output_cents_per_million=120,
        endpoint_family="openai_responses",
        pricing_version="model_upgrade_2026-08-22",
    ),
}


def _normalise_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    return {"gemini": "google", "claude": "anthropic"}.get(value, value)


def binding_id(provider: str, model_id: str) -> str:
    return f"{_normalise_provider(provider)}/{str(model_id or '').strip()}"


def split_binding(value: str) -> tuple[str, str]:
    provider, separator, model_id = str(value or "").partition("/")
    if not separator:
        return "", ""
    return _normalise_provider(provider), model_id.strip()


def _binding_set(env_name: str) -> set[str]:
    raw = str(os.environ.get(env_name) or "")
    return {
        binding_id(*split_binding(item))
        for item in raw.replace(";", ",").split(",")
        if all(split_binding(item))
    }


def _normalise_runtime_state(value: Any) -> str:
    if value is True:
        return RUNTIME_VERIFIED
    if value is False:
        return RUNTIME_UNAVAILABLE
    state = str(value or "").strip().lower()
    return state if state in _RUNTIME_STATES else RUNTIME_NOT_CHECKED


def runtime_state_for(
    provider: str,
    model_id: str,
    *,
    runtime_availability: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Return exact-model runtime state and its non-secret evidence source."""
    exact_id = binding_id(provider, model_id)
    if runtime_availability is not None and exact_id in runtime_availability:
        return _normalise_runtime_state(runtime_availability[exact_id]), "explicit_runtime_evidence"
    if exact_id in _binding_set("VKPI_LLM_RUNTIME_UNAVAILABLE_MODELS"):
        return RUNTIME_UNAVAILABLE, "environment_runtime_evidence"
    if exact_id in _binding_set("VKPI_LLM_RUNTIME_VERIFIED_MODELS"):
        return RUNTIME_VERIFIED, "environment_runtime_evidence"
    return RUNTIME_NOT_CHECKED, "no_runtime_evidence"


@dataclass(frozen=True, slots=True)
class ResolvedModelBinding:
    provider: str
    model_id: str
    model_key: str | None
    endpoint_family: str
    input_cents_per_million: float | None
    output_cents_per_million: float | None
    transport_ready: bool
    registered: bool
    runtime_availability: str
    runtime_evidence_source: str
    registry_source: str
    pricing_version: str

    @property
    def binding(self) -> str:
        return binding_id(self.provider, self.model_id)

    @property
    def pricing_known(self) -> bool:
        return self.input_cents_per_million is not None and self.output_cents_per_million is not None

    def blocker(
        self,
        *,
        require_registered: bool = False,
        require_runtime_verified: bool = False,
        require_pricing: bool = True,
    ) -> str:
        if not self.provider or not self.model_id:
            return "invalid_binding"
        if not self.transport_ready:
            return "transport_not_ready"
        if require_registered and not self.registered:
            return "model_not_registered"
        if require_pricing and not self.pricing_known:
            return "model_pricing_unknown"
        if require_runtime_verified and self.runtime_availability != RUNTIME_VERIFIED:
            return f"runtime_{self.runtime_availability}"
        if (
            require_runtime_verified
            and self.runtime_evidence_source == "environment_runtime_evidence"
        ):
            return "runtime_legacy_allowlist_not_authoritative"
        return ""

    def matches_response_model(self, actual_model_id: str) -> bool:
        return response_model_matches(self.model_id, actual_model_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding": self.binding,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_key": self.model_key,
            "endpoint_family": self.endpoint_family,
            "input_cents_per_million": self.input_cents_per_million,
            "output_cents_per_million": self.output_cents_per_million,
            "transport_ready": self.transport_ready,
            "registered": self.registered,
            "pricing_known": self.pricing_known,
            "runtime_availability": self.runtime_availability,
            "runtime_evidence_source": self.runtime_evidence_source,
            "registry_source": self.registry_source,
            "pricing_version": self.pricing_version,
        }


def resolve_model_binding(
    provider: str,
    model_id: str,
    *,
    gateway_config: Mapping[str, Any] | None = None,
    runtime_availability: Mapping[str, Any] | None = None,
) -> ResolvedModelBinding:
    """Resolve one exact provider/model pair without probing the provider."""
    provider_key = _normalise_provider(provider)
    exact_model = str(model_id or "").strip()
    runtime_state, runtime_source = runtime_state_for(
        provider_key,
        exact_model,
        runtime_availability=runtime_availability,
    )

    routed = next(
        (
            item
            for item in registry.list_models()
            if item.gateway_provider == provider_key and item.model_id == exact_model
        ),
        None,
    )
    if routed is not None:
        return ResolvedModelBinding(
            provider=provider_key,
            model_id=exact_model,
            model_key=routed.key,
            endpoint_family=routed.endpoint_family,
            input_cents_per_million=float(routed.input_cents_per_million),
            output_cents_per_million=float(routed.output_cents_per_million),
            transport_ready=bool(routed.transport_ready),
            registered=True,
            runtime_availability=runtime_state,
            runtime_evidence_source=runtime_source,
            registry_source="router_registry",
            pricing_version="router_registry_v1",
        )

    catalog = _EXACT_CATALOG.get((provider_key, exact_model))
    if catalog is not None:
        return ResolvedModelBinding(
            provider=provider_key,
            model_id=exact_model,
            model_key=None,
            endpoint_family=catalog.endpoint_family,
            input_cents_per_million=catalog.input_cents_per_million,
            output_cents_per_million=catalog.output_cents_per_million,
            transport_ready=True,
            registered=is_selectable_model(binding_id(provider_key, exact_model)),
            runtime_availability=runtime_state,
            runtime_evidence_source=runtime_source,
            registry_source="exact_runtime_catalog",
            pricing_version=catalog.pricing_version,
        )

    if is_selectable_model(binding_id(provider_key, exact_model)):
        return ResolvedModelBinding(
            provider=provider_key,
            model_id=exact_model,
            model_key=None,
            endpoint_family=_ENDPOINT_FAMILIES.get(provider_key, ""),
            input_cents_per_million=None,
            output_cents_per_million=None,
            transport_ready=provider_key in _ENDPOINT_FAMILIES,
            registered=True,
            runtime_availability=runtime_state,
            runtime_evidence_source=runtime_source,
            registry_source="core_registry_without_pricing",
            pricing_version="",
        )

    config = dict(gateway_config or {})
    config_model = str(config.get("model") or "").strip()
    is_legacy_default = bool(config_model and config_model == exact_model and provider_key in _ENDPOINT_FAMILIES)
    return ResolvedModelBinding(
        provider=provider_key,
        model_id=exact_model,
        model_key=None,
        endpoint_family=_ENDPOINT_FAMILIES.get(provider_key, ""),
        input_cents_per_million=(
            float(config.get("input_cents_per_million") or 0) if is_legacy_default else None
        ),
        output_cents_per_million=(
            float(config.get("output_cents_per_million") or 0) if is_legacy_default else None
        ),
        transport_ready=is_legacy_default,
        registered=False,
        runtime_availability=runtime_state,
        runtime_evidence_source=runtime_source,
        registry_source="legacy_gateway_default" if is_legacy_default else "unregistered",
        pricing_version="legacy_provider_default" if is_legacy_default else "",
    )


__all__ = [
    "RUNTIME_NOT_CHECKED",
    "RUNTIME_UNAVAILABLE",
    "RUNTIME_VERIFIED",
    "ResolvedModelBinding",
    "binding_id",
    "resolve_model_binding",
    "runtime_state_for",
    "split_binding",
]
