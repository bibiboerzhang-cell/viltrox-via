from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from scripts.ops import audit_vkpi_post_sync_state as audit_gate


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "ops" / "baselines" / "vkpi-post-sync-baseline.json"
INVOCATION_ID = "a" * 32


def _valid_payload() -> dict:
    task_ids = ["official-1", "kol-1"]
    completion = {
        "complete": True,
        "completion_scope": "provider_terminal",
        "provider_completion": "completed",
        "tasks_pending": 0,
    }
    return {
        "service_state": "inactive",
        "runtime_guard": {"terminal_success": True, "verified": True},
        "official_channels": {"active_channels": 18, "synced_channels": 18},
        "kol_pool": {"legacy_excel_p2d": 1011},
        "sync_log": {
            "batch_id": "daily-1",
            "latest_invocation_complete": True,
            "maintenance_completed": True,
            "finished_summary": {
                "status": "completed",
                "completion_scope": "provider_terminal",
                "provider_completion": "completed",
                "tasks_pending": 0,
                "task_ids": task_ids,
            },
        },
        "daily_batch": {
            "task_links_match": True,
            "parent": {"status": "completed", "finished_at": "2026-08-31T09:00:00Z"},
            "parent_summary": {
                "status": "completed",
                "completion_scope": "provider_terminal",
                "provider_completion": "completed",
                "completion": completion,
            },
            "ledger": {"all_terminal": True, "all_successful": True, "error": ""},
            "provider_claims": {"all_reconciled": True, "error": ""},
        },
        "official_run_evidence": {
            "active": 18,
            "expected_official_tasks": 18,
            "task_link_errors": [],
            "synced_since_parent": 18,
            "metrics_since_parent": 18,
            "provider_provenance_since_parent": 18,
            "exact_execution_provenance_since_parent": 18,
        },
    }


def _valid_systemd_snapshot(invocation_id: str = INVOCATION_ID) -> dict:
    return {
        "probe_ok": True,
        "active_state": "inactive",
        "sub_state": "dead",
        "result": "success",
        "invocation_id": invocation_id,
        "exec_main_code": "1",
        "exec_main_status": "0",
    }


def test_versioned_baseline_is_evidence_bound_to_observed_1011() -> None:
    baseline = audit_gate.load_baseline(BASELINE)

    assert baseline["schema_version"] == "vkpi-post-sync-baseline/v1"
    assert baseline["legacy_excel_p2d_minimum"] == 1011
    assert baseline["observed_at"]
    assert baseline["evidence"]["path"].endswith("prod-state-audit-20260810.json")
    assert len(baseline["evidence"]["sha256"]) == 64
    assert baseline["evidence"]["verified"] is True
    assert baseline["evidence"]["observed_value"] == 1011
    assert len(baseline["policy_sha256"]) == 64


def test_audit_has_no_stale_legacy_1012_contract() -> None:
    source = (ROOT / "scripts" / "ops" / "audit_vkpi_post_sync_state.py").read_text(encoding="utf-8")

    assert "legacy_1012" not in source
    assert "legacy_excel_p2d_minimum" in source
    assert "official_provider_provenance" in source
    assert "official_execution_provenance" in source
    assert "maintenance_completed" in source


def test_all_strict_evidence_passes_and_every_required_check_is_present() -> None:
    checks = audit_gate.evaluate_acceptance(_valid_payload(), audit_gate.load_baseline(BASELINE))

    assert set(checks) == set(audit_gate.REQUIRED_CHECKS)
    assert all(checks.values())


