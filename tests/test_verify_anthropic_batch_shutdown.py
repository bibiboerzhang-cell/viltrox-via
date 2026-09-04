from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "verify_anthropic_batch_shutdown.py"
SPEC = importlib.util.spec_from_file_location("verify_anthropic_batch_shutdown", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


class FakePage:
    def __init__(self, rows: list[tuple[str, str]], next_page: "FakePage | None" = None):
        self.data = [SimpleNamespace(id=batch_id, processing_status=status) for batch_id, status in rows]
        self._next_page = next_page

    def has_next_page(self) -> bool:
        return self._next_page is not None

    def get_next_page(self) -> "FakePage":
        assert self._next_page is not None
        return self._next_page


def _client(page: FakePage) -> SimpleNamespace:
    batches = SimpleNamespace(list=lambda **_kwargs: page)
    return SimpleNamespace(messages=SimpleNamespace(batches=batches))


def test_provider_receipt_scans_every_page_without_emitting_ids() -> None:
    final = FakePage([("msgbatch_3", "ended")])
    first = FakePage(
        [("msgbatch_1", "ended"), ("msgbatch_2", "ended")],
        final,
    )

    receipt = subject.build_provider_shutdown_receipt(_client(first))

    assert receipt == {
        "schema_version": "vkpi-anthropic-batch-shutdown/v1",
        "passed": True,
        "provider": "anthropic",
        "provider_scope": "api_key_workspace",
        "reconcile_complete": True,
        "pages_scanned": 2,
        "batches_scanned": 3,
        "active_count": 0,
        "ended_count": 3,
        "credentials_emitted": False,
        "batch_ids_emitted": False,
        "reason": "",
    }


@pytest.mark.parametrize("status", ["in_progress", "canceling"])
def test_provider_receipt_blocks_every_active_provider_status(status: str) -> None:
    receipt = subject.build_provider_shutdown_receipt(
        _client(FakePage([("msgbatch_active", status)]))
    )

    assert receipt["passed"] is False
    assert receipt["active_count"] == 1
    assert receipt["reason"] == "provider_batches_active"


@pytest.mark.parametrize(
    ("page", "reason"),
    [
        (FakePage([("msgbatch_unknown", "expired")]), "provider_batch_status_unknown"),
        (
            FakePage(
                [("msgbatch_duplicate", "ended")],
                FakePage([("msgbatch_duplicate", "ended")]),
            ),
            "provider_batch_identity_invalid",
        ),
    ],
)
def test_provider_receipt_fails_closed_on_ambiguous_list(page: FakePage, reason: str) -> None:
    with pytest.raises(subject.ProviderProofError, match=reason):
        subject.build_provider_shutdown_receipt(_client(page))


def test_cli_fails_closed_without_provider_credentials() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        env={},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )

    receipt = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert receipt["passed"] is False
    assert receipt["reason"] == "anthropic_api_key_missing"
    assert receipt["credentials_emitted"] is False
