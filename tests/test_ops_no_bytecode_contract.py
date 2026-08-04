from __future__ import annotations

import ast
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]

SHELL_CONTRACTS: dict[str, tuple[str, ...]] = {
    "scripts/ops/audit_prod_vkpi_state.sh": (
        'PYTHONDONTWRITEBYTECODE=1 \\',
        '"${remote_python}" -B -',
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


def test_prod_state_audit_loads_current_backend_with_root_shared_state() -> None:
    source = (ROOT / "scripts/ops/audit_prod_vkpi_state.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        'current_link="${remote_root_path}/current"',
        'releases_dir="${remote_root_path}/releases"',
        'current_path="$(readlink -f -- "${current_link}"',
        '"$(dirname -- "${current_path}")" != "${releases_dir}"',
        'cd "${current_path}"',
        'LOCAL_ENV_FILE="${remote_root_path}/.env"',
        'RUNTIME_ROOT="${remote_root_path}/runtime"',
        'PYTHONPATH="${current_path}/backend"',
        '"${remote_python}" -B -',
        '"${current_path}/BUILD_GIT_SHA"',
        '"${current_path}/.vkpi-release.json"',
        'manifest.get("git_sha") != build_sha',
    ):
        assert required in source
    assert "PYTHONPATH=backend '${PYTHON_BIN}'" not in source


def test_prod_state_audit_embedded_python_is_valid() -> None:
    source = (ROOT / "scripts/ops/audit_prod_vkpi_state.sh").read_text(
        encoding="utf-8"
    )
    marker = '"${remote_python}" -B - <<\'PY\'\n'
    embedded = source.split(marker, 1)[1].split("\nPY\n", 1)[0]

    ast.parse(embedded)


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
