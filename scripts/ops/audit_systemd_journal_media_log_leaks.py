#!/usr/bin/env python3
"""Read-only, cursor-bound leak audit for the reviewed systemd journal units.

The production units use ``StandardOutput=journal``.  This scanner therefore
uses one exact journal cursor as the release boundary instead of consulting the
legacy ``runtime/logs`` files.  Raw journal messages and matches are never
serialized; the receipt contains only unit names, cursors, byte/count totals and
the public finding-category vocabulary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Direct execution puts scripts/ops on sys.path.
    from audit_runtime_media_log_leaks import (
        PATTERNS,
        _PREFILTER_MARKERS,
        _normalize_sha256,
        _normalize_utc_timestamp,
        _pattern_set_sha256,
        _runtime_binding,
        AuditInputError,
    )
except ModuleNotFoundError:  # Package import in tests.
    from scripts.ops.audit_runtime_media_log_leaks import (
        PATTERNS,
        _PREFILTER_MARKERS,
        _normalize_sha256,
        _normalize_utc_timestamp,
        _pattern_set_sha256,
        _runtime_binding,
        AuditInputError,
    )


SCHEMA_VERSION = 1
SOURCE = "systemd_journal"
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_JOURNAL_OUTPUT_BYTES = 64 * 1024 * 1024
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
CURSOR_RE = re.compile(r"^[A-Za-z0-9_.:;=+,-]{8,2048}$")
CURSOR_PREFIX = b"-- cursor: "


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _cursor(value: Any, *, category: str) -> str:
    cursor = str(value or "").strip()
    if CURSOR_RE.fullmatch(cursor) is None:
        raise AuditInputError(category)
    return cursor


def _units(values: Sequence[str]) -> list[str]:
    units = sorted(set(str(value or "").strip() for value in values))
    if not units or len(units) != len(values):
        raise AuditInputError("journal_units_invalid")
    if any(UNIT_RE.fullmatch(unit) is None for unit in units):
        raise AuditInputError("journal_units_invalid")
    return units


def _safe_json(path: Path) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > MAX_RECEIPT_BYTES:
            raise AuditInputError("baseline_state_too_large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except AuditInputError:
        raise
    except FileNotFoundError:
        raise AuditInputError("baseline_state_not_found") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AuditInputError("baseline_state_invalid") from None
    if not isinstance(value, Mapping):
        raise AuditInputError("baseline_state_invalid")
    return value


def _safe_receipt(payload: Mapping[str, Any], *, binding: Mapping[str, Any], units: list[str]) -> str:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("source") != SOURCE:
        raise AuditInputError("baseline_schema_unsupported")
    if payload.get("mode") != "baseline" or payload.get("status") != "clean":
        raise AuditInputError("baseline_status_not_clean")
    if payload.get("runtime_binding") != binding:
        raise AuditInputError("baseline_runtime_binding_mismatch")
    if payload.get("reviewed_units") != units:
        raise AuditInputError("baseline_unit_set_mismatch")
    if payload.get("pattern_set_sha256") != _pattern_set_sha256():
        raise AuditInputError("baseline_pattern_set_mismatch")
    safety = payload.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(key) is not expected
        for key, expected in {
            "read_only": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        }.items()
    ):
        raise AuditInputError("baseline_safety_invalid")
    generated_at = _normalize_utc_timestamp(
        str(payload.get("generated_at") or ""),
        category="baseline_generated_at_invalid",
    )
    not_before = str(binding.get("worker_not_before") or "")
    if not not_before:
        raise AuditInputError("runtime_binding_required")
    if datetime.fromisoformat(generated_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        not_before.replace("Z", "+00:00")
    ):
        raise AuditInputError("baseline_predates_worker_restart")
    summary = payload.get("summary")
    if not isinstance(summary, Mapping) or any(
        summary.get(key) != 0
        for key in ("entries_scanned", "message_bytes_scanned", "growth_occurrences")
    ):
        raise AuditInputError("baseline_not_cursor_only")
    journal = payload.get("journal")
    if not isinstance(journal, Mapping):
        raise AuditInputError("baseline_cursor_missing")
    if journal.get("after_cursor_sha256") not in (None, ""):
        raise AuditInputError("baseline_cursor_chain_invalid")
    return _cursor(journal.get("cursor"), category="baseline_cursor_invalid")


def _message_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace")
    if isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 255
        for item in value
    ):
        return bytes(value)
    return b""


def audit_journal_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate journal JSON records without retaining their raw messages."""

    occurrences: dict[str, int] = {}
    entries_with_findings = 0
    message_bytes_scanned = 0
    for record in records:
        message = _message_bytes(record.get("MESSAGE"))
        message_bytes_scanned += len(message)
        lowered = message.lower()
        if not any(marker in lowered for marker in _PREFILTER_MARKERS):
            continue
        found = False
        for category, pattern in PATTERNS:
            count = sum(1 for _match in pattern.finditer(message))
            if count:
                occurrences[category] = occurrences.get(category, 0) + count
                found = True
        if found:
            entries_with_findings += 1
    return {
        "entries_scanned": len(records),
        "message_bytes_scanned": message_bytes_scanned,
        "entries_with_findings": entries_with_findings,
        "growth_occurrences": sum(occurrences.values()),
        "categories": {key: occurrences[key] for key in sorted(occurrences)},
    }


