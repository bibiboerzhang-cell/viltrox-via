from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domains.sync import cron
from app.services.scheduler import jobs_tasks


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/264_vkpi_composite_morning_sync_gate.sql"
DOWN = ROOT / "migrations/264_vkpi_composite_morning_sync_gate_down.sql"


def _shape(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_composite_morning_sync_fails_closed_without_deployment_gate(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    registry_keys: list[str] = []

    async def fake_run_job(name: str, payload: dict):
        calls.append((name, payload))
        return {"status": "queued"}

    monkeypatch.delenv(jobs_tasks.COMPOSITE_MORNING_SYNC_ENV, raising=False)
    monkeypatch.setattr(
        jobs_tasks,
        "_scheduler_task_enabled",
        lambda key: registry_keys.append(key) or True,
    )
    monkeypatch.setattr(cron, "run_job", fake_run_job)

    asyncio.run(jobs_tasks.job_vkpi_morning_sync())

    assert calls == []
    assert registry_keys == []


def test_composite_morning_sync_fails_closed_without_registry_gate(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def fake_run_job(name: str, payload: dict):
        calls.append((name, payload))
        return {"status": "queued"}

    monkeypatch.setenv(jobs_tasks.COMPOSITE_MORNING_SYNC_ENV, "1")
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda _key: False)
    monkeypatch.setattr(cron, "run_job", fake_run_job)

    asyncio.run(jobs_tasks.job_vkpi_morning_sync())

    assert calls == []


def test_composite_morning_sync_requires_both_explicit_gates(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    registry_keys: list[str] = []

    async def fake_run_job(name: str, payload: dict):
        calls.append((name, payload))
        return {"status": "queued"}

    def registry_gate(task_key: str) -> bool:
        registry_keys.append(task_key)
        return True

    monkeypatch.setenv(jobs_tasks.COMPOSITE_MORNING_SYNC_ENV, "true")
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", registry_gate)
    monkeypatch.setattr(cron, "run_job", fake_run_job)

    result = asyncio.run(jobs_tasks.job_vkpi_morning_sync())

    assert registry_keys == ["vkpi_morning_sync"]
    assert result == {"status": "queued"}
    assert calls == [
        (
            "morning_sync",
            {"limit": 100, "max_videos": 50, "period_days": 1},
        )
    ]


def test_composite_morning_sync_propagates_failure_to_scheduler(monkeypatch) -> None:
    async def fake_run_job(_name: str, _payload: dict):
        raise RuntimeError("enqueue failed")

    monkeypatch.setenv(jobs_tasks.COMPOSITE_MORNING_SYNC_ENV, "true")
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(cron, "run_job", fake_run_job)

    with pytest.raises(RuntimeError, match="enqueue failed"):
        asyncio.run(jobs_tasks.job_vkpi_morning_sync())


def test_channels_sync_returns_receipt_and_propagates_failure(monkeypatch) -> None:
    receipt = {"status": "queued", "channels_enqueued": 7}

    async def fake_success(name: str, payload: dict):
        assert (name, payload) == ("channels_sync", {})
        return receipt

    monkeypatch.setattr(cron, "run_job", fake_success)
    assert asyncio.run(jobs_tasks.job_vkpi_channels_sync()) is receipt

    async def fake_failure(_name: str, _payload: dict):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(cron, "run_job", fake_failure)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        asyncio.run(jobs_tasks.job_vkpi_channels_sync())


def test_migration_264_registers_only_a_default_off_high_risk_task() -> None:
    sql = _shape(UP)

    assert "insert into scheduler_tasks" in sql
    assert "'vkpi_morning_sync'" in sql
    assert "false, 1, 0" in sql
    assert "'marketing_ops', 'high'" in sql
    assert "on conflict (task_key) do nothing" in sql
    assert "insert into vkpi_business_audit_logs" not in sql
    assert "insert into vkpi_official_accounts" not in sql
    assert "begin;" not in sql
    assert "commit;" not in sql


def test_migration_264_down_removes_only_its_task_and_receipt() -> None:
    sql = _shape(DOWN)

    assert "delete from scheduler_tasks where task_key = 'vkpi_morning_sync'" in sql
    assert "where version_key = '264_vkpi_composite_morning_sync_gate.sql'" in sql
    assert "drop table" not in sql
    assert "delete from vkpi_official_accounts" not in sql


def test_dedicated_daily_sync_path_does_not_use_composite_scheduler_task() -> None:
    unit = (ROOT / "scripts/ops/systemd/vkpi-sync-daily.service").read_text(encoding="utf-8")
    script = (ROOT / "scripts/cron_daily_sync.py").read_text(encoding="utf-8")

    assert "scripts/cron_daily_sync.py" in unit
    assert "WorkingDirectory=/opt/viltrox-2.0/current" in unit
    assert "Environment=PYTHONPATH=/opt/viltrox-2.0/current/backend" in unit
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit
    assert (
        "env PYTHONDONTWRITEBYTECODE=1 "
        "/opt/viltrox-2.0/.venv/bin/python -B scripts/cron_daily_sync.py"
    ) in unit
    assert ".venv/bin/python scripts/cron_daily_sync.py" not in unit
    assert "daily_incremental_sync" in script
    assert "vkpi_morning_sync" not in unit
    assert "morning_sync" not in script


def test_daily_timer_installer_keeps_both_remote_python_paths_bytecode_free() -> None:
    installer = (ROOT / "scripts/ops/install_vkpi_daily_timers.sh").read_text(
        encoding="utf-8"
    )

    assert installer.count("WorkingDirectory=${REMOTE_ROOT}/current") == 2
    assert installer.count(
        "Environment=PYTHONPATH=${REMOTE_ROOT}/current/backend"
    ) == 2
    assert installer.count("Environment=PYTHONDONTWRITEBYTECODE=1") == 2
    assert installer.count(
        "env PYTHONDONTWRITEBYTECODE=1 ${REMOTE_ROOT}/.venv/bin/python -B "
        "scripts/cron_daily_sync.py"
    ) == 2
    assert (
        "${REMOTE_ROOT}/.venv/bin/python -B scripts/cron_daily_sync.py "
        "--official-max-posts 50 --skip-kol --include-qualified-kol "
        "--kol-tiers hot --kol-stale-days 1 --kol-max-posts 2"
    ) in installer
    assert "WorkingDirectory=${REMOTE_ROOT}\n" not in installer
    assert "Environment=PYTHONPATH=backend" not in installer
    assert " .venv/bin/python" not in installer
    assert installer.count("TimeoutStartSec=6h") == 2
    assert "TimeoutStartSec=2h" not in installer
    assert "Exit 75 raises OnFailure but is not auto-restarted" in installer
    assert "\nRestart=" not in installer


def test_qualified_timer_checks_primary_activity_at_0500_without_after_wait() -> None:
    installer = (ROOT / "scripts/ops/install_vkpi_daily_timers.sh").read_text(encoding="utf-8")
    qualified = installer.split("install_remote_qualified_kol_units()", 1)[1].split(
        "install_local_units()", 1
    )[0]

    assert "OnCalendar=*-*-* 05:00:00 UTC" in qualified
    assert "After=network-online.target viltrox-2.0-test.service\n" in qualified
    assert "After=network-online.target viltrox-2.0-test.service vkpi-sync-daily.service" not in qualified
    assert "systemctl is-active --quiet vkpi-sync-daily.service" in qualified
    assert "must observe a still-running primary and skip" in qualified
