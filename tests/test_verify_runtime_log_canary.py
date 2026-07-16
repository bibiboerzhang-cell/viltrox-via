from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from scripts.verify_runtime_log_canary import ReceiptError, main, validate_receipts


PATTERN_SHA = "a" * 64
WORKER_BOOT_SHA256 = "b" * 64
RESTARTED_AT = datetime(2026, 7, 14, 5, 0, tzinfo=timezone.utc)
EXPECTED_WORKER_COUNT = 3
EXPECTED_REDIS_WORKER_COUNT = 1
EXPECTED_LOGS = frozenset(
    {
        "runtime/logs/admin-8102-access.log",
        "runtime/logs/admin-8102-error.log",
        "runtime/logs/worker-interactive.log",
        "runtime/logs/worker-bulk1.log",
        "runtime/logs/worker-bulk2.log",
        "runtime/logs/worker-1.log",
        "runtime/logs/worker.log",
    }
)


def _state(offset: int) -> dict:
    return {
        "files": {
            label: {"next_baseline_offset": offset, "size_bytes": offset}
            for label in EXPECTED_LOGS
        }
    }


def _baseline() -> dict:
    return {
        "schema_version": 2,
        "generated_at": "2026-07-14T05:01:00Z",
        "runtime_binding": {
            "worker_boot_nonce_sha256": WORKER_BOOT_SHA256,
            "worker_not_before": "2026-07-14T05:00:00.000000Z",
        },
        "status": "historical_findings_only",
        "safety": {
            "read_only": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        },
        "pattern_set_sha256": PATTERN_SHA,
        "summary": {
            "historical_occurrences": 8,
            "growth_occurrences": 0,
            "unscanned_files": 0,
            "truncated_files": 0,
            "unscanned_tail_files": 0,
        },
        "scan_state": _state(100),
    }


def _canary() -> dict:
    rows = []
    for index, label in enumerate(sorted(EXPECTED_LOGS)):
        rows.append(
            {
                "file": label,
                "status": "scanned",
                "baseline_source": "provided",
                "baseline_offset": 100,
                "growth_byte_range": [100, 120],
                "growth_bytes_scanned": 20 if index == 0 else 0,
                "raw_content_included": False,
            }
        )
    return {
        "schema_version": 2,
        "generated_at": "2026-07-14T05:02:00Z",
        "runtime_binding": {
            "worker_boot_nonce_sha256": WORKER_BOOT_SHA256,
            "worker_not_before": "2026-07-14T05:00:00.000000Z",
        },
        "status": "historical_findings_only",
        "safety": {
            "read_only": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        },
        "pattern_set_sha256": PATTERN_SHA,
        "summary": {
            "historical_occurrences": 8,
            "growth_occurrences": 0,
            "unscanned_files": 0,
            "truncated_files": 0,
            "unscanned_tail_files": 0,
        },
        "files": rows,
        "scan_state": _state(120),
    }


def _validate(baseline: dict, canary: dict, *, boot_sha: str = WORKER_BOOT_SHA256) -> dict:
    return validate_receipts(
        baseline,
        canary,
        expected_worker_boot_nonce_sha256=boot_sha,
        worker_not_before=RESTARTED_AT,
        expected_logs=EXPECTED_LOGS,
        expected_worker_count=EXPECTED_WORKER_COUNT,
        expected_redis_worker_count=EXPECTED_REDIS_WORKER_COUNT,
    )


def test_complete_post_restart_canary_with_real_growth_passes() -> None:
    result = _validate(_baseline(), _canary())
    assert result == {
        "pass": True,
        "files": 7,
        "growth_bytes_scanned": 20,
        "growth_occurrences": 0,
    }


def test_baseline_must_be_newer_than_reviewed_restart() -> None:
    baseline = _baseline()
    baseline["generated_at"] = "2026-07-14T04:59:59Z"
    with pytest.raises(ReceiptError, match="predates"):
        _validate(baseline, _canary())


def test_partial_baseline_cannot_silently_rebase_missing_log_to_current_eof() -> None:
    baseline = _baseline()
    baseline["scan_state"]["files"].pop("runtime/logs/admin-8102-access.log")
    with pytest.raises(ReceiptError, match="expected runtime log manifest"):
        _validate(baseline, _canary())


def test_canary_must_use_every_provided_offset() -> None:
    canary = _canary()
    canary["files"][0]["baseline_source"] = "scan_start"
    with pytest.raises(ReceiptError, match="replaced a supplied baseline"):
        _validate(_baseline(), canary)


def test_canary_offset_must_equal_bound_baseline_receipt() -> None:
    canary = _canary()
    canary["files"][0]["baseline_offset"] = 99
    canary["files"][0]["growth_byte_range"] = [99, 120]
    with pytest.raises(ReceiptError, match="baseline offset differs"):
        _validate(_baseline(), canary)


