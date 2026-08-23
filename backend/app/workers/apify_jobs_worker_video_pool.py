"""单进程视频任务并发池(优化波 B·F2)。

背景(2026-08 隔离库 30 条 YouTube eval):一条 final_v1 端到端 p50 19-26s,其中 Gemini
看视频 15-21s 全是纯等待;``run_worker`` 的 claim → execute → claim 是串行的,单车道
同一时刻只跑 1 条视频。资源槽 ``APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY``(advisory lock,
跨进程)早就允许 N 路,但单进程从不并发——只有多开进程才吃得到。

本模块把「video 类型 job 的执行」放进有界线程池:

* 只接 ``resource_group_for_job(job) == "gemini_video"`` 的行(video 非 mock;
  kol_audience_stats_refresh 虽共用 Gemini 槽但不是视频,不进池);其他 job 类型原样
  走主循环串行,行为不变。
* 每个线程**独立 DB 连接**(advisory lock 是会话级:执行锁 / 资源槽 / LLM 槽都得在
  执行线程自己的连接上拿与放),主循环的连接只做 claim。
* 认领仍由主循环的 ``FOR UPDATE SKIP LOCKED`` 完成,池里的行已是 running + 租约,
  不会被别的进程重复认领;池满时 ``submit`` 返回 False,主循环按旧路径内联执行。
* 线程里复用主循环同款错误分流(ApifyExecutionClaimBlocked → requeue;其他 → fail_job),
  由调用方以回调注入,本模块不 import apify_jobs_worker(避免循环 import)。
* 停机:``drain(timeout)`` 等在飞任务收尾(子进程本身有 GEMINI_CALL_TIMEOUT 兜底)。

接线(跨车道请求,apify_jobs_worker.run_worker 不在 V 车道文件域):

    pool = VideoJobPool.from_env(db_url=DB_RUNTIME_URL, execute=_execute_claimed_job,
                                 on_claim_blocked=..., on_failure=_fail_job)
    ...
    job = _claim_job(conn)
    if pool.submit(job):      # 视频进池,主循环立刻回去再 claim
        continue
    final_status = _execute_claimed_job(conn, job)   # 旧路径
    ...
    finally: pool.drain(timeout=...)

``APIFY_WORKER_VIDEO_POOL_SIZE``(默认 = gemini_video 资源槽数;1 = 关闭进池,纯旧行为)。
真正的提供方并发上限仍由资源槽 + APIFY_WORKER_LLM_CONCURRENCY 把守:池只是让单进程
能把这些槽位用满。
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import psycopg

from app.core.logging import get_logger
from app.workers.apify_job_resource_slots import (
    MAX_RESOURCE_SLOT_CAP,
    resource_group_for_job,
    resource_slot_limits,
)


logger = get_logger(__name__)

VIDEO_POOL_SIZE_ENV = "APIFY_WORKER_VIDEO_POOL_SIZE"
VIDEO_POOL_RESOURCE_GROUP = "gemini_video"


def video_pool_size(env: Mapping[str, str]) -> int:
    """池大小:``APIFY_WORKER_VIDEO_POOL_SIZE`` 若设则用之,否则 = gemini_video 资源槽数。

    夹在 [1, MAX_RESOURCE_SLOT_CAP];1 表示不进池(主循环内联,零行为变化)。
    非法值 fail-closed 成 1,而不是悄悄放大并发。
    """

    limits = resource_slot_limits(env)
    default = int(limits.get(VIDEO_POOL_RESOURCE_GROUP) or 1)
    raw = str(env.get(VIDEO_POOL_SIZE_ENV, "") or "").strip()
    if not raw:
        return max(1, min(MAX_RESOURCE_SLOT_CAP, default))
    try:
        value = int(raw)
    except ValueError:
        logger.error("%s must be an integer; falling back to 1 (inline)", VIDEO_POOL_SIZE_ENV)
        return 1
    if not 1 <= value <= MAX_RESOURCE_SLOT_CAP:
        logger.error(
            "%s must stay within [1, %s]; falling back to 1 (inline)",
            VIDEO_POOL_SIZE_ENV,
            MAX_RESOURCE_SLOT_CAP,
        )
        return 1
    return value


def is_pooled_video_job(job: Mapping[str, Any]) -> bool:
    """只有真视频分析(video 且 derive_method 非 mock)进池。"""

    if str(job.get("job_type") or "").strip().lower() != "video":
        return False
    return resource_group_for_job(job) == VIDEO_POOL_RESOURCE_GROUP


class VideoJobPool:
    """有界线程池;每个任务一条独立连接;满了就让调用方内联。"""

    def __init__(
        self,
        *,
        max_workers: int,
        db_url: str,
        execute: Callable[[Any, dict[str, Any]], str],
        on_failure: Callable[[Any, dict[str, Any], Exception], None],
        on_claim_blocked: Callable[[Any, dict[str, Any], Exception], None] | None = None,
        claim_blocked_type: type[BaseException] | None = None,
        connect: Callable[..., Any] | None = None,
        accepts: Callable[[Mapping[str, Any]], bool] = is_pooled_video_job,
    ) -> None:
        self.max_workers = max(1, int(max_workers))
        self.db_url = str(db_url or "")
        self._execute = execute
        self._on_failure = on_failure
        self._on_claim_blocked = on_claim_blocked
        self._claim_blocked_type = claim_blocked_type
        self._connect = connect or (lambda: psycopg.connect(self.db_url, autocommit=True))
        self._accepts = accepts
        self._lock = threading.Lock()
        self._threads: dict[int, threading.Thread] = {}
        self._idle = threading.Event()
        self._idle.set()
        self.completed = 0
        self.failed = 0

    @classmethod
    def from_env(
        cls,
        *,
        db_url: str,
        execute: Callable[[Any, dict[str, Any]], str],
        on_failure: Callable[[Any, dict[str, Any], Exception], None],
        on_claim_blocked: Callable[[Any, dict[str, Any], Exception], None] | None = None,
        claim_blocked_type: type[BaseException] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "VideoJobPool":
        return cls(
            max_workers=video_pool_size(os.environ if env is None else env),
            db_url=db_url,
            execute=execute,
            on_failure=on_failure,
            on_claim_blocked=on_claim_blocked,
            claim_blocked_type=claim_blocked_type,
        )

    @property
    def enabled(self) -> bool:
        return self.max_workers > 1

    @property
    def in_flight(self) -> int:
        with self._lock:
            return len(self._threads)

    def has_capacity(self) -> bool:
        with self._lock:
            return len(self._threads) < self.max_workers

    def submit(self, job: Mapping[str, Any] | None) -> bool:
        """视频 job 且有空位 → 起线程执行并返回 True;否则 False(调用方内联执行)。"""

        if not job or not self.enabled or not self._accepts(job):
            return False
        job_dict = dict(job)
        job_id = int(job_dict["id"])
        with self._lock:
            if len(self._threads) >= self.max_workers:
                return False
            thread = threading.Thread(
                target=self._run,
                args=(job_id, job_dict),
                name=f"apify-video-pool-{job_id}",
                daemon=True,
            )
            self._threads[job_id] = thread
            self._idle.clear()
        thread.start()
        logger.info(
            "apify_jobs video job pooled | id=%s in_flight=%s max=%s",
            job_id,
            self.in_flight,
            self.max_workers,
        )
        return True

    def _run(self, job_id: int, job: dict[str, Any]) -> None:
        started = time.monotonic()
        conn = None
        try:
            conn = self._connect()
            try:
                final_status = self._execute(conn, job)
                verb = "requeued by executor" if final_status == "queued" else "done"
                logger.info(
                    "apify_jobs job %s (pooled) | id=%s status=%s elapsed_ms=%s",
                    verb,
                    job_id,
                    final_status or "unknown",
                    int((time.monotonic() - started) * 1000),
                )
                with self._lock:
                    self.completed += 1
            except Exception as exc:
                if (
                    self._claim_blocked_type is not None
                    and isinstance(exc, self._claim_blocked_type)
                    and self._on_claim_blocked is not None
                ):
                    self._on_claim_blocked(conn, job, exc)
                else:
                    logger.error(
                        "apify_jobs pooled job failed | id=%s error=%s",
                        job_id,
                        f"{type(exc).__name__}: {str(exc)[:200]}",
                    )
                    with self._lock:
                        self.failed += 1
                    self._on_failure(conn, job, exc)
        except Exception:
            # 连接都拿不到 / 失败回调自身炸:行仍 running,交给主循环的陈旧回收(stale reclaim)。
            logger.exception("apify_jobs pooled job lost its connection | id=%s", job_id)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            with self._lock:
                self._threads.pop(job_id, None)
                if not self._threads:
                    self._idle.set()

    def drain(self, timeout: float | None = None) -> bool:
        """等所有在飞任务结束;超时返回 False(线程是守护线程,进程仍可退出)。"""

        return self._idle.wait(timeout)


__all__ = [
    "VIDEO_POOL_RESOURCE_GROUP",
    "VIDEO_POOL_SIZE_ENV",
    "VideoJobPool",
    "is_pooled_video_job",
    "video_pool_size",
]
