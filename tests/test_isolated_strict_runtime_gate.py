from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from scripts.ops import isolated_strict_runtime_gate as strict
from scripts.ops.isolated_worktree_gate_cli import _strict_preflight_args, main as cli_main, parser


def test_default_controller_remains_phase_a_only(tmp_path: Path) -> None:
    args = parser(default_source=tmp_path).parse_args(["--output", str(tmp_path / "out")])
    assert args.strict_runtime is False
    assert args.source_database_url_file == ""


def test_strict_controller_cli_is_explicit(tmp_path: Path) -> None:
    args = parser(default_source=tmp_path).parse_args(
        [
            "--output", str(tmp_path / "out"), "--strict-runtime",
            "--source-database-url-file", str(tmp_path / "database-url"),
            "--strict-evidence-dir", str(tmp_path / "external-evidence"),
        ]
    )
    assert args.strict_runtime is True


def test_strict_cli_is_admission_blocked_before_phase_a_or_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "must-not-exist"
    arguments = ["isolated-gate", "--output", str(output), "--strict-runtime"]
    called = False
    def forbidden(_args):
        nonlocal called; called = True
        raise AssertionError("Phase A must not run while admission is blocked")
    monkeypatch.setattr("sys.argv", arguments)
    assert cli_main(run_phase_a=forbidden) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "admission blocked" in captured.err
    assert "setsid/double-fork" in captured.err
    assert called is False and not output.exists()


def test_phase_a_cli_runs_without_claiming_runtime_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "candidate"
    observed = []

    def phase_a(args):
        observed.append(args)
        return {
            "classification": "clean_content_candidate_not_runtime_acceptance",
            "runtime_acceptance": {"attempted": False},
        }

    monkeypatch.setattr(
        "sys.argv", ["isolated-gate", "--output", str(output), "--skip-build"]
    )
    assert cli_main(run_phase_a=phase_a) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "classification": "clean_content_candidate_not_runtime_acceptance",
        "runtime_acceptance": {"attempted": False},
    }
    assert len(observed) == 1
    assert observed[0].strict_runtime is False


def test_phase_a_cli_failure_is_nonzero_and_never_prints_a_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["isolated-gate", "--output", str(tmp_path / "candidate")]
    )

    def fail(_args):
        raise OSError("fixture failure")

    assert cli_main(run_phase_a=fail) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Phase-A candidate failed: OSError" in captured.err


