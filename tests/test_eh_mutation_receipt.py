"""Mutation receipt channel: runner helpers + fail-closed validation/merge."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_mutation as mutation
from scripts import vkpi_engineering_health_score_mutation as score_mutation


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(
        encoding="utf-8"
    )
)
NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)
KOL_PREFIXES = CONTRACT["code_evidence_methodology"]["core_mutation_score"][
    "core_scope_groups"
]["kol_search"]


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _git_state(head: str = "a" * 40) -> dict[str, object]:
    return {
        "branch": "codex/fixture",
        "head": head,
        "clean_worktree": True,
        "tracked_change_count": 0,
        "untracked_change_count": 0,
        "status_sha256": "1" * 64,
        "git_binary": "/usr/bin/git",
        "git_binary_sha256": "3" * 64,
    }


def _evidence(head: str = "a" * 40) -> dict[str, object]:
    return {
        "schema_version": "vkpi_engineering_health_evidence_v1",
        "candidate": {
            "repo": str(ROOT),
            "head": head,
            "branch": "codex/fixture",
            "clean_worktree": True,
            "status_sha256": "1" * 64,
            "source_content_sha256": "2" * 64,
            "source_file_count": 3,
            "source_and_status_stable": True,
        },
        "metrics": {"code": {}},
    }


def _per_file_rows(count: int) -> list[dict[str, object]]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "path": f"{KOL_PREFIXES[0]}module_{index:03d}.py",
                "killed": 6,
                "timeout": 1,
                "survived": 2,
                "no_tests": 1,
                "suspicious": 1,
                "skipped": 0,
                "segfault": 0,
                "caught_by_type_check": 0,
                "matched_tests": [f"tests/test_module_{index:03d}.py"],
            }
        )
    return rows


def _receipt(*, mode: str = "scored", file_count: int = 30) -> dict[str, object]:
    per_file = _per_file_rows(file_count)
    targets = [row["path"] for row in per_file]
    totals = {
        field: sum(int(row[field]) for row in per_file)
        for field in mutation.COUNT_FIELDS
    }
    killed_pool = totals["killed"] + totals["timeout"]
    denominator = killed_pool + totals["survived"]  # 合同公式:no_tests 不入分母
    identity = {"file_count": 3, "content_sha256": "2" * 64}
    return {
        "schema_version": mutation.SCHEMA_VERSION,
        "methodology_id": mutation.METHODOLOGY_ID,
        "generated_at": "2026-08-30T11:00:00Z",
        "passed": True,
        "candidate": {
            "repo": str(ROOT),
            "source_content_sha256": "2" * 64,
            "source_file_count": 3,
            "source_start": identity,
            "source_end": dict(identity),
            "git_start": _git_state(),
            "git_end": _git_state(),
        },
        "run": {
            "command": list(mutation.CANONICAL_RUN_COMMAND),
            "command_sha256": mutation.coverage_receipt.command_sha256(
                mutation.CANONICAL_RUN_COMMAND
            ),
            "mutmut_version": mutation.MUTMUT_PIN,
            "setup_cfg_sha256": "4" * 64,
            "pytest_ini_sha256": "5" * 64,
            "exit_code": 0,
            "started_at": "2026-08-30T10:00:00Z",
            "finished_at": "2026-08-30T11:00:00Z",
            "fresh_workspace_nonce": "3e5b7a80-0000-4000-8000-000000000000",
            "artifacts_existed_before": False,
            "stdout_sha256": "6" * 64,
            "stderr_sha256": "7" * 64,
            "db_isolation": {
                "mode": "hermetic",
                "environment_inherited": False,
                "database": None,
            },
        },
        "scope": {
            "mode": mode,
            "groups": ["kol_search"],
            "group_prefixes": {"kol_search": list(KOL_PREFIXES)},
            "eligible_file_count": 310,
            "target_file_count": len(targets),
            "target_files": targets,
            "target_files_sha256": mutation._lines_sha256(targets),  # noqa: SLF001
        },
        "results": {
            "per_file": per_file,
            "totals": totals,
            "scored_mutants": denominator,
            "core_mutation_score": killed_pool / denominator,
        },
        "artifacts": {
            "mutant_statuses": {
                "path": "runtime/engineering-health/mutation/mutant-statuses.json",
                "sha256": "8" * 64,
                "byte_count": 42,
            }
        },
    }


def _validate(receipt: dict[str, object], evidence: dict[str, object] | None = None):
    return score_mutation.validate_mutation_receipt(
        CONTRACT, evidence or _evidence(), receipt, now=NOW
    )


# --------------------------------------------------------------------------
# validator: acceptance
# --------------------------------------------------------------------------


def test_valid_scored_receipt_is_accepted() -> None:
    observed = _validate(_receipt())
    assert observed["mode"] == "scored"
    assert observed["scope_partial"] is True
    assert observed["target_file_count"] == 30
    assert observed["scored_mutants"] == 30 * 9  # 合同分母:7杀+2存,无 no_tests
    assert observed["core_mutation_score"] == pytest.approx(7 / 9)


def test_merge_attaches_observed_metric_with_required_fields() -> None:
    evidence = _evidence()
    score_mutation.merge_mutation_receipt(
        CONTRACT, evidence, ROOT / "receipt.json", _receipt(), now=NOW
    )
    entry = evidence["metrics"]["code"]["core_mutation_score"]
    assert entry["status"] == "observed"
    assert entry["value"] == pytest.approx(7 / 9)
    assert entry["source"].startswith("receipt://")
    assert entry["observed_at"] == "2026-08-30T11:00:00Z"
    assert entry["sample_count"] == 270  # 合同分母
    assert entry["details"]["scope_groups"] == ["kol_search"]
    assert entry["details"]["scope_partial"] is True


def test_merge_downgrades_smoke_receipt() -> None:
    evidence = _evidence()
    score_mutation.merge_mutation_receipt(
        CONTRACT,
        evidence,
        ROOT / "receipt.json",
        _receipt(mode="smoke", file_count=5),
        now=NOW,
    )
    entry = evidence["metrics"]["code"]["core_mutation_score"]
    assert entry["status"] == "missing_or_insufficient"
    assert entry["reason"] == score_mutation.SMOKE_REASON
    assert entry["value"] == pytest.approx(7 / 9)


def test_merge_downgrades_below_file_floor() -> None:
    evidence = _evidence()
    score_mutation.merge_mutation_receipt(
        CONTRACT,
        evidence,
        ROOT / "receipt.json",
        _receipt(mode="scored", file_count=29),
        now=NOW,
    )
    entry = evidence["metrics"]["code"]["core_mutation_score"]
    assert entry["status"] == "missing_or_insufficient"
    assert entry["reason"] == score_mutation.FILE_FLOOR_REASON


# --------------------------------------------------------------------------
# validator: fail-closed rejections
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(schema_version="other_v1"),
        lambda r: r.update(methodology_id="other"),
        lambda r: r.update(passed=False),
        lambda r: r["run"].update(exit_code=1),
        lambda r: r["run"].update(mutmut_version="2.5.1"),
        lambda r: r["run"].update(command=["mutmut", "run"]),
        lambda r: r["run"].update(command_sha256="0" * 64),
        lambda r: r["run"].update(artifacts_existed_before=True),
        lambda r: r["run"].update(finished_at="2026-08-28T11:00:00Z"),
        lambda r: r["run"].update(finished_at="2026-08-30T13:00:00Z"),
        lambda r: r["run"]["db_isolation"].update(environment_inherited=True),
        lambda r: r["run"]["db_isolation"].update(mode="prod"),
        lambda r: r["candidate"].update(source_content_sha256="9" * 64),
        lambda r: r["candidate"]["git_end"].update(head="b" * 40),
        lambda r: r["candidate"]["git_start"].update(head="b" * 40),
        lambda r: r["candidate"].update(source_end={"content_sha256": "x"}),
        lambda r: r["scope"].update(mode="nightly"),
        lambda r: r["scope"].update(groups=["kol_search", "kol_search"]),
        lambda r: r["scope"].update(groups=["unknown_group"]),
        lambda r: r["scope"]["group_prefixes"].update(kol_search=["backend/app/"]),
        lambda r: r["scope"].update(target_files_sha256="0" * 64),
        lambda r: r["scope"].update(eligible_file_count=1),
        lambda r: r["results"]["totals"].update(killed=1),
        lambda r: r["results"].update(scored_mutants=1),
        lambda r: r["results"].update(core_mutation_score=0.99),
        lambda r: r["results"]["per_file"][0].update(killed=-1),
        lambda r: r["results"]["per_file"][0].update(path="backend/app/other.py"),
    ],
)
def test_tampered_receipts_are_rejected(mutate) -> None:
    receipt = _receipt()
    mutate(receipt)
    with pytest.raises(score_mutation.MutationReceiptError):
        _validate(receipt)


def test_stale_generated_at_mismatch_rejected() -> None:
    receipt = _receipt()
    receipt["generated_at"] = "2026-08-30T10:30:00Z"
    with pytest.raises(score_mutation.MutationReceiptError):
        _validate(receipt)


def test_unstable_evidence_rejected() -> None:
    evidence = _evidence()
    evidence["candidate"]["source_and_status_stable"] = False
    with pytest.raises(score_mutation.MutationReceiptError):
        _validate(_receipt(), evidence)


def test_head_mismatch_with_evidence_rejected() -> None:
    with pytest.raises(score_mutation.MutationReceiptError):
        _validate(_receipt(), _evidence(head="b" * 40))


def test_zero_denominator_rejected() -> None:
    receipt = _receipt()
    for row in receipt["results"]["per_file"]:
        row.update(killed=0, timeout=0, survived=0, no_tests=0, suspicious=1)
    totals = {
        field: sum(int(row[field]) for row in receipt["results"]["per_file"])
        for field in mutation.COUNT_FIELDS
    }
    receipt["results"].update(totals=totals, scored_mutants=0, core_mutation_score=0.0)
    with pytest.raises(score_mutation.MutationReceiptError):
        _validate(receipt)


def test_contract_without_scope_groups_rejected() -> None:
    contract = copy.deepcopy(CONTRACT)
    del contract["code_evidence_methodology"]["core_mutation_score"]
    with pytest.raises(score_mutation.MutationReceiptError):
        score_mutation.validate_mutation_receipt(
            contract, _evidence(), _receipt(), now=NOW
        )


# --------------------------------------------------------------------------
# runner helpers (no subprocess, no mutmut)
# --------------------------------------------------------------------------


def test_score_from_totals_contract_pooling() -> None:
    totals = {
        "killed": 6,
        "timeout": 2,
        "survived": 1,
        "no_tests": 1,
        "suspicious": 5,
        "skipped": 5,
        "segfault": 5,
        "caught_by_type_check": 5,
    }
    # 合同 core-mutation-v1:killed/(killed+survived),timeout 计 killed;
    # no_tests/suspicious/skipped 全部不入公式(旧断言曾把 no_tests 计入分母,偏离合同已废)。
    score, denominator = mutation.score_from_totals(totals)
    assert denominator == 9
    assert abs(score - (8 / 9)) < 1e-9

def test_counts_from_meta_maps_mutmut_exit_codes(tmp_path: Path) -> None:
    meta = tmp_path / "module.py.meta"
    meta.write_text(
        json.dumps(
            {
                "exit_code_by_key": {
                    "app.mod.x_f__mutmut_1": 1,
                    "app.mod.x_f__mutmut_2": 0,
                    "app.mod.x_f__mutmut_3": 36,
                    "app.mod.x_f__mutmut_4": 33,
                    "app.mod.x_f__mutmut_5": 34,
                    "app.mod.x_f__mutmut_6": 99,
                }
            }
        ),
        encoding="utf-8",
    )
    counts, statuses = mutation.counts_from_meta(meta)
    assert counts == {
        "killed": 1,
        "timeout": 1,
        "survived": 1,
        "no_tests": 1,
        "suspicious": 1,
        "skipped": 1,
        "segfault": 0,
        "caught_by_type_check": 0,
    }
    assert statuses["app.mod.x_f__mutmut_6"] == "suspicious"


@pytest.mark.parametrize("exit_code", [None, 2])
def test_counts_from_meta_fails_closed_on_unfinished(tmp_path: Path, exit_code) -> None:
    meta = tmp_path / "module.py.meta"
    meta.write_text(
        json.dumps({"exit_code_by_key": {"app.mod.x_f__mutmut_1": exit_code}}),
        encoding="utf-8",
    )
    with pytest.raises(mutation.MutationRunError):
        mutation.counts_from_meta(meta)


def test_isolated_dsn_guard() -> None:
    assert mutation.validate_isolated_dsn(mutation.ISOLATED_PG_DEFAULT_DSN) == {
        "host": "127.0.0.1",
        "port": 54333,
        "database": "vkpi_closeout_test",
    }
    bad = [
        "postgresql://postgres@db.prod.internal:5432/vkpi",
        "postgresql://postgres@127.0.0.1:5432/vkpi_closeout_test",
        "postgresql://postgres:secret@127.0.0.1:54333/vkpi_closeout_test",
        "postgresql://postgres@127.0.0.1:54333/vkpi_production",
        "mysql://postgres@127.0.0.1:54333/vkpi_closeout_test",
    ]
    for dsn in bad:
        with pytest.raises(mutation.MutationRunError):
            mutation.validate_isolated_dsn(dsn)


def test_build_env_is_scrubbed_and_isolated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-should-never-leak/db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-leak")
    env, isolation = mutation.build_env(
        ROOT, tmp_path, db_mode="hermetic", pg_dsn=mutation.ISOLATED_PG_DEFAULT_DSN
    )
    assert "DATABASE_URL" not in env
    assert "LOCAL_DATABASE_URL" not in env
    assert "OPENAI_API_KEY" not in env
    assert "VKPI_PYTEST_ALLOW_LIVE_SERVICES" not in env
    assert env["VKPI_SKIP_DOTENV"] == "1"
    assert isolation == {
        "mode": "hermetic",
        "environment_inherited": False,
        "database": None,
    }
    env_pg, isolation_pg = mutation.build_env(
        ROOT, tmp_path, db_mode="isolated-pg", pg_dsn=mutation.ISOLATED_PG_DEFAULT_DSN
    )
    assert env_pg["DATABASE_URL"] == mutation.ISOLATED_PG_DEFAULT_DSN
    assert env_pg["VKPI_PYTEST_ALLOW_LIVE_SERVICES"] == "1"
    assert isolation_pg["database"] == {
        "host": "127.0.0.1",
        "port": 54333,
        "database": "vkpi_closeout_test",
    }
    with pytest.raises(mutation.MutationRunError):
        mutation.build_env(
            ROOT,
            tmp_path,
            db_mode="isolated-pg",
            pg_dsn="postgresql://postgres@10.0.0.5:54333/vkpi_closeout_test",
        )


def test_to_workspace_relative_guards_scope() -> None:
    assert (
        mutation.to_workspace_relative("backend/app/domains/kol/x.py")
        == "app/domains/kol/x.py"
    )
    with pytest.raises(mutation.MutationRunError):
        mutation.to_workspace_relative("scripts/x.py")


def test_render_setup_cfg_is_deterministic_and_parseable() -> None:
    from configparser import ConfigParser

    text = mutation.render_setup_cfg(
        ["app/domains/kol/b.py", "app/domains/kol/a.py"],
        ["tests/test_b.py", "tests/test_a.py"],
    )
    assert text == mutation.render_setup_cfg(
        ["app/domains/kol/a.py", "app/domains/kol/b.py"],
        ["tests/test_a.py", "tests/test_b.py"],
    )
    parser = ConfigParser()
    parser.read_string(text)
    only = [x for x in parser.get("mutmut", "only_mutate").split("\n") if x]
    assert only == ["app/domains/kol/a.py", "app/domains/kol/b.py"]
    assert parser.get("mutmut", "source_paths") == "app"
    assert parser.get("mutmut", "use_git_change_detection") == "false"


def test_match_tests_prefers_stem_named_files(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_kol_suite.py").write_text(
        "from app.domains.kol import widget\n", encoding="utf-8"
    )
    (tests_dir / "test_widget.py").write_text(
        "import app.domains.kol.widget\n", encoding="utf-8"
    )
    (tests_dir / "test_unrelated.py").write_text("x = 1\n", encoding="utf-8")
    matches = mutation.match_tests(tmp_path, ["backend/app/domains/kol/widget.py"])
    assert matches["backend/app/domains/kol/widget.py"] == (
        "tests/test_widget.py",
        "tests/test_kol_suite.py",
    )


def test_select_targets_smoke_and_scored_bounds() -> None:
    files = [
        type(
            "F",
            (),
            {"relative_path": f"backend/app/domains/kol/m{i:02d}.py", "physical_lines": i},
        )()
        for i in range(1, 8)
    ]
    matches = {f.relative_path: ("tests/test_x.py",) for f in files}
    matches[files[0].relative_path] = ()
    smoke = mutation.select_targets(
        files, matches, mode="smoke", max_files=40, explicit=[]
    )
    assert len(smoke) == mutation.SMOKE_FILE_COUNT
    assert files[0].relative_path not in smoke
    with pytest.raises(mutation.MutationRunError):
        mutation.select_targets(files, matches, mode="scored", max_files=5, explicit=[])
    with pytest.raises(mutation.MutationRunError):
        mutation.select_targets(
            files, matches, mode="smoke", max_files=40, explicit=["backend/app/nope.py"]
        )

def test_score_formula_matches_frozen_contract_verbatim():
    """合同 core-mutation-v1:killed/(killed+survived),timeout 计 killed,
    suspicious/skipped/no_tests 全部不入公式。脚本自创口径=偏离冻结合同。"""
    import json
    from pathlib import Path
    contract = json.loads(Path("docs/vkpi/engineering-health-score-contract-v1.json").read_text())
    definition = contract["code_evidence_methodology"]["core_mutation_score"]["definition"]
    assert "killed / (killed + survived)" in definition
    totals = {"killed": 6169, "timeout": 6, "survived": 6061, "no_tests": 11432,
              "suspicious": 2, "skipped": 0, "segfault": 0, "caught_by_type_check": 0}
    score, denom = mutation.score_from_totals(totals)
    assert denom == 6169 + 6 + 6061
    assert abs(score - (6175 / 12236)) < 1e-9
