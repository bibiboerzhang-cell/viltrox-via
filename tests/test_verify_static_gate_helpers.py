from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.verify_static_gate_helpers import (
    LINE_GUARD_ALLOWLIST,
    check_release_line_guard,
    validate_npm_audit_receipt,
)


def test_trusted_npm_audit_receipt_is_bound_to_lock_bytes(tmp_path: Path) -> None:
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "vkpi.controller-npm-audit/v1",
                "passed": True,
                "returncode": 0,
                "package_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    validate_npm_audit_receipt(receipt, lock)
    lock.write_text('{"lockfileVersion":2}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="receipt mismatch"):
        validate_npm_audit_receipt(receipt, lock)


def test_release_line_guard_has_zero_allowlist_and_rejects_debt(
    tmp_path: Path,
) -> None:
    assert LINE_GUARD_ALLOWLIST == frozenset()
    violation = tmp_path / "backend/app/new_debt.py"
    violation.parent.mkdir(parents=True, exist_ok=True)
    violation.write_text("x\n" * 1001, encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        check_release_line_guard(tmp_path)
    assert raised.value.code == 1