def test_strict_cli_rejects_database_url_symlink(tmp_path: Path) -> None:
    target = tmp_path / "database-url-target"
    target.write_text("postgresql://127.0.0.1:54329/vkpi\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "database-url"
    link.symlink_to(target)
    args = parser(default_source=tmp_path).parse_args([
        "--source", str(tmp_path), "--output", str(tmp_path / "candidate"),
        "--strict-runtime", "--source-database-url-file", str(link),
        "--strict-evidence-dir", str(tmp_path.parent / "external-evidence"),
    ])
    with pytest.raises(OSError):
        _strict_preflight_args(args)


def test_strict_cli_rejects_evidence_inside_source(tmp_path: Path) -> None:
    url_file = tmp_path / "database-url"
    url_file.write_text("postgresql://127.0.0.1:54329/vkpi\n", encoding="utf-8")
    url_file.chmod(0o600)
    args = parser(default_source=tmp_path).parse_args([
        "--source", str(tmp_path), "--output", str(tmp_path / "candidate"),
        "--strict-runtime", "--source-database-url-file", str(url_file),
        "--strict-evidence-dir", str(tmp_path / "evidence"),
    ])
    with pytest.raises(strict.StrictRuntimeGateError, match="new and external"):
        _strict_preflight_args(args)


def test_source_clone_is_forced_to_loopback_read_only(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    with pytest.raises(strict.StrictRuntimeGateError):
        strict._source_dump_environment("postgresql://db.example.com:5432/vkpi", root)
    environment = strict._source_dump_environment(
        "postgresql://127.0.0.1:54329/vkpi", root
    )
    assert environment["PGDATABASE"].endswith("/vkpi")
    assert "default_transaction_read_only=on" in environment["PGOPTIONS"]


@pytest.mark.parametrize("query", [
    "host=db.example.com", "hostaddr=8.8.8.8",
    "options=-c%20default_transaction_read_only=off", "service=prod",
])
def test_source_clone_rejects_libpq_target_and_policy_overrides(
    tmp_path: Path, query: str,
) -> None:
    with pytest.raises(strict.StrictRuntimeGateError):
        strict._source_dump_environment(
            f"postgresql://127.0.0.1:54329/vkpi?{query}", tmp_path
        )


def test_empty_receipts_are_rejected(tmp_path: Path) -> None:
    verify = tmp_path / "verify.json"
    acceptance = tmp_path / "acceptance.json"
    verify.write_text("{}\n", encoding="utf-8")
    acceptance.write_text("{}\n", encoding="utf-8")
    with pytest.raises(strict.StrictRuntimeGateError):
        strict._validate_bound_receipts(
            verify_path=verify, acceptance_path=acceptance,
            expected_head="a" * 40, expected_branch="main",
            base_url="http://127.0.0.1:18103/",
            expected_steps=["required"], expected_endpoints=["health"],
            runtime_nonce="nonce", runtime_ports="18103,15432,16379",
            candidate_digest="b" * 64,
        )


def test_single_step_endpoint_forge_and_cross_run_replay_are_rejected(tmp_path: Path) -> None:
    binding = {"nonce": "run-one", "ports": "18103,15432,16379", "candidate_sha256": "b" * 64}
    verify = {
        "schema_version": "vkpi_canonical_gate_receipt_v1", "passed": True,
        "candidate": {"release_head": "a" * 40, "git_head": "a" * 40, "branch": "main",
                      "clean_worktree": True, "dirty_path_count": 0},
        "verification": {"runtime": "verified", "acceptance": "verified"},
        "steps": [{"index": 1, "name": "attacker-fabricated-single-step",
                   "status": "passed", "exit_code": 0}], "failed_steps": [],
        "strict_runtime_binding": binding,
    }
    acceptance = {
        "schema_version": "vkpi.local-release-acceptance.v1", "base_url": "http://127.0.0.1:18103",
        "repo": {"head": "a" * 40},
        "overall": {"pass": True, "required_total": 1, "required_passed": 1,
                    "failed_endpoint_ids": [], "deadline_exhausted": False},
        "coverage": {"missing_board_families": []},
        "safety": {"loopback_only": True, "paid_provider_calls": False,
                   "business_record_mutations": False, "deadline_exhausted": False},
        "endpoints": [{"id": "attacker-fabricated-single-endpoint", "required": True, "pass": True}],
        "strict_runtime_binding": binding,
    }
    verify_path, acceptance_path = tmp_path / "verify.json", tmp_path / "acceptance.json"
    verify_path.write_text(json.dumps(verify), encoding="utf-8")
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    common = dict(verify_path=verify_path, acceptance_path=acceptance_path,
                  expected_head="a" * 40, expected_branch="main",
                  base_url="http://127.0.0.1:18103/", runtime_ports=binding["ports"],
                  candidate_digest=binding["candidate_sha256"])
    with pytest.raises(strict.StrictRuntimeGateError):
        strict._validate_bound_receipts(**common, expected_steps=["real-one", "real-two"],
                                        expected_endpoints=["real"], runtime_nonce="run-one")
    verify["steps"][0]["name"] = "real"
    acceptance["endpoints"][0]["id"] = "real"
    verify_path.write_text(json.dumps(verify), encoding="utf-8")
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
    with pytest.raises(strict.StrictRuntimeGateError, match="binding mismatch"):
        strict._validate_bound_receipts(**common, expected_steps=["real"],
                                        expected_endpoints=["real"], runtime_nonce="run-two")


def test_worker_environment_is_fenced_provider_free_and_private(tmp_path: Path) -> None:
    root = tmp_path / "runtime-root"
    for name in ("home", "tmp", "runtime"):
        (root / name).mkdir(parents=True, exist_ok=True)
    fence = root / "runtime/release-validation.fence"
    environment = strict._minimal_runtime_environment(
        root=root,
        candidate=tmp_path / "candidate",
        source=tmp_path / "source",
        ports=strict.RuntimePorts(web=18103, postgres=15432, redis=16379),
        git_sha="a" * 40,
        branch="codex/test",
        fence=fence,
    )
    assert environment["VKPI_RELEASE_VALIDATION_FENCE_PATH"] == str(fence)
    assert environment["VKPI_REDIS_WORKER_ALLOW_STALE_BACKLOG"] == "1"
    assert environment["REDIS_URL"] == "redis://127.0.0.1:16379/0"
    assert strict.PROVIDER_ENV_NAMES.isdisjoint(environment)


def test_three_runs_share_candidate_but_receive_independent_run_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    calls: list[tuple[int, Path]] = []

    def fake_run_once(**kwargs):
        calls.append((kwargs["run_number"], kwargs["candidate"]))
        return {
            "run": kwargs["run_number"], "pass": True,
            "candidate_content_sha256": "a" * 64,
            "candidate_manifest_sha256": "m" * 64,
            "ports": [18000 + kwargs["run_number"], 19000 + kwargs["run_number"], 20000 + kwargs["run_number"]],
        }

    monkeypatch.setattr(strict, "_run_once", fake_run_once)
    identity = {
        "content_sha256": "a" * 64, "manifest_sha256": "m" * 64,
        "snapshot_path": str(candidate), "file_count": 1,
        "git_head": "h" * 40, "git_tree": "t" * 40,
        "branch": "codex/test", "capsule_digest": "c" * 64,
    }
    monkeypatch.setattr(strict, "_phase_candidate_identity", lambda *_args: identity)
    result = strict.run_strict_runtime_gate(
        source=tmp_path,
        candidate=candidate,
        phase_payload={},
        source_database_url="postgresql://127.0.0.1:54329/vkpi",
        evidence_dir=tmp_path / "evidence",
    )
    assert result["run_count"] == 3
    assert calls == [(1, candidate), (2, candidate), (3, candidate)]


def test_one_run_fixture_binds_receipts_and_proves_exact_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private-runtime"
    root.mkdir(mode=0o700)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    clean_source = root / "clean-source"
    (clean_source / ".venv/bin").mkdir(parents=True)
    (clean_source / ".venv/bin/python").write_text("fixture\n", encoding="utf-8")
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").write_text("fixture\n", encoding="utf-8")

    class Process:
        pid = 9001
        returncode = None

        def poll(self):
            return None

    starts = 0
    monkeypatch.setattr(strict, "_private_root", lambda: root)
    monkeypatch.setattr(strict, "_unique_loopback_ports", lambda: (18103, 15432, 16379))
    monkeypatch.setattr(strict, "_binary", lambda name: f"/fixture/{name}")
    monkeypatch.setattr(strict, "_rebuild_clean_source", lambda **_kwargs: clean_source)
    monkeypatch.setattr(strict, "candidate_profile", lambda **_kwargs: "fixture-profile")
    monkeypatch.setattr(
        strict,
        "_copy_bound_manifest",
        lambda **_kwargs: (
            root / "candidate.manifest.json",
            {"build": {"identity": {"git_sha": "a" * 40, "git_branch": "main", "build_time": "now"}}},
        ),
    )
    monkeypatch.setattr(
        strict, "_prepare_postgres",
        lambda **_kwargs: (root / "runtime/data/postgres", root / "source.dump"),
    )
    (root / "source.dump").write_bytes(b"fixture")

    def start(*_args, **_kwargs):
        nonlocal starts
        starts += 1
        if starts == 2:
            fence = root / "runtime/release-validation.fence"
            fence.parent.mkdir(parents=True, exist_ok=True)
            fence.write_text("vkpi-release-validation/v1\n", encoding="utf-8")
        return Process(), io.BytesIO()

    monkeypatch.setattr(strict, "_start_process", start)
    monkeypatch.setattr(strict, "_wait_runtime_ready", lambda *_args, **_kwargs: None)

    def gate(args):
        Path(args.verify_json_out).write_text("{}\n", encoding="utf-8")
        Path(args.acceptance_json_out).write_text("{}\n", encoding="utf-8")
        return {"content_sha256": "b" * 64}

    monkeypatch.setattr(strict, "run_deploy_gate", gate)
    identity = {
        "content_sha256": "b" * 64, "manifest_sha256": "m" * 64,
        "snapshot_path": str(candidate), "file_count": 1,
        "git_head": "a" * 40, "git_tree": "t" * 40,
        "branch": "main", "capsule_digest": "c" * 64,
    }
    monkeypatch.setattr(strict, "_phase_candidate_identity", lambda *_args: identity)
    monkeypatch.setattr(strict, "_validate_bound_receipts", lambda **_kwargs: {
        "verify_sha256": "v" * 64, "acceptance_sha256": "r" * 64,
    })
    monkeypatch.setattr(strict, "expected_receipt_plan", lambda _root: (["fixture"], ["fixture"]))
    monkeypatch.setattr(
        strict, "_copy_receipt_nofollow",
        lambda source, target, _expected: target.write_bytes(source.read_bytes()),
    )
    monkeypatch.setattr(strict, "_stop_processes", lambda processes: ([{"stopped": True}] * len(processes), []))
    monkeypatch.setattr(strict, "_stop_private_postgres", lambda **_kwargs: {"stopped": True})
    monkeypatch.setattr(strict, "_port_closed", lambda _port: True)
    monkeypatch.setattr(strict, "_remove_exact_runtime_root", lambda path, _identity: shutil.rmtree(path))

    result = strict._run_once(
        run_number=1, source=tmp_path, candidate=candidate, phase_payload={},
        source_database_url="postgresql://127.0.0.1:54329/vkpi",
        evidence=evidence, timeout=30,
    )
    assert result["pass"] is True
    assert not root.exists()
    assert (evidence / "run-1-verify.json").is_file()
    assert (evidence / "run-1-acceptance.json").is_file()
    assert (evidence / "run-1-cleanup.json").is_file()


def test_strict_runtime_static_contract_has_no_business_queue_or_provider_lane() -> None:
    source = Path("scripts/ops/isolated_strict_runtime_gate.py").read_text(encoding="utf-8")
    assert '"--format=custom", "--no-owner", "--no-acl"' in source
    assert '"--exit-on-error", "--no-owner", "--no-acl"' in source
    assert '"--save", "", "--appendonly", "no"' in source
    assert '"-m", "app.workers.apify_jobs_worker"' in source
    assert '"-m", "app.workers.worker_main"' in source
    assert "run_deploy_gate(" in source
    assert "PROVIDER_ENV_NAMES" in source
