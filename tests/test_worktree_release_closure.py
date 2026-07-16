from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import worktree_release_closure as closure  # noqa: E402
import worktree_release_scope as scope  # noqa: E402


HEAD_BRANCH = "codex/fixture"


def _run(root: Path, *args: str) -> None:
    subprocess.run(args, cwd=root, check=True, capture_output=True, text=True)


def _write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "git", "init", "-b", HEAD_BRANCH)
    _run(root, "git", "config", "user.email", "fixture@example.invalid")
    _run(root, "git", "config", "user.name", "Fixture")
    _write(root, "README.md", "baseline\n")
    _write(root, "backend/app/domains/kol/pool.py", "VALUE = 'baseline'\n")
    _write(root, "backend/app/domains/kol/roi_aggregate.py", "VALUE = 'baseline'\n")
    _run(root, "git", "add", ".")
    _run(root, "git", "commit", "-m", "baseline")
    return root


def _review(
    inventory: dict[str, object],
    *,
    files: list[str] | None = None,
    lanes: list[str] | None = None,
) -> dict[str, object]:
    entries = {
        str(entry["path"]): entry
        for entry in inventory["entries"]  # type: ignore[index]
    }
    return {
        "schema": closure.REVIEW_SCHEMA,
        "inventory_digest": closure.inventory_digest(inventory),
        "expected_branch": inventory["branch"],
        "expected_head": inventory["head"],
        "review": {
            "status": closure.REVIEW_STATUS,
            "scope": closure.REVIEW_SCOPE,
            "reviewer_id": "test-reviewer",
            "reviewed_at": "2026-07-15T20:00:00Z",
            "deploy_authorized": False,
        },
        "lane_selections": [
            {
                "lane": lane,
                "selection_mode": "all_inventory_paths",
                "expected_path_count": sum(
                    1 for entry in entries.values() if entry["category"] == lane
                ),
                "expected_lane_digest": closure.lane_digest(inventory, lane),
            }
            for lane in lanes or []
        ],
        "file_selections": [
            {
                "path": path,
                "lane": entries[path]["category"],
                "expected_sha256": entries[path]["sha256"],
            }
            for path in files or []
        ],
    }


def _dirty_kol_pair(root: Path) -> None:
    _write(
        root,
        "backend/app/domains/kol/pool.py",
        "from app.domains.kol.roi_aggregate import VALUE\nRESULT = VALUE\n",
    )
    _write(root, "backend/app/domains/kol/roi_aggregate.py", "VALUE = 'changed'\n")


