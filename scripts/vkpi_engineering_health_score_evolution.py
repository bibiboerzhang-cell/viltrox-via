"""Fail-closed validation and merging for evolution-health receipts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


EVOLUTION_ALGORITHM_VERSION = "vkpi-evolution-git-v4"
EVOLUTION_QUALIFICATION_SCHEMA_VERSION = "vkpi_maintainer_qualification_receipt_v1"
EVOLUTION_BUS_FACTOR_SHARE = 0.50
EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE = 0.90
EVOLUTION_QUALIFICATION_POLICY = {
    "minimum_merged_pr_references": 3,
    "minimum_independent_review_references": 2,
    "minimum_operational_evidence_references": 1,
    "minimum_qualified_changed_line_share_per_domain": (
        EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE
    ),
}
EVOLUTION_CORE_DOMAINS = {
    "authentication", "tenant_boundary", "kol", "database_migrations",
    "workers", "frontend_delivery",
}
EXPECTED_SAMPLES = {
    "core_domain_bus_factor_min": (180, "days"),
    "temporal_coupling_p95": (180, "days"),
    "qualified_maintainer_domain_ratio": (len(EVOLUTION_CORE_DOMAINS), "core_domains"),
}
ALLOWED_COMPLETE_UNKNOWN_REASONS = {
    "author_identity_ambiguity_requires_mailmap",
    "identity_mailmap_not_committed",
    "maintainer_qualification_evidence_missing",
    "maintainer_qualification_incomplete",
    "insufficient_qualified_pairs",
    "metric_not_computable",
}


class EvolutionReceiptError(ValueError):
    """Raised when an evolution receipt cannot be trusted."""


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvolutionReceiptError(f"{label} must be numeric")
    return float(value)


def _receipt_context(
    evidence: dict[str, Any], receipt_path: Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    if receipt.get("schema_version") != "vkpi_engineering_health_evolution_receipt_v1":
        raise EvolutionReceiptError("unsupported evolution receipt schema")
    if receipt.get("algorithm_version") != EVOLUTION_ALGORITHM_VERSION:
        raise EvolutionReceiptError("unsupported evolution receipt algorithm")
    candidate = receipt.get("candidate") if isinstance(receipt.get("candidate"), dict) else {}
    expected_head = str((evidence.get("candidate") or {}).get("head") or "")
    if not expected_head or candidate.get("head") != expected_head:
        raise EvolutionReceiptError("evolution receipt head mismatch")
    if candidate.get("worktree_is_input") is not False:
        raise EvolutionReceiptError("evolution receipt must exclude the working tree")
    history = receipt.get("history") if isinstance(receipt.get("history"), dict) else {}
    if history.get("identity_mailmap_source") != "HEAD:.mailmap":
        raise EvolutionReceiptError("evolution receipt must bind identity aliases to HEAD:.mailmap")
    if history.get("working_tree_mailmap_ignored") is not True:
        raise EvolutionReceiptError("evolution receipt must ignore working-tree mailmap bytes")
    window = receipt.get("window") if isinstance(receipt.get("window"), dict) else {}
    required_days = window.get("required_days")
    if isinstance(required_days, bool) or required_days != 180:
        raise EvolutionReceiptError("evolution receipt must use the contract 180-day window")
    details = receipt.get("details") if isinstance(receipt.get("details"), dict) else {}
    return {
        "expected_head": expected_head, "history": history, "window": window,
        "metrics": receipt.get("metrics") if isinstance(receipt.get("metrics"), dict) else {},
        "destination": evidence.setdefault("metrics", {}).setdefault("evolution", {}),
        "source": f"receipt://{receipt_path.resolve()}",
        "complete": window.get("complete") is True and float(window.get("covered_days") or 0) >= 180,
        "bus_details": details.get("bus_factor") if isinstance(details.get("bus_factor"), dict) else {},
        "qualification": (
            receipt.get("maintainer_qualification")
            if isinstance(receipt.get("maintainer_qualification"), dict) else {}
        ),
    }


def _qualification_maps(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    history = context["history"]
    qualification = context["qualification"]
    if history.get("identity_mailmap_committed") is not True:
        raise EvolutionReceiptError("observed bus factor requires committed HEAD mailmap")
    entries = history.get("identity_mailmap_entry_count")
    if not isinstance(entries, int) or entries < 1:
        raise EvolutionReceiptError("observed bus factor requires reviewed HEAD mailmap entries")
    if history.get("identity_quality_complete") is not True:
        raise EvolutionReceiptError("observed bus factor requires complete identity quality")
    if (
        qualification.get("status") != "observed"
        or qualification.get("candidate_head") != context["expected_head"]
    ):
        raise EvolutionReceiptError("observed bus factor requires HEAD-bound maintainer qualification")
    if qualification.get("schema_version") != EVOLUTION_QUALIFICATION_SCHEMA_VERSION:
        raise EvolutionReceiptError("unsupported maintainer qualification receipt schema")
    if not str(qualification.get("source") or "").strip():
        raise EvolutionReceiptError("maintainer qualification receipt source is required")
    if not str(qualification.get("generated_at") or "").strip():
        raise EvolutionReceiptError("maintainer qualification receipt generated_at is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(qualification.get("sha256") or "")):
        raise EvolutionReceiptError("maintainer qualification receipt hash is required")
    if qualification.get("policy") != EVOLUTION_QUALIFICATION_POLICY:
        raise EvolutionReceiptError("maintainer qualification policy mismatch")
    identities = qualification.get("qualified_identities_by_domain")
    counts = qualification.get("qualified_people_by_domain")
    if not isinstance(identities, dict) or set(identities) != EVOLUTION_CORE_DOMAINS:
        raise EvolutionReceiptError("qualified identities must cover every canonical core domain")
    if not isinstance(counts, dict) or set(counts) != EVOLUTION_CORE_DOMAINS:
        raise EvolutionReceiptError("qualified person counts must cover every canonical core domain")
    return identities, counts


def _canonical_identities(identities: Any) -> list[str]:
    if not isinstance(identities, list):
        raise EvolutionReceiptError("qualified identities must be lists")
    normalized = [str(identity).strip().casefold() for identity in identities]
    invalid = any("@" not in identity for identity in normalized)
    if invalid or normalized != identities or len(set(normalized)) != len(normalized):
        raise EvolutionReceiptError("qualified identities must be unique canonical emails")
    return normalized


def _author_changed(item: dict[str, Any]) -> dict[str, float]:
    authors = item.get("authors")
    if not isinstance(authors, list):
        raise EvolutionReceiptError("domain authors must be a list")
    changed: dict[str, float] = {}
    for author in authors:
        if not isinstance(author, dict):
            raise EvolutionReceiptError("domain author must be an object")
        identity = str(author.get("identity") or "").strip().casefold()
        if not identity or identity in changed:
            raise EvolutionReceiptError("domain author identities must be unique")
        changed[identity] = _number(author.get("changed"), label="domain author changed lines")
    return changed


def _qualified_rows(
    item: dict[str, Any], identities: list[str]
) -> tuple[list[tuple[str, float]], float]:
    author_changed = _author_changed(item)
    if not set(identities).issubset(author_changed):
        raise EvolutionReceiptError("qualified identity has no domain contribution")
    rows = sorted(
        ((identity, author_changed[identity]) for identity in identities),
        key=lambda row: (-row[1], row[0]),
    )
    qualified_changed = sum(changed for _, changed in rows)
    domain_changed = _number(
        item.get("changed_lines_including_shared_accounts"),
        label="domain changed lines including shared accounts",
    )
    if domain_changed <= 0 or qualified_changed > domain_changed:
        raise EvolutionReceiptError("invalid qualified changed-line denominator")
    recorded = _number(item.get("qualified_changed_lines"), label="qualified changed lines")
    if abs(recorded - qualified_changed) > 1e-9:
        raise EvolutionReceiptError("qualified changed-line total mismatch")
    expected_share = qualified_changed / domain_changed
    share = _number(item.get("qualified_changed_line_share"), label="qualified changed-line share")
    if abs(share - expected_share) > 1e-9 or expected_share + 1e-12 < EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE:
        raise EvolutionReceiptError("qualified changed-line coverage is insufficient")
    return rows, domain_changed


def _recomputed_factor(rows: list[tuple[str, float]], domain_changed: float) -> int | None:
    cumulative = 0.0
    for index, (_, changed) in enumerate(rows, start=1):
        cumulative += changed
        if cumulative + 1e-12 >= domain_changed * EVOLUTION_BUS_FACTOR_SHARE:
            return index
    return None


def _validate_contributor_set(
    item: dict[str, Any], rows: list[tuple[str, float]], expected_factor: int
) -> None:
    contributor_set = item.get("qualified_minimum_contributor_set")
    expected = [identity for identity, _ in rows[:expected_factor]]
    if not isinstance(contributor_set, list) or not all(
        isinstance(person, dict) for person in contributor_set
    ):
        raise EvolutionReceiptError("qualified minimum contributor set mismatch")
    recorded = [
        str(person.get("identity") or "").strip().casefold() for person in contributor_set
    ]
    if recorded != expected:
        raise EvolutionReceiptError("qualified minimum contributor set mismatch")


def _validate_domain(
    item: dict[str, Any], raw_identities: Any, expected_count: Any
) -> float:
    identities = _canonical_identities(raw_identities)
    if expected_count != len(identities):
        raise EvolutionReceiptError("qualified person count mismatch")
    if item.get("qualified_author_count") != len(identities):
        raise EvolutionReceiptError("qualified author count mismatch")
    rows, domain_changed = _qualified_rows(item, identities)
    expected_factor = _recomputed_factor(rows, domain_changed)
    factor = _number(item.get("qualified_bus_factor"), label="qualified bus factor")
    if expected_factor is None or factor != expected_factor:
        raise EvolutionReceiptError("qualified bus factor failed independent recomputation")
    _validate_contributor_set(item, rows, expected_factor)
    return factor


def _validate_bus_summary(context: dict[str, Any], factors: list[float]) -> None:
    details = context["bus_details"]
    metrics = context["metrics"]
    bus_entry = metrics["core_domain_bus_factor_min"]
    if not factors or abs(min(factors) - _number(bus_entry.get("value"), label="bus factor")) > 1e-9:
        raise EvolutionReceiptError("observed bus factor does not match qualified domain minimum")
    ratio_entry = metrics.get("qualified_maintainer_domain_ratio")
    ratio_valid = (
        isinstance(ratio_entry, dict) and ratio_entry.get("status") == "observed"
        and _number(ratio_entry.get("value"), label="qualified domain ratio") == 1.0
        and _number(
            details.get("qualified_maintainer_domain_ratio"),
            label="qualified bus details domain ratio",
        ) == 1.0
        and details.get("qualification_ready_domain_count") == len(EVOLUTION_CORE_DOMAINS)
    )
    if not ratio_valid:
        raise EvolutionReceiptError("observed bus factor requires complete qualified domain ratio")


def _validate_observed_bus(context: dict[str, Any]) -> None:
    metrics = context["metrics"]
    bus_entry = metrics.get("core_domain_bus_factor_min")
    if not isinstance(bus_entry, dict) or bus_entry.get("status") != "observed":
        return
    details = context["bus_details"]
    domains = details.get("domains") if isinstance(details, dict) else None
    if not isinstance(domains, dict) or set(domains) != EVOLUTION_CORE_DOMAINS:
        raise EvolutionReceiptError("observed bus factor requires every canonical core domain")
    if any(not isinstance(item, dict) or item.get("qualification_ready") is not True for item in domains.values()):
        raise EvolutionReceiptError("observed bus factor requires complete per-domain qualification")
    policy_share = _number(
        details.get("minimum_qualified_changed_line_share_per_domain"),
        label="minimum qualified changed-line share",
    )
    if policy_share != EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE:
        raise EvolutionReceiptError("qualified changed-line policy mismatch")
    identities, counts = _qualification_maps(context)
    factors = [
        _validate_domain(item, identities[domain], counts[domain])
        for domain, item in domains.items()
    ]
    _validate_bus_summary(context, factors)


def _merge_metrics(context: dict[str, Any]) -> None:
    metrics = context["metrics"]
    for name, (expected_count, expected_unit) in EXPECTED_SAMPLES.items():
        entry = metrics.get(name) if isinstance(metrics.get(name), dict) else {}
        observed = entry.get("status") == "observed"
        if observed and not context["complete"]:
            raise EvolutionReceiptError(f"evolution receipt {name} observed before 180 days")
        if observed:
            if entry.get("sample_count") != expected_count or entry.get("sample_unit") != expected_unit:
                raise EvolutionReceiptError(f"evolution receipt {name} has invalid samples")
            _number(entry.get("value"), label=f"evolution receipt {name}.value")
            context["destination"][name] = {**entry, "source": context["source"]}
            continue
        reason = str(entry.get("reason") or "")
        if context["complete"] and reason not in ALLOWED_COMPLETE_UNKNOWN_REASONS:
            raise EvolutionReceiptError(f"evolution receipt {name} unknown reason missing")
        context["destination"][name] = {
            "status": "unknown", "value": None, "source": context["source"],
            "observed_at": entry.get("observed_at") or "",
            "sample_count": entry.get("sample_count"),
            "sample_unit": entry.get("sample_unit") or expected_unit,
            "reason": reason or "history_window_incomplete",
        }


def merge_evolution_receipt(
    evidence: dict[str, Any], receipt_path: Path, receipt: dict[str, Any]
) -> None:
    context = _receipt_context(evidence, receipt_path, receipt)
    _validate_observed_bus(context)
    _merge_metrics(context)
