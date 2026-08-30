"""Fail-closed model selection for evidence-backed reports.

Registration makes a model selectable. It does not prove that credentials,
account access, quota, or the provider endpoint can serve that model. Runtime
verification belongs to an explicit live benchmark.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.core.model_registry import is_selectable_model
from app.domains.market_brain.data_readiness import DataReadiness
from app.platform.models.readiness import (
    assess_model_readiness,
    configured_providers_from_environment,
    readiness_evidence_from_environment,
)
from app.platform.models.runtime import resolve_model_binding, split_binding


REPORT_MODEL_POLICY_VERSION = "report_model_policy_v3"
REPORT_PRIMARY_MODEL = "openai/gpt-5.6"
REPORT_CHALLENGER_MODEL = "anthropic/claude-fable-5"
REPORT_JUDGE_CANDIDATES = (REPORT_CHALLENGER_MODEL,)

DETERMINISTIC_DESCRIPTIVE_MODE = "deterministic_descriptive"
ADVANCED_MODEL_MODE = "advanced_model"
_TRUSTED_SOURCE_STATUS = "real"


def _status_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


@dataclass(frozen=True, slots=True)
class ReportSourceSample:
    """One required report source and its observed sample threshold."""

    key: str
    observed: int
    minimum: int
    source_count: int
    data_status: str = _TRUSTED_SOURCE_STATUS
    label: str = ""

    def __post_init__(self) -> None:
        key = str(self.key or "").strip()
        observed = int(self.observed)
        minimum = int(self.minimum)
        source_count = int(self.source_count)
        data_status = _status_value(self.data_status)
        if not key:
            raise ValueError("report source key is required")
        if observed < 0:
            raise ValueError("report source observed must be non-negative")
        if minimum < 1:
            raise ValueError("report source minimum must be at least one")
        if source_count < 0:
            raise ValueError("report source_count must be non-negative")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "observed", observed)
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "source_count", source_count)
        object.__setattr__(self, "data_status", data_status)
        object.__setattr__(self, "label", str(self.label or "").strip())

    def as_check(self) -> dict[str, Any]:
        source_ready = self.source_count > 0 and self.data_status == _TRUSTED_SOURCE_STATUS
        sample_ready = self.observed >= self.minimum
        return {
            "key": self.key,
            "label": self.label or self.key,
            "status": "ready" if source_ready and sample_ready else "blocked",
            "data_status": self.data_status,
            "source_count": self.source_count,
            "source_ready": source_ready,
            "observed": self.observed,
            "minimum": self.minimum,
            "sample_ready": sample_ready,
        }


@dataclass(frozen=True, slots=True)
class ReportModelDecision:
    mode: str
    provider_calls_allowed: bool
    claim_level: str
    primary_model: str | None
    challenger_model: str | None
    judge_candidates: tuple[str, ...]
    selected_models: tuple[str, ...]
    blockers: tuple[str, ...]
    checks: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        runtime_items = (
            (self.checks.get("model_runtime") or {}).get("items")
            if isinstance(self.checks.get("model_runtime"), Mapping)
            else {}
        ) or {}
        return {
            "version": REPORT_MODEL_POLICY_VERSION,
            "mode": self.mode,
            "provider_calls_allowed": self.provider_calls_allowed,
            "high_order_models_allowed": self.provider_calls_allowed,
            "deterministic_only": not self.provider_calls_allowed,
            "descriptive_only": not self.provider_calls_allowed,
            "claim_level": self.claim_level,
            "primary_model": self.primary_model,
            "challenger_model": self.challenger_model,
            "judge_candidates": list(self.judge_candidates),
            "selected_models": list(self.selected_models),
            "candidates": [
                {
                    "role": "primary",
                    "binding": REPORT_PRIMARY_MODEL,
                    **dict(runtime_items.get(REPORT_PRIMARY_MODEL) or {}),
                },
                {
                    "role": "challenger_and_judge_candidate",
                    "binding": REPORT_CHALLENGER_MODEL,
                    **dict(runtime_items.get(REPORT_CHALLENGER_MODEL) or {}),
                },
            ],
            "checks": self.checks,
            "blockers": list(self.blockers),
            "note": (
                "Registration and credentials are not availability. Every selected exact model "
                "must have fresh exact-model probe evidence and an actual versioned evaluation "
                "that passes production thresholds."
            ),
        }


def _readiness_payload(value: DataReadiness | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, DataReadiness):
        return value.to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        return dict(payload) if isinstance(payload, Mapping) else {}
    return {}


def _coerce_source(value: ReportSourceSample | Mapping[str, Any]) -> ReportSourceSample:
    if isinstance(value, ReportSourceSample):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("report source must be ReportSourceSample or mapping")

    def exact_int(raw: Any, *, default: int = 0) -> int:
        if raw is None or raw == "":
            return default
        if isinstance(raw, bool):
            raise ValueError("boolean is not a valid source count")
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("source count must be a finite integer") from exc
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError("source count must be a finite integer")
        return int(number)

    return ReportSourceSample(
        key=str(value.get("key") or value.get("source_key") or ""),
        observed=exact_int(value.get("observed", value.get("sample_count", 0))),
        minimum=exact_int(value.get("minimum", value.get("minimum_samples", 0))),
        source_count=exact_int(value.get("source_count", 0)),
        data_status=_status_value(value.get("data_status", value.get("status", ""))),
        label=str(value.get("label") or ""),
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _note_readiness_blockers(
    readiness_passed: bool, readiness_blockers: list[str], blockers: list[str]
) -> None:
    if not readiness_passed:
        if readiness_blockers:
            blockers.extend(f"data_readiness:{item}" for item in readiness_blockers)
        else:
            blockers.append("data_readiness:not_ready_or_claimable")


def _data_readiness_gate(
    data_readiness: DataReadiness | Mapping[str, Any], blockers: list[str]
) -> tuple[dict[str, Any], bool]:
    """Readiness gate: returns (checks fragment, passed); appends blockers in order."""
    data_readiness_payload = _readiness_payload(data_readiness)
    readiness_status = _status_value(data_readiness_payload.get("status"))
    readiness_ready = data_readiness_payload.get("ready") is True
    readiness_claimable = data_readiness_payload.get("claimable") is True
    readiness_passed = (
        readiness_status == "ready" and readiness_ready and readiness_claimable
    )
    raw_readiness_blockers = data_readiness_payload.get("blockers")
    readiness_blockers = (
        [str(item) for item in raw_readiness_blockers if str(item)]
        if isinstance(raw_readiness_blockers, (list, tuple))
        else []
    )
    if raw_readiness_blockers not in (None, "", [], ()) and not isinstance(
        raw_readiness_blockers, (list, tuple)
    ):
        blockers.append("data_readiness:blockers_invalid")
        readiness_passed = False
    _note_readiness_blockers(readiness_passed, readiness_blockers, blockers)
    check = {
        "passed": readiness_passed,
        "status": readiness_status or "missing",
        "ready": readiness_ready,
        "claimable": readiness_claimable,
        "claim_level": str(data_readiness_payload.get("claim_level") or ""),
        "blockers": readiness_blockers,
    }
    return check, readiness_passed


def _sources_gate(
    sources: Iterable[ReportSourceSample | Mapping[str, Any]], blockers: list[str]
) -> tuple[list[dict[str, Any]], int, bool]:
    """Source provenance + sample gate: (items, required_count, passed)."""
    source_items: list[dict[str, Any]] = []
    source_keys: set[str] = set()
    if isinstance(sources, (list, tuple)):
        raw_sources = list(sources)
    else:
        raw_sources = []
        blockers.append("sources:invalid_container")
    if not raw_sources:
        blockers.append("sources:missing")
    for index, raw_source in enumerate(raw_sources):
        try:
            source = _coerce_source(raw_source)
        except (TypeError, ValueError, OverflowError) as exc:
            blockers.append(f"sources:item_{index}:invalid:{str(exc)}")
            continue
        check = source.as_check()
        if source.key in source_keys:
            check["status"] = "blocked"
            check["duplicate"] = True
            blockers.append(f"sources:{source.key}:duplicate")
        source_keys.add(source.key)
        if not check["source_ready"]:
            blockers.append(f"sources:{source.key}:untrusted_or_missing")
        if not check["sample_ready"]:
            blockers.append(f"samples:{source.key}:observed<{source.minimum}")
        source_items.append(check)
    sources_passed = bool(source_items) and all(
        item.get("status") == "ready" for item in source_items
    )
    return source_items, len(raw_sources), sources_passed


def _registry_gate(blockers: list[str]) -> tuple[dict[str, bool], bool]:
    registry_items = {
        REPORT_PRIMARY_MODEL: is_selectable_model(REPORT_PRIMARY_MODEL),
        REPORT_CHALLENGER_MODEL: is_selectable_model(REPORT_CHALLENGER_MODEL),
    }
    for binding, selectable in registry_items.items():
        if not selectable:
            blockers.append(f"model_registry:{binding}:not_selectable")
    return registry_items, all(registry_items.values())


def _runtime_item(
    exact_binding: str,
    *,
    runtime_availability: Mapping[str, Any] | None,
    configured_providers: Mapping[str, Any],
    evidence_map: Mapping[str, Any] | None,
    evidence_as_of: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    """One exact binding: (runtime item, static_gate_reason, readiness_gate_reason, readiness)."""
    provider, model_id = split_binding(exact_binding)
    resolved = resolve_model_binding(
        provider,
        model_id,
        runtime_availability=runtime_availability,
    )
    static_gate_reason = resolved.blocker(
        require_registered=True,
        require_runtime_verified=False,
        require_pricing=True,
    )
    model_readiness = assess_model_readiness(
        resolved,
        configured=bool(configured_providers.get(provider)),
        evidence=(evidence_map or {}).get(exact_binding)
        if isinstance(evidence_map, Mapping)
        else None,
        as_of=evidence_as_of,
    )
    readiness_gate_reason = (
        ""
        if model_readiness["production_ready"]
        else "readiness_not_production_ready"
    )
    gate_reason = static_gate_reason or readiness_gate_reason
    resolved_payload = resolved.to_dict()
    resolved_payload.pop("runtime_availability", None)
    resolved_payload.pop("runtime_evidence_source", None)
    legacy_execution_state = {
        "verified": "operator_allowlisted",
        "unavailable": "operator_blocklisted",
        "not_checked": "not_configured",
    }.get(resolved.runtime_availability, "not_configured")
    item = {
        **resolved_payload,
        "legacy_runtime_execution_gate": legacy_execution_state,
        "legacy_runtime_availability_is_production_evidence": False,
        "readiness": model_readiness,
        "configured": model_readiness["configured"],
        "probed": model_readiness["probed"],
        "evaluated": model_readiness["evaluated"],
        "production_ready": model_readiness["production_ready"],
        "availability": model_readiness["availability"],
        "claim_status": model_readiness["claim_status"],
        "gate_reason": gate_reason or "ready",
        "passed": not gate_reason,
        "runtime_probe_ready": not static_gate_reason,
    }
    return item, static_gate_reason, readiness_gate_reason, model_readiness


def _runtime_gate(
    blockers: list[str],
    *,
    runtime_availability: Mapping[str, Any] | None,
    evidence_map: Mapping[str, Any] | None,
    evidence_as_of: Any,
    allow_runtime_probe: bool,
) -> tuple[dict[str, dict[str, Any]], bool, bool]:
    """Runtime gate over both report bindings: (items, probe_ready, passed)."""
    configured_providers = configured_providers_from_environment()
    runtime_items: dict[str, dict[str, Any]] = {}
    runtime_probe_ready = True
    for exact_binding in (REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL):
        item, static_gate_reason, readiness_gate_reason, model_readiness = _runtime_item(
            exact_binding,
            runtime_availability=runtime_availability,
            configured_providers=configured_providers,
            evidence_map=evidence_map,
            evidence_as_of=evidence_as_of,
        )
        runtime_items[exact_binding] = item
        if static_gate_reason:
            runtime_probe_ready = False
            blockers.append(f"model_runtime:{exact_binding}:{item['gate_reason']}")
        elif readiness_gate_reason and not allow_runtime_probe:
            detail = ",".join(model_readiness["failure_reasons"]) or readiness_gate_reason
            blockers.append(f"model_readiness:{exact_binding}:{detail}")
    runtime_passed = all(bool(item.get("passed")) for item in runtime_items.values())
    return runtime_items, runtime_probe_ready, runtime_passed


def _role_selection(allowed: bool, runtime_passed: bool) -> dict[str, Any]:
    return {
        "mode": ADVANCED_MODEL_MODE if allowed else DETERMINISTIC_DESCRIPTIVE_MODE,
        "claim_level": (
            "validated_analysis"
            if allowed and runtime_passed
            else "runtime_verification_pending"
            if allowed
            else "descriptive_only"
        ),
        "primary_model": REPORT_PRIMARY_MODEL if allowed else None,
        "challenger_model": REPORT_CHALLENGER_MODEL if allowed else None,
        "judge_candidates": REPORT_JUDGE_CANDIDATES if allowed else (),
        "selected_models": (
            (REPORT_PRIMARY_MODEL, REPORT_CHALLENGER_MODEL) if allowed else ()
        ),
    }


def evaluate_report_model_policy(
    data_readiness: DataReadiness | Mapping[str, Any],
    sources: Iterable[ReportSourceSample | Mapping[str, Any]],
    *,
    runtime_availability: Mapping[str, Any] | None = None,
    readiness_evidence: Mapping[str, Any] | None = None,
    evidence_as_of: Any = None,
    allow_runtime_probe: bool = False,
) -> ReportModelDecision:
    """Select report model roles only after every evidence gate passes.

    The function is pure and never invokes a provider. Callers must treat
    ``provider_calls_allowed`` as the mandatory precondition for any high-order
    model call.
    """
    blockers: list[str] = []
    readiness_check, readiness_passed = _data_readiness_gate(data_readiness, blockers)
    source_items, required_count, sources_passed = _sources_gate(sources, blockers)
    registry_items, registry_passed = _registry_gate(blockers)
    environment_evidence, evidence_source = readiness_evidence_from_environment()
    evidence_map = readiness_evidence if readiness_evidence is not None else environment_evidence
    runtime_items, runtime_probe_ready, runtime_passed = _runtime_gate(
        blockers,
        runtime_availability=runtime_availability,
        evidence_map=evidence_map,
        evidence_as_of=evidence_as_of,
        allow_runtime_probe=allow_runtime_probe,
    )

    blocker_tuple = _unique(blockers)
    base_allowed = readiness_passed and sources_passed and registry_passed
    runtime_gate_passed = runtime_passed or (
        bool(allow_runtime_probe) and runtime_probe_ready
    )
    allowed = (
        base_allowed
        and runtime_gate_passed
        and not blocker_tuple
    )
    roles = _role_selection(allowed, runtime_passed)
    return ReportModelDecision(
        mode=roles["mode"],
        provider_calls_allowed=allowed,
        claim_level=roles["claim_level"],
        primary_model=roles["primary_model"],
        challenger_model=roles["challenger_model"],
        judge_candidates=roles["judge_candidates"],
        selected_models=roles["selected_models"],
        blockers=blocker_tuple,
        checks={
            "evaluation_order": [
                "data_readiness",
                "source_provenance",
                "sample_thresholds",
                "model_registry",
                "model_runtime",
                "model_evaluation",
            ],
            "data_readiness": readiness_check,
            "sources": {
                "passed": sources_passed,
                "required_count": required_count,
                "items": source_items,
            },
            "model_registry": {
                "passed": registry_passed,
                "items": registry_items,
            },
            "model_runtime": {
                "passed": runtime_passed,
                "probe_authorized": bool(allow_runtime_probe),
                "probe_ready": runtime_probe_ready,
                "evidence_source": (
                    {"source": "explicit_argument", "parsed": True}
                    if readiness_evidence is not None
                    else evidence_source
                ),
                "legacy_runtime_availability_is_production_evidence": False,
                "items": runtime_items,
            },
        },
    )


__all__ = [
    "ADVANCED_MODEL_MODE",
    "DETERMINISTIC_DESCRIPTIVE_MODE",
    "REPORT_CHALLENGER_MODEL",
    "REPORT_JUDGE_CANDIDATES",
    "REPORT_MODEL_POLICY_VERSION",
    "REPORT_PRIMARY_MODEL",
    "ReportModelDecision",
    "ReportSourceSample",
    "evaluate_report_model_policy",
]
