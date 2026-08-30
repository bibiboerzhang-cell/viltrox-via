#!/usr/bin/env python3
"""Collect the read-only delivery-health receipt (``vkpi_delivery_receipt_v1``).

Inputs (read-only; no mutation, service, or network call):
* ``runtime/ops/post-deploy/`` — one dir per production deployment, named
  ``<UTC timestamp>-<sha12>``, optionally carrying an ``outcome.json`` written
  by ``scripts/ops/train.sh`` (``{"result": "success"|"rolled_back"|"failed",
  "rollback": {started_at, completed_at}|null, "hotfix_of": sha12|null}``).
* ``runtime/ops/incidents.jsonl`` — the incidents ledger; format and semantics
  in ``docs/vkpi/incidents-ledger-runbook.md``. The first ``ledger_opened``
  line anchors ``ledger_covered_days``.
* a directory of canonical-gate receipts written by ``scripts/verify.sh`` via
  ``VKPI_VERIFY_JSON_OUT`` (payloads carrying ``duration_seconds``).

Honesty rules: a metric lacking samples or a source is reported
``missing_or_insufficient`` with a reason — values are never invented. The
empty-ledger at-target semantics (zero incidents over a covered period) follow
the runbook and are formally carried by contract v1.1. This collector is the
only party computing delivery numbers; the scorer merges but never recomputes
(see ``scripts/vkpi_engineering_health_score_delivery.py``).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from scripts.stdout_utils import out as stdout_out
except ModuleNotFoundError:  # Direct execution: scripts/ is sys.path[0].
    from stdout_utils import out as stdout_out

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "vkpi_delivery_receipt_v1"
WINDOW_DAYS = 90
HOTFIX_WINDOW_HOURS = 24.0
DEFAULT_CONTRACT = ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"
DEFAULT_POST_DEPLOY = "runtime/ops/post-deploy"
DEFAULT_INCIDENTS = "runtime/ops/incidents.jsonl"
DEFAULT_VERIFY_RECEIPTS = "runtime/ops/verify-receipts"
CANONICAL_DIR_RE = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{12})$")
SHA12_RE = re.compile(r"[0-9a-f]{12}")
DIR_TS_FORMAT = "%Y%m%dT%H%M%SZ"
GATE_RECEIPT_SCHEMA = "vkpi_canonical_gate_receipt_v1"
OUTCOME_RESULTS = frozenset({"success", "rolled_back", "failed"})
INCIDENT_SEVERITIES = frozenset({"critical", "p1", "p2", "p3"})
EMPTY_SAMPLE_AT_TARGET = "empty_sample_at_target"
INSUFFICIENT = "sample_count_below_contract_minimum_samples"
# The canonical-gate pass-rate channel is owned by the scorer, never by this
# collector; the receipt carries exactly these nine metrics.
METRIC_NAMES = (
    "deployment_frequency_per_week",
    "lead_time_p50_hours",
    "change_failure_rate",
    "rollback_p95_minutes",
    "mttr_p50_minutes",
    "mttr_p90_minutes",
    "p1_p2_sla_rate",
    "overdue_critical_count",
    "build_test_p95_minutes",
)


class CollectionError(ValueError):
    """Raised when the requested collection cannot be performed safely."""


@dataclass(frozen=True)
class Deployment:
    directory: str
    deployed_at: datetime
    sha12: str
    outcome: dict[str, Any] | None


@dataclass(frozen=True)
class Incident:
    identifier: str
    severity: str
    detected_at: datetime
    resolved_at: datetime | None
    caused_by_release: str
    hotfix_of: str | None
    deadline_at: datetime | None


def _parse_utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CollectionError(f"{label} must be an ISO-8601 UTC string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CollectionError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CollectionError(f"{label} must carry an explicit timezone: {value!r}")
    return parsed.astimezone(UTC)


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Deterministic nearest-rank percentile over a non-empty sample."""
    if not values:
        raise CollectionError("percentile requested over an empty sample")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _metric(status: str, value: float | None, sample_count: int, reason: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"status": status, "value": value, "sample_count": sample_count}
    if reason is not None:
        entry["reason"] = reason
    return entry