@pytest.mark.parametrize(
    ("path", "value", "failed_check"),
    [
        (("runtime_guard", "terminal_success"), False, "service_inactive_proven"),
        (("runtime_guard", "verified"), False, "runtime_invocation_stable"),
        (("sync_log", "latest_invocation_complete"), False, "latest_invocation_complete"),
        (("official_channels", "synced_channels"), 17, "official_all_synced"),
        (("kol_pool", "legacy_excel_p2d"), 1010, "legacy_baseline_met"),
        (("sync_log", "finished_summary", "provider_completion"), "partial", "finished_receipt_terminal"),
        (("sync_log", "maintenance_completed"), False, "maintenance_completed"),
        (("daily_batch", "task_links_match"), False, "batch_bound_to_log"),
        (("daily_batch", "parent", "status"), "failed", "parent_terminal"),
        (("daily_batch", "parent_summary", "completion", "tasks_pending"), 1, "parent_summary_terminal"),
        (("daily_batch", "ledger", "all_terminal"), False, "child_ledger_terminal"),
        (("daily_batch", "ledger", "all_successful"), False, "child_ledger_successful"),
        (("daily_batch", "provider_claims", "all_reconciled"), False, "provider_claims_reconciled"),
        (("official_run_evidence", "synced_since_parent"), 17, "official_synced_in_batch"),
        (("official_run_evidence", "provider_provenance_since_parent"), 17, "official_provider_provenance"),
        (("official_run_evidence", "expected_official_tasks"), 17, "official_execution_provenance"),
        (("official_run_evidence", "exact_execution_provenance_since_parent"), 17, "official_execution_provenance"),
        (("official_run_evidence", "task_link_errors"), [{"reason": "duplicate"}], "official_execution_provenance"),
    ],
)
def test_each_missing_runtime_proof_fails_closed(path: tuple[str, ...], value: object, failed_check: str) -> None:
    payload = copy.deepcopy(_valid_payload())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    checks = audit_gate.evaluate_acceptance(payload, audit_gate.load_baseline(BASELINE))

    assert checks[failed_check] is False


def test_invalid_baseline_schema_and_boolean_minimum_are_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "baseline.json"
    invalid.write_text(json.dumps({
        "schema_version": "vkpi-post-sync-baseline/v1",
        "legacy_excel_p2d_minimum": True,
        "observed_at": "2026-08-31T00:00:00Z",
        "evidence": {"path": "receipt.json", "sha256": "0" * 64},
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="non-negative integer"):
        audit_gate.load_baseline(invalid)


def test_baseline_symlink_is_rejected_before_resolution(tmp_path: Path) -> None:
    link = tmp_path / "baseline-link.json"
    link.symlink_to(BASELINE)

    with pytest.raises(ValueError, match="non-symlink"):
        audit_gate.load_baseline(link)


def test_baseline_evidence_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"kol_pool":{"legacy_excel_p2d":1011}}', encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "schema_version": "vkpi-post-sync-baseline/v1",
        "legacy_excel_p2d_minimum": 1011,
        "observed_at": "2026-08-31T00:00:00Z",
        "evidence": {
            "path": "evidence.json",
            "sha256": "0" * 64,
            "field": "kol_pool.legacy_excel_p2d",
        },
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        audit_gate.load_baseline(baseline)


def test_active_service_is_a_nonzero_block_and_does_not_run_db_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit_gate,
        "local_systemd_snapshot",
        lambda service: {"active_state": "active", "invocation_id": INVOCATION_ID},
    )
    monkeypatch.setattr(audit_gate, "audit_local", lambda *args: pytest.fail("DB audit must not run"))

    assert audit_gate.main([
        "--local",
        "--remote-root",
        str(ROOT),
        "--baseline-file",
        str(BASELINE),
    ]) == 3


def test_runtime_guard_requires_two_identical_successful_systemd_probes() -> None:
    before = _valid_systemd_snapshot()
    assert audit_gate.runtime_guard(before, dict(before))["verified"] is True

    changed = _valid_systemd_snapshot("b" * 32)
    assert audit_gate.runtime_guard(before, changed)["verified"] is False
    failed = {**before, "result": "failed", "exec_main_status": "1"}
    assert audit_gate.runtime_guard(failed, dict(failed))["terminal_success"] is False


def test_main_fails_closed_when_invocation_changes_during_db_audit(monkeypatch) -> None:
    snapshots = iter([_valid_systemd_snapshot(), _valid_systemd_snapshot("b" * 32)])
    monkeypatch.setattr(audit_gate, "local_systemd_snapshot", lambda _service: next(snapshots))
    monkeypatch.setattr(audit_gate, "audit_local", lambda *_args: _valid_payload())
    monkeypatch.setattr(audit_gate, "stdout_out", lambda *_args, **_kwargs: None)

    assert audit_gate.main([
        "--local", "--remote-root", str(ROOT), "--baseline-file", str(BASELINE),
    ]) == 1


