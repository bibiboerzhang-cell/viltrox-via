from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/ops/legacy_to_atomic_preflight.py"
SPEC = importlib.util.spec_from_file_location("legacy_to_atomic_preflight", HELPER)
assert SPEC and SPEC.loader
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def _args():
    args = preflight.build_parser().parse_args([])
    args.expected_migration = "259_vkpi_dealer_reviewed_evidence.sql"
    args.required_domain = list(preflight.DEFAULT_REQUIRED_DOMAINS)
    preflight._validate_public_args(args)
    return args


def _unit(name: str) -> dict[str, object]:
    lane = None
    heartbeat = None
    role = "web" if name == preflight.WEB_UNIT else None
    if name == preflight.INTERACTIVE_UNIT:
        role = "worker"
        lane = "interactive"
        heartbeat = "apify-worker-interactive"
    elif name in preflight.BULK_UNITS:
        role = "worker"
        lane = "batch"
        heartbeat = f"apify-worker-bulk-{preflight.BULK_UNITS.index(name) + 1}"
    return {
        "name": name,
        "observable": True,
        "load_state": "loaded",
        "active_state": "active" if name in preflight.CORE_RUNTIME_UNITS else "inactive",
        "unit_file_state": "enabled",
        "fragment_path": f"/etc/systemd/system/{name}",
        "fragment_sha256": "b" * 64,
        "fragment_readable": True,
        "user": "viltrox" if name in preflight.CORE_RUNTIME_UNITS else "root",
        "group": "viltrox" if name in preflight.CORE_RUNTIME_UNITS else "root",
        "working_directory": "/opt/viltrox-2.0",
        "app_role": role,
        "environment_mode": "production" if role else None,
        "claim_lane": lane,
        "heartbeat_name": heartbeat,
    }


def _ready_snapshot() -> dict[str, object]:
    sha = "a" * 40
    return {
        "schema_version": 1,
        "collected_at": "2026-07-15T18:00:00Z",
        "release_layout": {
            "root_exists": True,
            "state": "legacy_flat",
            "flat_markers": {
                "backend": True,
                "frontend_dist": True,
                "environment": True,
                "build_git_sha": True,
            },
            "releases_directory": False,
            "current": {"kind": "absent", "safe_target": False, "target_name": None},
            "previous": {"kind": "absent", "safe_target": False, "target_name": None},
            "root_build_git_sha": sha,
            "atomic_helper_present": True,
        },
        "environment": {
            "regular_file": True,
            "app_user_nonroot": True,
            "owner": "root",
            "group": "viltrox",
            "mode": "0640",
            "app_readable": True,
            "app_writable": False,
            "database_configured": True,
            "redis_configured": True,
            "parse_ok": True,
        },
        "systemd_units": [_unit(name) for name in preflight.OBSERVED_UNITS],
        "database": {
            "reachable": True,
            "read_only_session": True,
            "database_name": "viltrox2_test",
            "migration_max": "259_vkpi_dealer_reviewed_evidence.sql",
            "migration_count": 257,
            "error_code": None,
        },
        "redis": {
            "reachable": True,
            "aof_enabled": True,
            "rdb_last_bgsave_status": "ok",
            "aof_last_write_status": "ok",
            "error_code": None,
        },
        "nginx": {
            "config_readable": True,
            "readable_file_count": 1,
            "domains": list(preflight.DEFAULT_REQUIRED_DOMAINS),
        },
        "health": {
            "reachable": True,
            "status": "ok",
            "server_git_sha": sha,
            "client_git_sha": sha,
            "sha_aligned": True,
            "db_migration_max": "259_vkpi_dealer_reviewed_evidence.sql",
            "worker_online": True,
            "worker_fleet_present": False,
            "error_code": None,
        },
        "backup": {
            "directory_readable": True,
            "candidate_count": 1,
            "latest_name": "20260715T180000Z",
            "latest_age_hours": 1.0,
            "fresh": True,
            "dump_size_bytes": 1024,
            "checksum_present": True,
            "checksum_verified": True,
            "catalog_verified": True,
            "runtime_state_present": True,
            "media_manifest_present": True,
            "encrypted_environment_snapshot_present": True,
            "off_host_receipt_present": True,
        },
    }