def _journalctl(
    *,
    binary: str,
    units: Sequence[str],
    after_cursor: str | None,
) -> tuple[str, list[Mapping[str, Any]]]:
    command = [
        binary,
        "--quiet",
        "--no-pager",
        "--output=json",
        "--show-cursor",
    ]
    if after_cursor is None:
        command.append("--lines=0")
    else:
        command.extend(("--after-cursor", after_cursor))
    for unit in units:
        command.extend(("--unit", unit))
    environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AuditInputError("journalctl_failed") from None
    if result.returncode != 0:
        raise AuditInputError("journalctl_failed")
    if len(result.stdout) > MAX_JOURNAL_OUTPUT_BYTES:
        raise AuditInputError("journal_output_too_large")

    cursor: str | None = None
    records: list[Mapping[str, Any]] = []
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith(CURSOR_PREFIX):
            try:
                cursor = _cursor(
                    raw_line[len(CURSOR_PREFIX) :].decode("ascii"),
                    category="journal_cursor_invalid",
                )
            except UnicodeDecodeError:
                raise AuditInputError("journal_cursor_invalid") from None
            continue
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AuditInputError("journal_output_invalid") from None
        if not isinstance(record, Mapping):
            raise AuditInputError("journal_output_invalid")
        records.append(record)
    if cursor is None:
        raise AuditInputError("journal_cursor_missing")
    return cursor, records


def build_report(
    *,
    units: Sequence[str],
    binding: Mapping[str, str | None],
    cursor: str,
    records: Sequence[Mapping[str, Any]],
    baseline_cursor: str | None,
) -> dict[str, Any]:
    summary = audit_journal_records(records)
    mode = "canary" if baseline_cursor is not None else "baseline"
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "mode": mode,
        "generated_at": utc_now(),
        "runtime_binding": dict(binding),
        "reviewed_units": list(units),
        "status": "new_findings" if summary["growth_occurrences"] else "clean",
        "safety": {
            "read_only": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        },
        "pattern_categories": [name for name, _pattern in PATTERNS],
        "pattern_set_sha256": _pattern_set_sha256(),
        "summary": summary,
        "journal": {
            "cursor": cursor,
            "after_cursor_sha256": (
                hashlib.sha256(baseline_cursor.encode("ascii")).hexdigest()
                if baseline_cursor is not None
                else None
            ),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only, cursor-bound leak audit of reviewed systemd journal units"
    )
    parser.add_argument("--unit", action="append", default=[], help="Reviewed systemd service unit")
    parser.add_argument("--baseline-state", default="")
    parser.add_argument("--worker-boot-nonce-sha256", default="")
    parser.add_argument("--worker-not-before", default="")
    parser.add_argument("--journalctl-bin", default="journalctl")
    parser.add_argument("--require-complete-baseline", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--fail-on-new", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        units = _units(args.unit)
        binding = _runtime_binding(
            args.worker_boot_nonce_sha256,
            args.worker_not_before,
        )
        if not binding.get("worker_boot_nonce_sha256"):
            raise AuditInputError("runtime_binding_required")
        baseline_cursor: str | None = None
        if args.baseline_state:
            baseline_cursor = _safe_receipt(
                _safe_json(Path(args.baseline_state)),
                binding=binding,
                units=units,
            )
        elif args.require_complete_baseline:
            raise AuditInputError("baseline_state_required")
        cursor, records = _journalctl(
            binary=args.journalctl_bin,
            units=units,
            after_cursor=baseline_cursor,
        )
        if baseline_cursor is None and records:
            raise AuditInputError("baseline_not_cursor_only")
        report = build_report(
            units=units,
            binding=binding,
            cursor=cursor,
            records=records,
            baseline_cursor=baseline_cursor,
        )
    except AuditInputError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "source": SOURCE,
            "status": "input_error",
            "error_category": exc.category,
            "raw_content_included": False,
        }
        json.dump(
            report,
            sys.stdout,
            ensure_ascii=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
        sys.stdout.write("\n")
        return 2

    json.dump(
        report,
        sys.stdout,
        ensure_ascii=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    sys.stdout.write("\n")
    if args.fail_on_new and int(report["summary"]["growth_occurrences"]) > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
