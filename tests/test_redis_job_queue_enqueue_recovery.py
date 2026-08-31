"""Redis enqueue durability, stream binding, and producer/worker race regressions."""
from __future__ import annotations

import asyncio

import pytest

from app.services.jobs import queue as queue_mod
from tests.redis_job_queue_test_support import ledger_conn, queue, seed  # noqa: F401


def test_enqueue_xadd_failure_terminalizes_ledger_and_releases_lock(
    queue,
    ledger_conn,
):
    original_xadd = queue._client.xadd

    async def fail_xadd(stream, fields):
        raise ConnectionError("redis unavailable")

    queue._client.xadd = fail_xadd
    with pytest.raises(ConnectionError, match="redis unavailable"):
        asyncio.run(
            queue.enqueue(
                "vkpi_test",
                {"user_id": 1},
                lock_key="daily:official:youtube",
            )
        )

    failed = ledger_conn.execute(
        "SELECT task_id, status, stage, error_message FROM job_execution_ledger WHERE lock_key=?",
        ("daily:official:youtube",),
    ).fetchone()
    assert failed["status"] == "failed"
    assert failed["stage"] == "enqueue_failed"
    assert "redis xadd failed: ConnectionError: redis unavailable" in failed["error_message"]
    assert queue._client.published == []

    queue._client.xadd = original_xadd
    retry_task_id = asyncio.run(
        queue.enqueue(
            "vkpi_test",
            {"user_id": 1},
            lock_key="daily:official:youtube",
        )
    )

    assert retry_task_id != failed["task_id"]
    retried = ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id=?",
        (retry_task_id,),
    ).fetchone()
    assert retried["status"] == "queued"


def test_enqueue_publish_failure_still_returns_bound_executable_task(
    queue,
    ledger_conn,
):
    original_publish = queue._client.publish

    async def fail_publish(channel, message):
        raise ConnectionError("pubsub unavailable")

    queue._client.publish = fail_publish
    task_id = asyncio.run(
        queue.enqueue(
            "vkpi_test",
            {"user_id": 1},
            lock_key="daily:official:instagram",
        )
    )

    row = ledger_conn.execute(
        "SELECT status, stream_id FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["stream_id"] == "1-0"

    queue._client.publish = original_publish
    stream, fields = queue._client.xadds[0]
    queue._client.xreadgroup_result = [(stream, [("1-0", fields)])]
    raw_job = asyncio.run(queue.pop_job("worker-after-publish-failure", timeout=1))
    assert raw_job and raw_job["task_id"] == task_id
    assert ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()["status"] == "processing"


def test_enqueue_stream_binding_does_not_revert_worker_processing(
    queue,
    ledger_conn,
):
    original_xadd = queue._client.xadd

    async def worker_claims_before_producer_binds(stream, fields):
        stream_id = await original_xadd(stream, fields)
        queue._update_job_ledger(
            str(fields["task_id"]),
            "processing",
            stream_id=stream_id,
            consumer_name="fast-worker",
            stage="processing",
        )
        return stream_id

    queue._client.xadd = worker_claims_before_producer_binds
    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}))

    row = ledger_conn.execute(
        "SELECT status, stream_id, consumer_name, started_at FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "processing"
    assert row["stream_id"] == "1-0"
    assert row["consumer_name"] == "fast-worker"
    assert row["started_at"]
    assert queue._client.published == []


def test_enqueue_stream_binding_failure_is_contained_and_fails_closed(
    queue,
    ledger_conn,
):
    def fail_binding(task_id, stream_id):
        raise RuntimeError("ledger bind unavailable")

    queue._bind_job_stream = fail_binding

    with pytest.raises(RuntimeError, match="ledger binding failed"):
        asyncio.run(
            queue.enqueue(
                "vkpi_test",
                {"user_id": 1},
                lock_key="daily:official:tiktok",
            )
        )

    row = ledger_conn.execute(
        "SELECT status, stage, error_message FROM job_execution_ledger WHERE lock_key=?",
        ("daily:official:tiktok",),
    ).fetchone()
    assert row["status"] == "failed"
    assert row["stage"] == "stream_bind_failed"
    assert "ledger bind unavailable" in row["error_message"]
    assert queue._client.deleted == ["1-0"]
    assert queue._client.published == []


def test_committed_queued_stream_survives_lost_bind_response(
    queue,
    ledger_conn,
):
    durable_bind = queue._bind_job_stream

    def bind_then_lose_response(task_id, stream_id):
        snapshot = durable_bind(task_id, stream_id)
        assert snapshot and snapshot["stream_id"] == stream_id
        raise ConnectionError("bind commit response lost")

    queue._bind_job_stream = bind_then_lose_response

    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}))

    row = ledger_conn.execute(
        "SELECT status, stream_id FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["stream_id"] == "1-0"
    assert queue._client.deleted == []


def test_stream_delete_cannot_mask_failed_ledger_terminalization(
    queue,
    ledger_conn,
):
    def fail_binding(task_id, stream_id):
        raise RuntimeError("ledger bind unavailable")

    def fail_terminalization(task_id, **extra):
        raise RuntimeError("ledger terminalization unavailable")

    queue._bind_job_stream = fail_binding
    queue._fail_unbound_stream_job = fail_terminalization

    with pytest.raises(RuntimeError, match="containment is unverified"):
        asyncio.run(
            queue.enqueue(
                "vkpi_test",
                {"user_id": 1},
                lock_key="daily:official:facebook",
            )
        )

    row = ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE lock_key=?",
        ("daily:official:facebook",),
    ).fetchone()
    assert row["status"] == "queued"
    assert queue._client.deleted == []


