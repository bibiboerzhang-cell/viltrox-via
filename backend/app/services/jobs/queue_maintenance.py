"""services/jobs/queue_maintenance.py — RedisJobQueue 台账维护协作对象。

class-LOC 棘轮拆分:行为逐字搬运自 queue.py::RedisJobQueue(台账读写、超时清扫、
队列汇总),门面方法留在 RedisJobQueue 上做薄委托。

所有可 monkeypatch 的模块级符号(get_conn、logger、常量、工具函数)一律经
``_qm()`` 惰性解析到 queue 模块,保持 tests 对 queue 模块的补丁面不变
(例:``patch.object(queue_mod, "get_conn", ...)`` 仍然生效)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _qm():
    """Resolve the queue module lazily so monkeypatches on it stay effective."""
    from app.services.jobs import queue as queue_module

    return queue_module


def status_row_to_dict(row: Any) -> Dict[str, Any]:
    qm = _qm()
    if row is None:
        return {}
    data = dict(row)
    extra = qm._decode_json(data.get("extra_json"), {})
    payload = qm._decode_json(data.get("payload_json"), {})
    if payload:
        data["payload_json"] = json.dumps(payload, ensure_ascii=False)
    for key, value in extra.items():
        data.setdefault(key, value)
    return {key: ("" if value is None else str(value) if isinstance(value, (int, float)) and key.endswith("_id") else value) for key, value in data.items()}


def find_active_lock_job(lock_key: str) -> Optional[str]:
    qm = _qm()
    lock_key = str(lock_key or "").strip()
    if not lock_key:
        return None
    conn = qm.get_conn()
    row = conn.execute(
        """
        SELECT task_id
        FROM job_execution_ledger
        WHERE lock_key=?
          AND status IN ('queued', 'retrying', 'processing', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (lock_key,),
    ).fetchone()
    return str(row["task_id"]) if row else None


