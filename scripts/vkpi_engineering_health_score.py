#!/usr/bin/env python3
"""Score a V-KPI engineering-health evidence receipt against contract v1.

The scorer never invents neutral values for missing evidence. A numerical
formal score is emitted only when overall and per-dimension evidence coverage
meet the contract and every structural hard gate is observable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from scripts import vkpi_engineering_health_coverage as coverage_evidence
    from scripts import vkpi_engineering_health_score_delivery as delivery_receipt_channel
    from scripts import vkpi_engineering_health_score_evolution as evolution_receipt
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import vkpi_engineering_health_coverage as coverage_evidence
    import vkpi_engineering_health_score_delivery as delivery_receipt_channel
    import vkpi_engineering_health_score_evolution as evolution_receipt
    from stdout_utils import out as stdout_out


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"
MIN_CANONICAL_RECEIPTS = 3
EVOLUTION_ALGORITHM_VERSION = evolution_receipt.EVOLUTION_ALGORITHM_VERSION
EVOLUTION_QUALIFICATION_SCHEMA_VERSION = (
    evolution_receipt.EVOLUTION_QUALIFICATION_SCHEMA_VERSION
)
EVOLUTION_BUS_FACTOR_SHARE = evolution_receipt.EVOLUTION_BUS_FACTOR_SHARE
EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE = (
    evolution_receipt.EVOLUTION_QUALIFIED_CONTRIBUTION_SHARE
)
EVOLUTION_QUALIFICATION_POLICY = evolution_receipt.EVOLUTION_QUALIFICATION_POLICY
EVOLUTION_CORE_DOMAINS = evolution_receipt.EVOLUTION_CORE_DOMAINS


class ContractError(ValueError):
    """Raised when a contract or evidence receipt is internally inconsistent."""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"top-level JSON object required: {path}")
    return payload


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{label} must be finite")
    return result


def validate_contract(contract: dict[str, Any]) -> None:
    if contract.get("schema_version") != "vkpi_engineering_health_contract_v1":
        raise ContractError("unsupported engineering-health contract schema")
    dimensions = contract.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != {"code", "architecture", "evolution", "delivery"}:
        raise ContractError("contract must define the four canonical dimensions")
    for dimension_name, dimension in dimensions.items():
        if not isinstance(dimension, dict) or not isinstance(dimension.get("metrics"), dict):
            raise ContractError(f"{dimension_name} metrics must be an object")
        weight_sum = sum(
            _number(metric.get("weight"), label=f"{dimension_name}.{metric_name}.weight")
            for metric_name, metric in dimension["metrics"].items()
            if isinstance(metric, dict)
        )
        if abs(weight_sum - 1.0) > 1e-9:
            raise ContractError(f"{dimension_name} metric weights must sum to 1, got {weight_sum}")
        for metric_name, metric in dimension["metrics"].items():
            if not isinstance(metric, dict) or metric.get("direction") not in {"min", "max"}:
                raise ContractError(f"invalid direction for {dimension_name}.{metric_name}")
            _number(metric.get("target"), label=f"{dimension_name}.{metric_name}.target")
            bound = "floor" if metric["direction"] == "min" else "ceiling"
            _number(metric.get(bound), label=f"{dimension_name}.{metric_name}.{bound}")


def contract_sha256(contract: dict[str, Any]) -> str:
    """Bind evidence to semantic contract content, independent of whitespace."""
    validate_contract(contract)
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _metric_score(value: float, rule: dict[str, Any]) -> float:
    target = _number(rule.get("target"), label="target")
    if rule.get("direction") == "min":
        floor = _number(rule.get("floor"), label="floor")
        if floor >= target:
            raise ContractError("minimum metric floor must be less than target")
        if value >= target:
            return 100.0
        if value <= floor:
            return 0.0
        return 100.0 * (value - floor) / (target - floor)
    ceiling = _number(rule.get("ceiling"), label="ceiling")
    if ceiling <= target:
        raise ContractError("maximum metric ceiling must be greater than target")
    if value <= target:
        return 100.0
    if value >= ceiling:
        return 0.0
    return 100.0 * (ceiling - value) / (ceiling - target)


def _observed_metric(entry: Any, rule: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(entry, dict) or entry.get("status") != "observed":
        return None
    for field in ("value", "source", "observed_at"):
        if entry.get(field) in {None, ""}:
            return None
    minimum_samples = rule.get("minimum_samples")
    if minimum_samples is not None:
        sample_count = entry.get("sample_count")
        if isinstance(sample_count, bool) or not isinstance(sample_count, (int, float)):
            return None
        if float(sample_count) < float(minimum_samples):
            return None
    raw_value = entry["value"]
    if (
        not isinstance(raw_value, bool)
        and isinstance(raw_value, (int, float))
        and not math.isfinite(float(raw_value))
    ):
        # -inf/+inf/nan evidence must never score (e.g. -inf on a "max"
        # metric would otherwise take the value<=target branch to 100).
        return None
    value = _number(raw_value, label="evidence value")
    return value, round(_metric_score(value, rule), 4)


def _lookup_metric(evidence: dict[str, Any], reference: str) -> Any:
    try:
        dimension_name, metric_name = reference.split(".", 1)
    except ValueError as exc:
        raise ContractError(f"invalid metric reference: {reference}") from exc
    metrics = evidence.get("metrics")
    dimension = metrics.get(dimension_name) if isinstance(metrics, dict) else None
    return dimension.get(metric_name) if isinstance(dimension, dict) else None


def _evaluate_gate(entry: Any, *, operator: str, threshold: float) -> dict[str, Any]:
    if not isinstance(entry, dict) or entry.get("status") != "observed":
        return {"status": "unknown", "passed": None}
    if entry.get("source") in {None, ""} or entry.get("observed_at") in {None, ""}:
        return {"status": "unknown", "passed": None}
    value = _number(entry.get("value"), label="gate evidence value")
    if operator == "le":
        passed = value <= threshold
    elif operator == "ge":
        passed = value >= threshold
    else:
        raise ContractError(f"unsupported hard-gate operator: {operator}")
    return {"status": "observed", "passed": passed, "value": value, "threshold": threshold, "operator": operator}


def _release_gate(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict) or entry.get("status") != "observed":
        return {"status": "unknown", "passed": None}
    if entry.get("source") in {None, ""} or entry.get("observed_at") in {None, ""}:
        return {"status": "unknown", "passed": None}
    if not isinstance(entry.get("value"), bool):
        raise ContractError("release-gate value must be boolean")
    return {"status": "observed", "passed": bool(entry["value"])}


def _score_dimensions(
    contract: dict[str, Any], evidence: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    exclude_missing = bool(
        (contract.get("evidence_policy") or {}).get("exclude_missing_from_denominator")
    )
    results: dict[str, Any] = {}
    total_coverage = 0.0
    for dimension_name, dimension in contract["dimensions"].items():
        evidence_metrics = (evidence.get("metrics") or {}).get(dimension_name) or {}
        weighted_score = 0.0
        observed_weight = 0.0
        metric_results: dict[str, Any] = {}
        for metric_name, rule in dimension["metrics"].items():
            weight = float(rule["weight"])
            observed = _observed_metric(evidence_metrics.get(metric_name), rule)
            if observed is None:
                metric_results[metric_name] = {
                    "status": "missing_or_insufficient", "weight": weight, "score": None,
                }
                continue
            value, metric_score = observed
            observed_weight += weight
            weighted_score += weight * metric_score
            metric_results[metric_name] = {
                "status": "observed", "weight": weight,
                "value": value, "score": metric_score,
            }
        coverage = round(observed_weight, 6)
        total_coverage += coverage / len(contract["dimensions"])
        denominator = observed_weight if exclude_missing else 1.0
        results[dimension_name] = {
            "target": float(dimension["target"]), "evidence_coverage": coverage,
            "scoring_denominator": round(denominator, 6),
            "observed_score": (
                round(weighted_score / denominator, 4) if observed_weight else None
            ),
            "metrics": metric_results,
        }
    return results, total_coverage


def _hard_gate_summary(
    contract: dict[str, Any], evidence: dict[str, Any]
) -> tuple[dict[str, Any], bool, bool]:
    results = {
        name: _evaluate_gate(
            _lookup_metric(evidence, str(rule["metric"])),
            operator=str(rule["operator"]), threshold=float(rule["threshold"]),
        )
        for name, rule in contract["hard_gates"].items()
    }
    all_observed = all(item["status"] == "observed" for item in results.values())
    all_pass = all_observed and all(bool(item["passed"]) for item in results.values())
    any_failed = any(
        item["status"] == "observed" and item["passed"] is False
        for item in results.values()
    )
    return results, all_pass, any_failed


def _formal_scores(
    contract: dict[str, Any], dimensions: dict[str, Any], total_coverage: float,
    *, contract_bound: bool, hard_gates_pass: bool, hard_gate_failed: bool,
) -> tuple[float | None, float | None, float | None]:
    minimum_overall = float(contract["minimum_evidence_coverage"])
    minimum_dimension = float(contract["minimum_dimension_evidence_coverage"])
    enough = (
        contract_bound and total_coverage + 1e-9 >= minimum_overall
        and all(
            item["evidence_coverage"] + 1e-9 >= minimum_dimension
            for item in dimensions.values()
        )
    )
    dimension_scores = [item["observed_score"] for item in dimensions.values()]
    raw_score = (
        round(sum(float(item) for item in dimension_scores) / len(dimension_scores), 4)
        if all(item is not None for item in dimension_scores) else None
    )
    grade_cap = (
        float(contract["grade_cap_on_hard_gate_failure"])
        if hard_gate_failed else 100.0 if hard_gates_pass else None
    )
    formal = (
        round(min(float(raw_score), grade_cap), 4)
        if enough and grade_cap is not None and raw_score is not None else None
    )
    return raw_score, grade_cap, formal


def _release_gate_summary(
    contract: dict[str, Any], evidence: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    source = evidence.get("release_gates")
    release_evidence = source if isinstance(source, dict) else {}
    results = {
        name: _release_gate(release_evidence.get(name))
        for name in contract["release_gates"]
    }
    observed = all(item["status"] == "observed" for item in results.values())
    return results, observed and all(bool(item["passed"]) for item in results.values())


def score_evidence(contract: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    validate_contract(contract)
    expected_hash = contract_sha256(contract)
    evidence_hash = evidence.get("contract_sha256")
    if evidence_hash not in {None, ""} and evidence_hash != expected_hash:
        raise ContractError("engineering-health evidence contract_sha256 mismatch")
    contract_bound = evidence_hash == expected_hash
    dimensions, total_coverage = _score_dimensions(contract, evidence)
    hard_gates, hard_pass, hard_failed = _hard_gate_summary(contract, evidence)
    raw_score, grade_cap, formal_score = _formal_scores(
        contract, dimensions, total_coverage, contract_bound=contract_bound,
        hard_gates_pass=hard_pass, hard_gate_failed=hard_failed,
    )
    release_gates, release_pass = _release_gate_summary(contract, evidence)
    release_eligible = bool(
        formal_score is not None and formal_score >= float(contract["minimum_floor"])
        and hard_pass and release_pass
    )
    target_achieved = bool(
        formal_score is not None and formal_score >= float(contract["formal_target"])
        and all(
            item["observed_score"] is not None
            and item["observed_score"] >= item["target"] for item in dimensions.values()
        )
        and hard_pass and release_pass
    )
    return {
        "schema_version": "vkpi_engineering_health_score_v1",
        "contract_schema_version": contract["schema_version"],
        "contract_sha256": expected_hash,
        "contract_binding_status": "observed" if contract_bound else "missing",
        "evidence_schema_version": evidence.get("schema_version"),
        "candidate": evidence.get("candidate") or {},
        "status": "formal" if formal_score is not None else "provisional",
        "evidence_coverage": round(total_coverage, 6), "raw_score": raw_score,
        "grade_cap": grade_cap, "formal_score": formal_score,
        "minimum_floor": float(contract["minimum_floor"]),
        "formal_target": float(contract["formal_target"]), "dimensions": dimensions,
        "hard_gates": hard_gates, "release_gates": release_gates,
        "release_eligible": release_eligible, "target_achieved": target_achieved,
        "generated_at": _utcnow(),
    }


def _git(root: Path, *args: str) -> str:
    try:
        output = subprocess.check_output(
            ["git", *args], cwd=root, stderr=subprocess.PIPE
        )
        return output.decode("utf-8", "strict").strip()
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        raise ContractError(f"git command failed: {' '.join(args)}") from exc


def evidence_template(contract: dict[str, Any], *, root: Path = ROOT) -> dict[str, Any]:
    validate_contract(contract)
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    status_lines = [line for line in status.splitlines() if line]
    tracked_changes = sum(1 for line in status_lines if not line.startswith("??"))
    untracked_changes = sum(1 for line in status_lines if line.startswith("??"))
    missing = {"status": "missing", "value": None, "source": "", "observed_at": ""}
    return {
        "schema_version": "vkpi_engineering_health_evidence_v1",
        "contract_sha256": contract_sha256(contract),
        "contract_hash_algorithm": "sha256:canonical-json-sort-keys",
        "generated_at": _utcnow(),
        "candidate": {
            "repo": str(root.resolve()),
            "branch": _git(root, "branch", "--show-current"),
            "head": _git(root, "rev-parse", "HEAD"),
            "clean_worktree": not status_lines,
            "tracked_change_count": tracked_changes,
            "untracked_change_count": untracked_changes,
            "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        },
        "metrics": {
            dimension_name: {metric_name: dict(missing) for metric_name in dimension["metrics"]}
            for dimension_name, dimension in contract["dimensions"].items()
        },
        "release_gates": {gate_name: dict(missing) for gate_name in contract["release_gates"]},
        "notes": ["Missing evidence is intentionally not replaced with a neutral score."],
    }


def capture_static_metrics(evidence: dict[str, Any], *, root: Path = ROOT) -> None:
    """Collect only metrics already covered by a reviewed repository scanner."""
    command = [
        sys.executable,
        str(root / "scripts/check_line_guard.py"),
        "backend/app",
        "frontend/src",
        "scripts",
        "--limit",
        "0",
        "--no-tests",
        "--json",
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ContractError(f"line guard static capture failed: {completed.stderr.strip()[:240]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("line guard static capture returned invalid JSON") from exc
    rows = payload.get("violations") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ContractError("line guard static capture returned no production source rows")
    largest = max(rows, key=lambda item: int(item.get("lines") or 0))
    timestamp = _utcnow()
    architecture = evidence.setdefault("metrics", {}).setdefault("architecture", {})
    architecture["module_loc_max"] = {
        "status": "observed",
        "value": int(largest.get("lines") or 0),
        "source": "command://scripts/check_line_guard.py --limit 0 --no-tests",
        "observed_at": timestamp,
        "sample_count": len(rows),
        "details": {
            "largest_path": str(largest.get("path") or ""),
            "production_source_file_count": len(rows),
            "roots": ["backend/app", "frontend/src", "scripts"],
        },
    }


def _canonical_expectations(evidence: dict[str, Any]) -> tuple[str, str, str, str]:
    candidate = evidence.get("candidate") or {}
    expected = (
        str(candidate.get("head") or ""), str(candidate.get("branch") or ""),
        str(candidate.get("status_sha256") or ""),
        str(candidate.get("source_content_sha256") or ""),
    )
    for label, value in (("status_sha256", expected[2]), ("source_content_sha256", expected[3])):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ContractError(f"evidence candidate {label} is required for canonical binding")
    if not expected[1]:
        raise ContractError("evidence candidate branch is required for canonical binding")
    return expected


def _canonical_fact(
    path: Path, receipt: dict[str, Any], expected: tuple[str, str, str, str]
) -> dict[str, Any]:
    expected_head, expected_branch, expected_status, expected_source = expected
    if receipt.get("schema_version") != "vkpi_canonical_gate_receipt_v1":
        raise ContractError(f"unsupported canonical receipt schema: {path}")
    candidate = receipt.get("candidate") if isinstance(receipt.get("candidate"), dict) else {}
    receipt_head = str(candidate.get("git_head") or "")
    if not expected_head or receipt_head != expected_head:
        raise ContractError(f"canonical receipt head mismatch: {path}")
    if str(candidate.get("branch") or "") != expected_branch:
        raise ContractError(f"canonical receipt branch mismatch: {path}")
    if str(candidate.get("status_sha256") or "") != expected_status:
        raise ContractError(f"canonical receipt worktree status mismatch: {path}")
    if str(candidate.get("source_content_sha256") or "") != expected_source:
        raise ContractError(f"canonical receipt source content mismatch: {path}")
    release_head = str(candidate.get("release_head") or "")
    return {
        "source": str(path.resolve()), "observed_at": str(receipt.get("generated_at") or ""),
        "passed": receipt.get("passed") is True,
        "provenance": (
            candidate.get("clean_worktree") is True and bool(release_head)
            and release_head == receipt_head
        ),
    }


def _attach_canonical_metrics(
    evidence: dict[str, Any], facts: list[dict[str, Any]]
) -> None:
    source = ",".join(fact["source"] for fact in facts)
    timestamp = max(fact["observed_at"] for fact in facts)
    enough = len(facts) >= MIN_CANONICAL_RECEIPTS
    passes = [fact["passed"] for fact in facts]
    provenance = [fact["provenance"] for fact in facts]
    common = {"source": source, "observed_at": timestamp, "sample_count": len(facts)}
    release_gates = evidence.setdefault("release_gates", {})
    release_gates["canonical_gate_pass"] = {
        "status": "observed", "value": enough and all(passes), **common,
    }
    release_gates["artifact_provenance_pass"] = {
        "status": "observed", "value": enough and all(provenance), **common,
    }
    delivery = evidence.setdefault("metrics", {}).setdefault("delivery", {})
    delivery["canonical_gate_pass_rate"] = {
        "status": "observed", "value": sum(passes) / len(passes), **common,
    }


def merge_canonical_receipts(
    evidence: dict[str, Any], receipts: list[tuple[Path, dict[str, Any]]]
) -> None:
    """Attach canonical-gate facts without treating one run as a three-run rate."""
    if not receipts:
        return
    expected = _canonical_expectations(evidence)
    facts: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_timestamps: set[str] = set()
    for path, receipt in receipts:
        fact = _canonical_fact(path, receipt, expected)
        if fact["source"] in seen_sources:
            raise ContractError(f"duplicate canonical receipt source: {path}")
        if fact["observed_at"] and fact["observed_at"] in seen_timestamps:
            raise ContractError(f"duplicate canonical receipt generated_at: {path}")
        seen_sources.add(fact["source"])
        seen_timestamps.add(fact["observed_at"])
        facts.append(fact)
    if any(not fact["observed_at"] for fact in facts):
        raise ContractError("canonical receipt generated_at is required")
    _attach_canonical_metrics(evidence, facts)


def merge_coverage_receipt(
    evidence: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> None:
    """Attach branch/line coverage only after the fresh receipt validates."""

    try:
        observed = coverage_evidence.validate_coverage_receipt(evidence, receipt)
    except coverage_evidence.CoverageReceiptError as exc:
        raise ContractError(f"coverage receipt rejected: {exc}") from exc
    source = f"receipt://{receipt_path.resolve()}"
    common_details = {
        "schema_version": receipt.get("schema_version"),
        "command": observed["command"],
        "command_sha256": observed["command_sha256"],
        "coverage_data_sha256": observed["coverage_data_sha256"],
        "coverage_json_sha256": observed["coverage_json_sha256"],
        "measured_file_count": observed["measured_file_count"],
        "measured_files_sha256": observed["measured_files_sha256"],
    }
    code = evidence.setdefault("metrics", {}).setdefault("code", {})
    code["branch_coverage"] = {
        "status": "observed",
        "value": observed["branch_coverage"],
        "source": source,
        "observed_at": observed["observed_at"],
        "sample_count": observed["num_branches"],
        "details": {
            **common_details,
            "covered_branches": observed["covered_branches"],
            "num_branches": observed["num_branches"],
        },
    }
    code["line_coverage"] = {
        "status": "observed",
        "value": observed["line_coverage"],
        "source": source,
        "observed_at": observed["observed_at"],
        "sample_count": observed["num_statements"],
        "details": {
            **common_details,
            "covered_lines": observed["covered_lines"],
            "num_statements": observed["num_statements"],
        },
    }


def merge_evolution_receipt(
    evidence: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> None:
    """Attach only independently validated, HEAD-bound evolution metrics."""
    try:
        evolution_receipt.merge_evolution_receipt(evidence, receipt_path, receipt)
    except evolution_receipt.EvolutionReceiptError as exc:
        raise ContractError(str(exc)) from exc


def merge_delivery_receipt(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
) -> None:
    """Attach only format-valid, HEAD-bound delivery metrics (collector owns values)."""
    try:
        delivery_receipt_channel.merge_delivery_receipt(
            contract, evidence, receipt_path, receipt
        )
    except delivery_receipt_channel.DeliveryReceiptError as exc:
        raise ContractError(str(exc)) from exc


def render_markdown(report: dict[str, Any]) -> str:
    score = "UNRATED" if report["formal_score"] is None else f"{report['formal_score']:.2f}"
    lines = [
        "# V-KPI Engineering Health Score",
        "",
        f"- Status: `{report['status']}`",
        f"- Formal score: `{score}`",
        f"- Evidence coverage: `{report['evidence_coverage'] * 100:.1f}%`",
        f"- Grade cap: `{report['grade_cap'] if report['grade_cap'] is not None else 'unknown'}`",
        f"- Release eligible: `{str(report['release_eligible']).lower()}`",
        f"- 85 target achieved: `{str(report['target_achieved']).lower()}`",
        "",
        "## Dimensions",
        "",
        "| Dimension | Observed score | Target | Evidence coverage |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, item in report["dimensions"].items():
        observed = "UNRATED" if item["observed_score"] is None else f"{item['observed_score']:.2f}"
        lines.append(f"| {name} | {observed} | {item['target']:.2f} | {item['evidence_coverage'] * 100:.1f}% |")
    lines.extend(["", "## Hard gates", ""])
    for name, item in report["hard_gates"].items():
        value = "unknown" if item["passed"] is None else str(bool(item["passed"])).lower()
        lines.append(f"- `{name}`: `{value}`")
    lines.extend(["", "## Release gates", ""])
    for name, item in report["release_gates"].items():
        value = "unknown" if item["passed"] is None else str(bool(item["passed"])).lower()
        lines.append(f"- `{name}`: `{value}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--evidence")
    parser.add_argument("--template", action="store_true")
    parser.add_argument("--canonical-receipt", action="append", default=[])
    parser.add_argument("--coverage-receipt", default="")
    parser.add_argument("--evolution-receipt", default="")
    parser.add_argument("--delivery-receipt", default="")
    parser.add_argument("--capture-static", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--require-formal", action="store_true")
    parser.add_argument("--require-release", action="store_true")
    parser.add_argument("--require-target", action="store_true")
    return parser.parse_args(argv)


def _write(path_value: str, content: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    contract = _load_json(Path(args.contract))
    if bool(args.template) == bool(args.evidence):
        raise ContractError("select exactly one of --template or --evidence")
    if args.template:
        if args.coverage_receipt or args.evolution_receipt or args.delivery_receipt:
            raise ContractError(
                "coverage/evolution/delivery receipt requires collected --evidence"
            )
        payload = evidence_template(contract)
        if args.capture_static:
            capture_static_metrics(payload)
        merge_canonical_receipts(payload, [(Path(path), _load_json(Path(path))) for path in args.canonical_receipt])
        output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _write(args.json_out, output)
        stdout_out(output, end="")
        return 0
    evidence = _load_json(Path(args.evidence))
    if args.capture_static:
        capture_static_metrics(evidence)
    merge_canonical_receipts(evidence, [(Path(path), _load_json(Path(path))) for path in args.canonical_receipt])
    if args.coverage_receipt:
        coverage_path = Path(args.coverage_receipt)
        merge_coverage_receipt(evidence, coverage_path, _load_json(coverage_path))
    if args.evolution_receipt:
        evolution_path = Path(args.evolution_receipt)
        merge_evolution_receipt(evidence, evolution_path, _load_json(evolution_path))
    if args.delivery_receipt:
        delivery_path = Path(args.delivery_receipt)
        merge_delivery_receipt(contract, evidence, delivery_path, _load_json(delivery_path))
    report = score_evidence(contract, evidence)
    json_output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown = render_markdown(report)
    _write(args.json_out, json_output)
    _write(args.md_out, markdown)
    stdout_out(json_output if args.json else markdown, end="")
    if args.require_target and not report["target_achieved"]:
        return 4
    if args.require_release and not report["release_eligible"]:
        return 3
    if args.require_formal and report["formal_score"] is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
