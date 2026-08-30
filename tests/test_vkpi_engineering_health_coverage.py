from __future__ import annotations

import copy
import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_collect as collector
from scripts import vkpi_engineering_health_coverage as coverage
from scripts import vkpi_engineering_health_score as score


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repo(root: Path) -> None:
    (root / "backend/app").mkdir(parents=True)
    (root / "frontend/src").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / ".gitignore").write_text("runtime/\n", encoding="utf-8")
    (root / "backend/app/example.py").write_text(
        "def choose(value: bool) -> int:\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (root / "frontend/src/example.ts").write_text(
        "export const ready = true;\n",
        encoding="utf-8",
    )
    (root / "scripts/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.email", "coverage@example.invalid")
    _run_git(root, "config", "user.name", "Coverage Fixture")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-qm", "fixture")


def _coverage_payload(*, branch_coverage: bool = True) -> dict[str, object]:
    summary = {
        "covered_lines": 8,
        "num_statements": 10,
        "covered_branches": 3,
        "num_branches": 4,
    }
    return {
        "meta": {
            "format": 3,
            "version": "7.13.5",
            "branch_coverage": branch_coverage,
        },
        "files": {"backend/app/example.py": {"summary": summary}},
        "totals": dict(summary),
    }


def _unknown_metric() -> dict[str, object]:
    return {
        "status": "missing",
        "value": None,
        "source": "",
        "observed_at": "",
    }


def _build_fixture(root: Path) -> tuple[dict[str, object], dict[str, object], Path, Path]:
    _make_repo(root)
    artifacts = root / "runtime/engineering-health/coverage"
    artifacts.mkdir(parents=True)
    data_path = artifacts / "fresh.coverage"
    json_path = artifacts / "fresh-coverage.json"
    data_path.write_bytes(b"synthetic fresh coverage data\n")
    json_path.write_text(
        json.dumps(_coverage_payload(), sort_keys=True),
        encoding="utf-8",
    )

    captured = coverage.source_snapshot(root)
    git_state = coverage.snapshot.trusted_git_state(root)
    now = datetime.now(UTC).replace(microsecond=0)
    receipt = coverage.build_coverage_receipt(
        root=root,
        coverage_data_source=data_path,
        coverage_json_source=json_path,
        coverage_data_receipt_path=data_path,
        coverage_json_receipt_path=json_path,
        command=coverage.CANONICAL_TEST_COMMAND,
        exit_code=0,
        started_at=(now - timedelta(seconds=2)).isoformat(),
        finished_at=now.isoformat(),
        source_before=captured,
        source_after=captured,
        git_before=git_state,
        git_after=git_state,
        fresh_workspace_nonce=str(uuid.uuid4()),
        artifacts_existed_before=False,
    )
    evidence: dict[str, object] = {
        "candidate": {
            "repo": str(root.resolve()),
            "head": git_state["head"],
            "branch": git_state["branch"],
            "status_sha256": git_state["status_sha256"],
            "source_content_sha256": captured.content_sha256,
            "source_file_count": len(captured.files),
            "source_and_status_stable": True,
        },
        "metrics": {
            "code": {
                "branch_coverage": _unknown_metric(),
                "line_coverage": _unknown_metric(),
            }
        },
    }
    return evidence, receipt, data_path, json_path


def test_fresh_receipt_merges_branch_and_line_counts(tmp_path: Path) -> None:
    evidence, receipt, _, _ = _build_fixture(tmp_path)
    receipt_path = tmp_path / "runtime/engineering-health/coverage/receipt.json"

    score.merge_coverage_receipt(evidence, receipt_path, receipt)

    code = evidence["metrics"]["code"]
    assert code["branch_coverage"]["status"] == "observed"
    assert code["branch_coverage"]["value"] == 0.75
    assert code["branch_coverage"]["sample_count"] == 4
    assert code["line_coverage"]["value"] == 0.8
    assert code["line_coverage"]["sample_count"] == 10
    assert code["line_coverage"]["details"]["command"] == list(
        coverage.CANONICAL_TEST_COMMAND
    )
    # No core-prefix files in this fixture: the metric stays missing, never neutral.
    assert receipt["coverage"]["core_path_coverage"] is None
    assert "core_path_coverage" not in code
    # Younger-than-window history: every line is in the 30-day change set, and a
    # summary-only coverage JSON takes the changed-file approximation lane.
    assert receipt["coverage"]["change_base"] == "empty_tree"
    assert (
        receipt["coverage"]["change_coverage_method"] == "changed_file_line_coverage_approx"
    )
    assert code["change_coverage"]["status"] == "observed"
    assert code["change_coverage"]["value"] == 0.8
    assert code["change_coverage"]["sample_count"] == 10


@pytest.mark.parametrize("field", ["source_content_sha256", "status_sha256"])
def test_evidence_drift_fails_closed_without_partial_merge(
    tmp_path: Path,
    field: str,
) -> None:
    evidence, receipt, _, _ = _build_fixture(tmp_path)
    before = copy.deepcopy(evidence["metrics"]["code"])
    evidence["candidate"][field] = "f" * 64

    with pytest.raises(score.ContractError, match="coverage receipt rejected"):
        score.merge_coverage_receipt(evidence, tmp_path / "receipt.json", receipt)

    assert evidence["metrics"]["code"] == before