def find_active_submission_job(job_type: str, submission_id: int) -> Optional[str]:
    qm = _qm()
    if not submission_id:
        return None
    conn = qm.get_conn()
    row = conn.execute(
        """
        SELECT task_id
        FROM job_execution_ledger
        WHERE job_type=?
          AND submission_id=?
          AND status IN ('queued', 'retrying', 'processing', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_type, submission_id),
    ).fetchone()
    return str(row["task_id"]) if row else None


def insert_job_ledger(job: Dict[str, Any]) -> None:
    qm = _qm()
    conn = qm.get_conn()
    now = qm._utcnow()
    payload_json = json.dumps(job["payload"], ensure_ascii=False)
    user_id = int(job["payload"].get("user_id") or 0)
    # R21 留痕:谁触发(payload.staff_id 优先,回退 user_id)+ 任务链上下文(为什么)。
    triggered_by = int(job["payload"].get("staff_id") or user_id or 0) or None
    task_chain_json = json.dumps(
        {
            "job_type": job["job_type"],
            "reason": job["payload"].get("reason"),
            "category": job["payload"].get("category"),
            "endpoint": job["payload"].get("endpoint"),
        },
        ensure_ascii=False,
        default=str,
    )
    columns = [
        "task_id",
        "job_type",
        "submission_id",
        "user_id",
        "status",
        "payload_json",
        "retry_count",
        "created_at",
        "updated_at",
        "stage",
        "extra_json",
        "triggered_by_staff_id",
        "task_chain_json",
    ]
    values: list[Any] = [
        job["task_id"],
        job["job_type"],
        int(job.get("submission_id") or 0),
        user_id,
        qm.TaskStatus.QUEUED.value,
        payload_json,
        0,
        now,
        now,
        "ingest",
        json.dumps({}, ensure_ascii=False),
        triggered_by,
        task_chain_json,
    ]
    if job.get("priority") is not None:
        columns.append("priority")
        values.append(int(job.get("priority") or 5))
    if job.get("lock_key"):
        columns.append("lock_key")
        values.append(str(job.get("lock_key") or ""))
    if job.get("timeout_seconds") is not None:
        columns.append("timeout_seconds")
        values.append(int(job.get("timeout_seconds") or 300))
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO job_execution_ledger ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        tuple(values),
    )
    conn.commit()


def rollback_job_ledger_insert() -> None:
    qm = _qm()
    try:
        qm.get_conn().rollback()
    except Exception as exc:
        qm.logger.warning("job ledger rollback failed: %s", exc)


def update_job_ledger(queue: Any, task_id: str, status: str, **extra: Any) -> Optional[Dict[str, Any]]:
    qm = _qm()
    conn = qm.get_conn()
    row = conn.execute(
        """
        SELECT task_id, user_id, status, extra_json, retry_count, result_json, stats_json
        FROM job_execution_ledger
        WHERE task_id=?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    current_status = str(row["status"] or "").lower()
    incoming_status = str(status or "").lower()
    if current_status in qm.TERMINAL_JOB_STATUSES and incoming_status != current_status:
        metadata_updates = []
        metadata_params = []
        if extra.get("stream_id"):
            metadata_updates.append("stream_id=COALESCE(stream_id, ?)")
            metadata_params.append(extra.get("stream_id"))
        if extra.get("consumer_name"):
            metadata_updates.append("consumer_name=COALESCE(consumer_name, ?)")
            metadata_params.append(extra.get("consumer_name"))
        if metadata_updates:
            conn.execute(
                f"UPDATE job_execution_ledger SET {', '.join(metadata_updates)} WHERE task_id=?",
                (*metadata_params, task_id),
            )
            conn.commit()
        updated = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
        snapshot = queue._status_row_to_dict(updated)
        snapshot["_stale_status_ignored"] = True
        return snapshot
    extra_json = qm._decode_json(row["extra_json"], {})
    for key, value in extra.items():
        if key in {"result_json", "stats_json", "summary", "error_message", "result_path", "detection_status", "stage", "stream_id", "consumer_name"}:
            continue
        extra_json[key] = value
    retry_count = int(extra.get("retry_count", row["retry_count"] or 0) or 0)
    now = qm._utcnow()
    conn.execute(
        """
        UPDATE job_execution_ledger
        SET status=?,
            updated_at=?,
            started_at=CASE WHEN ?='processing' AND started_at IS NULL THEN ? ELSE started_at END,
            finished_at=CASE WHEN ? IN ('done','partial_done','failed','prefilter_rejected','cancelled','timeout') THEN ? ELSE finished_at END,
            retry_count=?,
            error_message=?,
            summary=?,
            detection_status=?,
            result_path=?,
            result_json=?,
            stats_json=?,
            stage=?,
            stream_id=COALESCE(?, stream_id),
            consumer_name=COALESCE(?, consumer_name),
            extra_json=?
        WHERE task_id=?
        """,
        (
            status,
            now,
            status,
            now,
            status,
            now,
            retry_count,
            str(extra.get("error_message") or ""),
            str(extra.get("summary") or ""),
            str(extra.get("detection_status") or ""),
            str(extra.get("result_path") or ""),
            json.dumps(extra.get("result_json") or qm._decode_json(row["result_json"], {}), ensure_ascii=False),
            json.dumps(extra.get("stats_json") or qm._decode_json(row["stats_json"], {}), ensure_ascii=False),
            str(extra.get("stage") or extra_json.get("stage") or ""),
            extra.get("stream_id"),
            extra.get("consumer_name"),
            json.dumps(extra_json, ensure_ascii=False),
            task_id,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
    return queue._status_row_to_dict(updated)


def get_job_status(queue: Any, task_id: str) -> Optional[Dict[str, Any]]:
    qm = _qm()
    conn = qm.get_conn()
    row = conn.execute("SELECT * FROM job_execution_ledger WHERE task_id=?", (task_id,)).fetchone()
    return queue._status_row_to_dict(row) if row else None


def ledger_queue_summary() -> Dict[str, Any]:
    qm = _qm()
    conn = qm.get_conn()
    waiting_statuses = {qm.TaskStatus.QUEUED.value, qm.TaskStatus.RETRYING.value}
    processing_statuses = {qm.TaskStatus.PROCESSING.value, "running"}
    failed_statuses = {qm.TaskStatus.FAILED.value}
    completed_statuses = {qm.TaskStatus.DONE.value, qm.TaskStatus.PARTIAL.value, "cancelled", "timeout", "prefilter_rejected"}
    counts = {"waiting": 0, "processing": 0, "failed": 0, "completed": 0}
    by_type: Dict[str, Dict[str, int]] = {}
    oldest_waiting_age: Optional[int] = None
    now = datetime.now(timezone.utc)

    try:
        aggregate_rows = conn.execute(
            """
            SELECT job_type, status, COUNT(*) AS n, MIN(created_at) AS oldest
            FROM job_execution_ledger
            WHERE status IN ('queued', 'retrying', 'processing', 'running', 'failed')
            GROUP BY job_type, status
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT task_id, job_type, status, created_at, updated_at, started_at, finished_at
            FROM job_execution_ledger
            ORDER BY id DESC
            LIMIT ?
            """,
            (1000,),
        ).fetchall()
    except Exception:
        return {
            "waiting": 0,
            "processing": 0,
            "failed": 0,
            "avg_duration_seconds": None,
            "eta_wait_seconds": None,
            "configured_concurrency": qm.WORKER_CONFIGURED_CONCURRENCY,
            "worker_processes": qm.WORKER_SERVICE_PROCESSES,
            "worker_async_consumers": qm.WORKER_ASYNC_CONSUMERS,
            "note": "job_execution_ledger unavailable",
        }

    for row in aggregate_rows:
        status = str(row["status"] or "").lower()
        job_type = str(row["job_type"] or "unknown")
        n = int(row["n"] or 0)
        bucket = by_type.setdefault(job_type, {"waiting": 0, "processing": 0, "failed": 0, "completed": 0})
        if status in waiting_statuses:
            counts["waiting"] += n
            bucket["waiting"] += n
            created_at = qm._parse_ts(row["oldest"])
            if created_at:
                age = int(max(0, (now - created_at).total_seconds()))
                oldest_waiting_age = age if oldest_waiting_age is None else max(oldest_waiting_age, age)
        elif status in processing_statuses:
            counts["processing"] += n
            bucket["processing"] += n
        elif status in failed_statuses:
            counts["failed"] += n
            bucket["failed"] += n

    durations: list[float] = []
    completed_last_hour = 0

    for row in rows:
        status = str(row["status"] or "").lower()
        job_type = str(row["job_type"] or "unknown")
        bucket = by_type.setdefault(job_type, {"waiting": 0, "processing": 0, "failed": 0, "completed": 0})
        if status in completed_statuses:
            counts["completed"] += 1
            bucket["completed"] += 1
            duration = qm._seconds_between(row["started_at"] or row["created_at"], row["finished_at"] or row["updated_at"])
            if duration is not None:
                durations.append(duration)
            finished_at = qm._parse_ts(row["finished_at"] or row["updated_at"])
            if finished_at and (now - finished_at).total_seconds() <= 3600:
                completed_last_hour += 1

    avg_duration = round(sum(durations) / len(durations), 1) if durations else None
    eta_wait_seconds = None
    if counts["waiting"] > 0:
        if completed_last_hour > 0:
            eta_wait_seconds = int(counts["waiting"] / max(completed_last_hour / 3600, 0.001))
        elif avg_duration:
            eta_wait_seconds = int(counts["waiting"] * avg_duration / max(qm.WORKER_CONFIGURED_CONCURRENCY, 1))

    return {
        "waiting": counts["waiting"],
        "processing": counts["processing"],
        "failed": counts["failed"],
        "completed_recent_sample": counts["completed"],
        "completed_last_hour": completed_last_hour,
        "avg_duration_seconds": avg_duration,
        "eta_wait_seconds": eta_wait_seconds,
        "oldest_waiting_age_seconds": oldest_waiting_age,
        "configured_concurrency": qm.WORKER_CONFIGURED_CONCURRENCY,
        "worker_processes": qm.WORKER_SERVICE_PROCESSES,
        "worker_async_consumers": qm.WORKER_ASYNC_CONSUMERS,
        "by_job_type": by_type,
        "sample_size": len(rows),
    }


def mark_timed_out_jobs(queue: Any, limit: int = 100) -> int:
    qm = _qm()
    conn = qm.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT task_id, created_at, started_at, timeout_seconds
            FROM job_execution_ledger
            WHERE status IN ('queued', 'retrying', 'processing', 'running')
              AND job_type LIKE ?
              AND COALESCE(timeout_seconds, 0) > 0
            ORDER BY id ASC
            LIMIT ?
            """,
            ("vkpi_%", max(1, min(500, int(limit or 100)))),
        ).fetchall()
    except Exception:
        return 0
    now = datetime.now(timezone.utc)
    timed_out = 0
    for row in rows:
        baseline = qm._parse_ts(row["started_at"] or row["created_at"])
        if not baseline:
            continue
        timeout_seconds = int(row["timeout_seconds"] or 0)
        if timeout_seconds <= 0:
            continue
        if (now - baseline).total_seconds() <= timeout_seconds:
            continue
        queue._update_job_ledger(
            str(row["task_id"]),
            "timeout",
            error_message=f"job exceeded timeout_seconds={timeout_seconds}",
            stage="timeout",
        )
        try:
            conn.execute(
                """
                UPDATE vkpi_async_task_items
                SET status='failed',
                    error=?,
                    updated_at=?
                WHERE task_id=?
                  AND status IN ('pending', 'running')
                """,
                (f"job exceeded timeout_seconds={timeout_seconds}", qm._utcnow(), str(row["task_id"])),
            )
            conn.commit()
        except Exception as exc:
            qm.logger.warning("failed to mark timed-out async task %s: %s", row["task_id"], exc)
        timed_out += 1
    return timed_out
