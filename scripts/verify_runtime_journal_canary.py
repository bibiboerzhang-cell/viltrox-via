#!/usr/bin/env python3
"""Fail-closed validator for cursor-bound systemd journal release receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Direct CLI execution puts scripts/ on sys.path; tests import scripts.*.
    from stdout_utils import out as stdout_out
except ModuleNotFoundError:  # pragma: no cover - exercised by package import.
    from scripts.stdout_utils import out as stdout_out


SCHEMA_VERSION = 1
SOURCE = "systemd_journal"
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9_.:;=+,-]{8,2048}$")


class ReceiptError(ValueError):
    """Safe validation error that never includes receipt values."""


def _load(path: Path) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            raise ReceiptError("journal receipt is too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ReceiptError:
        raise
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        raise ReceiptError("journal receipt is unreadable") from None
    if not isinstance(value, Mapping):
        raise ReceiptError("journal receipt must be an object")
    return value


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ReceiptError(f"{label} timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceiptError(f"{label} timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _cursor(payload: Mapping[str, Any], *, label: str) -> str:
    journal = payload.get("journal")
    if not isinstance(journal, Mapping):
        raise ReceiptError(f"{label} journal cursor is missing")
    value = str(journal.get("cursor") or "").strip()
    if CURSOR_RE.fullmatch(value) is None:
        raise ReceiptError(f"{label} journal cursor is invalid")
    return value


def _summary(payload: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        raise ReceiptError(f"{label} summary is missing")
    for key in (
        "entries_scanned",
        "message_bytes_scanned",
        "entries_with_findings",
        "growth_occurrences",
    ):
        value = summary.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReceiptError(f"{label} summary is invalid")
    return summary


def _safety(payload: Mapping[str, Any], *, label: str) -> None:
    safety = payload.get("safety")
    if (
        not isinstance(safety, Mapping)
        or safety.get("read_only") is not True
        or safety.get("redacted_by_construction") is not True
        or safety.get("raw_content_included") is not False
    ):
        raise ReceiptError(f"{label} safety contract is invalid")


def _unit_set(values: Sequence[str]) -> list[str]:
    normalized = sorted(set(str(value or "").strip() for value in values))
    if not normalized or len(normalized) != len(values):
        raise ReceiptError("expected journal unit set is invalid")
    if any(UNIT_RE.fullmatch(value) is None for value in normalized):
        raise ReceiptError("expected journal unit set is invalid")
    return normalized


def validate_receipts(
    baseline: Mapping[str, Any],
    canary: Mapping[str, Any],
    *,
    expected_worker_boot_nonce_sha256: str,
    worker_not_before: datetime,
    expected_units: Sequence[str],
) -> dict[str, Any]:
    nonce = str(expected_worker_boot_nonce_sha256 or "").strip().lower()
    if SHA256_RE.fullmatch(nonce) is None:
        raise ReceiptError("expected worker boot binding is invalid")
    units = _unit_set(expected_units)
    if worker_not_before.tzinfo is None or worker_not_before.utcoffset() is None:
        raise ReceiptError("worker-not-before timestamp is invalid")
    normalized_not_before = worker_not_before.astimezone(timezone.utc)
    expected_binding = {
        "worker_boot_nonce_sha256": nonce,
        "worker_not_before": normalized_not_before.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
    }

    generated: dict[str, datetime] = {}
    for label, payload, mode in (
        ("baseline", baseline, "baseline"),
        ("canary", canary, "canary"),
    ):
        if payload.get("schema_version") != SCHEMA_VERSION or payload.get("source") != SOURCE:
            raise ReceiptError(f"{label} schema is unsupported")
        if payload.get("mode") != mode:
            raise ReceiptError(f"{label} mode is invalid")
        if payload.get("runtime_binding") != expected_binding:
            raise ReceiptError(f"{label} worker boot binding does not match")
        if payload.get("reviewed_units") != units:
            raise ReceiptError(f"{label} reviewed journal unit set does not match")
        generated[label] = _utc(payload.get("generated_at"), label=label)
        if generated[label] < normalized_not_before:
            raise ReceiptError(f"{label} predates the reviewed worker restart")
        if payload.get("status") != "clean":
            raise ReceiptError(f"{label} is not clean")
        _safety(payload, label=label)
    if generated["canary"] < generated["baseline"]:
        raise ReceiptError("canary receipt predates baseline receipt")

    pattern_sha = str(baseline.get("pattern_set_sha256") or "")
    if SHA256_RE.fullmatch(pattern_sha) is None:
        raise ReceiptError("baseline pattern set is invalid")
    if canary.get("pattern_set_sha256") != pattern_sha:
        raise ReceiptError("canary pattern set differs from baseline")

    baseline_cursor = _cursor(baseline, label="baseline")
    canary_cursor = _cursor(canary, label="canary")
    baseline_summary = _summary(baseline, label="baseline")
    canary_summary = _summary(canary, label="canary")
    if any(
        int(baseline_summary[key]) != 0
        for key in (
            "entries_scanned",
            "message_bytes_scanned",
            "entries_with_findings",
            "growth_occurrences",
        )
    ):
        raise ReceiptError("baseline is not a cursor-only boundary")
    if baseline_summary.get("categories") not in ({}, None):
        raise ReceiptError("baseline unexpectedly contains finding categories")

    journal = canary.get("journal")
    if not isinstance(journal, Mapping):  # defensive; _cursor already checks this
        raise ReceiptError("canary journal cursor is missing")
    expected_after_hash = hashlib.sha256(baseline_cursor.encode("ascii")).hexdigest()
    if journal.get("after_cursor_sha256") != expected_after_hash:
        raise ReceiptError("canary is not bound to the baseline cursor")
    if canary_cursor == baseline_cursor:
        raise ReceiptError("canary journal cursor did not advance")
    if int(canary_summary["entries_scanned"]) <= 0:
        raise ReceiptError("canary observed no post-baseline journal entries")
    if int(canary_summary["message_bytes_scanned"]) <= 0:
        raise ReceiptError("canary observed no post-baseline journal bytes")
    if int(canary_summary["growth_occurrences"]) != 0 or int(
        canary_summary["entries_with_findings"]
    ) != 0:
        raise ReceiptError("canary contains post-baseline sensitive findings")
    if canary_summary.get("categories") not in ({}, None):
        raise ReceiptError("canary contains post-baseline finding categories")

    return {
        "pass": True,
        "source": SOURCE,
        "units": len(units),
        "entries_scanned": int(canary_summary["entries_scanned"]),
        "message_bytes_scanned": int(canary_summary["message_bytes_scanned"]),
        "growth_occurrences": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate cursor-bound systemd journal canary receipts")
    parser.add_argument("--baseline-state", required=True)
    parser.add_argument("--canary-report", required=True)
    parser.add_argument("--expected-worker-boot-nonce-sha256", required=True)
    parser.add_argument("--worker-not-before", required=True)
    parser.add_argument("--unit", action="append", default=[], required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_receipts(
            _load(Path(args.baseline_state)),
            _load(Path(args.canary_report)),
            expected_worker_boot_nonce_sha256=args.expected_worker_boot_nonce_sha256,
            worker_not_before=_utc(args.worker_not_before, label="worker-not-before"),
            expected_units=args.unit,
        )
    except ReceiptError as exc:
        stdout_out(f"FAIL runtime-journal-canary: {exc}", file=sys.stderr)
        return 1
    stdout_out(
        "PASS runtime-journal-canary "
        f"units={result['units']} entries={result['entries_scanned']} "
        f"bytes={result['message_bytes_scanned']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