def _observed_or_insufficient(
    value: float, sample_count: int, minimum: float | None, reason: str | None = None
) -> dict[str, Any]:
    """Downgrade an honest measurement that is below the contract sample floor."""
    if sample_count < 1:
        raise CollectionError("observed metrics require at least one sample")
    if minimum is not None and sample_count < minimum:
        joined = INSUFFICIENT if reason is None else f"{INSUFFICIENT}; {reason}"
        return _metric("missing_or_insufficient", value, sample_count, joined)
    return _metric("observed", value, sample_count, reason)


def _load_contract_rules(contract_path: Path) -> dict[str, dict[str, Any]]:
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"contract is unreadable: {contract_path}") from exc
    dimensions = contract.get("dimensions") if isinstance(contract, dict) else None
    delivery = dimensions.get("delivery") if isinstance(dimensions, dict) else None
    rules = delivery.get("metrics") if isinstance(delivery, dict) else None
    if not isinstance(rules, dict):
        raise CollectionError("contract delivery metrics block is required")
    owned = {name: rule for name, rule in rules.items() if name in METRIC_NAMES}
    if set(owned) != set(METRIC_NAMES):
        missing = sorted(set(METRIC_NAMES) - set(owned))
        raise CollectionError(f"contract delivery block lacks metrics: {missing}")
    return owned


def _rule_number(rule: dict[str, Any], key: str, name: str, *, required: bool) -> float | None:
    value = rule.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionError(f"contract {name}.{key} must be numeric")
    return float(value)


def _load_outcome(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"outcome.json is unreadable: {path}") from exc
    if not isinstance(payload, dict) or payload.get("result") not in OUTCOME_RESULTS:
        raise CollectionError(f"outcome.json result is unsupported: {path}")
    rollback = payload.get("rollback")
    if rollback is not None and not isinstance(rollback, dict):
        raise CollectionError(f"outcome.json rollback must be object or null: {path}")
    hotfix_of = payload.get("hotfix_of")
    if hotfix_of is not None and (not isinstance(hotfix_of, str) or not SHA12_RE.fullmatch(hotfix_of)):
        raise CollectionError(f"outcome.json hotfix_of must be sha12 or null: {path}")
    return payload


def _scan_deployments(
    post_deploy_dir: Path, window_start: datetime, window_end: datetime
) -> tuple[list[Deployment], int]:
    """Parse canonical deployment evidence directories inside the window."""
    deployments: list[Deployment] = []
    outcome_files = 0
    if not post_deploy_dir.is_dir():
        return deployments, outcome_files
    for entry in sorted(post_deploy_dir.iterdir(), key=lambda item: item.name):
        matched = CANONICAL_DIR_RE.match(entry.name)
        if not entry.is_dir() or matched is None:
            continue  # legacy/foreign naming stays outside the receipt
        try:
            deployed_at = datetime.strptime(matched.group(1), DIR_TS_FORMAT).replace(tzinfo=UTC)
        except ValueError as exc:
            raise CollectionError(f"deployment dir timestamp invalid: {entry.name}") from exc
        if deployed_at < window_start or deployed_at > window_end:
            continue
        outcome_path = entry / "outcome.json"
        outcome = None
        if outcome_path.is_file():
            outcome = _load_outcome(outcome_path)
            outcome_files += 1
        deployments.append(Deployment(entry.name, deployed_at, matched.group(2), outcome))
    return deployments, outcome_files


def _parse_incident(record: dict[str, Any], *, label: str) -> Incident:
    severity = record.get("severity")
    if severity not in INCIDENT_SEVERITIES:
        raise CollectionError(f"{label}: unsupported incident severity {severity!r}")
    identifier = record.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise CollectionError(f"{label}: incident id is required")
    caused = record.get("caused_by_release")
    if not isinstance(caused, str) or not caused:
        raise CollectionError(f"{label}: caused_by_release is required (or 'unknown')")
    resolved_raw = record.get("resolved_at")
    deadline_raw = record.get("deadline_at")
    if severity == "critical" and not deadline_raw:
        raise CollectionError(f"{label}: critical incidents require deadline_at")
    hotfix_raw = record.get("hotfix_of")
    return Incident(
        identifier=identifier,
        severity=str(severity),
        detected_at=_parse_utc(record.get("detected_at"), label=f"{label} detected_at"),
        resolved_at=_parse_utc(resolved_raw, label=f"{label} resolved_at") if resolved_raw else None,
        caused_by_release=caused,
        hotfix_of=str(hotfix_raw) if hotfix_raw else None,
        deadline_at=_parse_utc(deadline_raw, label=f"{label} deadline_at") if deadline_raw else None,
    )