def test_embedded_remote_audit_is_valid_and_commands_keep_no_bytecode_guards() -> None:
    ast.parse(audit_gate.REMOTE_AUDIT)
    command = audit_gate._audit_command(
        "/opt/viltrox-2.0", "/var/log/vkpi/sync_daily_20260831.log", INVOCATION_ID,
    )

    assert "VKPI_CODE_ROOT" in command
    assert "VKPI_SYNC_LOG_PATH" in command
    assert "VKPI_EXPECTED_INVOCATION_ID" in command
    assert "env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -" in command


def test_every_supported_official_success_path_persists_provider_provenance() -> None:
    refill_path = ROOT / "backend" / "app" / "domains" / "channels" / "refill.py"
    source = refill_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    expected = {
        "_sync_youtube", "_sync_instagram", "_sync_tiktok",
        "_sync_facebook", "_sync_reddit", "_sync_x",
    }
    proved: set[str] = set()
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        if function.name not in expected:
            continue
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id != "_write_snapshot_with_progress":
                continue
            assert len(call.args) >= 3 and isinstance(call.args[2], ast.Dict)
            keys = {key.value for key in call.args[2].keys if isinstance(key, ast.Constant)}
            assert "provider" in keys
            proved.add(function.name)

    assert proved == expected
    assert "raw_payload_json=excluded.raw_payload_json" in source
    assert "_json(raw_payload)" in source
    assert "last_sync_status='synced'" in source


def test_embedded_audit_requires_each_metric_to_match_its_batch_and_task() -> None:
    tree = ast.parse(audit_gate.REMOTE_AUDIT)
    helper_names = {
        "json_object", "timestamp", "official_task_map", "official_run_evidence",
    }
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    namespace = {
        "json": json,
        "datetime": __import__("datetime").datetime,
        "timezone": __import__("datetime").timezone,
        "EXECUTION_PROVENANCE_SCHEMA": "vkpi-sync-execution-provenance/v1",
    }
    module = ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[]))
    exec(compile(module, "<embedded-audit-helpers>", "exec"), namespace)

    batch_id = "daily-20260831"
    links = [
        {"task_id": f"official-{channel_id}", "lane": "official", "channel_id": channel_id}
        for channel_id in range(1, 19)
    ]
    expected, link_errors = namespace["official_task_map"](links)

    def evidence_rows(*_args, **_kwargs):
        return [
            {
                "id": channel_id,
                "last_sync_status": "synced",
                "last_sync_at": "2026-08-31T09:05:00Z",
                "metric_captured_at": "2026-08-31T09:05:00Z",
                "raw_payload_json": json.dumps({
                    "provider": "provider_api",
                    "execution_provenance": {
                        "schema_version": "vkpi-sync-execution-provenance/v1",
                        "task_id": f"official-{channel_id}",
                        "orchestration_batch_id": batch_id,
                        "orchestration_lane": "official",
                    },
                }),
            }
            for channel_id in range(1, 19)
        ]

    namespace["rows"] = evidence_rows
    evidence = namespace["official_run_evidence"](
        "2026-08-31T09:00:00Z", "2026-08-31T09:10:00Z",
        batch_id, expected, link_errors,
    )
    assert evidence["expected_official_tasks"] == 18
    assert evidence["exact_execution_provenance_since_parent"] == 18
    assert evidence["missing_or_mismatched_execution_channel_ids"] == []

    tampered = evidence_rows()
    tampered[-1]["raw_payload_json"] = json.dumps({
        "provider": "provider_api",
        "execution_provenance": {
            "schema_version": "vkpi-sync-execution-provenance/v1",
            "task_id": "manual-task",
            "orchestration_batch_id": batch_id,
            "orchestration_lane": "official",
        },
    })
    namespace["rows"] = lambda *_args, **_kwargs: tampered
    evidence = namespace["official_run_evidence"](
        "2026-08-31T09:00:00Z", "2026-08-31T09:10:00Z",
        batch_id, expected, link_errors,
    )
    assert evidence["provider_provenance_since_parent"] == 18
    assert evidence["exact_execution_provenance_since_parent"] == 17
    assert evidence["missing_or_mismatched_execution_channel_ids"] == [18]

    late = evidence_rows()
    for row in late:
        row["last_sync_at"] = "2026-08-31T09:10:01Z"
        row["metric_captured_at"] = "2026-08-31T09:10:01Z"
    namespace["rows"] = lambda *_args, **_kwargs: late
    evidence = namespace["official_run_evidence"](
        "2026-08-31T09:00:00Z", "2026-08-31T09:10:00Z",
        batch_id, expected, link_errors,
    )
    assert evidence["synced_since_parent"] == 0
    assert evidence["exact_execution_provenance_since_parent"] == 0


