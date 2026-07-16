#!/usr/bin/env python3
"""Stream runtime logs for media credential/signature leak indicators.

Safety properties:

* read-only: source logs and baseline state are never modified;
* streaming: files are processed one binary line at a time;
* redacted by construction: output contains no matched text, URL, query value,
  access-key id or token; only paths, offsets, counts, categories and line ranges;
* baseline-aware: a prior report's ``scan_state`` (or explicit offsets) divides
  historical findings from bytes appended after the baseline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCRIPT_VERSION = 2
DEFAULT_LOG_PATHS = (
    "runtime/logs/worker.log",
    "runtime/logs/admin-8102-access.log",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

_QUERY_BOUNDARY = rb"(?:[?&]|%3[fF]|%26|%253[fF]|%2526)"
_QUERY_ASSIGN = rb"(?:=|%3[dD]|%253[dD])"

# Pattern names are the complete public vocabulary.  Regex matches are never
# serialized, hashed individually or placed in an exception message.
PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    (
        "media_proxy_embedded_url",
        re.compile(
            rb"/api/admin/vkpi/media/(?:image-proxy|video-proxy|video-redirect)"
            rb"(?:\?|%3[fF])url(?:=|%3[dD])",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_authorization_credential",
        re.compile(rb"AWS4-HMAC-SHA256(?:\s|%20)+Credential(?:=|%3[dD]|%253[dD])", re.IGNORECASE),
    ),
    (
        "aws_query_credential",
        re.compile(_QUERY_BOUNDARY + rb"x-amz-credential" + _QUERY_ASSIGN, re.IGNORECASE),
    ),
    (
        "aws_query_signature",
        re.compile(_QUERY_BOUNDARY + rb"x-amz-signature" + _QUERY_ASSIGN, re.IGNORECASE),
    ),
    (
        "legacy_aws_query_access_key",
        re.compile(_QUERY_BOUNDARY + rb"awsaccesskeyid" + _QUERY_ASSIGN, re.IGNORECASE),
    ),
    (
        "authorization_bearer",
        re.compile(rb"authorization(?:['\"\s:]|%3[aA]){0,8}bearer(?:\s|%20)+", re.IGNORECASE),
    ),
    (
        "query_token",
        re.compile(
            _QUERY_BOUNDARY + rb"(?:access[_-]?token|refresh[_-]?token|id[_-]?token|token)" + _QUERY_ASSIGN,
            re.IGNORECASE,
        ),
    ),
    (
        "query_api_or_access_key",
        re.compile(
            _QUERY_BOUNDARY + rb"(?:api[_-]?key|access[_-]?key(?:[_-]?id)?)" + _QUERY_ASSIGN,
            re.IGNORECASE,
        ),
    ),
    (
        "query_secret",
        re.compile(
            _QUERY_BOUNDARY + rb"(?:secret(?:[_-]?key)?|client[_-]?secret)" + _QUERY_ASSIGN,
            re.IGNORECASE,
        ),
    ),
    (
        "query_signature",
        re.compile(_QUERY_BOUNDARY + rb"(?:signature|x-signature|sig)" + _QUERY_ASSIGN, re.IGNORECASE),
    ),
)
_PREFILTER_MARKERS = (
    b"media/image-proxy",
    b"media/video-proxy",
    b"media/video-redirect",
    b"credential",
    b"signature",
    b"authorization",
    b"awsaccesskeyid",
    b"access_token",
    b"refresh_token",
    b"id_token",
    b"api_key",
    b"access_key",
    b"client_secret",
    b"secret",
    b"token",
)


class AuditInputError(ValueError):
    """Safe, category-only input error."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _normalize_utc_timestamp(value: str, *, category: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise AuditInputError(category) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditInputError(category)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_sha256(value: str, *, category: str) -> str:
    normalized = str(value).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise AuditInputError(category)
    return normalized