def test_public_cli_exposes_no_mutation_or_authorization_bypass() -> None:
    help_text = preflight.build_parser().format_help()
    for forbidden in ("--execute", "--apply", "--deploy", "--mutate", "--confirm"):
        assert forbidden not in help_text

    report = preflight._build_report(_ready_snapshot(), _args())
    assert report["decision"] == "go"
    assert report["safety_contract"] == {
        "remote_write_operations": [],
        "mutation_interface_present": False,
        "execution_allowed": False,
        "future_mutation_requires_distinct_explicit_approvals": 2,
        "future_mutation_authorization_implemented": False,
        "go_means_preflight_ready_only": True,
    }


def test_remote_command_runner_is_fail_closed_to_read_only_allowlist(monkeypatch) -> None:
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    with pytest.raises(preflight.PreflightError, match="remote_command_not_allowlisted"):
        preflight._readonly_command(["systemctl", "restart", preflight.WEB_UNIT])
    assert called is False

    with pytest.raises(preflight.PreflightError, match="remote_command_not_allowlisted"):
        preflight._readonly_command(["/tmp/systemctl", "show", preflight.WEB_UNIT])
    assert called is False

    preflight._readonly_command(["systemctl", "show", preflight.WEB_UNIT])
    assert called is True


def test_source_contract_has_no_remote_mutation_primitives_or_write_sql() -> None:
    source = HELPER.read_text(encoding="utf-8")
    forbidden = (
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "systemctl enable",
        "systemctl disable",
        "daemon-reload",
        "sudo ",
        "rsync ",
        "scp ",
        ".write_text(",
        ".write_bytes(",
        ".mkdir(",
        ".unlink(",
        ".rename(",
        ".replace(",
        ".chmod(",
        ".chown(",
    )
    for token in forbidden:
        assert token not in source

    sql = re.findall(r'cursor\.execute\("([^"]+)"\)', source)
    assert sql
    assert all(statement.startswith(("SHOW ", "SELECT ")) for statement in sql)
    assert not re.search(r"\b(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b", " ".join(sql), re.I)


def test_ssh_transport_uses_stdin_and_rejects_injection() -> None:
    args = _args()
    command = preflight._ssh_command(args)
    assert command[:2] == ["ssh", "-o"]
    assert "--" in command
    assert command[-2] == "viltrox"
    assert command[-1].startswith("/opt/viltrox-2.0/.venv/bin/python - --remote-collect")
    assert "sudo" not in command[-1]
    assert ">" not in command[-1]

    args.ssh_target = "viltrox;touch_/tmp/pwned"
    with pytest.raises(preflight.PreflightError, match="invalid_ssh_target"):
        preflight._ssh_command(args)

    args = _args()
    args.remote_python = "/tmp/writer"
    with pytest.raises(
        preflight.PreflightError, match="remote_python_outside_reviewed_venv"
    ):
        preflight._ssh_command(args)

    args = _args()
    args.root = "/tmp/alternate"
    args.remote_python = "/tmp/alternate/.venv/bin/python"
    with pytest.raises(
        preflight.PreflightError, match="root_outside_reviewed_installation"
    ):
        preflight._ssh_command(args)


def test_root_app_user_and_hostname_alias_cannot_weaken_readonly_contract() -> None:
    args = _args()
    args.app_user = "root"
    with pytest.raises(preflight.PreflightError, match="invalid_app_user"):
        preflight._validate_public_args(args)

    with pytest.raises(preflight.PreflightError, match="health_url_must_be_loopback_get"):
        preflight._validate_health_url("http://localhost:8001/health")


def test_health_probe_refuses_redirects_and_proxy_use() -> None:
    handlers = preflight._HEALTH_OPENER.handlers
    assert any(isinstance(handler, preflight._NoRedirectHandler) for handler in handlers)
    assert preflight._HEALTH_PROXY_HANDLER.proxies == {}
    redirect = preflight._NoRedirectHandler()
    assert redirect.redirect_request(None, None, 302, "Found", {}, "http://example.test") is None


