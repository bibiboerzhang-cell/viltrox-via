"""Marketing Brain Model Router / Registry (model-agnostic layer).

A thin, additive side-car over the existing ``app.platform.llm_gateway``. It
does NOT re-implement any provider transport. Instead it formalises:

- ``registry``  — model capability / cost / context / latency / locality metadata
- ``router``    — pick a model for a skill from quality/cost/speed thresholds,
                  with an explicit fallback chain, then delegate the real call
                  to ``llm_gateway.invoke`` (provider transport untouched).
- ``adapters``  — a uniform adapter interface (openai / qwen example stubs).
- ``cost_policy`` / ``quality_policy`` — pure scoring helpers used by the router.

Nothing here mutates gateway behaviour; existing call-sites keep working.
"""

from __future__ import annotations

from .registry import (
    ModelSpec,
    list_models,
    get_model,
    models_for_provider,
    local_models,
    transport_ready_models,
)
from .runtime import ResolvedModelBinding, resolve_model_binding
from .readiness import (
    MODEL_READINESS_VERSION,
    MODEL_PROBE_EVIDENCE_VERSION,
    ReadinessThresholds,
    TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS,
    assess_model_readiness,
    build_model_readiness_catalog,
    exact_binding_readiness_from_environment,
    model_attestation_trust_root_status,
)
from .evaluation_artifact import (
    MODEL_EVALUATION_ARTIFACT_VERSION,
    TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS,
    build_model_evaluation_artifact,
    verify_model_evaluation_artifact,
)
from .router import (
    RouteRequest,
    RouteDecision,
    route,
    route_and_invoke,
)

__all__ = [
    "ModelSpec",
    "list_models",
    "get_model",
    "models_for_provider",
    "local_models",
    "transport_ready_models",
    "ResolvedModelBinding",
    "resolve_model_binding",
    "MODEL_READINESS_VERSION",
    "MODEL_PROBE_EVIDENCE_VERSION",
    "ReadinessThresholds",
    "TRUSTED_EXACT_PROBE_ED25519_PUBLIC_KEYS",
    "assess_model_readiness",
    "build_model_readiness_catalog",
    "exact_binding_readiness_from_environment",
    "model_attestation_trust_root_status",
    "MODEL_EVALUATION_ARTIFACT_VERSION",
    "TRUSTED_EVALUATION_ED25519_PUBLIC_KEYS",
    "build_model_evaluation_artifact",
    "verify_model_evaluation_artifact",
    "RouteRequest",
    "RouteDecision",
    "route",
    "route_and_invoke",
]
