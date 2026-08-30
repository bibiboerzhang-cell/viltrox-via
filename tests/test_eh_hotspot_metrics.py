"""Hotspot metric tests (合同 v1.1 口径甲): heat threshold, CC mean, merging.

Covers the two evolution hotspot metrics:
- unhealthy_hotspot_count: >=10 non-merge window commits AND mean function CC > 12
- hotspot_cc_mean: mean CC of all functions across all hot files (2 decimals)

Both metrics carry no contract minimum_samples, so they must be observed even
when the 180-day history window is incomplete.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_evolution as evolution
from scripts import vkpi_engineering_health_score_evolution as score_evolution


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _init(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")


def _commit(
    root: Path,
    *,
    timestamp: datetime,
    files: dict[str, str],
    remove: tuple[str, ...] = (),
    author: str = "Dev",
    email: str = "dev@example.com",
) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for relative in remove:
        _git(root, "rm", "-q", relative)
    _git(root, "add", ".")
    stamp = timestamp.astimezone(UTC).isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": stamp,
    }
    _git(root, "commit", "-q", "-m", f"rev at {stamp}", env=env)


def _py_with_ccs(ccs: list[int], rev: int) -> str:
    """Python source whose functions have exactly the requested CC values.

    CC = 1 + decision points; each plain ``if`` adds one decision.
    """
    parts = [f"# rev {rev}"]
    for index, cc in enumerate(ccs):
        lines = [f"def f{index}(x=0):"]
        for j in range(cc - 1):
            lines.append(f"    if x > {j}:")
            lines.append("        x += 1")
        lines.append("    return x")
        parts.append("\n".join(lines))
    return "\n\n\n".join(parts) + "\n"


HOT_BOUNDARY = "backend/app/domains/kol/hot_boundary.py"  # mean CC exactly 12.0
HOT_UNHEALTHY = "backend/app/workers/hot_unhealthy.py"  # mean CC 12.25 (> 12)
WARM_NINE = "backend/app/core/warm_nine.py"  # only 9 commits, huge CC
SCRIPT_HOT = "scripts/hot_script.py"  # excluded: not a production root
FRONTEND_HOT = "frontend/src/hot_ui.ts"  # hot, but no Python CC
TEST_HOT = "backend/app/domains/kol/test_hot_file.py"  # excluded: test file
DELETED_HOT = "backend/app/domains/kol/deleted_hot.py"  # hot churn, gone at HEAD


def _build_hotspot_repo(root: Path) -> None:
    """12 commits inside a ~33-day (incomplete) window.

    Touch counts: HOT_UNHEALTHY/SCRIPT_HOT/TEST_HOT = 12; HOT_BOUNDARY (one of
    them authored by a bot), FRONTEND_HOT = 10; WARM_NINE = 9; DELETED_HOT = 10
    touches then deleted in the final commit.
    """
    _init(root)
    start = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(12):
        files: dict[str, str] = {
            HOT_UNHEALTHY: _py_with_ccs([12, 12, 12, 13], i),
            SCRIPT_HOT: _py_with_ccs([40], i),
            TEST_HOT: _py_with_ccs([40], i),
        }
        if i < 10:
            files[HOT_BOUNDARY] = _py_with_ccs([11, 13], i)
            files[FRONTEND_HOT] = f"export const UI = 'rev{i}'\n"
            files[DELETED_HOT] = _py_with_ccs([40], i)
        if i < 9:
            files[WARM_NINE] = _py_with_ccs([40], i)
        remove = (DELETED_HOT,) if i == 11 else ()
        author, email = ("Dev", "dev@example.com")
        if i == 4:
            # Heat counts ALL non-merge commits: a bot commit must count too.
            author, email = ("release-bot[bot]", "release-bot@noreply.example.com")
        _commit(
            root,
            timestamp=start + timedelta(days=3 * i),
            files=files,
            remove=remove,
            author=author,
            email=email,
        )


def test_production_file_filter_excludes_scripts_and_tests() -> None:
    assert evolution._hotspot_production_file("backend/app/domains/kol/pool.py")
    assert evolution._hotspot_production_file("frontend/src/components/App.tsx")
    assert not evolution._hotspot_production_file("scripts/anything.py")
    assert not evolution._hotspot_production_file("backend/app/tests/util.py")
    assert not evolution._hotspot_production_file("backend/app/domains/test_x.py")
    assert not evolution._hotspot_production_file("frontend/src/a.test.ts")
    assert not evolution._hotspot_production_file("backend/app/data.json")
    assert not evolution._hotspot_production_file("migrations/0001.sql")


def test_hotspot_thresholds_exclusions_and_cc_mean(tmp_path: Path) -> None:
    _build_hotspot_repo(tmp_path)
    receipt = evolution.build_receipt(tmp_path)

    hotspot = receipt["details"]["hotspot"]
    rows = {row["path"]: row for row in hotspot["files"]}

    # Heat threshold: 10 commits qualify, 9 do not; scripts/tests excluded.
    assert set(rows) == {HOT_BOUNDARY, HOT_UNHEALTHY, FRONTEND_HOT}
    assert WARM_NINE not in rows  # 9 commits with CC 40 must not leak in
    assert SCRIPT_HOT not in rows
    assert TEST_HOT not in rows
    assert rows[HOT_BOUNDARY]["window_commits"] == 10
    assert rows[HOT_UNHEALTHY]["window_commits"] == 12

    # Deleted-in-window hot path is reported separately, never counted.
    assert hotspot["hot_paths_missing_from_head"] == [DELETED_HOT]

    # Unhealthy boundary is strict: mean 12.0 stays healthy, 12.25 does not.
    assert rows[HOT_BOUNDARY]["cc_mean"] == 12.0
    assert rows[HOT_BOUNDARY]["unhealthy"] is False
    assert rows[HOT_UNHEALTHY]["cc_mean"] == 12.25
    assert rows[HOT_UNHEALTHY]["unhealthy"] is True

    # Non-Python hot file counts for heat but contributes no CC samples.
    assert rows[FRONTEND_HOT]["cc_status"] == "non_python_no_cc"
    assert rows[FRONTEND_HOT]["function_count"] is None
    assert rows[FRONTEND_HOT]["unhealthy"] is False

    # Pool = [11, 13] + [12, 12, 12, 13] -> 73 / 6 = 12.1666… -> 12.17.
    assert hotspot["cc_function_count"] == 6
    assert hotspot["hotspot_cc_mean"] == 12.17
    assert hotspot["unhealthy_hotspot_count"] == 1
    assert hotspot["python_parse_failures"] == []


def test_hotspot_metrics_observed_despite_incomplete_window(tmp_path: Path) -> None:
    _build_hotspot_repo(tmp_path)
    receipt = evolution.build_receipt(tmp_path)

    assert receipt["window"]["complete"] is False
    metrics = receipt["metrics"]
    # The 180-day-gated metrics stay fail-closed…
    assert metrics["core_domain_bus_factor_min"]["status"] == "unknown"
    assert metrics["temporal_coupling_p95"]["status"] == "unknown"
    # …while the hotspot metrics (no contract minimum_samples) are observed.
    count_entry = metrics["unhealthy_hotspot_count"]
    mean_entry = metrics["hotspot_cc_mean"]
    assert count_entry["status"] == "observed"
    assert count_entry["value"] == 1
    assert mean_entry["status"] == "observed"
    assert mean_entry["value"] == 12.17
    for entry in (count_entry, mean_entry):
        assert entry["sample_unit"] == "days"
        assert entry["sample_count"] == 33  # actually covered days, not 180


def test_worktree_edits_do_not_change_hotspot_metrics(tmp_path: Path) -> None:
    _build_hotspot_repo(tmp_path)
    clean = evolution.build_receipt(tmp_path)
    # Blow up the CC of a hot file in the worktree only (uncommitted).
    (tmp_path / HOT_BOUNDARY).write_text(_py_with_ccs([90, 90], 99), encoding="utf-8")
    dirty = evolution.build_receipt(tmp_path)

    assert dirty["candidate"]["worktree_dirty"] is True
    assert clean["metrics"] == dirty["metrics"]
    assert clean["details"]["hotspot"] == dirty["details"]["hotspot"]


def test_no_hotspot_files_yields_zero_count_and_unknown_mean(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(2):
        _commit(
            tmp_path,
            timestamp=end - timedelta(days=1 - i),
            files={HOT_BOUNDARY: _py_with_ccs([3], i)},
        )
    receipt = evolution.build_receipt(tmp_path)

    count_entry = receipt["metrics"]["unhealthy_hotspot_count"]
    mean_entry = receipt["metrics"]["hotspot_cc_mean"]
    assert count_entry["status"] == "observed"
    assert count_entry["value"] == 0
    assert mean_entry["status"] == "unknown"
    assert mean_entry["reason"] == "no_hotspot_functions"
    assert receipt["details"]["hotspot"]["files"] == []


def _merge(receipt: dict, tmp_path: Path) -> dict:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = {"candidate": {"head": receipt["candidate"]["head"]}, "metrics": {}}
    score_evolution.merge_evolution_receipt(evidence, receipt_path, receipt)
    return evidence["metrics"]["evolution"]


def test_merge_accepts_partial_window_hotspot_metrics(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_hotspot_repo(repo)
    receipt = evolution.build_receipt(repo)

    merged = _merge(receipt, tmp_path)

    assert merged["unhealthy_hotspot_count"]["status"] == "observed"
    assert merged["unhealthy_hotspot_count"]["value"] == 1
    assert merged["unhealthy_hotspot_count"]["sample_count"] == 33
    assert merged["hotspot_cc_mean"]["status"] == "observed"
    assert merged["hotspot_cc_mean"]["value"] == 12.17
    # The gated metrics still merge as unknown.
    assert merged["core_domain_bus_factor_min"]["status"] == "unknown"
    assert merged["temporal_coupling_p95"]["status"] == "unknown"


def test_merge_still_rejects_gated_metrics_before_180_days(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_hotspot_repo(repo)
    receipt = evolution.build_receipt(repo)
    tampered = copy.deepcopy(receipt)
    tampered["metrics"]["temporal_coupling_p95"].update(
        {"status": "observed", "value": 0.1, "sample_count": 180}
    )

    with pytest.raises(score_evolution.EvolutionReceiptError, match="before 180 days"):
        _merge(tampered, tmp_path)


def test_merge_rejects_invalid_hotspot_samples(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_hotspot_repo(repo)
    receipt = evolution.build_receipt(repo)

    bad_count = copy.deepcopy(receipt)
    bad_count["metrics"]["hotspot_cc_mean"]["sample_count"] = 999
    with pytest.raises(score_evolution.EvolutionReceiptError, match="invalid samples"):
        _merge(bad_count, tmp_path)

    bad_unit = copy.deepcopy(receipt)
    bad_unit["metrics"]["unhealthy_hotspot_count"]["sample_unit"] = "commits"
    with pytest.raises(score_evolution.EvolutionReceiptError, match="invalid samples"):
        _merge(bad_unit, tmp_path)

    bad_value = copy.deepcopy(receipt)
    bad_value["metrics"]["unhealthy_hotspot_count"]["value"] = "seven"
    with pytest.raises(score_evolution.EvolutionReceiptError, match="must be numeric"):
        _merge(bad_value, tmp_path)