def test_projection_drops_unknown_secret_bearing_snapshot_fields() -> None:
    snapshot = _ready_snapshot()
    snapshot["DATABASE_URL"] = "postgresql://admin:raw-password@example.test/db"
    snapshot["environment"]["API_TOKEN"] = "sk-do-not-print-this"
    snapshot["database"]["error_detail"] = "Bearer do-not-print-this"
    report = preflight._build_report(snapshot, _args())
    encoded = json.dumps(report, sort_keys=True)
    assert "raw-password" not in encoded
    assert "sk-do-not-print-this" not in encoded
    assert "do-not-print-this" not in encoded
    assert report["secret_free"] is True
    assert preflight._contains_secret(report) is False


def test_blockers_cover_legacy_cloud_risks_without_mutating() -> None:
    snapshot = _ready_snapshot()
    snapshot["environment"]["owner"] = "viltrox"
    snapshot["environment"]["mode"] = "0600"
    snapshot["environment"]["app_writable"] = True
    for unit in snapshot["systemd_units"]:
        if unit["name"] in preflight.BULK_UNITS:
            unit["claim_lane"] = "all"
            unit["user"] = "root"
    snapshot["redis"]["aof_enabled"] = False
    snapshot["redis"]["aof_last_write_status"] = "err"
    snapshot["health"]["sha_aligned"] = False
    snapshot["backup"]["encrypted_environment_snapshot_present"] = False
    snapshot["backup"]["off_host_receipt_present"] = False

    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "no-go"
    assert {
        "environment.app_readonly",
        "systemd.nonroot_app_identity",
        "workers.lane_contract",
        "redis.aof_enabled",
        "redis.persistence_last_write_healthy",
        "health.release_sha_aligned",
        "backup.encrypted_environment_snapshot",
        "backup.off_host_receipt",
    }.issubset(report["blocking_check_ids"])
    assert report["safety_contract"]["execution_allowed"] is False


def test_hardened_environment_permissions_are_a_blocking_gate() -> None:
    snapshot = _ready_snapshot()
    snapshot["environment"]["mode"] = "0444"
    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "no-go"
    assert "environment.hardened_permissions" in report["blocking_check_ids"]

    snapshot = _ready_snapshot()
    snapshot["environment"]["app_user_nonroot"] = False
    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "no-go"
    assert "environment.app_user_nonroot" in report["blocking_check_ids"]


def test_release_pointer_and_same_ordinal_migration_evidence_fail_closed() -> None:
    snapshot = _ready_snapshot()
    snapshot["release_layout"]["previous"] = {
        "kind": "symlink",
        "safe_target": False,
        "target_name": "outside",
    }
    snapshot["database"]["migration_max"] = "259_unreviewed.sql"
    snapshot["health"]["db_migration_max"] = "259_unreviewed.sql"
    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "no-go"
    assert {
        "release.previous_pointer_safe",
        "database.not_ahead_of_candidate",
    }.issubset(report["blocking_check_ids"])


def test_health_alignment_is_recomputed_from_exact_shas() -> None:
    snapshot = _ready_snapshot()
    snapshot["health"]["server_git_sha"] = "b" * 40
    snapshot["health"]["sha_aligned"] = True
    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "no-go"
    assert "health.release_sha_aligned" in report["blocking_check_ids"]


def test_pending_migration_requires_staging_clone_but_is_not_itself_a_failure() -> None:
    snapshot = _ready_snapshot()
    snapshot["database"]["migration_max"] = "233_scheduler_task_kol_id.sql"
    snapshot["health"]["db_migration_max"] = "233_scheduler_task_kol_id.sql"
    report = preflight._build_report(snapshot, _args())
    assert report["decision"] == "go"
    assert report["rollback_prerequisites"]["staging_clone_required"] is True
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["database.not_ahead_of_candidate"]["pass"] is True


