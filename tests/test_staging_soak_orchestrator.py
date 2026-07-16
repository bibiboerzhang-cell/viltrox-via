from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.ops import staging_soak_orchestrator as soak


HEAD = "fe3871c438ff9de8884589e052d8dd8b82b94b83"
MIGRATION = "260_vkpi_dealer_map_management.sql"
UNITS = ("viltrox-2.0-test.service", "vkpi-worker-interactive.service")


def _config(*, duration: float = 2.0, interval: float = 1.0) -> soak.SoakConfig:
    return soak.SoakConfig(
        health_url="http://127.0.0.1:8001/health",
        env_file="/private/staging.env",
        root="/opt/viltrox-2.0",
        expected_head=HEAD,
        expected_migration=MIGRATION,
        expected_apify_workers=2,
        expected_redis_workers=1,
        systemd_units=UNITS,
        diagnostic_target_seconds=duration,
        interval_seconds=interval,
        max_sample_latency_ms=5000.0,
        max_p95_latency_ms=1000.0,
        max_queue_age_seconds=3600.0,
        max_lock_waits=0,
        min_disk_free_bytes=1024**3,
    )


def _sample(*, cursor_index: int = 1, restart: bool = False) -> dict[str, Any]:
    apify_nonces = ["1" * 64, "2" * 64]
    redis_worker_nonces = ["3" * 64]
    pid_offset = 100 if restart else 0
    return {
        "environment": {"content_sha256": "0" * 64, "bytes": 1024},
        "health": {
            "status": "ok",
            "server_git_sha": HEAD,
            "client_git_sha": HEAD,
            "sha_aligned": True,
            "migration_max": MIGRATION,
            "latency_ms": 12.5,
            "apify": {
                "online_count": 2,
                "unique_names": True,
                "unique_pids": True,
                "all_worker_sha_aligned": True,
                "all_heartbeats_fresh": True,
                "lane_coverage": ["batch", "interactive"],
                "boot_nonce_sha256_set": apify_nonces,
            },
            "redis_worker": {
                "online_count": 1,
                "all_worker_sha_aligned": True,
                "all_heartbeats_fresh": True,
                "all_redis_ready": True,
                "boot_nonce_sha256_set": redis_worker_nonces,
            },
        },
        "database": {
            "database_name_sha256": "5" * 64,
            "migration_max": MIGRATION,
            "transaction_mode": "read_only",
            "connections": {"active": 4, "idle": 2},
            "idle_in_transaction_over_30s": 0,
            "lock_waits": 0,
            "queue": {
                "present": True,
                "queued": 0,
                "oldest_queued_age_seconds": None,
                "failed_or_triage": 0,
            },
        },
        "redis": {
            "aof_enabled": True,
            "aof_last_write_status": "ok",
            "rdb_last_bgsave_status": "ok",
            "uptime_in_seconds": 3600 + cursor_index,
            "run_id_sha256": "4" * 64,
            "used_memory": 1024,
        },
        "disk": {"total_bytes": 10 * 1024**3, "used_bytes": 1024, "free_bytes": 9 * 1024**3},
        "systemd": {
            "units": [
                {
                    "unit": unit,
                    "load_state": "loaded",
                    "active_state": "active",
                    "sub_state": "running",
                    "n_restarts": 0,
                    "main_pid": index + 10 + pid_offset,
                }
                for index, unit in enumerate(UNITS)
            ]
        },
        "journal": {
            "entries": 1,
            "priority_error_entries": 0,
            "priority_warning_entries": 0,
            "cursor_sha256": f"{cursor_index:064x}",
            "raw_messages_persisted": False,
        },
    }


class FakeClock:
    def __init__(self) -> None:
        self.value = 1_700_000_000.0

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeCollector:
    def __init__(self, *, restart_at: int | None = None) -> None:
        self.calls = 0
        self.restart_at = restart_at

    def collect(self, *, journal_cursor: str | None) -> tuple[dict[str, Any], str]:
        self.calls += 1
        if self.calls > 1:
            assert journal_cursor == f"cursor-{self.calls - 1}"
        return (
            _sample(cursor_index=self.calls, restart=self.calls == self.restart_at),
            f"cursor-{self.calls}",
        )


def _record(seq: int, captured_at: str, *, violations: list[str] | None = None) -> dict[str, Any]:
    return {
        "seq": seq,
        "captured_at": captured_at,
        "sample": _sample(cursor_index=seq),
        "violations": violations or [],
        "record_sha256": f"{seq:064x}",
    }


def test_short_run_is_resumable_but_never_release_eligible(tmp_path: Path) -> None:
    config = _config()
    config.validate(allow_short_diagnostic=True)
    clock = FakeClock()
    collector = FakeCollector()

    with soak.StateStore(tmp_path / "state", config) as store:
        result = soak.SoakOrchestrator(
            store=store,
            collector=collector,
            now=clock.now,
            sleep=clock.sleep,
        ).run()
        records = store.records()
        receipt = soak.build_receipt(records, config)

    assert result["status"] == "pending"
    assert result["diagnostic_target_reached"] is True
    assert result["natural_elapsed_seconds"] == 2.0
    assert "natural_72h_not_elapsed" in result["blockers"]
    assert receipt["release_gate_eligible"] is False
    assert receipt["sample_chain"]["hash_chained"] is True
    assert receipt["sample_chain"]["raw_journal_messages_included"] is False
    assert collector.calls == 3