def _runtime_binding(
    worker_boot_nonce_sha256: str | None,
    worker_not_before: str | None,
) -> dict[str, str | None]:
    nonce_value = str(worker_boot_nonce_sha256 or "").strip()
    not_before_value = str(worker_not_before or "").strip()
    if bool(nonce_value) != bool(not_before_value):
        raise AuditInputError("runtime_binding_incomplete")
    if not nonce_value:
        return {
            "worker_boot_nonce_sha256": None,
            "worker_not_before": None,
        }
    return {
        "worker_boot_nonce_sha256": _normalize_sha256(
            nonce_value,
            category="worker_boot_nonce_sha256_invalid",
        ),
        "worker_not_before": _normalize_utc_timestamp(
            not_before_value,
            category="worker_not_before_invalid",
        ),
    }


@dataclass
class _CategoryAccumulator:
    max_ranges: int
    occurrences: int = 0
    line_count: int = 0
    first_line: int | None = None
    last_line: int | None = None
    range_count: int = 0
    ranges: list[list[int]] = field(default_factory=list)
    ranges_truncated: bool = False
    _last_observed_line: int | None = None

    def add(self, line_number: int, occurrences: int) -> None:
        if occurrences <= 0:
            return
        self.occurrences += int(occurrences)
        if self._last_observed_line == line_number:
            return
        self.line_count += 1
        self.first_line = line_number if self.first_line is None else self.first_line
        self.last_line = line_number

        is_new_range = self._last_observed_line is None or line_number != self._last_observed_line + 1
        if is_new_range:
            self.range_count += 1
            if len(self.ranges) < self.max_ranges:
                self.ranges.append([line_number, line_number])
            else:
                self.ranges_truncated = True
        elif self.ranges and self.ranges[-1][1] == self._last_observed_line:
            self.ranges[-1][1] = line_number
        self._last_observed_line = line_number

    def as_dict(self) -> dict[str, Any]:
        return {
            "occurrences": self.occurrences,
            "lines": self.line_count,
            "first_line": self.first_line,
            "last_line": self.last_line,
            "range_count": self.range_count,
            "line_ranges": self.ranges,
            "ranges_truncated": self.ranges_truncated,
        }


def _pattern_set_sha256() -> str:
    public_spec = "\n".join(f"{name}:{pattern.pattern!r}" for name, pattern in PATTERNS)
    return hashlib.sha256(public_spec.encode("utf-8")).hexdigest()


def _report_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _safe_file_error(path_label: str, category: str) -> dict[str, Any]:
    return {
        "file": path_label,
        "status": "unscanned",
        "error_category": category,
        "raw_content_included": False,
    }


