from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]

SHELL_CONTRACTS: dict[str, tuple[str, ...]] = {
    "scripts/ops/audit_prod_vkpi_state.sh": (
        "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend '${PYTHON_BIN}' -B -",
    ),
    "scripts/ops/benchmark_vkpi_perf.sh": (
        'PYTHONDONTWRITEBYTECODE=1 RUNS="${RUNS}" PYTHONPATH=backend "${PYTHON_BIN}" -B -',
    ),
    "scripts/ops/run_prod_vkpi_job.sh": (
        "PYTHONDONTWRITEBYTECODE=1 python3 -B -m json.tool",
        "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend '${PYTHON_BIN}' -B -",
    ),
    "scripts/ops/seal_official_baseline_deltas.sh": (
        "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend '${PYTHON_BIN}' -B -",
    ),
    "scripts/ops/backfill_vkpi_dimensions11_after_sync.sh": (
        'STATUS_JSON="${status_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B -',
        'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B scripts/vkpi_dimensions11_dry_run.py',
        'PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"',
    ),
    "scripts/ops/scan_vkpi_brand_signals_after_sync.sh": (
        'STATUS_JSON="${status_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B -',
        'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B "${args[@]}"',
        'PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"',
    ),
    "scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh": (
        'STATUS_JSON="${status_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B -',
        'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B "${args[@]}"',
        'PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"',
    ),
    "scripts/ops/migrate_vkpi_media_cache_to_r2_after_sync.sh": (
        'STATUS_JSON="${status_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B -',
        'r2_json="$(PYTHONDONTWRITEBYTECODE=1 "${R2_READINESS_SCRIPT}"',
        'R2_JSON="${r2_json}" PYTHONDONTWRITEBYTECODE=1 python3 -B -',
        'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend "${PYTHON_BIN}" -B "${args[@]}"',
        'PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B "${args[@]}"',
    ),
    "scripts/ops/backup_prod_vkpi.sh": (
        'PYTHONDONTWRITEBYTECODE=1 "${LOCAL_PYTHON_BIN}" -B -',
        'PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" -B -',
        'PYTHONDONTWRITEBYTECODE=1 "${remote_python}" -B -',
    ),
    "scripts/ops/check_vkpi_daily_sync_status.sh": (
        "PYTHONDONTWRITEBYTECODE=1 python3 -B -",
    ),
}

PYTHON_ENTRYPOINTS = (
    "scripts/ops/audit_vkpi_post_sync_state.py",
    "scripts/ops/check_vkpi_r2_readiness.py",
    "scripts/ops/legacy_to_atomic_preflight_transport.py",
)


@pytest.mark.parametrize(("relative", "required"), SHELL_CONTRACTS.items())
def test_ops_shell_python_invocations_disable_bytecode(
    relative: str,
    required: tuple[str, ...],
) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")
    for snippet in required:
        assert snippet in source, f"{relative} is missing bytecode guard: {snippet}"


@pytest.mark.parametrize("relative", SHELL_CONTRACTS)
def test_ops_shell_scripts_remain_valid_bash(relative: str) -> None:
    result = subprocess.run(
        ["bash", "-n", str(ROOT / relative)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("relative", PYTHON_ENTRYPOINTS)
def test_ops_python_entrypoints_disable_bytecode_before_local_imports(
    relative: str,
) -> None:
    source = (ROOT / relative).read_text(encoding="utf-8")
    guard_at = source.index("dont_write_bytecode = True")
    if "from stdout_utils import" in source:
        assert guard_at < source.index("from stdout_utils import")
    else:
        assert guard_at < source.index("import argparse")


def test_python_generated_remote_commands_have_both_guards() -> None:
    audit = (ROOT / "scripts/ops/audit_vkpi_post_sync_state.py").read_text(
        encoding="utf-8"
    )
    readiness = (ROOT / "scripts/ops/check_vkpi_r2_readiness.py").read_text(
        encoding="utf-8"
    )
    transport = (
        ROOT / "scripts/ops/legacy_to_atomic_preflight_transport.py"
    ).read_text(encoding="utf-8")

    assert audit.count(
        "env PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -"
    ) == 2
    assert "env PYTHONDONTWRITEBYTECODE=1 python3 -B -" in readiness
    assert '"PYTHONDONTWRITEBYTECODE=1"' in transport
    assert 'python_path,\n        "-B",\n        "-",' in transport
