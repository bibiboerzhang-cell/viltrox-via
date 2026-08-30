"""core_path_coverage 与 change_coverage 两指标的回执/合并契约(合同 v1.1)。

- core_path_coverage:合同 core_scope_groups 三组前缀(kol/discovery/search、
  services/ai、projects/launch)文件集的行覆盖;
- change_coverage:coverage 逐行 executed/missing 集合 × git diff 近 30 天
  改动行集(窗口锚定 HEAD 提交时间);coverage JSON 无逐行数据时降级为
  改动文件行覆盖近似,口径写进 change_coverage_reason;
- 两字段落在 receipt["coverage"] 的四元绑定盘里(篡改即拒),再经
  score.merge_coverage_receipt 注入 evidence(仿 branch/line 注入法);
  空 core 集 / 空改动窗口保持 missing,绝不折算中性分。
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_coverage as coverage
from scripts import vkpi_engineering_health_score as score


CORE_FILE = "backend/app/domains/kol/ranker.py"
PLAIN_FILE = "backend/app/example.py"
RANKER_V1 = (
    "def rank(value: int) -> int:\n"
    "    if value > 10:\n"
    "        return 3\n"
    "    if value > 5:\n"
    "        return 2\n"
    "    return 1\n"
)
RANKER_V2 = (
    "def rank(value: int) -> int:\n"
    "    if value > 12:\n"
    "        return 3\n"
    "    if value > 5:\n"
    "        return 2\n"
    "    if value > 0:\n"
    "        return 1\n"
    "    return 0\n"
)
PLAIN_V1 = (
    "def choose(value: bool) -> int:\n"
    "    if value:\n"
    "        return 1\n"
    "    return 0\n"
)


def _run_git(root: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+0000")
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    completed = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, env=env
    )
    return completed.stdout.strip()


def _make_repo(root: Path, *, core_change: bool) -> None:
    """Base commit 40 days old; HEAD commit is fresh (inside the 30-day window)."""
    (root / "backend/app/domains/kol").mkdir(parents=True)
    (root / ".gitignore").write_text("runtime/\n", encoding="utf-8")
    (root / PLAIN_FILE).write_text(PLAIN_V1, encoding="utf-8")
    if core_change:
        (root / CORE_FILE).write_text(RANKER_V1, encoding="utf-8")
    _run_git(root, "init", "-q")
    _run_git(root, "config", "user.email", "corepath@example.invalid")
    _run_git(root, "config", "user.name", "Corepath Fixture")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-qm", "base", when=datetime.now(UTC) - timedelta(days=40))
    if core_change:
        (root / CORE_FILE).write_text(RANKER_V2, encoding="utf-8")
    else:
        (root / "README.md").write_text("docs only, no backend change\n", encoding="utf-8")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-qm", "head")


def _file_row(
    covered: int,
    statements: int,
    covered_branches: int,
    branches: int,
    *,
    executed: list[int] | None = None,
    missing: list[int] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "summary": {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }
    }
    if executed is not None and missing is not None:
        row["executed_lines"] = executed
        row["missing_lines"] = missing
    return row


def _payload(rows: dict[str, dict[str, object]]) -> dict[str, object]:
    totals = {"covered_lines": 0, "num_statements": 0, "covered_branches": 0, "num_branches": 0}
    for row in rows.values():
        for field in totals:
            totals[field] += row["summary"][field]  # type: ignore[index]
    return {
        "meta": {"format": 3, "version": "7.13.5", "branch_coverage": True},
        "files": rows,
        "totals": totals,
    }


def _precise_rows() -> dict[str, dict[str, object]]:
    return {
        CORE_FILE: _file_row(7, 8, 5, 6, executed=[1, 2, 3, 4, 5, 6, 7], missing=[8]),
        PLAIN_FILE: _file_row(3, 4, 1, 2, executed=[1, 2, 3], missing=[4]),
    }


def _summary_only_rows() -> dict[str, dict[str, object]]:
    return {
        CORE_FILE: _file_row(7, 8, 5, 6),
        PLAIN_FILE: _file_row(3, 4, 1, 2),
    }


def _build_fixture(
    root: Path, rows: dict[str, dict[str, object]]
) -> tuple[dict[str, object], dict[str, object], Path]:
    artifacts = root / "runtime/engineering-health/coverage"
    artifacts.mkdir(parents=True)
    data_path = artifacts / "fresh.coverage"
    json_path = artifacts / "fresh-coverage.json"
    data_path.write_bytes(b"synthetic fresh coverage data\n")
    json_path.write_text(json.dumps(_payload(rows), sort_keys=True), encoding="utf-8")
    captured = coverage.source_snapshot(root)
    git_state = coverage.snapshot.trusted_git_state(root)
    now = datetime.now(UTC).replace(microsecond=0)
    receipt = coverage.build_coverage_receipt(
        root=root,
        coverage_data_source=data_path, coverage_json_source=json_path,
        coverage_data_receipt_path=data_path, coverage_json_receipt_path=json_path,
        command=coverage.CANONICAL_TEST_COMMAND, exit_code=0,
        started_at=(now - timedelta(seconds=2)).isoformat(), finished_at=now.isoformat(),
        source_before=captured, source_after=captured,
        git_before=git_state, git_after=git_state,
        fresh_workspace_nonce=str(uuid.uuid4()), artifacts_existed_before=False,
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
        "metrics": {"code": {}},
    }
    return evidence, receipt, artifacts / "receipt.json"


def test_receipt_carries_core_path_coverage_over_contract_prefixes(tmp_path: Path) -> None:
    _make_repo(tmp_path, core_change=True)
    _, receipt, _ = _build_fixture(tmp_path, _precise_rows())

    observed = receipt["coverage"]
    assert observed["line_coverage"] == 10 / 12
    assert observed["core_path_coverage"] == 7 / 8
    assert observed["core_path_covered_lines"] == 7
    assert observed["core_path_num_statements"] == 8
    assert observed["core_path_file_count"] == 1


def test_change_coverage_uses_line_hits_times_30day_diff(tmp_path: Path) -> None:
    _make_repo(tmp_path, core_change=True)
    _, receipt, _ = _build_fixture(tmp_path, _precise_rows())

    observed = receipt["coverage"]
    # HEAD edited ranker.py lines {2, 6, 7, 8}; executed among them = {2, 6, 7}.
    assert observed["change_coverage_method"] == "coverage_line_hits_x_git_diff_30d"
    assert observed["change_base"] == _run_git(tmp_path, "rev-parse", "HEAD~1")
    assert observed["change_window_days"] == 30
    assert observed["change_file_count"] == 1
    assert observed["change_covered_lines"] == 3
    assert observed["change_num_statements"] == 4
    assert observed["change_coverage"] == 3 / 4


def test_change_coverage_falls_back_without_line_contexts(tmp_path: Path) -> None:
    _make_repo(tmp_path, core_change=True)
    _, receipt, _ = _build_fixture(tmp_path, _summary_only_rows())

    observed = receipt["coverage"]
    assert observed["change_coverage_method"] == "changed_file_line_coverage_approx"
    assert "口径近似" in observed["change_coverage_reason"]
    # Approximation = whole-file line coverage of the one changed file.
    assert observed["change_file_count"] == 1
    assert observed["change_coverage"] == 7 / 8
    assert observed["change_num_statements"] == 8


def test_merge_injects_both_metrics_into_evidence(tmp_path: Path) -> None:
    _make_repo(tmp_path, core_change=True)
    evidence, receipt, receipt_path = _build_fixture(tmp_path, _precise_rows())

    score.merge_coverage_receipt(evidence, receipt_path, receipt)

    code = evidence["metrics"]["code"]
    core = code["core_path_coverage"]
    assert core["status"] == "observed"
    assert core["value"] == 7 / 8
    assert core["sample_count"] == 8
    assert core["details"]["core_path_file_count"] == 1
    assert core["details"]["command"] == list(coverage.CANONICAL_TEST_COMMAND)
    change = code["change_coverage"]
    assert change["status"] == "observed"
    assert change["value"] == 3 / 4
    assert change["sample_count"] == 4
    assert change["details"]["method"] == "coverage_line_hits_x_git_diff_30d"
    assert change["details"]["change_window_days"] == 30
    assert change["details"]["change_base"] == _run_git(tmp_path, "rev-parse", "HEAD~1")
    assert change["observed_at"] == receipt["test"]["finished_at"]


def test_empty_core_scope_and_empty_window_stay_missing_not_neutral(tmp_path: Path) -> None:
    _make_repo(tmp_path, core_change=False)
    evidence, receipt, receipt_path = _build_fixture(
        tmp_path, {PLAIN_FILE: _file_row(3, 4, 1, 2, executed=[1, 2, 3], missing=[4])}
    )

    observed = receipt["coverage"]
    assert observed["core_path_coverage"] is None
    assert observed["core_path_num_statements"] == 0
    assert observed["change_coverage"] is None
    assert observed["change_coverage_method"] == "none"
    assert observed["change_coverage_reason"] == "no_measured_backend_changes_in_window"

    score.merge_coverage_receipt(evidence, receipt_path, receipt)
    code = evidence["metrics"]["code"]
    assert "core_path_coverage" not in code
    assert "change_coverage" not in code
    assert code["line_coverage"]["status"] == "observed"


@pytest.mark.parametrize("field", ["core_path_coverage", "change_coverage"])
def test_tampered_scope_metric_fails_receipt_binding(tmp_path: Path, field: str) -> None:
    _make_repo(tmp_path, core_change=True)
    evidence, receipt, _ = _build_fixture(tmp_path, _precise_rows())
    tampered = copy.deepcopy(receipt)
    tampered["coverage"][field] = 0.999

    with pytest.raises(
        coverage.CoverageReceiptError, match="coverage metrics do not match coverage JSON"
    ):
        coverage.validate_coverage_receipt(evidence, tampered)
