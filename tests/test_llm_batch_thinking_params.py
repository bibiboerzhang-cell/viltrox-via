"""The Anthropic async transport is a non-configurable fail-closed facade."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.platform import llm_batch  # noqa: E402


def test_transport_cannot_be_enabled_by_environment(monkeypatch) -> None:
    for name in (
        "VKPI_ANTHROPIC_BATCH_ENABLED",
        "VKPI_CONTENT_FIT_BATCH_ENABLED",
        "ENABLE_ANTHROPIC_BATCH",
    ):
        monkeypatch.setenv(name, "1")

    llm_batch.register_consumer("test", lambda _results, _request_map: {})
    assert llm_batch.anthropic_batch_transport_enabled() is False
    assert llm_batch.submit_anthropic_batch(
        [{"custom_id": "one", "prompt": "must never leave this process"}],
        consumer="test",
        purpose="test",
        cost_scope="test",
    ) is None
    assert llm_batch.poll_pending_batches() == {
        "polled": 0,
        "collected": 0,
        "status": "disabled",
        "reason": "durable_idempotency_unavailable",
    }


def test_disabled_facade_contains_no_provider_or_database_call() -> None:
    source = Path(llm_batch.__file__).read_text(encoding="utf-8")

    assert "batches.create" not in source
    assert "get_claude_client" not in source
    assert "get_conn" not in source
    assert "os.environ" not in source


def test_enabled_scheduler_flag_still_stops_before_database_scan(monkeypatch) -> None:
    from app.db import connection
    from app.services.scheduler import jobs_tasks, jobs_tasks_batch

    database_calls: list[str] = []
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda _name: True)
    monkeypatch.setattr(
        connection,
        "get_conn",
        lambda: database_calls.append("database") or None,
    )

    asyncio.run(jobs_tasks_batch.job_vkpi_content_fit_batch_refresh())
    assert database_calls == []
