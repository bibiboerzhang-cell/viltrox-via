from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from scripts.ops.audit_systemd_journal_media_log_leaks import (
    audit_journal_records,
    build_report,
    main as audit_main,
)
from scripts.verify_runtime_journal_canary import ReceiptError, validate_receipts


NONCE = "b" * 64
PATTERN_SHA = "a" * 64
NOT_BEFORE = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
UNITS = [
    "viltrox-2.0-test.service",
    "vkpi-worker-interactive.service",
    "vkpi-worker-bulk@1.service",
    "vkpi-redis-worker.service",
]
BASE_CURSOR = "s=base;i=1;b=boot;m=1;t=1;x=1"
NEXT_CURSOR = "s=base;i=2;b=boot;m=2;t=2;x=2"


def _binding() -> dict[str, str]:
    return {
        "worker_boot_nonce_sha256": NONCE,
        "worker_not_before": "2026-07-15T09:00:00.000000Z",
    }


def _receipt(*, mode: str) -> dict:
    baseline = mode == "baseline"
    return {
        "schema_version": 1,
        "source": "systemd_journal",
        "mode": mode,
        "generated_at": "2026-07-15T09:01:00Z" if baseline else "2026-07-15T09:02:00Z",
        "runtime_binding": _binding(),
        "reviewed_units": sorted(UNITS),
        "status": "clean",
        "safety": {
            "read_only": True,
            "redacted_by_construction": True,
            "raw_content_included": False,
        },
        "pattern_set_sha256": PATTERN_SHA,
        "summary": {
            "entries_scanned": 0 if baseline else 4,
            "message_bytes_scanned": 0 if baseline else 128,
            "entries_with_findings": 0,
            "growth_occurrences": 0,
            "categories": {},
        },
        "journal": {
            "cursor": BASE_CURSOR if baseline else NEXT_CURSOR,
            "after_cursor_sha256": (
                None if baseline else hashlib.sha256(BASE_CURSOR.encode("ascii")).hexdigest()
            ),
        },
    }


def test_cursor_bound_journal_canary_passes_with_real_growth() -> None:
    result = validate_receipts(
        _receipt(mode="baseline"),
        _receipt(mode="canary"),
        expected_worker_boot_nonce_sha256=NONCE,
        worker_not_before=NOT_BEFORE,
        expected_units=UNITS,
    )
    assert result == {
        "pass": True,
        "source": "systemd_journal",
        "units": 4,
        "entries_scanned": 4,
        "message_bytes_scanned": 128,
        "growth_occurrences": 0,
    }


def test_journal_canary_fails_when_no_post_cursor_entry_exists() -> None:
    canary = _receipt(mode="canary")
    canary["summary"]["entries_scanned"] = 0
    canary["summary"]["message_bytes_scanned"] = 0
    with pytest.raises(ReceiptError, match="no post-baseline journal entries"):
        validate_receipts(
            _receipt(mode="baseline"),
            canary,
            expected_worker_boot_nonce_sha256=NONCE,
            worker_not_before=NOT_BEFORE,
            expected_units=UNITS,
        )


def test_journal_canary_fails_on_wrong_unit_filter_or_cursor() -> None:
    canary = _receipt(mode="canary")
    canary["reviewed_units"] = ["viltrox-2.0-test.service"]
    with pytest.raises(ReceiptError, match="unit set"):
        validate_receipts(
            _receipt(mode="baseline"),
            canary,
            expected_worker_boot_nonce_sha256=NONCE,
            worker_not_before=NOT_BEFORE,
            expected_units=UNITS,
        )

    canary = _receipt(mode="canary")
    canary["journal"]["after_cursor_sha256"] = "c" * 64
    with pytest.raises(ReceiptError, match="baseline cursor"):
        validate_receipts(
            _receipt(mode="baseline"),
            canary,
            expected_worker_boot_nonce_sha256=NONCE,
            worker_not_before=NOT_BEFORE,
            expected_units=UNITS,
        )


def test_scanner_counts_secret_patterns_without_serializing_raw_messages() -> None:
    secret_message = "GET /?X-Amz-Signature=super-secret-value"
    summary = audit_journal_records([{"MESSAGE": secret_message}, {"MESSAGE": "healthy"}])
    assert summary["growth_occurrences"] == 1
    assert summary["categories"] == {"aws_query_signature": 1}

    report = build_report(
        units=sorted(UNITS),
        binding=_binding(),
        cursor=NEXT_CURSOR,
        records=[{"MESSAGE": secret_message}],
        baseline_cursor=BASE_CURSOR,
    )
    serialized = json.dumps(report)
    assert "super-secret-value" not in serialized
    assert report["safety"]["raw_content_included"] is False


def test_any_new_sensitive_finding_fails_closed() -> None:
    canary = deepcopy(_receipt(mode="canary"))
    canary["status"] = "new_findings"
    canary["summary"]["growth_occurrences"] = 1
    canary["summary"]["entries_with_findings"] = 1
    canary["summary"]["categories"] = {"query_token": 1}
    with pytest.raises(ReceiptError, match="not clean"):
        validate_receipts(
            _receipt(mode="baseline"),
            canary,
            expected_worker_boot_nonce_sha256=NONCE,
            worker_not_before=NOT_BEFORE,
            expected_units=UNITS,
        )


def test_scanner_cli_uses_cursor_boundary_and_reads_only_growth(tmp_path, capsys) -> None:
    journalctl = tmp_path / "journalctl"
    journalctl.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *' --lines=0 '*) printf '%s\\n' '-- cursor: s=base;i=1;b=boot;m=1;t=1;x=1' ;;\n"
        "  *) printf '%s\\n' '{\"MESSAGE\":\"healthy request\"}' "
        "'-- cursor: s=base;i=2;b=boot;m=2;t=2;x=2' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    journalctl.chmod(0o700)
    common = [
        "--unit",
        "viltrox-2.0-test.service",
        "--worker-boot-nonce-sha256",
        NONCE,
        "--worker-not-before",
        "2020-01-01T00:00:00Z",
        "--journalctl-bin",
        str(journalctl),
        "--compact",
    ]
    assert audit_main(common) == 0
    baseline = json.loads(capsys.readouterr().out)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    assert audit_main([*common, "--baseline-state", str(baseline_path), "--require-complete-baseline"]) == 0
    canary = json.loads(capsys.readouterr().out)
    assert canary["mode"] == "canary"
    assert canary["summary"]["entries_scanned"] == 1
    assert canary["summary"]["message_bytes_scanned"] > 0
    assert canary["summary"]["growth_occurrences"] == 0
    assert canary["journal"]["cursor"] == NEXT_CURSOR
