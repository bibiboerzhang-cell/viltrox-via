"""Fail-fast behavior for V-KPI daily KOL lightweight sync."""
from __future__ import annotations

import json
import asyncio
import concurrent.futures

import pytest

from app.services.vkpi import daily_sync


def _rows(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": idx,
            "platform": "youtube",
            "handle": f"creator-{idx}",
            "display_name": f"Creator {idx}",
        }
        for idx in range(1, count + 1)
    ]


def _install_harness(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    events: dict[str, list[dict[str, object]]] = {"start": [], "finish": [], "interrupt": []}
    monkeypatch.setattr(daily_sync, "_kol_light_rows", lambda **_: rows)
    monkeypatch.setattr(
        daily_sync,
        "_kol_source_counts",
        lambda **_: {
            "source_total": len(rows),
            "source_by_platform": {"youtube": len(rows)},
            "unsupported_total": 0,
            "unsupported_by_platform": {},
        },
    )
    monkeypatch.setattr(daily_sync, "start_sync_run", lambda **kwargs: events["start"].append(kwargs))
    monkeypatch.setattr(daily_sync, "finish_sync_run", lambda **kwargs: events["finish"].append(kwargs))
    monkeypatch.setattr(daily_sync, "record_sync_interrupt", lambda **kwargs: events["interrupt"].append(kwargs) or True)
    return events


def _synced_result() -> dict[str, object]:
    return {"sync_status": "synced", "provider_status": "synced"}


def test_kol_light_refresh_records_completed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _rows(3)
    events = _install_harness(monkeypatch, rows)
    monkeypatch.setattr(daily_sync.kol_pool, "enrich_item", lambda *_args, **_kwargs: _synced_result())

    result = daily_sync.run_kol_pool_light_refresh({"run_id": "unit-run-complete"})

    assert result["refreshed"] == 3
    assert result["errors"] == 0
    assert events["start"][0]["run_id"] == "unit-run-complete"
    assert events["finish"][0]["status"] == "completed"
    assert events["finish"][0]["last_success_index"] == 3
    assert not events["interrupt"]


def test_kol_light_refresh_fail_fast_on_connection_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class ClosedConnectionError(Exception):
        sqlstate = "08003"

    rows = _rows(3)
    events = _install_harness(monkeypatch, rows)

    def enrich(kol_pool_id: int, **_kwargs: object) -> dict[str, object]:
        if kol_pool_id == 2:
            raise ClosedConnectionError("the connection is closed")
        return _synced_result()

    monkeypatch.setattr(daily_sync.kol_pool, "enrich_item", enrich)

    with pytest.raises(daily_sync.SyncFailFast) as exc_info:
        daily_sync.run_kol_pool_light_refresh({"run_id": "unit-run-closed"})

    assert exc_info.value.exit_code == 75
    assert events["interrupt"][0]["run_id"] == "unit-run-closed"
    assert events["interrupt"][0]["interrupted_at_index"] == 2
    assert events["interrupt"][0]["interrupted_kol_pool_id"] == 2
    assert events["interrupt"][0]["last_success_index"] == 1
    assert events["interrupt"][0]["error_type"] == "db_connection_lost"
    assert events["interrupt"][0]["reason"] == "connection_closed"


def test_kol_light_refresh_fail_fast_on_admin_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class AdminShutdown(Exception):
        sqlstate = "57P01"

    rows = _rows(3)
    events = _install_harness(monkeypatch, rows)

    def enrich(kol_pool_id: int, **_kwargs: object) -> dict[str, object]:
        if kol_pool_id == 2:
            raise AdminShutdown("terminating connection due to administrator command")
        return _synced_result()

    monkeypatch.setattr(daily_sync.kol_pool, "enrich_item", enrich)

    with pytest.raises(daily_sync.SyncFailFast) as exc_info:
        daily_sync.run_kol_pool_light_refresh({"run_id": "unit-run-admin"})

    assert exc_info.value.exit_code == 75
    assert events["interrupt"][0]["interrupted_at_index"] == 2
    assert events["interrupt"][0]["interrupted_kol_pool_id"] == 2
    assert events["interrupt"][0]["last_success_index"] == 1
    assert events["interrupt"][0]["error_type"] == "db_connection_lost"
    assert events["interrupt"][0]["reason"] == "admin_shutdown"
    assert events["interrupt"][0]["error_class"] == "AdminShutdown"


def test_kol_light_refresh_provider_error_continues_and_records_error_context(monkeypatch: pytest.MonkeyPatch) -> None:
    assert daily_sync._classify_sync_error(asyncio.TimeoutError("api timed out"))[0] == "provider_timeout"
    assert daily_sync._classify_sync_error(concurrent.futures.TimeoutError("worker timed out"))[0] == "provider_timeout"

    rows = _rows(3)
    events = _install_harness(monkeypatch, rows)

    def enrich(kol_pool_id: int, **_kwargs: object) -> dict[str, object]:
        if kol_pool_id == 2:
            raise TimeoutError("provider timeout")
        return _synced_result()

    monkeypatch.setattr(daily_sync.kol_pool, "enrich_item", enrich)

    result = daily_sync.run_kol_pool_light_refresh({"run_id": "unit-run-provider"})

    assert result["refreshed"] == 2
    assert result["errors"] == 1
    assert result["error_sample"][0]["kol_pool_id"] == 2
    assert result["error_sample"][0]["error_class"] == "TimeoutError"
    assert result["error_sample"][0]["error_type"] == "provider_timeout"
    assert events["finish"][0]["status"] == "completed"
    assert events["finish"][0]["last_success_index"] == 3
    assert not events["interrupt"]


def test_kol_light_refresh_interrupt_record_failure_emits_stderr_json_and_exits_75(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ClosedConnectionError(Exception):
        sqlstate = "08006"

    rows = _rows(2)
    _install_harness(monkeypatch, rows)

    def enrich(kol_pool_id: int, **_kwargs: object) -> dict[str, object]:
        if kol_pool_id == 2:
            raise ClosedConnectionError("server closed the connection")
        return _synced_result()

    def fail_record(**_kwargs: object) -> bool:
        raise RuntimeError("interrupt table unavailable")

    monkeypatch.setattr(daily_sync.kol_pool, "enrich_item", enrich)
    monkeypatch.setattr(daily_sync, "record_sync_interrupt", fail_record)

    with pytest.raises(daily_sync.SyncFailFast) as exc_info:
        daily_sync.run_kol_pool_light_refresh({"run_id": "unit-run-record-fail"})

    assert exc_info.value.exit_code == 75
    stderr = capsys.readouterr().err.strip().splitlines()
    assert stderr
    event = json.loads(stderr[-1])
    assert event["event"] == "vkpi_sync_interrupt_record_failed"
    assert event["run_id"] == "unit-run-record-fail"
    assert event["interrupted_at_index"] == 2
    assert event["interrupted_kol_pool_id"] == 2
    assert event["error_type"] == "db_connection_lost"
    assert "interrupt table unavailable" in event["record_error"]


def test_sync_health_blocks_next_run_when_failure_rate_exceeds_threshold() -> None:
    health = daily_sync._sync_health_from_summary(
        {
            "official": {"requested": 18, "failed": 0},
            "kol_pool_light": {"requested": 10, "errors": 2},
        }
    )

    assert health["total_requested"] == 28
    assert health["total_errors"] == 2
    assert health["has_errors"] is True
    assert health["blocked_next_run"] is False

    blocked = daily_sync._sync_health_from_summary(
        {
            "official": {"requested": 0, "failed": 0},
            "kol_pool_light": {"requested": 10, "errors": 2},
        }
    )

    assert blocked["failure_rate"] == 0.2
    assert blocked["blocked_next_run"] is True
    assert blocked["block_reason"] == "failure_rate_threshold_exceeded"


def test_daily_sync_guard_blocks_after_unacked_failed_run(monkeypatch: pytest.MonkeyPatch) -> None:
    blocking = {
        "run_id": "daily-summary-bad",
        "stage": "daily_summary",
        "status": "failed",
        "reason": "failure_rate_threshold_exceeded",
        "health": {"blocked_next_run": True},
    }
    monkeypatch.setattr(daily_sync, "_blocking_sync_run", lambda _scope: blocking)

    with pytest.raises(daily_sync.SyncGuardBlocked) as exc_info:
        daily_sync.check_daily_sync_guard({})

    assert exc_info.value.exit_code == 76
    assert exc_info.value.blocking_run_id == "daily-summary-bad"
    assert exc_info.value.summary["ack_required"] is True


def test_daily_sync_guard_ignores_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_sync, "_blocking_sync_run", lambda _scope: {"run_id": "bad"})

    result = daily_sync.check_daily_sync_guard({"dry_run": True})

    assert result["allowed"] is True
    assert result["skipped"] is True


def test_record_daily_sync_summary_persists_failed_status_for_high_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(
        daily_sync,
        "_write_sync_run",
        lambda sql, params: writes.append({"sql": sql, "params": params}),
    )
    monkeypatch.setattr(daily_sync, "_upsert_sync_health_alert", lambda **_kwargs: None)

    health = daily_sync.record_daily_sync_summary(
        "unit-run",
        {
            "run_id": "unit-run",
            "started_at": "2026-05-22T00:00:00Z",
            "official": {"requested": 0, "failed": 0},
            "kol_pool_light": {"requested": 10, "errors": 2},
        },
    )

    assert health["blocked_next_run"] is True
    assert writes
    params = writes[0]["params"]
    assert params[0] == "unit-run_summary"
    assert params[5] == "failed"
    assert params[8] == "failure_rate_threshold_exceeded"
    assert params[9] == "other"


def test_daily_sync_dry_run_does_not_persist_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_sync, "record_daily_sync_summary", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run wrote summary")))

    result = daily_sync.run_daily_incremental({"dry_run": True, "skip_official": True, "skip_kol": True})

    assert result["dry_run"] is True
    assert result["health"]["total_errors"] == 0
    assert result["health"]["blocked_next_run"] is False


def test_daily_sync_skips_legacy_kol_refresh_without_explicit_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_sync, "run_kol_pool_light_refresh", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy kol refresh ran")))

    result = daily_sync.run_daily_incremental({"dry_run": True, "skip_official": True, "skip_kol": False})

    assert result["kol_pool_light"]["skipped"] is True
    assert result["kol_pool_light"]["reason"] == daily_sync.LEGACY_KOL_REFRESH_GUARD_REASON
    assert result["kol_pool_light"]["requires"] == "allow_legacy_kol_full_refresh"


def test_daily_sync_runs_legacy_kol_refresh_when_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        daily_sync,
        "run_kol_pool_light_refresh",
        lambda payload: {"requested": 1, "refreshed": 1, "errors": 0, "allow": payload.get("allow_legacy_kol_full_refresh")},
    )

    result = daily_sync.run_daily_incremental({
        "dry_run": True,
        "skip_official": True,
        "skip_kol": False,
        "allow_legacy_kol_full_refresh": True,
    })

    assert result["kol_pool_light"]["requested"] == 1
    assert result["kol_pool_light"]["allow"] is True
