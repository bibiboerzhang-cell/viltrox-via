"""Fail-closed validation and merging for core-mutation receipts.

The mutation receipt (``vkpi_engineering_health_mutation_receipt_v1``) is
produced by ``scripts/vkpi_engineering_health_mutation.py``, the only party
that runs mutants.  Styled after the delivery channel: this module never
reruns anything — it verifies format, binding to the current scoring
candidate, freshness, contract scope, and the arithmetic of the pooled score
before attaching ``core_mutation_score`` to the evidence payload.  Anything
ambiguous is rejected; anything honest-but-not-scoreable (a smoke run, a
below-floor file count) is downgraded to ``missing_or_insufficient`` — never
silently scored.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from scripts import vkpi_engineering_health_mutation as mutation_runner
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    import vkpi_engineering_health_mutation as mutation_runner


MUTATION_RECEIPT_SCHEMA_VERSION = mutation_runner.SCHEMA_VERSION
MUTATION_METRIC_NAME = "core_mutation_score"
MUTATION_MODES = frozenset(mutation_runner.MODES)
MAX_RECEIPT_AGE = timedelta(hours=24)
SMOKE_REASON = "smoke_run_not_scoreable"
FILE_FLOOR_REASON = "target_file_count_below_channel_minimum"
_SCORE_TOLERANCE = 1e-12


class MutationReceiptError(ValueError):
    """Raised when a mutation receipt cannot be trusted."""


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise MutationReceiptError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MutationReceiptError(f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _nonnegative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MutationReceiptError(f"{label} must be a non-negative integer")
    return value


def expected_scope_groups(contract: dict[str, Any]) -> dict[str, list[str]]:
    """Contract core-scope groups this channel is allowed to score."""
    methodology = contract.get("code_evidence_methodology")
    rule = methodology.get(MUTATION_METRIC_NAME) if isinstance(methodology, dict) else None
    groups = rule.get("core_scope_groups") if isinstance(rule, dict) else None
    if not isinstance(groups, dict) or not groups:
        raise MutationReceiptError("contract core_scope_groups block is required")
    validated: dict[str, list[str]] = {}
    for name, prefixes in groups.items():
        if not isinstance(prefixes, list) or not all(
            isinstance(prefix, str) and prefix for prefix in prefixes
        ):
            raise MutationReceiptError(f"contract scope group {name} is malformed")
        validated[str(name)] = list(prefixes)
    return validated


def _require_fields(
    expected_source: dict[str, Any],
    actual: dict[str, Any],
    fields: tuple[str, ...],
    *,
    label: str,
) -> None:
    for field in fields:
        expected = str(expected_source.get(field) or "")
        if not expected or str(actual.get(field) or "") != expected:
            raise MutationReceiptError(f"mutation {label} {field} mismatch")


def _validate_candidate(evidence: dict[str, Any], receipt: dict[str, Any]) -> None:
    evidence_candidate = evidence.get("candidate")
    candidate = receipt.get("candidate")
    if not isinstance(evidence_candidate, dict) or not isinstance(candidate, dict):
        raise MutationReceiptError("mutation candidate binding is required")
    if evidence_candidate.get("source_and_status_stable") is not True:
        raise MutationReceiptError("engineering evidence source/status is not stable")
    _require_fields(
        evidence_candidate, candidate, ("source_content_sha256",), label="receipt"
    )
    git_start = candidate.get("git_start")
    if not isinstance(git_start, dict) or git_start != candidate.get("git_end"):
        raise MutationReceiptError("mutation receipt Git start/end mismatch")
    _require_fields(
        evidence_candidate,
        git_start,
        ("head", "branch", "status_sha256"),
        label="candidate",
    )
    if candidate.get("source_start") != candidate.get("source_end"):
        raise MutationReceiptError("mutation receipt source start/end mismatch")


def _validate_run_provenance(run: dict[str, Any]) -> None:
    command = run.get("command")
    if not isinstance(command, list) or tuple(command) != mutation_runner.CANONICAL_RUN_COMMAND:
        raise MutationReceiptError("mutation receipt command is not canonical")
    expected_hash = mutation_runner.coverage_receipt.command_sha256(command)
    if run.get("command_sha256") != expected_hash:
        raise MutationReceiptError("mutation command hash mismatch")
    if run.get("mutmut_version") != mutation_runner.MUTMUT_PIN:
        raise MutationReceiptError("mutation receipt mutmut version is not pinned")
    if isinstance(run.get("exit_code"), bool) or run.get("exit_code") != 0:
        raise MutationReceiptError("mutation run did not pass")
    if run.get("artifacts_existed_before") is not False:
        raise MutationReceiptError("mutation run did not use a fresh workspace")


def _validate_run_isolation(run: dict[str, Any]) -> None:
    isolation = run.get("db_isolation")
    if not isinstance(isolation, dict) or isolation.get("mode") not in mutation_runner.DB_MODES:
        raise MutationReceiptError("mutation receipt db isolation is undeclared")
    if isolation.get("environment_inherited") is not False:
        raise MutationReceiptError("mutation run inherited the operator environment")


def _validate_run_freshness(
    receipt: dict[str, Any], run: dict[str, Any], *, now: datetime | None
) -> None:
    started = _parse_timestamp(run.get("started_at"), label="run.started_at")
    finished = _parse_timestamp(run.get("finished_at"), label="run.finished_at")
    if finished < started:
        raise MutationReceiptError("mutation run finished before it started")
    if str(receipt.get("generated_at") or "") != str(run.get("finished_at") or ""):
        raise MutationReceiptError("mutation receipt generated_at mismatch")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if finished > current + timedelta(minutes=5):
        raise MutationReceiptError("mutation receipt timestamp is in the future")
    if current - finished > MAX_RECEIPT_AGE:
        raise MutationReceiptError("mutation receipt is older than 24 hours")


def _validate_run(receipt: dict[str, Any], *, now: datetime | None) -> dict[str, Any]:
    run = receipt.get("run")
    if not isinstance(run, dict):
        raise MutationReceiptError("mutation receipt run object is required")
    _validate_run_provenance(run)
    _validate_run_isolation(run)
    _validate_run_freshness(receipt, run, now=now)
    return run


def _validate_scope_groups(
    contract: dict[str, Any], scope: dict[str, Any]
) -> dict[str, list[str]]:
    contract_groups = expected_scope_groups(contract)
    groups = scope.get("groups")
    if not isinstance(groups, list) or not groups or groups != sorted(set(groups)):
        raise MutationReceiptError("mutation scope groups must be sorted and unique")
    unknown = [name for name in groups if name not in contract_groups]
    if unknown:
        raise MutationReceiptError(f"mutation scope groups outside contract: {unknown}")
    expected_prefixes = {name: contract_groups[name] for name in groups}
    if scope.get("group_prefixes") != expected_prefixes:
        raise MutationReceiptError("mutation scope prefixes do not match the contract")
    return expected_prefixes


def _validate_scope_targets(
    scope: dict[str, Any], expected_prefixes: dict[str, list[str]]
) -> list[str]:
    targets = scope.get("target_files")
    if not isinstance(targets, list) or not targets or targets != sorted(set(targets)):
        raise MutationReceiptError("mutation target files must be sorted and unique")
    flat = tuple(prefix for prefixes in expected_prefixes.values() for prefix in prefixes)
    for target in targets:
        if not isinstance(target, str) or not target.endswith(".py") or not target.startswith(flat):
            raise MutationReceiptError(f"mutation target outside scope: {target}")
    return targets


def _validate_scope_counts(scope: dict[str, Any], targets: list[str]) -> None:
    if scope.get("target_file_count") != len(targets):
        raise MutationReceiptError("mutation target file count mismatch")
    eligible = _nonnegative_int(
        scope.get("eligible_file_count"), label="scope.eligible_file_count"
    )
    if eligible < len(targets):
        raise MutationReceiptError("mutation targets exceed the eligible scope")
    if scope.get("target_files_sha256") != mutation_runner._lines_sha256(targets):  # noqa: SLF001
        raise MutationReceiptError("mutation target file list hash mismatch")


def _validate_scope(
    contract: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise MutationReceiptError("mutation receipt scope object is required")
    if scope.get("mode") not in MUTATION_MODES:
        raise MutationReceiptError("mutation receipt mode is unsupported")
    expected_prefixes = _validate_scope_groups(contract, scope)
    targets = _validate_scope_targets(scope, expected_prefixes)
    _validate_scope_counts(scope, targets)
    return scope


def _validate_per_file_row(row: Any) -> dict[str, int]:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        raise MutationReceiptError("mutation per-file rows must be objects with a path")
    return {
        field: _nonnegative_int(row.get(field), label=f"{row['path']}.{field}")
        for field in mutation_runner.COUNT_FIELDS
    }


def _recompute_totals(
    per_file: list[Any], scope: dict[str, Any]
) -> dict[str, int]:
    observed_counts = [_validate_per_file_row(row) for row in per_file]
    paths = [row["path"] for row in per_file]
    if paths != list(scope["target_files"]):
        raise MutationReceiptError("mutation per-file paths must equal the target list")
    return {
        field: sum(counts[field] for counts in observed_counts)
        for field in mutation_runner.COUNT_FIELDS
    }


def _validate_score(
    results: dict[str, Any], recomputed: dict[str, int]
) -> tuple[float, int]:
    killed_pool = recomputed["killed"] + recomputed["timeout"]
    # 合同 core-mutation-v1:killed/(killed+survived),no_tests 不入公式
    # (与 mutation.py score_from_totals 同源对齐,2026-08-31 公式修正)。
    denominator = killed_pool + recomputed["survived"]
    if denominator <= 0:
        raise MutationReceiptError("mutation score denominator must be positive")
    if results.get("scored_mutants") != denominator:
        raise MutationReceiptError("mutation scored_mutants mismatch")
    declared_score = results.get("core_mutation_score")
    if isinstance(declared_score, bool) or not isinstance(declared_score, (int, float)):
        raise MutationReceiptError("mutation score must be numeric")
    expected_score = killed_pool / denominator
    if not math.isfinite(float(declared_score)) or abs(
        float(declared_score) - expected_score
    ) > _SCORE_TOLERANCE:
        raise MutationReceiptError("mutation score does not match pooled counts")
    return expected_score, denominator


def _validate_results(
    receipt: dict[str, Any], scope: dict[str, Any]
) -> dict[str, Any]:
    results = receipt.get("results")
    if not isinstance(results, dict):
        raise MutationReceiptError("mutation receipt results object is required")
    per_file = results.get("per_file")
    if not isinstance(per_file, list) or not per_file:
        raise MutationReceiptError("mutation receipt per-file results are required")
    recomputed = _recompute_totals(per_file, scope)
    if results.get("totals") != recomputed:
        raise MutationReceiptError("mutation totals do not match per-file counts")
    expected_score, denominator = _validate_score(results, recomputed)
    return {
        "totals": recomputed,
        "scored_mutants": denominator,
        "core_mutation_score": expected_score,
    }


def validate_mutation_receipt(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate format, binding, freshness and arithmetic; return the summary."""
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != MUTATION_RECEIPT_SCHEMA_VERSION
    ):
        raise MutationReceiptError("unsupported mutation receipt schema")
    if receipt.get("methodology_id") != mutation_runner.METHODOLOGY_ID:
        raise MutationReceiptError("unsupported mutation methodology")
    if receipt.get("passed") is not True:
        raise MutationReceiptError("mutation receipt did not pass")
    _validate_candidate(evidence, receipt)
    run = _validate_run(receipt, now=now)
    scope = _validate_scope(contract, receipt)
    results = _validate_results(receipt, scope)
    contract_groups = expected_scope_groups(contract)
    return {
        "observed_at": str(run["finished_at"]),
        "mode": str(scope["mode"]),
        "groups": list(scope["groups"]),
        "scope_partial": sorted(scope["groups"]) != sorted(contract_groups),
        "target_file_count": len(scope["target_files"]),
        "eligible_file_count": int(scope["eligible_file_count"]),
        **results,
    }