def test_zero_byte_canary_cannot_claim_the_exercised_paths_are_clean() -> None:
    canary = _canary()
    for row in canary["files"]:
        row["growth_bytes_scanned"] = 0
    with pytest.raises(ReceiptError, match="no post-baseline"):
        _validate(_baseline(), canary)


def test_pattern_set_change_invalidates_old_baseline() -> None:
    canary = deepcopy(_canary())
    canary["pattern_set_sha256"] = "b" * 64
    with pytest.raises(ReceiptError, match="pattern set differs"):
        _validate(_baseline(), canary)


def test_worker_boot_binding_cannot_be_reused_across_deployments() -> None:
    with pytest.raises(ReceiptError, match="worker boot binding"):
        _validate(_baseline(), _canary(), boot_sha="c" * 64)


def test_receipt_producer_cannot_shrink_the_reviewed_fleet_manifest() -> None:
    baseline = _baseline()
    canary = _canary()
    for label in (
        "runtime/logs/worker-bulk2.log",
        "runtime/logs/worker-interactive.log",
    ):
        baseline["scan_state"]["files"].pop(label)
        canary["scan_state"]["files"].pop(label)
        canary["files"] = [row for row in canary["files"] if row["file"] != label]
    with pytest.raises(ReceiptError, match="expected runtime log manifest"):
        _validate(baseline, canary)


def test_verifier_caller_cannot_shrink_the_reviewed_fleet_manifest() -> None:
    with pytest.raises(ReceiptError, match="reviewed fleet shape"):
        validate_receipts(
            _baseline(),
            _canary(),
            expected_worker_boot_nonce_sha256=WORKER_BOOT_SHA256,
            worker_not_before=RESTARTED_AT,
            expected_logs=EXPECTED_LOGS - {"runtime/logs/worker-bulk2.log"},
            expected_worker_count=EXPECTED_WORKER_COUNT,
            expected_redis_worker_count=EXPECTED_REDIS_WORKER_COUNT,
        )


def test_legacy_worker_log_is_optional_for_a_multi_lane_fleet() -> None:
    baseline = _baseline()
    canary = _canary()
    baseline["scan_state"]["files"].pop("runtime/logs/worker.log")
    canary["scan_state"]["files"].pop("runtime/logs/worker.log")
    canary["files"] = [
        row for row in canary["files"] if row["file"] != "runtime/logs/worker.log"
    ]
    result = validate_receipts(
        baseline,
        canary,
        expected_worker_boot_nonce_sha256=WORKER_BOOT_SHA256,
        worker_not_before=RESTARTED_AT,
        expected_logs=EXPECTED_LOGS - {"runtime/logs/worker.log"},
        expected_worker_count=EXPECTED_WORKER_COUNT,
        expected_redis_worker_count=EXPECTED_REDIS_WORKER_COUNT,
    )
    assert result["files"] == 6


def test_added_log_invalidates_the_exact_manifest() -> None:
    baseline = _baseline()
    baseline["scan_state"]["files"]["runtime/logs/worker-bulk3.log"] = {
        "next_baseline_offset": 100,
        "size_bytes": 100,
    }
    with pytest.raises(ReceiptError, match="expected runtime log manifest"):
        _validate(baseline, _canary())


def test_expected_manifest_rejects_path_escape() -> None:
    with pytest.raises(ReceiptError, match="invalid path"):
        validate_receipts(
            _baseline(),
            _canary(),
            expected_worker_boot_nonce_sha256=WORKER_BOOT_SHA256,
            worker_not_before=RESTARTED_AT,
            expected_logs=[*EXPECTED_LOGS, "../outside.log"],
            expected_worker_count=EXPECTED_WORKER_COUNT,
            expected_redis_worker_count=EXPECTED_REDIS_WORKER_COUNT,
        )


def test_deploy_cli_argument_contract_validates_both_receipts(tmp_path, capsys) -> None:
    baseline_path = tmp_path / "baseline.json"
    canary_path = tmp_path / "canary.json"
    baseline_path.write_text(json.dumps(_baseline()))
    canary_path.write_text(json.dumps(_canary()))

    exit_code = main(
        [
            "--baseline-state",
            str(baseline_path),
            "--canary-report",
            str(canary_path),
            "--expected-worker-boot-nonce-sha256",
            WORKER_BOOT_SHA256,
            "--worker-not-before",
            "2026-07-14T05:00:00Z",
            "--expected-worker-count",
            str(EXPECTED_WORKER_COUNT),
            "--expected-redis-worker-count",
            str(EXPECTED_REDIS_WORKER_COUNT),
            *[
                value
                for label in sorted(EXPECTED_LOGS)
                for value in ("--expected-log", label)
            ],
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "PASS" in captured.out
    assert WORKER_BOOT_SHA256 not in captured.out + captured.err