def _load_incidents(path: Path) -> tuple[datetime | None, list[Incident], int]:
    """Return (ledger_opened_at, incidents-after-corrections, total_lines)."""
    if not path.is_file():
        return None, [], 0
    opened_at: datetime | None = None
    by_id: dict[str, Incident] = {}
    order: list[str] = []
    total_lines = 0
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CollectionError(f"incidents ledger is unreadable: {path}") from exc
    for index, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        total_lines += 1
        label = f"{path.name}:{index}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CollectionError(f"{label}: ledger line is not JSON") from exc
        if not isinstance(record, dict):
            raise CollectionError(f"{label}: ledger line must be an object")
        record_type = record.get("type")
        if record_type == "ledger_opened":
            at = _parse_utc(record.get("at"), label=f"{label} at")
            if opened_at is None or at < opened_at:
                opened_at = at
            continue
        if record_type != "incident":
            raise CollectionError(f"{label}: unsupported ledger type {record_type!r}")
        incident = _parse_incident(record, label=label)
        corrects = record.get("corrects")
        key = str(corrects) if corrects else incident.identifier
        if key not in by_id:
            order.append(key)
        by_id[key] = incident  # append-only ledger: later corrections win
    return opened_at, [by_id[key] for key in order], total_lines


def _load_verify_durations(
    receipts_dir: Path, window_start: datetime, window_end: datetime
) -> tuple[list[float], int]:
    """Return (duration minutes inside the window, parsed receipt count)."""
    durations: list[float] = []
    parsed = 0
    if not receipts_dir.is_dir():
        return durations, parsed
    for entry in sorted(receipts_dir.iterdir(), key=lambda item: item.name):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectionError(f"verify receipt is unreadable: {entry}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != GATE_RECEIPT_SCHEMA:
            continue  # foreign JSON in the directory is not a gate receipt
        parsed += 1
        duration = payload.get("duration_seconds")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            continue  # pre-timing receipts carry no duration: skip, never guess
        generated_at = _parse_utc(payload.get("generated_at"), label=f"{entry.name} generated_at")
        if window_start <= generated_at <= window_end:
            durations.append(float(duration) / 60.0)
    return durations, parsed


def _default_authored_at(root: Path) -> Callable[[str], datetime | None]:
    def resolver(sha12: str) -> datetime | None:
        completed = subprocess.run(
            ["git", "show", "-s", "--format=%at", f"{sha12}^{{commit}}"],
            cwd=root, capture_output=True, text=True, check=False,
        )
        text = completed.stdout.strip()
        if completed.returncode != 0 or not text.isdigit():
            return None
        return datetime.fromtimestamp(int(text), tz=UTC)

    return resolver


def _git_candidate(root: Path) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    if head.returncode != 0 or not head.stdout.strip():
        raise CollectionError(f"git HEAD is unavailable under {root}")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--no-renames"],
        cwd=root, capture_output=True, text=True, check=False,
    )
    if status.returncode != 0:
        raise CollectionError(f"git status is unavailable under {root}")
    return {"head": head.stdout.strip(), "worktree_dirty": bool(status.stdout.strip())}


def _deployment_events(deployments: Sequence[Deployment]) -> list[Deployment]:
    """Deploy events that reached production (a failed deploy never went live)."""
    return [item for item in deployments if item.outcome is None or item.outcome["result"] != "failed"]