def test_file_review_expands_dirty_python_dependency_and_never_grants_authority(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _dirty_kol_pair(root)
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/pool.py"])

    manifest = closure.build_closure_manifest(root, inventory, review)

    files = {entry["path"]: entry for entry in manifest["files"]}
    assert set(files) == {
        "backend/app/domains/kol/pool.py",
        "backend/app/domains/kol/roi_aggregate.py",
    }
    assert files["backend/app/domains/kol/pool.py"]["inclusion"] == "reviewed_file"
    dependency = files["backend/app/domains/kol/roi_aggregate.py"]
    assert dependency["inclusion"] == "derived_dependency"
    assert dependency["dependency_of"] == ["backend/app/domains/kol/pool.py"]
    assert manifest["selection"] == {
        "requested_path_count": 1,
        "derived_dependency_count": 1,
        "closure_path_count": 2,
        "requested_paths_sha256": closure._digest(["backend/app/domains/kol/pool.py"]),
    }
    assert manifest["safety"]["authority"] == "none"
    assert manifest["safety"]["deploy_authorized"] is False
    assert manifest["safety"]["bundle_created"] is False
    assert manifest["result"]["release_ready"] is False
    assert closure.verify_closure_manifest(root, manifest)["verified_file_count"] == 2


def test_exact_lane_digest_selects_lane_but_digest_or_authority_drift_fails(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    _dirty_kol_pair(root)
    inventory = scope.build_manifest(root)
    review = _review(inventory, lanes=["p1_kol_performance"])

    manifest = closure.build_closure_manifest(root, inventory, review)
    assert manifest["selection"]["requested_path_count"] == 2
    assert manifest["selection"]["derived_dependency_count"] == 0

    review["lane_selections"][0]["expected_lane_digest"] = "0" * 64
    with pytest.raises(closure.ClosureError, match="lane_digest_mismatch"):
        closure.build_closure_manifest(root, inventory, review)

    review = _review(inventory, lanes=["p1_kol_performance"])
    review["review"]["deploy_authorized"] = True
    with pytest.raises(closure.ClosureError, match="deploy_authority_must_be_false"):
        closure.build_closure_manifest(root, inventory, review)


def test_direct_script_import_adds_dirty_scripts_helper(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root, "scripts/ops/atomic_release_probe.py", "from helper import VALUE\n")
    _write(root, "scripts/ops/helper.py", "VALUE = 1\n")
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["scripts/ops/atomic_release_probe.py"])

    manifest = closure.build_closure_manifest(root, inventory, review)

    files = {entry["path"]: entry for entry in manifest["files"]}
    assert files["scripts/ops/helper.py"]["inclusion"] == "derived_dependency"
    assert files["scripts/ops/helper.py"]["dependency_of"] == [
        "scripts/ops/atomic_release_probe.py"
    ]


def test_any_dirty_worktree_drift_fails_even_when_unselected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _dirty_kol_pair(root)
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/pool.py"])

    _write(root, "unselected-note.txt", "drift after review\n")

    with pytest.raises(closure.ClosureError, match="dirty_worktree_drift"):
        closure.build_closure_manifest(root, inventory, review)


def test_manual_secret_symlink_and_staged_paths_fail_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _write(root, "manual.txt", "manual\n")
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["manual.txt"])
    with pytest.raises(closure.ClosureError, match="manual_review_path_forbidden"):
        closure.build_closure_manifest(root, inventory, review)

    (root / "manual.txt").unlink()
    _write(root, "backend/app/domains/kol/private.pem", "not-even-a-real-key\n")
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/private.pem"])
    with pytest.raises(closure.ClosureError, match="secret_bearing_path_forbidden"):
        closure.build_closure_manifest(root, inventory, review)

    (root / "backend/app/domains/kol/private.pem").unlink()
    (root / "backend/app/domains/kol/link.py").symlink_to(root / "README.md")
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/link.py"])
    with pytest.raises(closure.ClosureError, match="non_regular_file_forbidden"):
        closure.build_closure_manifest(root, inventory, review)

    (root / "backend/app/domains/kol/link.py").unlink()
    _write(root, "backend/app/domains/kol/pool.py", "VALUE = 'staged'\n")
    _run(root, "git", "add", "backend/app/domains/kol/pool.py")
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/pool.py"])
    with pytest.raises(closure.ClosureError, match="unsupported_or_staged_git_status"):
        closure.build_closure_manifest(root, inventory, review)


def test_high_confidence_secret_and_tampered_manifest_fail_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fake_secret = "sk_" + "live_" + "1234567890ABCDEF"
    _write(
        root,
        "backend/app/domains/kol/pool.py",
        f"TOKEN = '{fake_secret}'\n",
    )
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/pool.py"])
    with pytest.raises(closure.ClosureError, match="high_confidence_secret_detected"):
        closure.build_closure_manifest(root, inventory, review)

    _dirty_kol_pair(root)
    inventory = scope.build_manifest(root)
    review = _review(inventory, files=["backend/app/domains/kol/pool.py"])
    manifest = closure.build_closure_manifest(root, inventory, review)
    manifest["files"][0]["sha256"] = "f" * 64
    with pytest.raises(closure.ClosureError, match="closure_manifest_tampered"):
        closure.verify_closure_manifest(root, manifest)


def test_output_is_private_exclusive_and_cli_errors_have_no_authority(tmp_path: Path) -> None:
    target = tmp_path / "closure.json"
    closure._write_exclusive_json(target, {"ok": True})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(closure.ClosureError, match="refusing_to_overwrite_output"):
        closure._write_exclusive_json(target, {"ok": False})

    assert closure.main([]) == 2
    payload = json.loads(closure._error_payload(closure.ClosureError("blocked")))
    assert payload["status"] == "blocked"
    assert payload["deploy_authorized"] is False