def test_live_claim_bind_failure_returns_durable_enqueue_and_preserves_stream(
    queue,
    ledger_conn,
):
    original_xadd = queue._client.xadd

    async def fast_worker_claim(stream, fields):
        stream_id = await original_xadd(stream, fields)
        task_id = str(fields["task_id"])
        queue._update_job_ledger(
            task_id,
            "processing",
            stream_id=stream_id,
            consumer_name="fast-worker",
            stage="processing",
        )
        ledger_conn.execute(
            """
            INSERT INTO vkpi_provider_execution_claims (task_id, state, lease_expires_at)
            VALUES (?, 'active', ?)
            """,
            (task_id, "2999-01-01T00:00:00Z"),
        )
        ledger_conn.commit()
        return stream_id

    def fail_binding(task_id, stream_id):
        raise RuntimeError("producer bind failed after fast claim")

    queue._client.xadd = fast_worker_claim
    queue._bind_job_stream = fail_binding

    task_id = asyncio.run(
        queue.enqueue(
            "vkpi_test",
            {"user_id": 1},
            lock_key="daily:official:reddit",
        )
    )

    row = ledger_conn.execute(
        "SELECT task_id, status, stream_id FROM job_execution_ledger WHERE lock_key=?",
        ("daily:official:reddit",),
    ).fetchone()
    assert task_id == row["task_id"]
    assert row["status"] == "processing"
    assert row["stream_id"] == "1-0"
    assert queue._find_active_lock_job("daily:official:reddit") == row["task_id"]
    assert queue._client.deleted == []
    assert queue._client.xadds and queue._client.xadds[0][0] == queue_mod.REDIS_JOB_STREAM_KEY


def test_completed_worker_wins_producer_bind_error_for_same_stream(
    queue,
    ledger_conn,
):
    original_xadd = queue._client.xadd

    async def fast_worker_completes(stream, fields):
        stream_id = await original_xadd(stream, fields)
        task_id = str(fields["task_id"])
        queue._update_job_ledger(
            task_id,
            "processing",
            stream_id=stream_id,
            consumer_name="fast-worker",
            stage="processing",
        )
        queue._update_job_ledger(task_id, "done", stage="done")
        return stream_id

    def fail_binding(task_id, stream_id):
        raise RuntimeError("producer bind response lost after worker completion")

    queue._client.xadd = fast_worker_completes
    queue._bind_job_stream = fail_binding

    task_id = asyncio.run(queue.enqueue("vkpi_test", {"user_id": 1}))

    row = ledger_conn.execute(
        "SELECT status, stream_id FROM job_execution_ledger WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "done"
    assert row["stream_id"] == "1-0"
    assert queue._client.deleted == []


def test_durable_worker_stream_prevents_producer_failed_containment(
    queue,
    ledger_conn,
):
    seed(
        ledger_conn,
        "provider-owned",
        status="processing",
        stream_id="12-0",
        started_at=queue_mod._utcnow(),
    )
    snapshot = queue._fail_unbound_stream_job(
        "provider-owned",
        expected_stream_id="12-0",
        error_message="producer bind failed",
    )

    assert snapshot and snapshot["status"] == "processing"
    assert snapshot["_stream_bind_failed_applied"] is False
    assert snapshot["_durable_stream_won"] is True
    assert ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id='provider-owned'"
    ).fetchone()["status"] == "processing"


def test_stream_bind_containment_reports_its_own_cas_win(queue, ledger_conn):
    seed(ledger_conn, "producer-contained", status="queued")

    snapshot = queue._fail_unbound_stream_job(
        "producer-contained",
        expected_stream_id="15-0",
        error_message="producer bind failed",
    )

    assert snapshot and snapshot["status"] == "failed"
    assert snapshot["stage"] == "stream_bind_failed"
    assert snapshot["_stream_bind_failed_applied"] is True
    assert "_durable_stream_won" not in snapshot


def test_provider_dispatch_authorization_requires_processing_bound_stream(
    queue,
    ledger_conn,
):
    seed(
        ledger_conn,
        "dispatch-ready",
        status="processing",
        stream_id="13-0",
        started_at=queue_mod._utcnow(),
    )
    seed(ledger_conn, "dispatch-failed", status="failed", stream_id="14-0")
    seed(
        ledger_conn,
        "dispatch-retry",
        status="retrying",
        stream_id="15-0",
        started_at="2000-01-01T00:00:00Z",
    )

    ready = asyncio.run(
        queue.authorize_provider_dispatch("dispatch-ready", "13-0")
    )
    retried = asyncio.run(
        queue.authorize_provider_dispatch("dispatch-retry", "15-0")
    )
    terminal = asyncio.run(
        queue.authorize_provider_dispatch("dispatch-failed", "14-0")
    )

    assert ready["authorized"] is True
    assert ready["status"] == "processing"
    assert retried["authorized"] is True
    assert retried["status"] == "processing"
    assert retried["started_at"] != "2000-01-01T00:00:00Z"
    assert terminal["authorized"] is False
    assert terminal["status"] == "failed"