def _frequency_metric(
    events: Sequence[Deployment], window_start: datetime, window_end: datetime, minimum: float | None
) -> dict[str, Any]:
    if not events:
        return _metric("missing_or_insufficient", None, 0, "no_production_deployments_in_window")
    earliest = min(item.deployed_at for item in events)
    span_days = (window_end - max(window_start, earliest)).total_seconds() / 86400.0
    if span_days <= 0:
        return _metric("missing_or_insufficient", None, 0, "evidence_span_is_empty")
    value = round(len(events) / (span_days / 7.0), 2)
    whole_weeks = int(span_days // 7)
    return _observed_or_insufficient(
        value, max(whole_weeks, 1), minimum, f"computed_over_evidence_span_{span_days:.1f}_days"
    )


def _lead_time_metric(
    events: Sequence[Deployment], authored_at: Callable[[str], datetime | None], minimum: float | None
) -> dict[str, Any]:
    hours: list[float] = []
    unresolved = 0
    for item in events:
        authored = authored_at(item.sha12)
        if authored is None:
            unresolved += 1
        else:
            hours.append((item.deployed_at - authored).total_seconds() / 3600.0)
    reason = f"unresolved_sha_count_{unresolved}" if unresolved else None
    if not hours:
        return _metric(
            "missing_or_insufficient", None, 0, reason or "no_deployments_with_resolvable_commits"
        )
    return _observed_or_insufficient(round(_nearest_rank(hours, 50.0), 2), len(hours), minimum, reason)


def _failed_release_shas(deployments: Sequence[Deployment], incidents: Sequence[Incident]) -> set[str]:
    """sha12 set whose deployment triggered a rollback or a <=24h hotfix."""
    failed: set[str] = set()
    events_by_sha: dict[str, list[Deployment]] = {}
    for item in _deployment_events(deployments):
        events_by_sha.setdefault(item.sha12, []).append(item)
    hotfix_pairs: list[tuple[str, datetime]] = []
    for item in deployments:
        if item.outcome is None:
            continue
        if item.outcome["result"] == "rolled_back":
            failed.add(item.sha12)
        hotfix_of = item.outcome.get("hotfix_of")
        if hotfix_of:
            hotfix_pairs.append((hotfix_of, item.deployed_at))
    for incident in incidents:
        target = incident.caused_by_release
        if not incident.hotfix_of or not SHA12_RE.fullmatch(target):
            continue  # "unknown" carries no CFR attribution
        for hotfix_event in events_by_sha.get(incident.hotfix_of, ()):
            hotfix_pairs.append((target, hotfix_event.deployed_at))
    limit = timedelta(hours=HOTFIX_WINDOW_HOURS)
    for target_sha, hotfix_at in hotfix_pairs:
        for original in events_by_sha.get(target_sha, ()):
            if timedelta(0) <= hotfix_at - original.deployed_at <= limit:
                failed.add(target_sha)
                break
    return failed


def _change_failure_metric(
    deployments: Sequence[Deployment],
    incidents: Sequence[Incident],
    ledger_opened_at: datetime | None,
    minimum: float | None,
) -> dict[str, Any]:
    observable = {
        item.sha12
        for item in _deployment_events(deployments)
        if item.outcome is not None
        or (ledger_opened_at is not None and item.deployed_at >= ledger_opened_at)
    }
    if not observable:
        return _metric(
            "missing_or_insufficient", None, 0,
            "no_deployments_with_failure_observability_outcome_or_ledger_coverage",
        )
    failed = _failed_release_shas(deployments, incidents) & observable
    return _observed_or_insufficient(round(len(failed) / len(observable), 4), len(observable), minimum)


def _rollback_metric(deployments: Sequence[Deployment], minimum: float | None) -> dict[str, Any]:
    minutes: list[float] = []
    untimed = 0
    for item in deployments:
        if item.outcome is None or item.outcome["result"] != "rolled_back":
            continue
        rollback = item.outcome.get("rollback")
        started = rollback.get("started_at") if isinstance(rollback, dict) else None
        completed = rollback.get("completed_at") if isinstance(rollback, dict) else None
        if not started or not completed:
            untimed += 1  # the train watcher missed a stamp: never guess
            continue
        delta = (
            _parse_utc(completed, label=f"{item.directory} rollback completed_at")
            - _parse_utc(started, label=f"{item.directory} rollback started_at")
        ).total_seconds() / 60.0
        if delta < 0:
            raise CollectionError(f"{item.directory}: rollback interval is negative")
        minutes.append(delta)
    reason = f"untimed_rollback_count_{untimed}" if untimed else None
    if not minutes:
        if untimed:
            # 有回滚但观察哨没打上时戳:绝不猜时长,如实缺证。
            return _metric("missing_or_insufficient", None, 0, reason)
        # 零回滚是好状态不是缺证据(runbook / 合同 v1.1 empty_sample_at_target,与
        # MTTR/SLA 同语义):按合同 target 15.0 记 observed,样本 = 有 outcome.json
        # 覆盖的部署数;零覆盖 = 零暴露,无法诚实观测。
        exposure = sum(1 for item in deployments if item.outcome is not None)
        if exposure < 1:
            return _metric("missing_or_insufficient", None, 0, "no_outcome_coverage")
        return _observed_or_insufficient(15.0, exposure, minimum, "no_rollbacks_at_target")
    return _observed_or_insufficient(round(_nearest_rank(minutes, 95.0), 2), len(minutes), minimum, reason)


def _ledger_gate(
    relevant: Sequence[Incident],
    exposure: int,
    ledger_covered_days: float,
    at_target: float,
    minimum: float | None,
) -> dict[str, Any] | None:
    """Shared degradation ladder for ledger-backed metrics; None when samples exist.

    Zero relevant incidents over a covered period is a good state, not missing
    evidence (runbook / contract v1.1): it is recorded at-target with the
    ``empty_sample_at_target`` reason, sampled by the number of production
    deployments the ledger actually covered — zero covered deployments means
    zero exposure and therefore no honest observation.
    """
    if ledger_covered_days <= 0:
        return _metric("missing_or_insufficient", None, 0, "ledger_not_opened")
    if relevant:
        return None
    if exposure < 1:
        return _metric("missing_or_insufficient", None, 0, "ledger_covered_zero_deployments")
    return _observed_or_insufficient(at_target, exposure, minimum, EMPTY_SAMPLE_AT_TARGET)


def _mttr_metric(
    incidents: Sequence[Incident],
    exposure: int,
    ledger_covered_days: float,
    percentile: float,
    at_target: float,
    minimum: float | None,
) -> dict[str, Any]:
    gated = _ledger_gate(incidents, exposure, ledger_covered_days, at_target, minimum)
    if gated is not None:
        return gated
    resolved = [
        (item.resolved_at - item.detected_at).total_seconds() / 60.0
        for item in incidents
        if item.resolved_at is not None
    ]
    if not resolved:
        return _metric(
            "missing_or_insufficient", None, 0, f"unresolved_incident_count_{len(incidents)}"
        )
    return _observed_or_insufficient(
        round(_nearest_rank(resolved, percentile), 2),
        max(exposure, len(resolved)),
        minimum,
        f"resolved_incidents_{len(resolved)}",
    )


def _sla_metric(
    incidents: Sequence[Incident],
    exposure: int,
    ledger_covered_days: float,
    at_target: float,
    minimum: float | None,
) -> dict[str, Any]:
    relevant = [item for item in incidents if item.severity in {"p1", "p2"}]
    gated = _ledger_gate(relevant, exposure, ledger_covered_days, at_target, minimum)
    if gated is not None:
        return gated
    met = sum(
        1
        for item in relevant
        if item.resolved_at is not None
        and (item.deadline_at is None or item.resolved_at <= item.deadline_at)
    )
    return _observed_or_insufficient(round(met / len(relevant), 4), len(relevant), minimum)


def _overdue_metric(
    incidents: Sequence[Incident],
    exposure: int,
    ledger_covered_days: float,
    window_end: datetime,
    minimum: float | None,
) -> dict[str, Any]:
    criticals = [item for item in incidents if item.severity == "critical"]
    gated = _ledger_gate(criticals, exposure, ledger_covered_days, 0.0, minimum)
    if gated is not None:
        return gated
    overdue = 0
    for item in criticals:
        if item.deadline_at is None:
            raise CollectionError(f"{item.identifier}: critical without deadline_at")
        resolved_late = item.resolved_at is not None and item.resolved_at > item.deadline_at
        still_open_late = item.resolved_at is None and window_end > item.deadline_at
        if resolved_late or still_open_late:
            overdue += 1
    return _observed_or_insufficient(float(overdue), len(criticals), minimum)


def _build_test_metric(durations: Sequence[float], minimum: float | None) -> dict[str, Any]:
    if not durations:
        return _metric("missing_or_insufficient", None, 0, "no_verify_receipts_with_duration")
    return _observed_or_insufficient(round(_nearest_rank(durations, 95.0), 2), len(durations), minimum)


def build_receipt(
    *,
    candidate: dict[str, Any],
    post_deploy_dir: Path,
    incidents_path: Path,
    receipts_dir: Path,
    contract_path: Path,
    observed_at: datetime,
    authored_at: Callable[[str], datetime | None],
) -> dict[str, Any]:
    rules = _load_contract_rules(contract_path)
    window_end = observed_at.astimezone(UTC)
    window_start = window_end - timedelta(days=WINDOW_DAYS)
    deployments, outcome_files = _scan_deployments(post_deploy_dir, window_start, window_end)
    ledger_opened_at, all_incidents, incidents_lines = _load_incidents(incidents_path)
    durations, verify_receipts = _load_verify_durations(receipts_dir, window_start, window_end)
    ledger_covered_days = 0.0
    if ledger_opened_at is not None:
        covered = (window_end - max(window_start, ledger_opened_at)).total_seconds()
        ledger_covered_days = round(min(max(covered / 86400.0, 0.0), float(WINDOW_DAYS)), 2)
    events = _deployment_events(deployments)
    incidents = [item for item in all_incidents if window_start <= item.detected_at <= window_end]
    exposure = 0
    if ledger_opened_at is not None:
        exposure = sum(1 for item in events if item.deployed_at >= ledger_opened_at)

    def minimum(name: str) -> float | None:
        return _rule_number(rules[name], "minimum_samples", name, required=False)

    def target(name: str) -> float:
        result = _rule_number(rules[name], "target", name, required=True)
        assert result is not None  # required=True never returns None
        return result

    metrics = {
        "deployment_frequency_per_week": _frequency_metric(
            events, window_start, window_end, minimum("deployment_frequency_per_week")
        ),
        "lead_time_p50_hours": _lead_time_metric(events, authored_at, minimum("lead_time_p50_hours")),
        "change_failure_rate": _change_failure_metric(
            deployments, incidents, ledger_opened_at, minimum("change_failure_rate")
        ),
        "rollback_p95_minutes": _rollback_metric(deployments, minimum("rollback_p95_minutes")),
        "mttr_p50_minutes": _mttr_metric(
            incidents, exposure, ledger_covered_days, 50.0,
            target("mttr_p50_minutes"), minimum("mttr_p50_minutes"),
        ),
        "mttr_p90_minutes": _mttr_metric(
            incidents, exposure, ledger_covered_days, 90.0,
            target("mttr_p90_minutes"), minimum("mttr_p90_minutes"),
        ),
        "p1_p2_sla_rate": _sla_metric(
            incidents, exposure, ledger_covered_days,
            target("p1_p2_sla_rate"), minimum("p1_p2_sla_rate"),
        ),
        "overdue_critical_count": _overdue_metric(
            incidents, exposure, ledger_covered_days, window_end, minimum("overdue_critical_count")
        ),
        "build_test_p95_minutes": _build_test_metric(durations, minimum("build_test_p95_minutes")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate": {
            "head": str(candidate["head"]),
            "worktree_dirty": bool(candidate["worktree_dirty"]),
        },
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": WINDOW_DAYS,
            "ledger_covered_days": ledger_covered_days,
        },
        "metrics": metrics,
        "sources": {
            "post_deploy_dirs": len(deployments),
            "incidents_lines": incidents_lines,
            "verify_receipts": verify_receipts,
            "outcome_files": outcome_files,
        },
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_output(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="Repository root")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument(
        "--post-deploy-dir", default=DEFAULT_POST_DEPLOY,
        help="post-deploy evidence directory (relative paths resolve under --root)",
    )
    parser.add_argument("--incidents", default=DEFAULT_INCIDENTS)
    parser.add_argument(
        "--verify-receipts-dir", default=DEFAULT_VERIFY_RECEIPTS,
        help="directory of canonical gate receipts written via VKPI_VERIFY_JSON_OUT",
    )
    parser.add_argument(
        "--observed-at", default=None,
        help="Fixed ISO-8601 window end for reproducible output (default: now UTC)",
    )
    parser.add_argument("--output", default="", help="Optional JSON output path")
    return parser.parse_args(argv)


def _resolve_under(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        raise CollectionError(f"repository root with .git required: {root}")
    observed_at = (
        _parse_utc(args.observed_at, label="--observed-at") if args.observed_at else datetime.now(UTC)
    )
    receipt = build_receipt(
        candidate=_git_candidate(root),
        post_deploy_dir=_resolve_under(root, args.post_deploy_dir),
        incidents_path=_resolve_under(root, args.incidents),
        receipts_dir=_resolve_under(root, args.verify_receipts_dir),
        contract_path=Path(args.contract),
        observed_at=observed_at,
        authored_at=_default_authored_at(root),
    )
    data = _json_bytes(receipt)
    if args.output:
        _write_output(Path(args.output), data)
    stdout_out(data.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