def test_embedded_audit_rejects_maintenance_from_another_batch(tmp_path: Path) -> None:
    tree = ast.parse(audit_gate.REMOTE_AUDIT)
    helper_names = {"log_events", "sync_log_evidence"}
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    namespace = {"json": json, "Path": Path, "MAX_LOG_BYTES": 8 * 1024 * 1024}
    module = ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[]))
    exec(compile(module, "<embedded-log-audit-helpers>", "exec"), namespace)
    log_path = tmp_path / "daily.log"
    events = [
        {"event": "cron_daily_sync_started", "invocation_id": INVOCATION_ID},
        {
            "event": "cron_daily_sync_finished",
            "invocation_id": INVOCATION_ID,
            "summary": {"batch_id": "daily-a"},
        },
        {"event": "cron_daily_sync_gapfill", "batch_id": "daily-b", "invocation_id": INVOCATION_ID},
        {"event": "cron_daily_sync_index_maint_done", "batch_id": "daily-b", "invocation_id": INVOCATION_ID},
    ]
    log_path.write_text(
        "".join(json.dumps(item) + "\n" for item in events),
        encoding="utf-8",
    )

    evidence = namespace["sync_log_evidence"](str(log_path), INVOCATION_ID)
    assert evidence["batch_id"] == "daily-a"
    assert evidence["maintenance_completed"] is False
    assert evidence["unmatched_maintenance_events"] == [
        "cron_daily_sync_gapfill", "cron_daily_sync_index_maint_done",
    ]

    events.extend([
        {"event": "cron_daily_sync_gapfill", "batch_id": "daily-a", "invocation_id": INVOCATION_ID},
        {"event": "cron_daily_sync_index_maint_done", "batch_id": "daily-a", "invocation_id": INVOCATION_ID},
    ])
    log_path.write_text(
        "".join(json.dumps(item) + "\n" for item in events),
        encoding="utf-8",
    )
    assert namespace["sync_log_evidence"](str(log_path), INVOCATION_ID)["maintenance_completed"] is False

    valid_events = [events[0], events[1], events[-2], events[-1]]
    log_path.write_text(
        "".join(json.dumps(item) + "\n" for item in valid_events),
        encoding="utf-8",
    )
    valid = namespace["sync_log_evidence"](str(log_path), INVOCATION_ID)
    assert valid["latest_invocation_complete"] is True
    assert valid["maintenance_completed"] is True


def test_embedded_audit_rejects_old_success_when_latest_attempt_is_unfinished(tmp_path: Path) -> None:
    tree = ast.parse(audit_gate.REMOTE_AUDIT)
    helpers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {"log_events", "sync_log_evidence"}
    ]
    namespace = {"json": json, "Path": Path, "MAX_LOG_BYTES": 8 * 1024 * 1024}
    exec(compile(ast.fix_missing_locations(ast.Module(body=helpers, type_ignores=[])), "<log-audit>", "exec"), namespace)
    current = "b" * 32
    events = [
        {"event": "cron_daily_sync_started", "invocation_id": INVOCATION_ID},
        {"event": "cron_daily_sync_finished", "invocation_id": INVOCATION_ID, "summary": {"batch_id": "old"}},
        {"event": "cron_daily_sync_started", "invocation_id": current},
    ]
    log_path = tmp_path / "daily.log"
    log_path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")

    evidence = namespace["sync_log_evidence"](str(log_path), current)
    assert evidence["invocation_matches"] is True
    assert evidence["finished_present"] is False
    assert evidence["latest_invocation_complete"] is False

    events.append({"event": "cron_daily_sync_failed", "invocation_id": current})
    log_path.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
    evidence = namespace["sync_log_evidence"](str(log_path), current)
    assert evidence["attempt_failure_events"] == ["cron_daily_sync_failed"]
    assert evidence["latest_invocation_complete"] is False
