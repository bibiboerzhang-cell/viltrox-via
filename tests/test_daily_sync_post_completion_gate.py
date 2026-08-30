from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATUS_SCRIPT = ROOT / "scripts" / "ops" / "check_vkpi_daily_sync_status.sh"
QUEUED_FIXTURE = ROOT / "tests" / "fixtures" / "vkpi_sync_daily_systemd_success_enqueue_only.jsonl"
POST_SYNC_SCRIPTS = (
    "scripts/ops/deploy_after_vkpi_sync.sh",
    "scripts/ops/backfill_vkpi_dimensions11_after_sync.sh",
    "scripts/ops/backfill_vkpi_competitor_relations_after_sync.sh",
    "scripts/ops/migrate_vkpi_media_cache_to_r2_after_sync.sh",
    "scripts/ops/scan_vkpi_brand_signals_after_sync.sh",
)


def _embedded_status_python() -> str:
    source = STATUS_SCRIPT.read_text(encoding="utf-8")
    return source.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_systemd_success_with_queued_receipt_is_not_provider_completion(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
case "$*" in
  *ActiveState*) echo inactive ;;
  *SubState*) echo dead ;;
  *ExecMainStatus*) echo 0 ;;
  *Result*) echo success ;;
  *) echo inactive ;;
esac
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "SYNC_SERVICE": "vkpi-sync-daily.service",
        "LOG_PATH": str(QUEUED_FIXTURE),
    }

    result = subprocess.run(
        [sys.executable, "-B", "-c", _embedded_status_python()],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"] == "success"
    assert payload["exec_main_status"] == "0"
    assert payload["orchestration_status"] == "queued"
    assert payload["completion_scope"] == "enqueue_only"
    assert payload["provider_completion"] == "unknown"
    assert payload["post_sync_safe"] is False
    assert payload["finished_summary"]["official_enqueued"] == 18


@pytest.mark.parametrize("relative", POST_SYNC_SCRIPTS)
def test_post_sync_writers_refuse_enqueue_only_receipt(
    relative: str,
    tmp_path: Path,
) -> None:
    status_script = _write_executable(
        tmp_path / "status.sh",
        """#!/bin/sh
echo '{"service_state":"inactive","active_state":"inactive","sub_state":"dead","result":"success","exec_main_status":"0","failure_tail":[],"orchestration_status":"queued","completion_scope":"enqueue_only","provider_completion":"unknown","post_sync_safe":false}'
""",
    )
    marker = tmp_path / "dangerous-command-ran"
    dangerous = _write_executable(
        tmp_path / "dangerous.sh",
        f"""#!/bin/sh
touch {marker!s}
exit 0
""",
    )
    env = {
        **os.environ,
        "SYNC_STATUS_SCRIPT": str(status_script),
        "RUN_REMOTE": "0",
        "RUN_BENCHMARK": "0",
        "DIMENSIONS_SCRIPT": str(dangerous),
        "COMPETITOR_SCRIPT": str(dangerous),
        "BRAND_SIGNAL_SCRIPT": str(dangerous),
        "R2_READINESS_SCRIPT": str(dangerous),
        "MIGRATION_SCRIPT": str(dangerous),
        "DEPLOY_SCRIPT": str(dangerous),
        "POST_SYNC_AUDIT_SCRIPT": str(dangerous),
        "PLAN_STATUS_SCRIPT": str(dangerous),
        "BENCHMARK_SCRIPT": str(dangerous),
    }

    result = subprocess.run(
        ["bash", str(ROOT / relative)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "no verified provider completion" in result.stderr
    assert not marker.exists(), f"{relative} ran a downstream mutation"
