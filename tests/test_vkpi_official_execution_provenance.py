from __future__ import annotations

import asyncio
import json

import pytest

from app.domains.channels import refill
from app.workers.tasks import vkpi as vkpi_tasks


EXPECTED_PROVENANCE = {
    "schema_version": "vkpi-sync-execution-provenance/v1",
    "task_id": "official-7",
    "orchestration_batch_id": "daily-20260831",
    "orchestration_lane": "official",
}


def test_execution_provenance_is_minimal_and_drops_untrusted_fields() -> None:
    assert refill._execution_provenance({
        "task_id": "official-7",
        "orchestration_batch_id": "daily-20260831",
        "orchestration_lane": "OFFICIAL",
        "schema_version": "attacker-controlled",
        "token": "must-not-persist",
        "staff": {"email": "must-not-persist@example.com"},
    }) == EXPECTED_PROVENANCE


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"task_id": "official-7", "orchestration_lane": "official"},
        {"orchestration_batch_id": "daily-20260831", "orchestration_lane": "official"},
        {
            "task_id": "official-7",
            "orchestration_batch_id": "daily-20260831",
            "orchestration_lane": "kol_pool_light",
        },
        {
            "task_id": "x" * 129,
            "orchestration_batch_id": "daily-20260831",
            "orchestration_lane": "official",
        },
    ],
)
def test_incomplete_or_invalid_execution_provenance_is_rejected(value) -> None:
    assert refill._execution_provenance(value) == {}


def test_snapshot_dispatch_uses_a_private_copy_and_trusted_receipt(monkeypatch) -> None:
    captured: dict = {}

    def fake_youtube(channel, **_kwargs):
        captured.update(channel)
        return {"sync_status": "synced"}

    monkeypatch.setattr(refill, "ensure_vkpi_channels_schema", lambda: None)
    monkeypatch.setattr(refill, "_sync_youtube", fake_youtube)
    original = {
        "id": 7,
        "platform": "youtube",
        "_execution_provenance": {"task_id": "forged"},
    }

    refill.sync_channel_snapshot(
        original,
        execution_provenance={
            **EXPECTED_PROVENANCE,
            "token": "must-not-persist",
        },
    )

    assert original["_execution_provenance"] == {"task_id": "forged"}
    assert captured["_execution_provenance"] == EXPECTED_PROVENANCE


class _Cursor:
    def fetchone(self):
        return None


class _Connection:
    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.commands.append((sql, tuple(params)))
        return _Cursor()

    def commit(self) -> None:
        return None


def test_snapshot_persists_only_worker_execution_receipt(monkeypatch) -> None:
    conn = _Connection()
    monkeypatch.setattr(refill, "get_conn", lambda: conn)
    monkeypatch.setattr(refill, "prewarm_official_media_cache", lambda *_args: {})
    monkeypatch.setattr(refill, "record_channel_post_metrics", lambda **_kwargs: {})
    monkeypatch.setattr(refill, "_clear_channel_read_cache", lambda: None)

    refill._write_snapshot(
        {
            "id": 7,
            "platform": "youtube",
            "staff_id": 1,
            "_execution_provenance": {
                **EXPECTED_PROVENANCE,
                "token": "must-not-persist",
            },
        },
        {},
        {
            "provider": "youtube_api",
            "execution_provenance": {
                "task_id": "spoofed-provider-task",
                "token": "provider-secret",
            },
        },
        staff=None,
    )

    insert_params = next(
        params for sql, params in conn.commands
        if "INSERT INTO vkpi_channel_metrics" in sql
    )
    persisted = json.loads(insert_params[14])
    assert persisted["execution_provenance"] == EXPECTED_PROVENANCE
    assert "token" not in json.dumps(persisted["execution_provenance"])


class _Queue:
    def __init__(self) -> None:
        self.status = "queued"

    async def get_status(self, _task_id):
        return {"status": self.status}

    async def set_status(self, _task_id, status, **_kwargs):
        self.status = status


def test_official_worker_forwards_exact_batch_task_identity(monkeypatch) -> None:
    captured: dict = {}

    def fake_sync(_channel, **kwargs):
        captured.update(kwargs["execution_provenance"])
        return {"sync_status": "synced", "message": "done"}

    monkeypatch.setattr(vkpi_tasks.task_enqueue, "task_cancel_requested", lambda _task_id: False)
    monkeypatch.setattr(vkpi_tasks.task_enqueue, "upsert_task_item", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(vkpi_tasks.channels, "get_channel", lambda *_args, **_kwargs: {"channel": {"id": 7}})
    monkeypatch.setattr(vkpi_tasks.channel_refill, "sync_channel_snapshot", fake_sync)

    asyncio.run(vkpi_tasks.process_vkpi_official_channel_sync_job(
        _Queue(),
        {
            "task_id": "official-7",
            "payload": {
                "channel_id": 7,
                "orchestration_batch_id": "daily-20260831",
                "orchestration_lane": "official",
            },
        },
    ))

    assert captured == {
        "task_id": "official-7",
        "orchestration_batch_id": "daily-20260831",
        "orchestration_lane": "official",
    }
