"""优化波 B·F2(worker 侧):单进程视频任务并发池——每任务独立连接、池满内联、错误分流、停机排空。"""
from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.workers import apify_jobs_worker_video_pool as pool_mod
from app.workers.apify_jobs_worker_video_pool import VideoJobPool


def _video_job(job_id: int, derive: str = "video_analysis_final_v1") -> dict[str, Any]:
    return {"id": job_id, "job_type": "video", "payload": {"derive_method": derive}, "lease_owner": "w:1"}


class _Conn:
    instances: list["_Conn"] = []

    def __init__(self) -> None:
        self.closed = False
        self.thread = threading.current_thread().name
        _Conn.instances.append(self)

    def close(self) -> None:
        self.closed = True


class _Blocked(Exception):
    pass


def test_pool_size_defaults_to_gemini_video_slots_and_fails_closed() -> None:
    assert pool_mod.video_pool_size({}) == 1
    assert pool_mod.video_pool_size({"APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY": "4"}) == 4
    assert pool_mod.video_pool_size({"APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY": "4", "APIFY_WORKER_VIDEO_POOL_SIZE": "2"}) == 2
    assert pool_mod.video_pool_size({"APIFY_WORKER_VIDEO_POOL_SIZE": "99"}) == 1
    assert pool_mod.video_pool_size({"APIFY_WORKER_VIDEO_POOL_SIZE": "0"}) == 1
    assert pool_mod.video_pool_size({"APIFY_WORKER_VIDEO_POOL_SIZE": "lots"}) == 1


def test_only_real_video_jobs_are_pooled() -> None:
    assert pool_mod.is_pooled_video_job(_video_job(1)) is True
    assert pool_mod.is_pooled_video_job(_video_job(2, derive="mock")) is False
    assert pool_mod.is_pooled_video_job({"job_type": "kol_audience_stats_refresh"}) is False
    assert pool_mod.is_pooled_video_job({"job_type": "session_advance"}) is False


def test_disabled_pool_never_accepts() -> None:
    pool = VideoJobPool(max_workers=1, db_url="x", execute=lambda c, j: "done", on_failure=lambda c, j, e: None, connect=_Conn)
    assert pool.enabled is False
    assert pool.submit(_video_job(1)) is False


def test_jobs_run_concurrently_on_their_own_connections_and_overflow_is_inline() -> None:
    _Conn.instances.clear()
    gate = threading.Event()
    seen_threads: list[str] = []

    def _execute(conn: Any, job: dict[str, Any]) -> str:
        seen_threads.append(threading.current_thread().name)
        assert isinstance(conn, _Conn) and conn.thread == threading.current_thread().name
        gate.wait(5)
        return "done"

    pool = VideoJobPool(max_workers=2, db_url="x", execute=_execute, on_failure=lambda c, j, e: None, connect=_Conn)
    assert pool.submit(_video_job(1)) is True
    assert pool.submit(_video_job(2)) is True
    assert pool.in_flight == 2
    assert pool.submit(_video_job(3)) is False  # 池满 → 调用方内联
    assert pool.submit({"job_type": "session_advance", "id": 4}) is False  # 非视频从不进池
    assert pool.drain(timeout=0.05) is False
    gate.set()
    assert pool.drain(timeout=5) is True
    assert pool.in_flight == 0
    assert pool.completed == 2
    assert sorted(seen_threads) == ["apify-video-pool-1", "apify-video-pool-2"]
    assert len(_Conn.instances) == 2 and all(c.closed for c in _Conn.instances)


def test_failure_and_claim_blocked_are_routed_to_callbacks() -> None:
    failures: list[tuple[int, str]] = []
    blocked: list[int] = []

    def _execute(conn: Any, job: dict[str, Any]) -> str:
        if job["id"] == 1:
            raise _Blocked("lease held")
        raise RuntimeError("provider exploded")

    pool = VideoJobPool(
        max_workers=4,
        db_url="x",
        execute=_execute,
        on_failure=lambda c, j, e: failures.append((int(j["id"]), type(e).__name__)),
        on_claim_blocked=lambda c, j, e: blocked.append(int(j["id"])),
        claim_blocked_type=_Blocked,
        connect=_Conn,
    )
    assert pool.submit(_video_job(1)) and pool.submit(_video_job(2))
    assert pool.drain(timeout=5)
    assert blocked == [1]
    assert failures == [(2, "RuntimeError")]
    assert pool.failed == 1 and pool.completed == 0


def test_connection_failure_releases_slot_without_crashing() -> None:
    def _connect() -> Any:
        raise RuntimeError("db down")

    pool = VideoJobPool(max_workers=2, db_url="x", execute=lambda c, j: "done", on_failure=lambda c, j, e: None, connect=_connect)
    assert pool.submit(_video_job(1)) is True
    assert pool.drain(timeout=5) is True
    assert pool.in_flight == 0
    assert pool.has_capacity() is True


def test_from_env_reads_pool_size(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = VideoJobPool.from_env(
        db_url="postgresql://x",
        execute=lambda c, j: "done",
        on_failure=lambda c, j, e: None,
        env={"APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY": "3"},
    )
    assert pool.max_workers == 3 and pool.enabled is True
    started = time.monotonic()
    assert pool.drain(timeout=0.01) is True  # 空池立即 idle
    assert time.monotonic() - started < 0.5
