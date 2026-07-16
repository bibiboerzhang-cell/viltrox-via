#!/usr/bin/env python3
"""Fail-closed verifier for one post-restart runtime-log canary receipt.

The scanner is deliberately a generic read-only audit utility.  This verifier
adds the stricter release semantics: the baseline must have been captured after
the reviewed worker restart, must cover the explicit fleet-derived log manifest
with the current pattern set, and the canary must use those supplied offsets and
observe actual post-baseline bytes.  It never reads the source logs or emits
their content.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Collection, Mapping

try:  # Direct CLI execution puts scripts/ on sys.path; tests import scripts.*.
    from stdout_utils import out as stdout_out
except ModuleNotFoundError:  # pragma: no cover - exercised by package import.
    from scripts.stdout_utils import out as stdout_out


MAX_RECEIPT_BYTES = 4 * 1024 * 1024
SCHEMA_VERSION = 2
REQUIRED_ADMIN_LOGS = frozenset(
    {
        "runtime/logs/admin-8102-access.log",
        "runtime/logs/admin-8102-error.log",
    }
)
LEGACY_WORKER_LOG = "runtime/logs/worker.log"
MAX_EXPECTED_LOGS = 64
WORKER_LOG_RE = re.compile(
    r"^runtime/logs/(?:worker|worker-interactive|worker-bulk[1-9][0-9]*|worker-[1-9][0-9]*)\.log$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ReceiptError(ValueError):
    """Safe validation error whose message contains no receipt values."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("receipt contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise ReceiptError("receipt contains a non-finite JSON number")


