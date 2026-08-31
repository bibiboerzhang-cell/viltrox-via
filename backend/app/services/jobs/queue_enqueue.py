"""Redis enqueue transaction, stream binding, and failure containment.

All queue-owned symbols are resolved from the bound live facade so existing
module-level monkeypatch contracts remain effective without a reverse import.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from app.services.jobs.queue_runtime import queue_facade


def _qm():
    """Resolve the bound live facade to preserve its monkeypatch surface."""
    return queue_facade()


async def _find_enqueue_duplicate(
    queue: Any,
    job_type: str,
    submission_id: int,
    lock_key: str,
) -> Optional[str]:
    qm = _qm()
    if job_type == "audit_submission" and submission_id:
        async with qm.db_connection_scope():
            existing_task_id = await asyncio.to_thread(
                queue._find_active_submission_job,
                job_type,
                submission_id,
            )
        if existing_task_id:
            return existing_task_id
    if not lock_key:
        return None
    async with qm.db_connection_scope():
        return await asyncio.to_thread(queue._find_active_lock_job, lock_key)


async def _persist_enqueue_ledger(
    queue: Any,
    job: Dict[str, Any],
    lock_key: str,
) -> Optional[str]:
    qm = _qm()
    async with qm.db_connection_scope():
        try:
            await asyncio.to_thread(queue._insert_job_ledger, job)
        except Exception:
            if not lock_key:
                raise
            await asyncio.to_thread(queue._rollback_job_ledger_insert)
            existing_task_id = await asyncio.to_thread(
                queue._find_active_lock_job,
                lock_key,
            )
            if existing_task_id:
                return existing_task_id
            raise
    return None


def _enqueue_error_message(prefix: str, exc: BaseException) -> str:
    detail = str(exc).strip().replace("\n", " ")[:400]
    if detail:
        return f"{prefix}: {type(exc).__name__}: {detail}"
    return f"{prefix}: {type(exc).__name__}"


async def _terminalize_xadd_failure(
    queue: Any,
    task_id: str,
    stream_exc: BaseException,
) -> None:
    qm = _qm()
    error_message = _enqueue_error_message("redis xadd failed", stream_exc)
    try:
        async with qm.db_connection_scope():
            snapshot = await asyncio.to_thread(
                queue._update_job_ledger,
                task_id,
                qm.TaskStatus.FAILED.value,
                error_message=error_message,
                stage="enqueue_failed",
            )
    except Exception:
        qm.logger.exception(
            "redis xadd failed and ledger terminalization also failed | task_id=%s",
            task_id,
        )
        raise RuntimeError(
            "redis xadd failed and job ledger could not be terminalized"
        ) from stream_exc
    if not snapshot:
        qm.logger.error(
            "redis xadd failed but ledger disappeared | task_id=%s",
            task_id,
        )
        raise RuntimeError(
            "redis xadd failed and job ledger did not reach failed"
        ) from stream_exc
    if str(snapshot.get("status") or "").lower() != qm.TaskStatus.FAILED.value:
        qm.logger.error(
            "redis xadd failed but ledger did not reach failed | task_id=%s status=%s",
            task_id,
            snapshot.get("status"),
        )
        raise RuntimeError(
            "redis xadd failed and job ledger did not reach failed"
        ) from stream_exc


async def _append_enqueue_stream(
    queue: Any,
    job: Dict[str, Any],
    user_id: int,
) -> str:
    qm = _qm()
    try:
        stream_id = await queue._client.xadd(
            qm.REDIS_JOB_STREAM_KEY,
            {
                "task_id": job["task_id"],
                "job_type": job["job_type"],
                "submission_id": str(job["submission_id"]),
                "payload_json": json.dumps(job["payload"], ensure_ascii=False),
                "user_id": str(user_id),
                "created_at": qm._utcnow(),
            },
        )
    except Exception as stream_exc:
        # Keep the inserted ledger as audit evidence, but terminalize it so a
        # failed Redis append cannot retain the active dedupe lock.
        await _terminalize_xadd_failure(queue, job["task_id"], stream_exc)
        raise
    return str(stream_id)


def _stream_binding_is_invalid(
    snapshot: Optional[Dict[str, Any]],
    stream_id: str,
) -> bool:
    if not snapshot:
        return True
    if snapshot.get("_stream_bind_conflict"):
        return True
    return str(snapshot.get("stream_id") or "") != stream_id


async def _try_fail_unbound_stream(
    queue: Any,
    task_id: str,
    stream_id: str,
    error_message: str,
) -> Optional[Dict[str, Any]]:
    qm = _qm()
    try:
        async with qm.db_connection_scope():
            return await asyncio.to_thread(
                queue._fail_unbound_stream_job,
                task_id,
                expected_stream_id=stream_id,
                error_message=error_message,
            )
    except Exception:
        qm.logger.exception(
            "stream binding failed and ledger terminalization also failed | task_id=%s",
            task_id,
        )
        return None


def _stream_containment_state(
    snapshot: Optional[Dict[str, Any]],
) -> tuple[str, bool, bool]:
    if not snapshot:
        return "", False, False
    qm = _qm()
    failed_status = str(snapshot.get("status") or "").lower()
    containment_applied = bool(snapshot.get("_stream_bind_failed_applied"))
    ledger_failed = (
        containment_applied
        and failed_status == qm.TaskStatus.FAILED.value
        and str(snapshot.get("stage") or "") == "stream_bind_failed"
    )
    durable_stream_won = (
        not containment_applied and bool(snapshot.get("_durable_stream_won"))
    )
    return failed_status, ledger_failed, durable_stream_won


async def _delete_contained_stream(
    queue: Any,
    task_id: str,
    stream_id: str,
) -> None:
    qm = _qm()
    try:
        await queue._client.xdel(qm.REDIS_JOB_STREAM_KEY, stream_id)
    except Exception:
        qm.logger.exception(
            "stream binding failed and redis message deletion also failed | task_id=%s stream_id=%s",
            task_id,
            stream_id,
        )


async def _recover_enqueue_stream_binding(
    queue: Any,
    task_id: str,
    stream_id: str,
    bind_exc: BaseException,
) -> Dict[str, Any]:
    qm = _qm()
    error_message = _enqueue_error_message(
        "redis stream ledger binding failed",
        bind_exc,
    )
    failed_snapshot = await _try_fail_unbound_stream(
        queue,
        task_id,
        stream_id,
        error_message,
    )
    failed_status, ledger_failed, durable_stream_won = _stream_containment_state(
        failed_snapshot
    )
    if ledger_failed:
        await _delete_contained_stream(queue, task_id, stream_id)
    if durable_stream_won:
        qm.logger.warning(
            "producer stream bind lost to durable ledger state | task_id=%s stream_id=%s status=%s",
            task_id,
            stream_id,
            failed_status,
        )
        return failed_snapshot or {}
    if ledger_failed:
        raise RuntimeError(
            "redis stream ledger binding failed after xadd"
        ) from bind_exc
    raise RuntimeError(
        "redis stream ledger binding failed and containment is unverified"
    ) from bind_exc


async def _bind_enqueue_stream(
    queue: Any,
    task_id: str,
    stream_id: str,
) -> tuple[Dict[str, Any], bool]:
    qm = _qm()
    try:
        async with qm.db_connection_scope():
            snapshot = await asyncio.to_thread(
                queue._bind_job_stream,
                task_id,
                stream_id,
            )
        if _stream_binding_is_invalid(snapshot, stream_id):
            raise RuntimeError("job ledger stream binding was not durable")
        return snapshot or {}, False
    except Exception as bind_exc:
        recovered = await _recover_enqueue_stream_binding(
            queue,
            task_id,
            stream_id,
            bind_exc,
        )
        return recovered, True


async def _publish_queued_enqueue_event(
    queue: Any,
    task_id: str,
    stream_id: str,
    snapshot: Dict[str, Any],
    user_id: int,
) -> None:
    qm = _qm()
    if str(snapshot.get("status") or "").lower() != qm.TaskStatus.QUEUED.value:
        return
    event = {
        "event_type": qm.TaskStatus.QUEUED.value,
        "task_id": task_id,
        "status": qm.TaskStatus.QUEUED.value,
        "created_at": qm._utcnow(),
        "submission_id": snapshot.get("submission_id") or "",
        "retry_count": snapshot.get("retry_count") or "0",
        "stage": snapshot.get("stage") or "ingest",
    }
    try:
        await queue._publish_event(task_id, event, user_id=user_id)
    except Exception:
        qm.logger.warning(
            "queued event publish failed after durable enqueue | task_id=%s stream_id=%s",
            task_id,
            stream_id,
            exc_info=True,
        )


async def enqueue(
    queue: Any,
    job_type: str,
    payload: Any,
    submission_id: Optional[int] = None,
    *,
    priority: int | None = None,
    lock_key: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    qm = _qm()
    await queue._ensure_ready()
    payload_dict = qm._normalize_payload(payload)
    user_id = int(payload_dict.get("user_id") or 0)
    effective_submission_id = int(
        submission_id or payload_dict.get("submission_id") or 0
    )
    normalized_lock_key = str(lock_key or "").strip()
    existing_task_id = await _find_enqueue_duplicate(
        queue,
        job_type,
        effective_submission_id,
        normalized_lock_key,
    )
    if existing_task_id:
        return existing_task_id

    task_id = str(uuid.uuid4())
    job = {
        "task_id": task_id,
        "job_type": job_type,
        "submission_id": effective_submission_id,
        "payload": payload_dict,
        "priority": priority,
        "lock_key": normalized_lock_key,
        "timeout_seconds": timeout_seconds,
    }
    insert_winner = await _persist_enqueue_ledger(
        queue,
        job,
        normalized_lock_key,
    )
    if insert_winner:
        return insert_winner

    stream_id = await _append_enqueue_stream(queue, job, user_id)
    snapshot, recovered = await _bind_enqueue_stream(queue, task_id, stream_id)
    if recovered:
        return task_id
    await _publish_queued_enqueue_event(
        queue,
        task_id,
        stream_id,
        snapshot,
        user_id,
    )
    return task_id