@pytest.mark.parametrize("artifact", ["data", "json"])
def test_artifact_mutation_is_rejected(tmp_path: Path, artifact: str) -> None:
    evidence, receipt, data_path, json_path = _build_fixture(tmp_path)
    target = data_path if artifact == "data" else json_path
    target.write_bytes(target.read_bytes() + b"tampered")

    with pytest.raises(coverage.CoverageReceiptError, match="artifact hash mismatch"):
        coverage.validate_coverage_receipt(evidence, receipt)


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("source", "current source content"),
        ("status", "current Git status"),
    ],
)
def test_current_workspace_drift_is_rejected(
    tmp_path: Path,
    drift: str,
    message: str,
) -> None:
    evidence, receipt, _, _ = _build_fixture(tmp_path)
    if drift == "source":
        (tmp_path / "backend/app/example.py").write_text(
            "VALUE = 'changed'\n",
            encoding="utf-8",
        )
    else:
        (tmp_path / "untracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(coverage.CoverageReceiptError, match=message):
        coverage.validate_coverage_receipt(evidence, receipt)


def test_stale_receipt_is_rejected(tmp_path: Path) -> None:
    evidence, receipt, _, _ = _build_fixture(tmp_path)
    stale = copy.deepcopy(receipt)
    stale_time = datetime.now(UTC) - timedelta(hours=25)
    stale["test"]["started_at"] = (stale_time - timedelta(seconds=2)).isoformat()
    stale["test"]["finished_at"] = stale_time.isoformat()
    stale["generated_at"] = stale_time.isoformat()

    with pytest.raises(coverage.CoverageReceiptError, match="older than 24 hours"):
        coverage.validate_coverage_receipt(evidence, stale)


def test_builder_rejects_noncanonical_or_reused_artifacts(tmp_path: Path) -> None:
    evidence, receipt, data_path, json_path = _build_fixture(tmp_path)
    captured = coverage.source_snapshot(tmp_path)
    git_state = coverage.snapshot.trusted_git_state(tmp_path)
    common = {
        "root": tmp_path,
        "coverage_data_source": data_path,
        "coverage_json_source": json_path,
        "coverage_data_receipt_path": data_path,
        "coverage_json_receipt_path": json_path,
        "exit_code": 0,
        "started_at": receipt["test"]["started_at"],
        "finished_at": receipt["test"]["finished_at"],
        "source_before": captured,
        "source_after": captured,
        "git_before": git_state,
        "git_after": git_state,
        "fresh_workspace_nonce": str(uuid.uuid4()),
        "artifacts_existed_before": False,
    }

    with pytest.raises(coverage.CoverageReceiptError, match="non-canonical"):
        coverage.build_coverage_receipt(command=("pytest",), **common)
    with pytest.raises(coverage.CoverageReceiptError, match="old artifacts"):
        coverage.build_coverage_receipt(
            command=coverage.CANONICAL_TEST_COMMAND,
            **{**common, "artifacts_existed_before": True},
        )
    assert evidence["metrics"]["code"]["line_coverage"]["status"] == "missing"


def test_builder_rejects_source_or_git_drift(tmp_path: Path) -> None:
    _, receipt, data_path, json_path = _build_fixture(tmp_path)
    captured = coverage.source_snapshot(tmp_path)
    changed_source = copy.deepcopy(captured)
    object.__setattr__(changed_source, "content_sha256", "f" * 64)
    git_state = coverage.snapshot.trusted_git_state(tmp_path)
    changed_git = {**git_state, "status_sha256": "f" * 64}
    common = {
        "root": tmp_path,
        "coverage_data_source": data_path,
        "coverage_json_source": json_path,
        "coverage_data_receipt_path": data_path,
        "coverage_json_receipt_path": json_path,
        "command": coverage.CANONICAL_TEST_COMMAND,
        "exit_code": 0,
        "started_at": receipt["test"]["started_at"],
        "finished_at": receipt["test"]["finished_at"],
        "source_before": captured,
        "git_before": git_state,
        "fresh_workspace_nonce": str(uuid.uuid4()),
        "artifacts_existed_before": False,
    }

    with pytest.raises(coverage.CoverageReceiptError, match="source content drifted"):
        coverage.build_coverage_receipt(
            source_after=changed_source,
            git_after=git_state,
            **common,
        )
    with pytest.raises(coverage.CoverageReceiptError, match="Git status drifted"):
        coverage.build_coverage_receipt(
            source_after=captured,
            git_after=changed_git,
            **common,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload["meta"].update(branch_coverage=False), "branch coverage"),
        (lambda payload: payload["totals"].update(num_statements=11), "totals mismatch"),
        (lambda payload: payload["files"].clear(), "measured files"),
    ],
)
def test_coverage_json_contract_rejects_incomplete_or_inconsistent_data(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    _make_repo(tmp_path)
    captured = coverage.source_snapshot(tmp_path)
    payload = _coverage_payload()
    mutation(payload)

    with pytest.raises(coverage.CoverageReceiptError, match=message):
        coverage.parse_coverage_json(
            json.dumps(payload).encode(),
            root=tmp_path,
            captured_source=captured,
        )


def test_coverage_json_requires_every_captured_backend_python_file(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path)
    (tmp_path / "backend/app/unmeasured.py").write_text("VALUE = 2\n", encoding="utf-8")
    captured = coverage.source_snapshot(tmp_path)

    with pytest.raises(coverage.CoverageReceiptError, match="complete backend source scope"):
        coverage.parse_coverage_json(
            json.dumps(_coverage_payload()).encode(),
            root=tmp_path,
            captured_source=captured,
        )


def test_coverage_source_binding_matches_static_collector(tmp_path: Path) -> None:
    _make_repo(tmp_path)

    receipt_source = coverage.source_snapshot(tmp_path)
    collector_source = collector._take_source_snapshot(tmp_path)  # noqa: SLF001

    assert receipt_source.identity() == collector_source.identity()


def test_score_cli_accepts_coverage_receipt_only_with_evidence() -> None:
    args = score.parse_args(
        ["--evidence", "evidence.json", "--coverage-receipt", "coverage.json"]
    )
    assert args.coverage_receipt == "coverage.json"
