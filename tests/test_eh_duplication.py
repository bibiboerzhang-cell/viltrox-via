"""Contract v1.1 code-dimension duplication counter + composition-root fan-out.

Covers the two W5 collector cuts that share one ALGORITHM_VERSION bump
(``-fanout-roots1-dup1``):

* ``scripts/vkpi_engineering_health_duplication.py`` — the frozen
  ``stdlib-tokenize-w-shingling-50-v1`` methodology: exact (type, string)
  token identity, 50-token windows at stride 1 per file, corpus-wide
  (file, offset) duplicate positions, covered-token rate, fail-closed
  tokenize errors, and byte-determinism;
* the ``internal_fan_out_max`` composition-root exemption: the formal value
  excludes the contract-registered roots (``app.main``) while the exempted
  modules and their fan-out stay reported in the evidence details.

Fixture arithmetic used throughout: one ``x{i} = {i}`` line contributes
exactly 3 kept tokens (NAME, OP, NUMBER — NEWLINE is excluded), so the
20-line block is a 60-token file with 11 windows.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from scripts import vkpi_engineering_health_collect as collector
from scripts import vkpi_engineering_health_duplication as duplication


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(encoding="utf-8")
)
OBSERVED_AT = "2026-08-30T12:00:00Z"
BLOCK = "".join(f"x{index} = {index}\n" for index in range(20)).encode()  # 60 kept tokens


def _write(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)


def _source(path: str, content: bytes) -> SimpleNamespace:
    return SimpleNamespace(relative_path=path, content=content)


# ---------------------------------------------------------------------------
# methodology: stdlib-tokenize-w-shingling-50-v1
# ---------------------------------------------------------------------------


def test_kept_token_stream_matches_the_contract_exclusion_list() -> None:
    assert duplication.WINDOW_TOKENS == 50
    assert duplication.STRIDE == 1
    assert duplication.EXCLUDED_TOKEN_TYPE_NAMES == (
        "COMMENT", "DEDENT", "ENCODING", "ENDMARKER", "INDENT", "NEWLINE", "NL",
    )
    assert len(duplication.kept_token_pairs(BLOCK)) == 60
    assert duplication.kept_token_pairs(b"x0 = 0  # note\n\n")[:3] == duplication.kept_token_pairs(b"x0 = 0\n")[:3]


def test_identical_twin_files_are_fully_duplicated_with_exact_counters() -> None:
    result = duplication.measure_duplication([("a.py", BLOCK), ("b.py", BLOCK)])

    assert result.total_kept_tokens == 120
    assert result.shingle_count == 22          # 11 windows per file
    assert result.duplicated_shingle_count == 22
    assert result.distinct_duplicated_shingle_count == 11
    assert result.duplicated_token_count == 120
    assert result.duplication_rate == 1.0
    assert result.files_with_shingles == 2
    assert result.complete is True


def test_type1_identity_is_exact_one_renamed_identifier_breaks_the_clone() -> None:
    # Same 20-line block but the final identifier is renamed: kept tokens differ
    # only at index 57, so windows 0..7 (covering tokens 0..56) still match.
    renamed = "".join(f"x{index} = {index}\n" for index in range(19)).encode() + b"q19 = 19\n"

    result = duplication.measure_duplication([("a.py", BLOCK), ("b.py", renamed)])

    assert [(row.path, row.duplicated_token_count) for row in result.files] == [
        ("a.py", 57),
        ("b.py", 57),
    ]
    assert result.duplication_rate == round(114 / 120, 8)


def test_small_files_produce_no_shingles_but_stay_in_the_denominator() -> None:
    result = duplication.measure_duplication(
        [("a.py", BLOCK), ("b.py", BLOCK), ("c.py", b"a = 1\n")]
    )

    small = next(row for row in result.files if row.path == "c.py")
    assert small.kept_token_count == 3
    assert small.shingle_count == 0
    assert small.duplicated_token_count == 0
    assert result.files_with_shingles == 2
    assert result.total_kept_tokens == 123
    assert result.duplicated_token_count == 120
    assert result.duplication_rate == round(120 / 123, 8)


def test_same_file_repeats_count_and_overlapping_windows_cover_every_token() -> None:
    # BLOCK twice in one file: 120 kept tokens with period 60, so window o
    # matches window o+60 for o in 0..10 — 22 duplicated positions, 11 distinct.
    result = duplication.measure_duplication([("a.py", BLOCK + BLOCK)])

    assert result.shingle_count == 71
    assert result.duplicated_shingle_count == 22
    assert result.distinct_duplicated_shingle_count == 11
    assert result.duplicated_token_count == 120
    assert result.duplication_rate == 1.0


def test_comments_blank_lines_and_layout_do_not_break_type1_identity() -> None:
    commented = "".join(f"x{index} = {index}  # note\n\n" for index in range(20)).encode()

    result = duplication.measure_duplication([("a.py", BLOCK), ("b.py", commented)])

    assert result.duplication_rate == 1.0


def test_windows_never_cross_a_file_boundary() -> None:
    # Two halves of BLOCK in separate files: every 50-token window would need
    # tokens from both halves, so no window exists and nothing is duplicated.
    half_a = "".join(f"x{index} = {index}\n" for index in range(10)).encode()
    half_b = "".join(f"x{index} = {index}\n" for index in range(10, 20)).encode()

    result = duplication.measure_duplication(
        [("whole.py", BLOCK), ("half_a.py", half_a), ("half_b.py", half_b)]
    )

    assert result.shingle_count == 11  # only whole.py reaches 50 kept tokens
    assert result.duplicated_shingle_count == 0
    assert result.duplication_rate == 0.0


def test_tokenize_failure_fails_the_corpus_closed() -> None:
    result = duplication.measure_duplication([("a.py", BLOCK), ("bad.py", b"t = (1, 2\n")])

    assert result.complete is False
    assert result.duplication_rate is None
    assert [(item.path, item.error_type) for item in result.failures] == [("bad.py", "TokenError")]


def test_measurement_and_observation_are_deterministic() -> None:
    sources = [("a.py", BLOCK), ("b.py", BLOCK), ("c.py", b"a = 1\n")]
    files = [_source(path, content) for path, content in sources]

    first = duplication.measure_duplication(sources)
    second = duplication.measure_duplication(list(reversed(sources)))
    observation_one = duplication.observation(files, snapshot_complete=True, top_limit=25)
    observation_two = duplication.observation(files, snapshot_complete=True, top_limit=25)

    assert first == second  # input order cannot change the corpus verdict
    assert json.dumps(observation_one, sort_keys=True) == json.dumps(observation_two, sort_keys=True)


def test_observation_withholds_the_rate_unless_the_corpus_is_trustworthy() -> None:
    files = [_source("a.py", BLOCK), _source("b.py", BLOCK)]

    observed = duplication.observation(files, snapshot_complete=True, top_limit=25)
    incomplete_snapshot = duplication.observation(files, snapshot_complete=False, top_limit=25)
    empty = duplication.observation([], snapshot_complete=True, top_limit=25)
    zero_tokens = duplication.observation(  # rate 0/0 is undefined, never "observed"
        [_source("empty.py", b"")], snapshot_complete=True, top_limit=25
    )

    assert observed["status"] == "observed"
    assert observed["methodology_id"] == "stdlib-tokenize-w-shingling-50-v1"
    assert observed["duplication_rate"] == 1.0
    assert observed["top_files"] == [
        {"path": "a.py", "kept_token_count": 60, "duplicated_token_count": 60, "duplicated_token_ratio": 1.0},
        {"path": "b.py", "kept_token_count": 60, "duplicated_token_count": 60, "duplicated_token_ratio": 1.0},
    ]
    assert incomplete_snapshot["status"] == "unknown"
    assert incomplete_snapshot["duplication_rate"] is None
    assert incomplete_snapshot["duplicated_token_count"] == 120  # honest trace stays
    assert empty["status"] == "unknown"
    assert zero_tokens["status"] == "unknown"
    assert zero_tokens["duplication_rate"] is None


def test_contract_registered_methodology_matches_the_counter_constants() -> None:
    registered = CONTRACT["code_evidence_methodology"]["duplication_rate"]

    assert registered["methodology_id"] == duplication.METHODOLOGY_ID
    for name in duplication.EXCLUDED_TOKEN_TYPE_NAMES:
        assert name in registered["token_stream"]


# ---------------------------------------------------------------------------
# collector wiring: evidence metric + ALGORITHM_VERSION
# ---------------------------------------------------------------------------


def test_evidence_reports_the_duplication_rate_with_details_and_version_marker(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/llm_a.py", BLOCK)
    _write(tmp_path / "backend/app/llm_b.py", BLOCK)
    _git_repo(tmp_path)

    first = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)
    second = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)
    metric = first["metrics"]["code"]["duplication_rate"]

    assert collector._json_bytes(first) == collector._json_bytes(second)
    assert first["collector"]["algorithm_version"].endswith("-fanout-roots1-dup1")
    assert metric["status"] == "observed"
    assert metric["value"] == 1.0
    assert metric["sample_count"] == 120
    assert metric["source"] == "collector://vkpi-engineering-health/v1/python-tokenize-duplication"
    assert metric["details"]["methodology_id"] == "stdlib-tokenize-w-shingling-50-v1"
    assert metric["details"]["duplicated_token_count"] == 120
    assert [row["path"] for row in metric["details"]["top_files"]] == [
        "backend/app/llm_a.py",
        "backend/app/llm_b.py",
    ]


def test_untokenizable_production_file_keeps_the_metric_unknown(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/llm_a.py", BLOCK)
    _write(tmp_path / "backend/app/broken.py", b"t = (1, 2\n")
    _git_repo(tmp_path)

    evidence = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)
    observation = evidence["collector"]["observations"]["python_duplication"]
    metric = evidence["metrics"]["code"]["duplication_rate"]

    assert evidence["collector"]["status"] == "partial"
    assert observation["status"] == "unknown"
    assert observation["tokenize_errors"] == [
        {"path": "backend/app/broken.py", "error_type": "TokenError", "line": 1}
    ]
    assert metric["status"] == "unknown"
    assert metric["value"] is None


# ---------------------------------------------------------------------------
# composition-root fan-out exemption (contract v1.1 internal_fan_out_max)
# ---------------------------------------------------------------------------


def _fan_out_fixture(tmp_path: Path) -> None:
    _write(tmp_path / "backend/app/__init__.py", "")
    _write(tmp_path / "backend/app/main.py", "import app.alpha\nimport app.beta\nimport app.gamma\n")
    _write(tmp_path / "backend/app/alpha.py", "import app.beta\nimport app.gamma\n")
    _write(tmp_path / "backend/app/beta.py", "")
    _write(tmp_path / "backend/app/gamma.py", "")


def test_internal_fan_out_max_exempts_registered_composition_roots(tmp_path: Path) -> None:
    # app.main fans out to 4 unique targets (app + three imports); app.alpha to 3.
    _fan_out_fixture(tmp_path)

    graph = collector.collect_observations(tmp_path, observed_at=OBSERVED_AT)["backend_import_graph"]

    assert graph["status"] == "observed"
    assert graph["internal_fan_out_max"] == 3
    assert graph["max_fan_out_modules"] == ["app.alpha"]
    assert graph["composition_roots"] == ["app.main"]
    assert graph["composition_root_exemptions"] == [{"module": "app.main", "fan_out": 4}]
    assert graph["raw_fan_out_max"] == 4


def test_fan_out_evidence_keeps_the_exempted_values_in_the_details(tmp_path: Path) -> None:
    _fan_out_fixture(tmp_path)
    _git_repo(tmp_path)

    metric = collector.build_evidence(tmp_path, CONTRACT, observed_at=OBSERVED_AT)[
        "metrics"
    ]["architecture"]["internal_fan_out_max"]

    assert metric["status"] == "observed"
    assert metric["value"] == 3
    assert metric["details"] == {
        "max_fan_out_modules": ["app.alpha"],
        "composition_roots": ["app.main"],
        "composition_root_exemptions": [{"module": "app.main", "fan_out": 4}],
        "raw_fan_out_max": 4,
    }


def test_composition_roots_match_the_contract_registration_exactly() -> None:
    registered = CONTRACT["static_evidence_methodology"]["internal_fan_out_max"]["composition_roots"]

    assert registered == list(collector.COMPOSITION_ROOTS)
