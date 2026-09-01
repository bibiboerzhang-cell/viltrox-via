"""O2: candidate browser runtime temporary Postgres must be stopped.

Split out of tests/test_cloud_web_runtime_contract.py (line guard ≤1000): the
freeze-side strict-runtime gate stops /tmp/vkpi-candidate-browser-runtime.*
postmasters in its ``finally`` and receipts every outcome.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _runtime_module():
    import importlib
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("scripts.ops.deploy_gate_runtime")


def _pg_bin() -> Path | None:
    for candidate in (
        Path(os.environ.get("POSTGRES_BIN") or "/nonexistent"),
        Path("/opt/homebrew/opt/postgresql@16/bin"),
        Path("/usr/lib/postgresql/16/bin"),
    ):
        if (candidate / "initdb").is_file() and (candidate / "pg_ctl").is_file():
            return candidate
    return None


def test_freeze_deploy_gate_stops_candidate_browser_runtime_postgres_in_finally() -> None:
    freeze = _read("scripts/ops/freeze_deploy_gate.py")
    runtime = _read("scripts/ops/deploy_gate_runtime.py")
    gate = freeze.split("def run_deploy_gate(", 1)[1]
    finally_block = gate.split("    finally:\n", 1)[1].split("    if completed is None", 1)[0]
    assert '"status": "controller_registry_cleanup_required"' in finally_block
    assert '"destructive_cleanup_performed": False' in finally_block
    assert "runtime_root" in finally_block
    assert "runtime_root = str(args.runtime_root)" in gate
    assert 'after["candidate_browser_runtime_postgres"] = candidate_postgres_receipts' in gate
    helper = runtime.split("def stop_candidate_browser_runtime_postgres(", 1)[1]
    assert '"stop", "-m", "fast"' in helper
    assert '"status": "stop_failed"' in helper and "_log.error(" in helper
    assert ".glob(" not in helper
    assert "print(" not in helper


def test_candidate_browser_runtime_postgres_cleanup_refuses_unsafe_roots_and_reports_stale(tmp_path: Path, monkeypatch) -> None:
    import pytest

    runtime = _runtime_module()
    outside = tmp_path / "vkpi-candidate-browser-runtime.outside"
    (outside / "runtime" / "data" / "postgres").mkdir(parents=True)
    (outside / "runtime" / "data" / "postgres" / "postmaster.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    with pytest.raises(runtime.DeployGateRuntimeError, match="0700"):
        runtime.stop_candidate_browser_runtime_postgres(outside)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="vkpi-candidate-browser-runtime.", dir="/tmp") as raw_root:
        root = Path(raw_root)
        assert runtime.stop_candidate_browser_runtime_postgres(root) == []
        data_dir = root / "runtime" / "data" / "postgres"
        data_dir.mkdir(parents=True)
        (data_dir / "postmaster.pid").write_text("999999999\n", encoding="utf-8")
        assert runtime.stop_candidate_browser_runtime_postgres(root) == [
            {"root": str(root), "status": "stale_pidfile"}
        ]
        # Live pid but no pg_ctl anywhere: reported, never silently dropped.
        (data_dir / "postmaster.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        monkeypatch.setenv("POSTGRES_BIN", str(tmp_path / "no-bin"))
        monkeypatch.setattr(runtime, "_PG_CTL_FALLBACKS", ())
        monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
        receipts = runtime.stop_candidate_browser_runtime_postgres(root)
        assert receipts == [{"root": str(root), "status": "pg_ctl_missing", "pid": os.getpid()}]


def test_candidate_browser_runtime_postgres_cleanup_stops_a_real_postmaster() -> None:
    import socket
    import tempfile

    pg_bin = _pg_bin()
    if pg_bin is None:
        import pytest

        pytest.skip("postgres binaries unavailable")
    runtime = _runtime_module()
    # initdb rejects the operator's CJK locale (memory: dev PG 启动要 LC_ALL);
    # pin C for the fixture's own initdb/start, the helper inherits os.environ.
    pg_env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="vkpi-candidate-browser-runtime.", dir="/tmp") as raw_root:
        root = Path(raw_root)
        data_dir = root / "runtime" / "data" / "postgres"
        socket_dir = root / "runtime" / "postgres-socket"
        data_dir.parent.mkdir(parents=True)
        socket_dir.mkdir(parents=True)
        subprocess.run(
            [str(pg_bin / "initdb"), "-D", str(data_dir), "--username=postgres", "--auth=trust"],
            check=True, capture_output=True, env=pg_env,
        )
        with (data_dir / "postgresql.conf").open("a", encoding="utf-8") as conf:
            conf.write(f"\nport = {port}\nlisten_addresses = '127.0.0.1'\nunix_socket_directories = '{socket_dir}'\n")
        subprocess.run(
            [str(pg_bin / "pg_ctl"), "-D", str(data_dir), "-l", str(root / "postgres.log"), "-w", "start"],
            check=True, capture_output=True, env=pg_env,
        )
        try:
            assert runtime._live_postmaster(data_dir / "postmaster.pid") is not None
            os.environ["POSTGRES_BIN"] = str(pg_bin)
            receipts = runtime.stop_candidate_browser_runtime_postgres(root)
            assert len(receipts) == 1 and receipts[0]["status"] == "stopped", receipts
            assert not (data_dir / "postmaster.pid").exists()
            # Idempotent: a second pass finds nothing to do.
            assert runtime.stop_candidate_browser_runtime_postgres(root) == []
        finally:
            os.environ.pop("POSTGRES_BIN", None)
            subprocess.run(
                [str(pg_bin / "pg_ctl"), "-D", str(data_dir), "stop", "-m", "immediate"],
                check=False, capture_output=True, env=pg_env,
            )