def _extract_state_files(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    direct = payload.get("scan_state")
    if isinstance(direct, Mapping) and isinstance(direct.get("files"), Mapping):
        return direct["files"]
    if isinstance(payload.get("files"), Mapping):
        return payload["files"]
    nested = payload.get("log_leak_audit")
    if isinstance(nested, Mapping):
        scan_state = nested.get("scan_state")
        if isinstance(scan_state, Mapping) and isinstance(scan_state.get("files"), Mapping):
            return scan_state["files"]
    raise AuditInputError("baseline_state_missing_files")


def load_baseline_report(path: Path) -> Mapping[str, Any]:
    """Load one redacted baseline report without exposing any receipt values."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise AuditInputError("baseline_state_not_found") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AuditInputError("baseline_state_invalid") from None
    if not isinstance(payload, Mapping):
        raise AuditInputError("baseline_state_invalid")
    return payload


def baseline_offsets(payload: Mapping[str, Any]) -> dict[str, int]:
    """Extract only reviewed non-negative byte offsets from a report."""

    offsets: dict[str, int] = {}
    for file_label, state in _extract_state_files(payload).items():
        if not isinstance(file_label, str) or not isinstance(state, Mapping):
            continue
        raw_offset = state.get("next_baseline_offset", state.get("size_bytes"))
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            continue
        if offset >= 0:
            offsets[file_label] = offset
    if not offsets:
        raise AuditInputError("baseline_state_has_no_valid_offsets")
    return offsets


def load_baseline_offsets(path: Path) -> dict[str, int]:
    """Load only safe file offsets from a previous report."""

    return baseline_offsets(load_baseline_report(path))


def validate_complete_baseline(
    payload: Mapping[str, Any],
    *,
    expected_binding: Mapping[str, str | None],
    expected_files: set[str],
) -> None:
    """Require a complete, current-pattern baseline bound to this worker boot."""

    if payload.get("schema_version") != SCRIPT_VERSION:
        raise AuditInputError("baseline_schema_unsupported")
    if payload.get("runtime_binding") != expected_binding:
        raise AuditInputError("baseline_runtime_binding_mismatch")
    if payload.get("pattern_set_sha256") != _pattern_set_sha256():
        raise AuditInputError("baseline_pattern_set_mismatch")
    if str(payload.get("status") or "") not in {"clean", "historical_findings_only"}:
        raise AuditInputError("baseline_status_not_clean")
    safety = payload.get("safety")
    if (
        not isinstance(safety, Mapping)
        or safety.get("read_only") is not True
        or safety.get("redacted_by_construction") is not True
        or safety.get("raw_content_included") is not False
    ):
        raise AuditInputError("baseline_safety_invalid")
    summary = payload.get("summary")
    if not isinstance(summary, Mapping) or any(
        summary.get(key) != 0
        for key in ("unscanned_files", "truncated_files", "unscanned_tail_files")
    ):
        raise AuditInputError("baseline_incomplete")
    state_files = _extract_state_files(payload)
    if set(state_files) != expected_files:
        raise AuditInputError("baseline_file_set_incomplete")
    if set(baseline_offsets(payload)) != expected_files:
        raise AuditInputError("baseline_offsets_incomplete")
    generated_at = _normalize_utc_timestamp(
        str(payload.get("generated_at") or ""),
        category="baseline_generated_at_invalid",
    )
    not_before = str(expected_binding.get("worker_not_before") or "")
    if not not_before:
        raise AuditInputError("runtime_binding_required")
    if datetime.fromisoformat(generated_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        not_before.replace("Z", "+00:00")
    ):
        raise AuditInputError("baseline_predates_worker_restart")


def _resolve_baseline_offset(path: Path, label: str, offsets: Mapping[str, int], default: int) -> tuple[int, str]:
    candidates = (label, path.as_posix(), path.resolve().as_posix())
    for candidate in candidates:
        if candidate in offsets:
            value = int(offsets[candidate])
            if value < 0:
                raise AuditInputError("negative_baseline_offset")
            return value, "provided"
    return int(default), "scan_start"


def _empty_segment(max_ranges: int) -> dict[str, _CategoryAccumulator]:
    return {name: _CategoryAccumulator(max_ranges=max_ranges) for name, _pattern in PATTERNS}


def _serialize_segment(
    accumulators: Mapping[str, _CategoryAccumulator],
    lines_with_findings: int,
) -> dict[str, Any]:
    categories = {
        name: accumulator.as_dict()
        for name, accumulator in accumulators.items()
        if accumulator.occurrences > 0
    }
    return {
        "occurrences": sum(item.occurrences for item in accumulators.values()),
        "lines_with_findings": int(lines_with_findings),
        "categories": categories,
    }


def audit_file(
    path: Path,
    *,
    root: Path,
    baseline_offsets: Mapping[str, int],
    max_ranges: int,
) -> dict[str, Any]:
    """Stream one file and return a redacted finding summary."""

    label = _report_path(path, root)
    if max_ranges < 1:
        raise AuditInputError("max_ranges_out_of_range")
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return _safe_file_error(label, "file_not_found")
    except OSError:
        return _safe_file_error(label, "file_unreadable")

    historical = _empty_segment(max_ranges)
    growth = _empty_segment(max_ranges)
    segment_line_counts = {"historical": 0, "growth": 0}
    bytes_scanned = 0
    line_count = 0
    boundary_line: int | None = None

    with handle:
        try:
            size_at_start = int(os.fstat(handle.fileno()).st_size)
        except OSError:
            return _safe_file_error(label, "file_stat_failed")
        baseline_offset, baseline_source = _resolve_baseline_offset(path, label, baseline_offsets, size_at_start)

        try:
            for line_count, line in enumerate(handle, start=1):
                line_start = bytes_scanned
                line_end = line_start + len(line)
                if line_start < baseline_offset < line_end:
                    boundary_line = line_count
                lowered = line.lower()
                if not any(marker in lowered for marker in _PREFILTER_MARKERS):
                    bytes_scanned = line_end
                    continue
                found_segments: set[str] = set()
                per_line: dict[tuple[str, str], int] = {}
                for category, pattern in PATTERNS:
                    for match in pattern.finditer(line):
                        absolute_offset = line_start + match.start()
                        segment = "historical" if absolute_offset < baseline_offset else "growth"
                        key = (segment, category)
                        per_line[key] = per_line.get(key, 0) + 1
                        found_segments.add(segment)
                for (segment, category), occurrences in per_line.items():
                    target = historical if segment == "historical" else growth
                    target[category].add(line_count, occurrences)
                for segment in found_segments:
                    segment_line_counts[segment] += 1
                bytes_scanned = line_end
        except OSError:
            return _safe_file_error(label, "file_read_failed")

    try:
        size_at_end = int(path.stat().st_size)
    except OSError:
        size_at_end = bytes_scanned

    historical_summary = _serialize_segment(historical, segment_line_counts["historical"])
    growth_summary = _serialize_segment(growth, segment_line_counts["growth"])
    truncated = size_at_start < baseline_offset or size_at_end < baseline_offset
    unscanned_tail_bytes = max(0, size_at_end - bytes_scanned)
    complete_snapshot = not truncated and unscanned_tail_bytes == 0
    return {
        "file": label,
        "status": "scanned",
        "size_at_start": size_at_start,
        "size_at_end": size_at_end,
        "bytes_scanned": bytes_scanned,
        "lines_scanned": line_count,
        "baseline_offset": baseline_offset,
        "baseline_source": baseline_source,
        "historical_byte_range": [0, min(baseline_offset, bytes_scanned)],
        "growth_byte_range": [min(baseline_offset, bytes_scanned), bytes_scanned],
        "growth_bytes_scanned": max(0, bytes_scanned - baseline_offset),
        "unscanned_tail_bytes": unscanned_tail_bytes,
        "baseline_split_line": boundary_line,
        "truncated_since_baseline": truncated,
        "historical": historical_summary,
        "growth": growth_summary,
        "next_baseline_offset": bytes_scanned if complete_snapshot else None,
        "raw_content_included": False,
    }


def audit_files(
    paths: Iterable[Path],
    *,
    root: Path,
    baseline_offsets: Mapping[str, int] | None = None,
    max_ranges: int = 50,
    runtime_binding: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    offsets = dict(baseline_offsets or {})
    file_results = [
        audit_file(Path(path), root=root, baseline_offsets=offsets, max_ranges=max_ranges)
        for path in paths
    ]
    historical_occurrences = sum(
        int(item.get("historical", {}).get("occurrences", 0)) for item in file_results
    )
    growth_occurrences = sum(int(item.get("growth", {}).get("occurrences", 0)) for item in file_results)
    unscanned_files = sum(1 for item in file_results if item.get("status") != "scanned")
    truncated_files = sum(1 for item in file_results if item.get("truncated_since_baseline"))
    unscanned_tail_files = sum(1 for item in file_results if int(item.get("unscanned_tail_bytes", 0)) > 0)

    if unscanned_files or truncated_files or unscanned_tail_files:
        status = "incomplete"
    elif growth_occurrences:
        status = "new_findings"
    elif historical_occurrences:
        status = "historical_findings_only"
    else:
        status = "clean"

    state_files = {
        str(item["file"]): {
            "next_baseline_offset": int(item["next_baseline_offset"]),
            "size_bytes": int(item["next_baseline_offset"]),
        }
        for item in file_results
        if item.get("status") == "scanned" and item.get("next_baseline_offset") is not None
    }
    return {
        "schema_version": SCRIPT_VERSION,
        "generated_at": utc_now(),
        "runtime_binding": dict(runtime_binding or _runtime_binding(None, None)),
        "status": status,
        "safety": {
            "read_only": True,
            "streaming": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        },
        "pattern_categories": [name for name, _pattern in PATTERNS],
        "pattern_set_sha256": _pattern_set_sha256(),
        "summary": {
            "historical_occurrences": historical_occurrences,
            "growth_occurrences": growth_occurrences,
            "unscanned_files": unscanned_files,
            "truncated_files": truncated_files,
            "unscanned_tail_files": unscanned_tail_files,
        },
        "files": file_results,
        "scan_state": {"files": state_files},
    }


def _parse_explicit_offsets(values: Sequence[str], root: Path) -> dict[str, int]:
    offsets: dict[str, int] = {}
    for value in values:
        label, separator, raw_offset = str(value).rpartition("=")
        if not separator or not label:
            raise AuditInputError("baseline_offset_invalid")
        try:
            offset = int(raw_offset)
        except ValueError:
            raise AuditInputError("baseline_offset_invalid") from None
        if offset < 0:
            raise AuditInputError("negative_baseline_offset")
        candidate = Path(label)
        resolved = candidate if candidate.is_absolute() else root / candidate
        offsets[label] = offset
        offsets[resolved.resolve().as_posix()] = offset
    return offsets


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only redacted runtime media log leak audit")
    parser.add_argument("files", nargs="*", help="Log files; defaults to worker and admin access logs")
    parser.add_argument("--root", default="", help="Repository root used for relative file labels")
    parser.add_argument("--baseline-state", default="", help="Previous redacted audit JSON")
    parser.add_argument("--worker-boot-nonce-sha256", default="")
    parser.add_argument("--worker-not-before", default="")
    parser.add_argument(
        "--require-complete-baseline",
        action="store_true",
        help="Fail unless the baseline is complete and bound to this worker restart.",
    )
    parser.add_argument(
        "--baseline-offset",
        action="append",
        default=[],
        metavar="FILE=BYTES",
        help="Explicit historical byte boundary; may be repeated",
    )
    parser.add_argument("--max-ranges", type=int, default=50, help="Maximum emitted line ranges per category")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    parser.add_argument("--fail-on-new", action="store_true", help="Exit 3 when growth contains findings")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[2]
    file_args = args.files or list(DEFAULT_LOG_PATHS)
    paths = [Path(value) if Path(value).is_absolute() else repo_root / value for value in file_args]

    try:
        binding = _runtime_binding(
            args.worker_boot_nonce_sha256,
            args.worker_not_before,
        )
        if args.require_complete_baseline and not binding.get("worker_boot_nonce_sha256"):
            raise AuditInputError("runtime_binding_required")
        offsets: dict[str, int] = {}
        if args.baseline_state:
            baseline_path = Path(args.baseline_state)
            if not baseline_path.is_absolute():
                baseline_path = repo_root / baseline_path
            baseline_payload = load_baseline_report(baseline_path)
            if args.require_complete_baseline:
                validate_complete_baseline(
                    baseline_payload,
                    expected_binding=binding,
                    expected_files={_report_path(path, repo_root) for path in paths},
                )
            offsets.update(baseline_offsets(baseline_payload))
        elif args.require_complete_baseline:
            raise AuditInputError("baseline_state_required")
        offsets.update(_parse_explicit_offsets(args.baseline_offset, repo_root))
        report = audit_files(
            paths,
            root=repo_root,
            baseline_offsets=offsets,
            max_ranges=args.max_ranges,
            runtime_binding=binding,
        )
    except AuditInputError as exc:
        report = {
            "schema_version": SCRIPT_VERSION,
            "status": "input_error",
            "error_category": exc.category,
            "raw_content_included": False,
        }
        json.dump(report, sys.stdout, ensure_ascii=True, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2)
        sys.stdout.write("\n")
        return 2

    json.dump(report, sys.stdout, ensure_ascii=True, separators=(",", ":") if args.compact else None, indent=None if args.compact else 2)
    sys.stdout.write("\n")
    if args.fail_on_new and int(report["summary"]["growth_occurrences"]) > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