def test_formal_72h_aggregate_pass_requires_natural_time_and_minute_coverage() -> None:
    config = _config(duration=soak.FORMAL_DURATION_SECONDS, interval=60.0)
    config.validate(allow_short_diagnostic=False)
    records = [
        _record(
            index + 1,
            soak._utc(1_700_000_000.0 + index * 60.0),
        )
        for index in range(soak.FORMAL_DURATION_SECONDS // 60 + 1)
    ]

    result = soak.aggregate(records, config)

    assert result["status"] == "passed"
    assert result["formal_gate_pass"] is True
    assert result["natural_elapsed_seconds"] == soak.FORMAL_DURATION_SECONDS
    assert result["sample_coverage_ratio"] == 1.0
    assert result["blockers"] == []


def test_formal_elapsed_without_coverage_remains_pending() -> None:
    config = _config(duration=soak.FORMAL_DURATION_SECONDS, interval=60.0)
    records = [
        _record(1, soak._utc(1_700_000_000.0)),
        _record(2, soak._utc(1_700_000_000.0 + soak.FORMAL_DURATION_SECONDS)),
    ]

    result = soak.aggregate(records, config)

    assert result["status"] == "pending"
    assert "sample_coverage_below_98_percent" in result["blockers"]
    assert "sample_gap_exceeded" in result["blockers"]


def test_formal_elapsed_with_any_sample_violation_is_failed() -> None:
    config = _config(duration=soak.FORMAL_DURATION_SECONDS, interval=60.0)
    records = [
        _record(
            index + 1,
            soak._utc(1_700_000_000.0 + index * 60.0),
            violations=["redis_persistence"] if index == 1 else [],
        )
        for index in range(soak.FORMAL_DURATION_SECONDS // 60 + 1)
    ]

    result = soak.aggregate(records, config)

    assert result["status"] == "failed"
    assert result["formal_gate_pass"] is False
    assert result["would_fail_if_finalized"] is True
    assert result["violation_codes"] == ["redis_persistence"]


def test_restart_is_a_terminal_soak_violation(tmp_path: Path) -> None:
    config = _config()
    clock = FakeClock()

    with soak.StateStore(tmp_path / "state", config) as store:
        orchestrator = soak.SoakOrchestrator(
            store=store,
            collector=FakeCollector(restart_at=2),
            now=clock.now,
            sleep=clock.sleep,
        )
        orchestrator.take_sample()
        clock.sleep(1.0)
        second = orchestrator.take_sample()
        result = soak.aggregate(store.records(), config)

    assert "systemd_restart_or_pid_change" in second["violations"]
    assert result["status"] == "pending"
    assert "sample_violations_present" in result["blockers"]
    assert result["would_fail_if_finalized"] is True


def test_hash_chain_tampering_and_resume_config_drift_fail_closed(tmp_path: Path) -> None:
    config = _config()
    state_dir = tmp_path / "state"
    clock = FakeClock()
    with soak.StateStore(state_dir, config) as store:
        soak.SoakOrchestrator(
            store=store,
            collector=FakeCollector(),
            now=clock.now,
            sleep=clock.sleep,
        ).take_sample()

    chain = state_dir / "samples.jsonl"
    row = json.loads(chain.read_text(encoding="utf-8"))
    row["captured_at"] = soak._utc(clock.now() + 1)
    chain.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with soak.StateStore(state_dir, config) as store:
        with pytest.raises(soak.SoakError, match="hash chain"):
            store.records()

    other = tmp_path / "other"
    with soak.StateStore(other, config):
        pass
    changed = soak.SoakConfig(**{**config.__dict__, "expected_apify_workers": 3})
    with pytest.raises(soak.SoakError, match="resume configuration"):
        with soak.StateStore(other, changed):
            pass


def test_short_duration_without_explicit_diagnostic_flag_is_rejected() -> None:
    with pytest.raises(soak.SoakError, match="allow-short-diagnostic"):
        _config().validate(allow_short_diagnostic=False)


def test_collector_failure_is_redacted_and_preserves_empty_resume_cursor(tmp_path: Path) -> None:
    class FailingCollector:
        def collect(self, *, journal_cursor: str | None) -> tuple[dict[str, Any], str]:
            assert journal_cursor is None
            raise soak.CollectionError("database_read_failed")

    config = _config()
    with soak.StateStore(tmp_path / "state", config) as store:
        record = soak.SoakOrchestrator(store=store, collector=FailingCollector()).take_sample()

    assert record["sample"] == {}
    assert record["collection_error"] == "database_read_failed"
    assert record["journal_cursor"] == ""
    assert record["violations"] == ["collection:database_read_failed"]
    assert "password" not in json.dumps(record).lower()