def test_release_layout_rejects_releases_directory_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    release = outside / "release-a"
    release.mkdir()
    (root / "releases").symlink_to(outside, target_is_directory=True)
    (root / "current").symlink_to(release, target_is_directory=True)

    observed = preflight._release_layout(root)
    assert observed["releases_directory"] is False
    assert observed["current"]["safe_target"] is False
    assert observed["state"] == "unrecognized"


def test_nginx_read_allows_internal_symlink_and_rejects_escape(
    tmp_path: Path, monkeypatch
) -> None:
    nginx = tmp_path / "nginx"
    enabled = nginx / "sites-enabled"
    available = nginx / "sites-available"
    enabled.mkdir(parents=True)
    available.mkdir()
    inside = available / "vkpi"
    inside.write_text("server { server_name viltroxtest.com; }", encoding="utf-8")
    (enabled / "vkpi").symlink_to(inside)
    outside = tmp_path / "outside.conf"
    outside.write_text("server { server_name leaked.example; }", encoding="utf-8")
    (enabled / "escape").symlink_to(outside)
    monkeypatch.setattr(preflight, "NGINX_ROOT", nginx)

    observed = preflight._nginx_state()
    assert observed == {
        "config_readable": True,
        "readable_file_count": 1,
        "domains": ["viltroxtest.com"],
    }


def test_backup_presence_evidence_rejects_symlinks_and_future_timestamps(
    tmp_path: Path, monkeypatch
) -> None:
    backup = tmp_path / "backups/ops/20260715T180000Z"
    backup.mkdir(parents=True)
    dump = backup / "prod-db.dump"
    dump.write_bytes(b"safe-dump")
    os.utime(dump, (time.time() + 600, time.time() + 600))
    outside = tmp_path / "outside"
    outside.write_text("evidence", encoding="utf-8")
    for name in (
        "runtime-state.txt",
        "media-cache-manifest.tsv",
        "environment.age",
        "off-host-backup-receipt.json",
    ):
        (backup / name).symlink_to(outside)
    monkeypatch.setattr(preflight.shutil, "which", lambda _name: None)

    observed = preflight._backup_state(tmp_path, 24)
    assert observed["fresh"] is False
    assert observed["runtime_state_present"] is False
    assert observed["media_manifest_present"] is False
    assert observed["encrypted_environment_snapshot_present"] is False
    assert observed["off_host_receipt_present"] is False


def test_collection_failure_never_copies_remote_stderr(monkeypatch) -> None:
    args = _args()

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 255, "", "DATABASE_URL=postgres://u:raw-secret@host/db")

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    with pytest.raises(preflight.PreflightError) as caught:
        preflight._collect_via_ssh(args)
    assert str(caught.value) == "ssh_collection_failed"
    assert "raw-secret" not in str(caught.value)


def test_main_emits_json_and_returns_gate_exit_codes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(preflight, "_collect_via_ssh", lambda _args: _ready_snapshot())
    assert preflight.main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "go"
    assert payload["safety_contract"]["execution_allowed"] is False

    blocked = _ready_snapshot()
    blocked["redis"]["aof_enabled"] = False
    monkeypatch.setattr(preflight, "_collect_via_ssh", lambda _args: blocked)
    assert preflight.main([]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "no-go"
    assert "redis.aof_enabled" in payload["blocking_check_ids"]


def test_report_has_all_requested_evidence_sections() -> None:
    report = preflight._build_report(_ready_snapshot(), _args())
    observed = report["observed"]
    assert set(observed) == {
        "collected_at",
        "release_layout",
        "environment",
        "systemd_units",
        "database",
        "redis",
        "nginx",
        "health",
        "backup",
    }
    assert len(observed["systemd_units"]) == len(preflight.OBSERVED_UNITS)
    assert set(report["rollback_prerequisites"]) == {
        "legacy_source_tree_identified",
        "atomic_helper_present",
        "unit_fragments_captured",
        "database_backup_verified",
        "environment_recovery_evidence",
        "health_baseline_captured",
        "sync_timer_state_captured",
        "staging_clone_required",
    }
