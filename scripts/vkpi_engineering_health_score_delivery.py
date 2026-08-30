"""Fail-closed validation and merging for delivery-health receipts.

The delivery receipt (``vkpi_delivery_receipt_v1``) is produced by the
delivery evidence collector, which is the only party that computes delivery
numbers.  This module never recomputes values: it verifies format, binding to
the current scoring candidate, and the contract sample floors before
attaching metrics to the evidence payload.  Anything ambiguous is rejected or
downgraded to ``missing_or_insufficient`` — never silently scored.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any


DELIVERY_RECEIPT_SCHEMA_VERSION = "vkpi_delivery_receipt_v1"
# Mirrors contract evidence_policy.delivery_window_days; the receipt must
# declare exactly this window so stale or over-broad ledgers fail closed.
DELIVERY_WINDOW_DAYS = 90
DELIVERY_SOURCE_KEYS = frozenset(
    {"post_deploy_dirs", "incidents_lines", "verify_receipts", "outcome_files"}
)
# canonical_gate_pass_rate is attached exclusively by the canonical-gate
# receipt channel (merge_canonical_receipts in the scorer). The delivery
# collector must never carry it, or the two channels could disagree.
DELIVERY_CANONICAL_CHANNEL_METRICS = frozenset({"canonical_gate_pass_rate"})
DELIVERY_METRIC_STATUSES = frozenset({"observed", "missing_or_insufficient"})
INSUFFICIENT_SAMPLES_REASON = "sample_count_below_contract_minimum_samples"


class DeliveryReceiptError(ValueError):
    """Raised when a delivery receipt cannot be trusted."""


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DeliveryReceiptError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise DeliveryReceiptError(f"{label} must be finite")
    return result


def expected_delivery_metrics(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Contract delivery rules owned by this channel (canonical channel excluded)."""
    dimensions = contract.get("dimensions")
    delivery = dimensions.get("delivery") if isinstance(dimensions, dict) else None
    rules = delivery.get("metrics") if isinstance(delivery, dict) else None
    if not isinstance(rules, dict) or not rules:
        raise DeliveryReceiptError("contract delivery metrics block is required")
    owned = {
        name: rule
        for name, rule in rules.items()
        if name not in DELIVERY_CANONICAL_CHANNEL_METRICS
    }
    if not owned:
        raise DeliveryReceiptError("contract delivery block has no collector-owned metrics")
    return owned


def _validate_candidate(evidence: dict[str, Any], receipt: dict[str, Any]) -> None:
    candidate = receipt.get("candidate") if isinstance(receipt.get("candidate"), dict) else {}
    expected_head = str((evidence.get("candidate") or {}).get("head") or "")
    if not expected_head:
        raise DeliveryReceiptError("scoring candidate head is required for delivery binding")
    if candidate.get("head") != expected_head:
        raise DeliveryReceiptError("delivery receipt head mismatch")
    if not isinstance(candidate.get("worktree_dirty"), bool):
        raise DeliveryReceiptError("delivery receipt worktree_dirty must be boolean")


def _validate_window(receipt: dict[str, Any]) -> dict[str, Any]:
    window = receipt.get("window") if isinstance(receipt.get("window"), dict) else {}
    days = window.get("days")
    if isinstance(days, bool) or days != DELIVERY_WINDOW_DAYS:
        raise DeliveryReceiptError(
            f"delivery receipt must declare the contract {DELIVERY_WINDOW_DAYS}-day window"
        )
    for field in ("start", "end"):
        if not str(window.get(field) or "").strip():
            raise DeliveryReceiptError(f"delivery receipt window {field} is required")
    covered = _finite_number(
        window.get("ledger_covered_days"), label="delivery window ledger_covered_days"
    )
    if covered < 0 or covered > DELIVERY_WINDOW_DAYS:
        raise DeliveryReceiptError("delivery window ledger_covered_days out of range")
    return window


def _validate_sources(receipt: dict[str, Any]) -> None:
    sources = receipt.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(DELIVERY_SOURCE_KEYS):
        raise DeliveryReceiptError(
            "delivery receipt sources must list exactly the four canonical source kinds"
        )


def _missing_entry(name: str, entry: dict[str, Any], reason: Any) -> dict[str, Any]:
    value = entry.get("value")
    if value is not None:
        # Even unscored numbers must be sane; a non-finite value means the
        # collector is broken and nothing in the receipt can be trusted.
        _finite_number(value, label=f"delivery metric {name}.value")
    sample_count = entry.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int):
        sample_count = None
    return {
        "status": "missing_or_insufficient",
        "value": None,
        "sample_count": sample_count,
        "reason": str(reason or "not_observed"),
    }


def _validate_entry(name: str, entry: Any, rule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise DeliveryReceiptError(f"delivery metric {name} must be an object")
    status = entry.get("status")
    if status not in DELIVERY_METRIC_STATUSES:
        raise DeliveryReceiptError(f"delivery metric {name} has unsupported status")
    reason = entry.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise DeliveryReceiptError(f"delivery metric {name} reason must be a string")
    if status != "observed":
        return _missing_entry(name, entry, reason)
    value = _finite_number(entry.get("value"), label=f"delivery metric {name}.value")
    sample_count = entry.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 1:
        raise DeliveryReceiptError(
            f"delivery metric {name} sample_count must be a positive integer"
        )
    minimum = rule.get("minimum_samples")
    if minimum is not None and sample_count < _finite_number(
        minimum, label=f"contract delivery {name}.minimum_samples"
    ):
        # Fail closed: an under-sampled observation is downgraded here so it
        # can never reach the scorer as scoreable evidence. The measured value
        # is kept for transparency but carries no score weight downstream.
        return {
            "status": "missing_or_insufficient",
            "value": value,
            "sample_count": sample_count,
            "reason": INSUFFICIENT_SAMPLES_REASON,
        }
    result: dict[str, Any] = {
        "status": "observed",
        "value": value,
        "sample_count": sample_count,
    }
    if reason:
        result["reason"] = reason
    return result


def validate_delivery_receipt(
    contract: dict[str, Any], evidence: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Validate format and binding; return merge-ready entries keyed by metric."""
    if not isinstance(receipt, dict) or receipt.get("schema_version") != DELIVERY_RECEIPT_SCHEMA_VERSION:
        raise DeliveryReceiptError("unsupported delivery receipt schema")
    _validate_candidate(evidence, receipt)
    _validate_window(receipt)
    _validate_sources(receipt)
    rules = expected_delivery_metrics(contract)
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(rules):
        raise DeliveryReceiptError(
            "delivery receipt metric names must match the contract delivery block"
        )
    return {name: _validate_entry(name, metrics[name], rules[name]) for name in rules}


def merge_delivery_receipt(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> None:
    """Attach only format-valid, HEAD-bound delivery metrics to the evidence."""
    validated = validate_delivery_receipt(contract, evidence, receipt)
    observed_at = str(receipt["window"].get("end") or "")
    source = f"receipt://{receipt_path.resolve()}"
    destination = evidence.setdefault("metrics", {}).setdefault("delivery", {})
    for name, entry in validated.items():
        destination[name] = {**entry, "source": source, "observed_at": observed_at}