def load_receipt(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReceiptError("receipt is missing")
    size = path.stat().st_size
    if size <= 0 or size > MAX_RECEIPT_BYTES:
        raise ReceiptError("receipt size is outside the reviewed bound")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("receipt root is not an object")
    return payload


def parse_utc(value: Any, *, label: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceiptError(f"{label} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _require_safety(payload: Mapping[str, Any], *, label: str) -> None:
    safety = payload.get("safety")
    if not isinstance(safety, Mapping):
        raise ReceiptError(f"{label} safety proof is missing")
    if safety.get("read_only") is not True:
        raise ReceiptError(f"{label} is not read-only")
    if safety.get("redacted_by_construction") is not True:
        raise ReceiptError(f"{label} is not redacted by construction")
    if safety.get("raw_content_included") is not False:
        raise ReceiptError(f"{label} includes raw content")


def _require_complete_summary(payload: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        raise ReceiptError(f"{label} summary is missing")
    for key in ("unscanned_files", "truncated_files", "unscanned_tail_files"):
        value = summary.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            raise ReceiptError(f"{label} is incomplete")
    return summary


def expected_runtime_logs(
    *,
    expected_worker_count: int,
    expected_redis_worker_count: int,
) -> frozenset[str]:
    """Return the deterministic mandatory local log set for one fleet shape.

    The release gate independently proves these same worker counts from
    ``/health``.  Deriving paths from the reviewed counts prevents a receipt
    producer from shrinking its own evidence set to whichever files happen to
    be convenient at verification time.
    """

    for value, label in (
        (expected_worker_count, "Apify worker count"),
        (expected_redis_worker_count, "Redis worker count"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ReceiptError(f"expected {label} must be a positive integer")
    if expected_worker_count > MAX_EXPECTED_LOGS or expected_redis_worker_count > MAX_EXPECTED_LOGS:
        raise ReceiptError("expected worker count exceeds the reviewed bound")

    logs = set(REQUIRED_ADMIN_LOGS)
    if expected_worker_count == 1:
        logs.add(LEGACY_WORKER_LOG)
    else:
        logs.add("runtime/logs/worker-interactive.log")
        logs.update(
            f"runtime/logs/worker-bulk{index}.log"
            for index in range(1, expected_worker_count)
        )
    logs.update(
        f"runtime/logs/worker-{index}.log"
        for index in range(1, expected_redis_worker_count + 1)
    )
    return frozenset(logs)


def validate_expected_logs(
    values: Collection[str],
    *,
    expected_worker_count: int,
    expected_redis_worker_count: int,
) -> frozenset[str]:
    if not values or len(values) > MAX_EXPECTED_LOGS:
        raise ReceiptError("expected runtime log manifest size is outside the reviewed bound")
    normalized: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != value
            or (value not in REQUIRED_ADMIN_LOGS and WORKER_LOG_RE.fullmatch(value) is None)
        ):
            raise ReceiptError("expected runtime log manifest contains an invalid path")
        normalized.append(value)
    if len(normalized) != len(set(normalized)):
        raise ReceiptError("expected runtime log manifest contains a duplicate path")

    supplied = frozenset(normalized)
    mandatory = expected_runtime_logs(
        expected_worker_count=expected_worker_count,
        expected_redis_worker_count=expected_redis_worker_count,
    )
    allowed = mandatory | ({LEGACY_WORKER_LOG} if expected_worker_count > 1 else set())
    if not mandatory.issubset(supplied) or not supplied.issubset(allowed):
        raise ReceiptError("expected runtime log manifest does not match the reviewed fleet shape")
    return supplied


def _state_offsets(
    payload: Mapping[str, Any],
    *,
    label: str,
    expected_logs: frozenset[str],
) -> Mapping[str, Any]:
    state = payload.get("scan_state")
    files = state.get("files") if isinstance(state, Mapping) else None
    if not isinstance(files, Mapping) or set(files) != expected_logs:
        raise ReceiptError(f"{label} does not cover the expected runtime log manifest")
    for row in files.values():
        if not isinstance(row, Mapping):
            raise ReceiptError(f"{label} scan state is malformed")
        offset = row.get("next_baseline_offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ReceiptError(f"{label} scan offset is invalid")
    return files


def _require_runtime_binding(
    payload: Mapping[str, Any],
    *,
    label: str,
    expected_worker_boot_nonce_sha256: str,
    worker_not_before: datetime,
) -> None:
    binding = payload.get("runtime_binding")
    if not isinstance(binding, Mapping):
        raise ReceiptError(f"{label} runtime binding is missing")
    if binding.get("worker_boot_nonce_sha256") != expected_worker_boot_nonce_sha256:
        raise ReceiptError(f"{label} worker boot binding does not match")
    bound_not_before = parse_utc(
        binding.get("worker_not_before"),
        label=f"{label} worker not-before",
    )
    if bound_not_before != worker_not_before.astimezone(timezone.utc):
        raise ReceiptError(f"{label} worker restart binding does not match")


def validate_receipts(
    baseline: Mapping[str, Any],
    canary: Mapping[str, Any],
    *,
    expected_worker_boot_nonce_sha256: str,
    worker_not_before: datetime,
    expected_logs: Collection[str],
    expected_worker_count: int,
    expected_redis_worker_count: int,
) -> dict[str, Any]:
    expected_boot = str(expected_worker_boot_nonce_sha256 or "").strip().lower()
    if not SHA256_RE.fullmatch(expected_boot):
        raise ReceiptError("expected worker boot nonce SHA-256 is invalid")
    trusted_logs = validate_expected_logs(
        expected_logs,
        expected_worker_count=expected_worker_count,
        expected_redis_worker_count=expected_redis_worker_count,
    )
    for payload, label in ((baseline, "baseline"), (canary, "canary")):
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ReceiptError(f"{label} schema is unsupported")
        _require_safety(payload, label=label)
        _require_complete_summary(payload, label=label)
        pattern_sha = str(payload.get("pattern_set_sha256") or "").strip().lower()
        if not SHA256_RE.fullmatch(pattern_sha):
            raise ReceiptError(f"{label} pattern-set identity is invalid")
        _require_runtime_binding(
            payload,
            label=label,
            expected_worker_boot_nonce_sha256=expected_boot,
            worker_not_before=worker_not_before,
        )

    baseline_status = str(baseline.get("status") or "")
    if baseline_status not in {"clean", "historical_findings_only"}:
        raise ReceiptError("baseline is not a clean complete snapshot")
    baseline_at = parse_utc(baseline.get("generated_at"), label="baseline generated_at")
    if baseline_at < worker_not_before.astimezone(timezone.utc):
        raise ReceiptError("baseline predates the reviewed worker restart")
    baseline_offsets = _state_offsets(
        baseline,
        label="baseline",
        expected_logs=trusted_logs,
    )

    if canary.get("pattern_set_sha256") != baseline.get("pattern_set_sha256"):
        raise ReceiptError("canary pattern set differs from the baseline")
    canary_at = parse_utc(canary.get("generated_at"), label="canary generated_at")
    if canary_at < baseline_at:
        raise ReceiptError("canary predates its baseline")
    canary_status = str(canary.get("status") or "")
    if canary_status not in {"clean", "historical_findings_only"}:
        raise ReceiptError("canary contains new findings or is incomplete")
    summary = canary["summary"]
    growth = summary.get("growth_occurrences")
    if isinstance(growth, bool) or not isinstance(growth, int) or growth != 0:
        raise ReceiptError("canary growth contains a sensitive-log finding")

    rows = canary.get("files")
    if not isinstance(rows, list) or len(rows) != len(trusted_logs):
        raise ReceiptError("canary file evidence is incomplete")
    observed: set[str] = set()
    growth_bytes = 0
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReceiptError("canary file evidence is malformed")
        label = str(row.get("file") or "")
        observed.add(label)
        if row.get("status") != "scanned":
            raise ReceiptError("canary did not scan every log")
        if row.get("baseline_source") != "provided":
            raise ReceiptError("canary silently replaced a supplied baseline")
        if row.get("raw_content_included") is not False:
            raise ReceiptError("canary file evidence includes raw content")
        baseline_state = baseline_offsets.get(label)
        expected_offset = (
            baseline_state.get("next_baseline_offset")
            if isinstance(baseline_state, Mapping)
            else None
        )
        if row.get("baseline_offset") != expected_offset:
            raise ReceiptError("canary baseline offset differs from the bound receipt")
        growth_range = row.get("growth_byte_range")
        if (
            not isinstance(growth_range, list)
            or len(growth_range) != 2
            or growth_range[0] != expected_offset
        ):
            raise ReceiptError("canary growth range is not bound to the supplied offset")
        value = row.get("growth_bytes_scanned")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReceiptError("canary growth-byte evidence is invalid")
        growth_bytes += value
    if observed != trusted_logs:
        raise ReceiptError("canary did not cover the expected runtime log manifest")
    if growth_bytes <= 0:
        raise ReceiptError("canary observed no post-baseline log bytes")
    _state_offsets(canary, label="canary", expected_logs=trusted_logs)

    return {
        "pass": True,
        "files": len(rows),
        "growth_bytes_scanned": growth_bytes,
        "growth_occurrences": 0,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a post-restart runtime-log canary receipt")
    parser.add_argument("--baseline-state", required=True)
    parser.add_argument("--canary-report", required=True)
    parser.add_argument("--expected-worker-boot-nonce-sha256", required=True)
    parser.add_argument("--worker-not-before", required=True)
    parser.add_argument("--expected-log", action="append", required=True)
    parser.add_argument("--expected-worker-count", type=int, required=True)
    parser.add_argument("--expected-redis-worker-count", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        worker_not_before = parse_utc(args.worker_not_before, label="worker not-before")
        result = validate_receipts(
            load_receipt(Path(args.baseline_state)),
            load_receipt(Path(args.canary_report)),
            expected_worker_boot_nonce_sha256=args.expected_worker_boot_nonce_sha256,
            worker_not_before=worker_not_before,
            expected_logs=args.expected_log,
            expected_worker_count=args.expected_worker_count,
            expected_redis_worker_count=args.expected_redis_worker_count,
        )
    except (OSError, ReceiptError) as exc:
        stdout_out(f"[verify-runtime-log-canary] FAIL: {exc}", file=sys.stderr)
        return 1
    stdout_out(
        "[verify-runtime-log-canary] PASS: "
        f"files={result['files']} growth_bytes={result['growth_bytes_scanned']} "
        "growth_occurrences=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
