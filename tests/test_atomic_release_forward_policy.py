"""Synthetic filesystem CLI contracts; no services, database or provider access."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts.ops.atomic_release_integrity import RELEASE_MANIFEST_NAME
from scripts.ops.release_forward_policy import FORWARD_ONLY_POLICY
from test_atomic_release_layout import UNITS, _layout, _prepare_args, _release, _rollback_metadata, _run
from test_release_forward_policy import NAMES, ROOT, plan


def _evidence_file(tmp_path, root, **changes):
    evidence = {**plan(), "env_sha256": hashlib.sha256((root / ".env").read_bytes()).hexdigest(), **changes}
    target = tmp_path / "synthetic-forward-plan.json"
    target.write_text(json.dumps(evidence), encoding="utf-8")
    target.chmod(0o600)
    return target, evidence


def _flags(evidence_file):
    return ("--pending-migrations", ",".join(NAMES), "--release-policy", FORWARD_ONLY_POLICY,
            "--forward-only-evidence-file", str(evidence_file))


def _setup(tmp_path):
    root, unit_dir = _layout(tmp_path)
    old = _release(root, "old", "0" * 40, seal_args=())
    (root / "current").symlink_to(old)
    release_id = plan()["release_id"]
    migrations = root / "releases" / release_id / "migrations"
    migrations.mkdir(parents=True)
    for name in NAMES:
        (migrations / name).write_bytes((ROOT / "migrations" / name).read_bytes())
    evidence_file, evidence = _evidence_file(tmp_path, root)
    return root, unit_dir, release_id, evidence_file, evidence


def _prepare_forward(root, unit_dir, release_id, evidence_file, *, check=True):
    args = ["prepare", "--root", str(root), "--release-id", release_id, "--unit-dir", str(unit_dir), *_flags(evidence_file)]
    for unit in UNITS:
        args.extend(("--unit-name", unit))
    return _run(*args, check=check)


def _state(root, unit_dir):
    return {
        "current": os.readlink(root / "current"),
        "previous": os.readlink(root / "previous") if (root / "previous").is_symlink() else None,
        "env": (root / ".env").read_bytes(),
        "units": {path.name: path.read_bytes() for path in unit_dir.iterdir()},
        "unknown": (root / "runtime/unknown.json").read_bytes(),
        "fence": (root / "runtime/synthetic-fence").read_bytes(),
    }


def _simulate_active_pointer_for_restore_fixture(root, release_id):
    """Model already-existing state to test restore denial, never a live activation proof."""
    (root / "current").unlink()
    (root / "current").symlink_to(root / "releases" / release_id)


def test_seal_prepare_readonly_inspect_and_restore_keep_unknown_and_fence(tmp_path):
    root, unit_dir, release_id, evidence_file, evidence = _setup(tmp_path)
    release = _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    _prepare_forward(root, unit_dir, release_id, evidence_file)
    (root / "runtime/unknown.json").write_text('{"state":"unknown","actual_cost_usd":null,"held":1.23}')
    (root / "runtime/synthetic-fence").write_text("active\n")
    before = _state(root, unit_dir)
    inspected = json.loads(_run("inspect-policy", "--root", str(root), "--release-id", release_id).stdout)
    assert inspected["forward_only"] is True
    assert inspected["live_drain_authorized"] is False
    assert inspected["claim_status"] == "seal_plan_binding_only"
    manifest = json.loads((release / RELEASE_MANIFEST_NAME).read_text())
    captured = _rollback_metadata(root, release_id)
    for document in (manifest, captured):
        assert document["forward_only_evidence"] == evidence
        assert document["database_rollback"] == "forbidden-forward-only"
        assert document["app_rollback_allowed"] is document["database_rollback_allowed"] is False
        assert document["forward_compatible_migrations"] == []
    restored = _run("restore", "--root", str(root), "--release-id", release_id,
                    "--unit-dir", str(unit_dir), check=False)
    assert restored.returncode != 0
    assert "keep the fence" in restored.stderr
    assert _state(root, unit_dir) == before


def test_plan_only_release_cannot_activate_even_after_valid_prepare(tmp_path):
    root, unit_dir, release_id, evidence_file, _evidence = _setup(tmp_path)
    _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    _prepare_forward(root, unit_dir, release_id, evidence_file)
    before = (os.readlink(root / "current"), os.readlink(root / "previous"), (root / ".env").read_bytes())
    result = _run("activate", "--root", str(root), "--release-id", release_id, check=False)
    assert result.returncode != 0
    assert "not live drain authorization" in result.stderr
    assert (os.readlink(root / "current"), os.readlink(root / "previous"), (root / ".env").read_bytes()) == before


@pytest.mark.parametrize("operation", ["seal", "prepare"])
@pytest.mark.parametrize("case", ["missing", "symlink", "hardlink", "mode", "duplicate", "other-target", "other-release", "wrong-env"])
def test_plan_file_boundary_fails_without_filesystem_mutation(tmp_path, operation, case):
    root, unit_dir, release_id, evidence_file, evidence = _setup(tmp_path)
    if operation == "prepare":
        _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    if case == "missing":
        evidence_file.unlink()
    elif case == "symlink":
        other = tmp_path / "other-plan"
        evidence_file.rename(other)
        evidence_file.symlink_to(other)
    elif case == "hardlink":
        (tmp_path / "linked-plan").hardlink_to(evidence_file)
    elif case == "mode":
        evidence_file.chmod(0o644)
    elif case == "duplicate":
        evidence_file.write_text(json.dumps(evidence)[:-1] + ',"schema":"duplicate"}')
    else:
        key = {"other-target": "target_git_sha", "other-release": "release_id", "wrong-env": "env_sha256"}[case]
        evidence[key] = {"other-target": "f" * 40, "other-release": "other-release", "wrong-env": "d" * 64}[case]
        evidence_file.write_text(json.dumps(evidence))
    if operation == "prepare":
        result = _prepare_forward(root, unit_dir, release_id, evidence_file, check=False)
    else:
        # Populate the remaining synthetic payload without asking a real runner to execute it.
        import test_atomic_release_layout as fixture
        from unittest.mock import patch
        with patch.object(fixture, "_run"):
            _release(root, release_id, "1" * 40, seal_args=())
        result = _run("seal", "--root", str(root), "--release-id", release_id, "--git-sha", "1" * 40,
                      *_flags(evidence_file), check=False)
    assert result.returncode != 0
    assert not (root / ".release-controller").exists()
    assert not (root / "previous").exists()
    if operation == "seal":
        assert not (root / "releases" / release_id / RELEASE_MANIFEST_NAME).exists()
    assert "APP_GIT_SHA=old" in (root / ".env").read_text()


def test_prepare_must_use_exact_sealed_plan_and_observed_source(tmp_path):
    root, unit_dir, release_id, evidence_file, evidence = _setup(tmp_path)
    _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    for field, changed in (("backup_receipt_sha256", "f" * 64), ("source_git_sha", "e" * 40)):
        evidence_file.write_text(json.dumps({**evidence, field: changed}))
        result = _prepare_forward(root, unit_dir, release_id, evidence_file, check=False)
        assert result.returncode != 0
        assert not (root / ".release-controller").exists()
    evidence_file.write_text(json.dumps(evidence))
    default = _run("prepare", "--root", str(root), "--release-id", release_id,
                   "--unit-dir", str(unit_dir), check=False)
    assert default.returncode != 0
    assert not (root / ".release-controller").exists()


def test_source_sha_is_checked_even_when_plan_and_seal_agree(tmp_path):
    root, unit_dir, release_id, evidence_file, evidence = _setup(tmp_path)
    evidence["source_git_sha"] = "e" * 40
    evidence_file.write_text(json.dumps(evidence))
    _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    result = _prepare_forward(root, unit_dir, release_id, evidence_file, check=False)
    assert result.returncode != 0
    assert "source SHA" in result.stderr
    assert not (root / ".release-controller").exists()


def test_missing_capture_flags_do_not_bypass_sealed_forward_only_restore(tmp_path):
    from test_atomic_release_layout import _rewrite_rollback_metadata
    root, unit_dir, release_id, evidence_file, _evidence = _setup(tmp_path)
    _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    _prepare_forward(root, unit_dir, release_id, evidence_file)
    _simulate_active_pointer_for_restore_fixture(root, release_id)
    metadata = _rollback_metadata(root, release_id)
    stripped = {key: value for key, value in metadata.items()
                if key not in {"release_policy", "recovery_mode", "app_rollback_allowed", "database_rollback_allowed",
                               "preserve_unknown_budget", "live_drain_authorized", "claim_status"}
                and not key.startswith("forward_only_")}
    _rewrite_rollback_metadata(root, release_id, stripped)
    before = (os.readlink(root / "current"), (root / ".env").read_bytes())
    result = _run("restore", "--root", str(root), "--release-id", release_id,
                  "--unit-dir", str(unit_dir), check=False)
    assert result.returncode != 0
    assert "forward-only" in result.stderr
    assert (os.readlink(root / "current"), (root / ".env").read_bytes()) == before


def test_old_default_capture_cannot_roll_back_active_forward_only_release(tmp_path):
    root, unit_dir, release_id, evidence_file, _evidence = _setup(tmp_path)
    _run(*_prepare_args(root, unit_dir, "old"))
    _release(root, release_id, "1" * 40, seal_args=_flags(evidence_file))
    _prepare_forward(root, unit_dir, release_id, evidence_file)
    _simulate_active_pointer_for_restore_fixture(root, release_id)
    result = _run("restore", "--root", str(root), "--release-id", "old",
                  "--unit-dir", str(unit_dir), check=False)
    assert result.returncode != 0
    assert "forward-only" in result.stderr
    assert Path(os.readlink(root / "current")).name == release_id


def test_legacy_default_policy_cli_remains_readable(tmp_path):
    root, _unit_dir = _layout(tmp_path)
    _release(root, "old", "0" * 40, seal_args=())
    result = _run("inspect-policy", "--root", str(root), "--release-id", "old")
    assert json.loads(result.stdout) == {"release_policy": "rollback-compatible", "forward_only": False}


def test_plan_owned_by_another_controller_is_rejected(tmp_path, monkeypatch):
    from argparse import Namespace
    from scripts.ops import atomic_release_manifest as adapter
    from scripts.ops.atomic_release_units import LayoutError
    root, _unit_dir, release_id, evidence_file, _evidence = _setup(tmp_path)
    args = Namespace(root=str(root), release_id=release_id, pending_migrations=",".join(NAMES),
                     release_policy=FORWARD_ONLY_POLICY, forward_only_evidence_file=str(evidence_file))
    original_uid = os.geteuid()
    monkeypatch.setattr(adapter.os, "geteuid", lambda: original_uid + 1)
    with pytest.raises(LayoutError, match="owned by its controller"):
        adapter.policy_arguments(args, git_sha="1" * 40)


def test_sql_drift_between_plan_check_and_payload_fingerprint_cannot_be_sealed(tmp_path, monkeypatch):
    import test_atomic_release_layout as fixture
    from unittest.mock import patch
    from scripts.ops import atomic_release_layout as layout, atomic_release_manifest as adapter
    from scripts.ops.atomic_release_units import LayoutError
    root, _unit_dir, release_id, evidence_file, _evidence = _setup(tmp_path)
    with patch.object(fixture, "_run"):
        release = _release(root, release_id, "1" * 40, seal_args=())
    original = adapter.payload_fingerprint
    def changed_fingerprint(*args, **kwargs):
        with (release / "migrations" / NAMES[0]).open("ab") as handle:
            handle.write(b"\n-- changed after plan check\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, "payload_fingerprint", changed_fingerprint)
    args = layout.parser().parse_args(["seal", "--root", str(root), "--release-id", release_id,
                                      "--git-sha", "1" * 40, *_flags(evidence_file)])
    with pytest.raises(LayoutError, match="not reviewed"):
        layout.seal(args)
    assert Path(os.readlink(root / "current")).name == "old"


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_default_restore_does_not_require_failed_candidate_payload_to_be_healthy(tmp_path, damage):
    root, unit_dir = _layout(tmp_path)
    old = _release(root, "old", "0" * 40, seal_args=())
    (root / "current").symlink_to(old)
    failed = _release(root, "failed-default", "1" * 40)
    _run(*_prepare_args(root, unit_dir, "failed-default"))
    _run("activate", "--root", str(root), "--release-id", "failed-default")
    module = failed / "backend/app/workers/apify_jobs_worker.py"
    if damage == "changed":
        module.chmod(0o600)
        module.write_text("# damaged candidate\n")
    else:
        module.parent.chmod(0o700)
        module.unlink()
    (root / ".env").write_text("APP_GIT_SHA=failed\n")
    result = _run("restore", "--root", str(root), "--release-id", "failed-default",
                  "--unit-dir", str(unit_dir), check=False)
    assert result.returncode == 0, result.stderr
    assert (root / "current").resolve() == old
    assert "APP_GIT_SHA=old" in (root / ".env").read_text()