def _downgrade_reason(observed: dict[str, Any]) -> str | None:
    if observed["mode"] != "scored":
        return SMOKE_REASON
    if observed["target_file_count"] < mutation_runner.MIN_SCORED_FILES:
        return FILE_FLOOR_REASON
    return None


def merge_mutation_receipt(
    contract: dict[str, Any],
    evidence: dict[str, Any],
    receipt_path: Path,
    receipt: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Attach only format-valid, candidate-bound mutation metrics to evidence."""
    observed = validate_mutation_receipt(contract, evidence, receipt, now=now)
    source = f"receipt://{receipt_path.resolve()}"
    details = {
        "schema_version": MUTATION_RECEIPT_SCHEMA_VERSION,
        "methodology_id": mutation_runner.METHODOLOGY_ID,
        "mode": observed["mode"],
        "scope_groups": observed["groups"],
        "scope_partial": observed["scope_partial"],
        "target_file_count": observed["target_file_count"],
        "eligible_file_count": observed["eligible_file_count"],
        "totals": observed["totals"],
    }
    entry: dict[str, Any] = {
        "status": "observed",
        "value": observed["core_mutation_score"],
        "source": source,
        "observed_at": observed["observed_at"],
        "sample_count": observed["scored_mutants"],
        "details": details,
    }
    reason = _downgrade_reason(observed)
    if reason is not None:
        # Fail closed: honest-but-unscoreable runs keep their measured value
        # for transparency but can never reach the scorer as evidence.
        entry["status"] = "missing_or_insufficient"
        entry["reason"] = reason
    code = evidence.setdefault("metrics", {}).setdefault("code", {})
    code[MUTATION_METRIC_NAME] = entry
