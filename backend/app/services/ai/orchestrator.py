from __future__ import annotations

"""
services/ai/orchestrator.py — VideoOrchestrator 核心编排器
从 worker_original.py 提取
"""
"""
worker.py — Viltrox Video Analysis Orchestrator
================================================
替换 main.py 里的 _analysis_worker + _pending_analyses asyncio.Queue。

集成方式（Option A）：
  - /api/audit 端点流程不变，最后一步把任务 enqueue 到这里
  - 本模块负责 Gemini / Claude / GPT 三层分析 + DB 回写
  - 通过依赖注入接收真实函数，避免循环 import

main.py 需要做的改动：
  1. 在文件顶部: from worker import build_orchestrator, VideoJobInput
  2. 删掉: _analysis_semaphore, _pending_analyses, _analysis_worker()
  3. 在 lifespan 里懒初始化（Fix 1）——绝对不能在模块顶层创建 orchestrator，
     因为 analyze_youtube_with_gemini 等函数定义在文件更后面：

       orchestrator: Optional[VideoOrchestrator] = None  # 顶层只声明，不创建

       @asynccontextmanager
       async def lifespan(app):
           global orchestrator
           orchestrator = build_orchestrator(   # 这里所有函数已定义，安全
               gemini_fn=analyze_youtube_with_gemini,
               gpt_fn=gpt_prefilter_caption,
               claude_fn=analyze_text_content,
               get_conn_fn=get_conn,
               compute_weighted_fn=compute_weighted_scores,
               update_benchmark_fn=update_genre_benchmark,
               get_vertical_fn=get_vertical,
               apply_learned_weights_fn=apply_learned_weights,
           )
           await orchestrator.start()
           yield
           await orchestrator.stop()

       app = FastAPI(title="Viltrox Omni Audit API", version="3.0.0", lifespan=lifespan)

  4. /api/audit 末尾 enqueue 那段: await orchestrator.enqueue(...)
"""


import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.logging import get_logger
from app.services.ai.orchestrator_analyzers import (
    AnalyzerPolicy,
    AnalyzerResult,
    BaseAnalyzer,
    ClaudeAnalyzer,
    GPTPrefilterAnalyzer,
    GeminiAnalyzer,
    TaskStatus,
    VideoJobInput,
    VideoTask,
)
from app.services.ai.orchestrator_write_helpers import (
    apply_weighted_scores,
    merge_provider_payloads,
    resolve_product_detection,
    score_submission,
)

# ──────────────────────────────────────────────
# 配置（全部可通过环境变量覆盖）
# ──────────────────────────────────────────────
import os
from app.core.config import VIDEO_WORKERS as CONFIG_VIDEO_WORKERS

MAX_WORKERS    = int(os.getenv("VIDEO_WORKERS", str(CONFIG_VIDEO_WORKERS)))
MAX_RETRIES    = int(os.getenv("VIDEO_MAX_RETRIES", "2"))
QUEUE_MAXSIZE  = int(os.getenv("VIDEO_QUEUE_MAXSIZE", "2000"))
RETRY_BASE_SEC = float(os.getenv("VIDEO_RETRY_BASE_SEC", "2.0"))

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 内存任务存储（后续可替换为 Redis/PostgreSQL）
# ──────────────────────────────────────────────
class TaskStore:
    def __init__(self) -> None:
        self._tasks: Dict[str, VideoTask] = {}
        self._lock  = asyncio.Lock()

    async def put(self, task: VideoTask) -> None:
        async with self._lock:
            self._tasks[task.task_id] = task

    async def get(self, task_id: str) -> VideoTask:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        return task

    async def set_status(self, task_id: str, status: TaskStatus) -> None:
        async with self._lock:
            t = self._tasks[task_id]
            t.status     = status
            t.updated_at = time.time()
            if status == TaskStatus.PROCESSING and t.started_at is None:
                t.started_at = time.time()
            if status in {TaskStatus.DONE, TaskStatus.PARTIAL, TaskStatus.FAILED,
                           TaskStatus.PREFILTER_REJECTED}:
                t.finished_at = time.time()

    async def add_result(self, task_id: str, r: AnalyzerResult) -> None:
        async with self._lock:
            t = self._tasks[task_id]
            t.results[r.provider] = {
                "ok": r.ok, "payload": r.payload,
                "error": r.error, "latency_ms": r.latency_ms,
            }
            if not r.ok and r.error:
                t.errors.append(f"{r.provider}: {r.error}")
            t.updated_at = time.time()

    async def bump_retry(self, task_id: str) -> int:
        async with self._lock:
            t = self._tasks[task_id]
            t.retry_count += 1
            t.updated_at   = time.time()
            return t.retry_count

    def stats(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for t in self._tasks.values():
            counts[t.status] = counts.get(t.status, 0) + 1
        return counts


# ──────────────────────────────────────────────
# DB 回写
# 把分析结果写入 submissions 表
# （依赖注入：接收 get_conn, compute_weighted_scores,
#   update_genre_benchmark, get_vertical, apply_learned_weights）
# ──────────────────────────────────────────────
class DBWriter:
    def __init__(
        self,
        get_conn_fn:              Callable,
        compute_weighted_fn:      Callable,
        update_benchmark_fn:      Callable,
        get_vertical_fn:          Callable,
        apply_learned_weights_fn: Callable,
    ) -> None:
        self._get_conn          = get_conn_fn
        self._compute_weighted  = compute_weighted_fn
        self._update_benchmark  = update_benchmark_fn
        self._get_vertical      = get_vertical_fn
        self._apply_learned     = apply_learned_weights_fn

    def mark_running(self, submission_id: int) -> None:
        conn = self._get_conn()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            "UPDATE submissions SET job_status='processing', started_at=? WHERE id=?",
            (now, submission_id)
        )
        conn.commit()

    def mark_failed(self, submission_id: int, error_msg: str) -> None:
        conn = self._get_conn()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            "UPDATE submissions SET job_status='failed', finished_at=?, error_message=? WHERE id=?",
            (now, error_msg, submission_id)
        )
        conn.commit()

    def write(self, task: VideoTask) -> None:
        """同步写入（在 asyncio.to_thread 里调用）— 完整评分版"""
        submission_id = task.job.submission_id

        # ── 合并 Gemini + Claude 结果，优先用 Gemini（视频更准）──
        vr = merge_provider_payloads(task.results)

        if not vr:
            logger.warning("worker write skipped | submission_id=%s | reason=no_usable_payload", submission_id)
            return

        # ── 加权分数 ──
        genre, vertical, ws, pct = apply_weighted_scores(
            vr,
            get_vertical=self._get_vertical,
            apply_learned=self._apply_learned,
            compute_weighted=self._compute_weighted,
            update_benchmark=self._update_benchmark,
        )
        brand_exp = vr["brand_exposure_score"]
        story_sc = vr["storytelling_score"]
        tech_stat = ws["tech_status"]
        tech_sc = ws["tech_score"]
        mkt_sc = ws["marketing_score"]

        # ── 产品分类 + 检测状态 ──
        from app.services.audit.similarity import classify_product
        from app.services.scoring.campaign import (
            compute_campaign_score, compute_creator_score,
        )

        product_match, viltrox_detected, detection_status = resolve_product_detection(
            vr,
            classify_product=classify_product,
        )

        # ── 评分 ──
        scores = score_submission(
            task,
            vr,
            detection_status=detection_status,
            viltrox_detected=viltrox_detected,
            compute_creator_score=compute_creator_score,
            compute_campaign_score=compute_campaign_score,
        )
        views = scores["views"]
        likes = scores["likes"]
        comments = scores["comments"]
        shares = scores["shares"]
        favorites = scores["favorites"]
        final_score = scores["final_score"]
        creator_score = scores["creator_score"]
        overall_score = scores["overall_score"]
        recommendation = scores["recommendation"]

        risk_score = 0  # stub — risk.py 后续补真逻辑

        # ── Memo ──
        products_str = ", ".join(vr.get("products_detected", [])[:3])
        memo = (
            f"[Worker] status={detection_status} | "
            f"products={products_str or 'none'} | "
            f"camera={vr.get('camera_body') or '?'} | "
            f"final={final_score} creator={creator_score}"
        )

        # ── 写 DB ──
        va_json = json.dumps(vr, ensure_ascii=False)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        evidence_json = json.dumps(vr.get("brand_elements", []))
        content_types_json = json.dumps(vr.get("content_types", []))

        conn = self._get_conn()
        conn.execute("""
            UPDATE submissions SET
                video_analysis=?,
                tech_score=?, marketing_score=?,
                brand_exposure_score=?, storytelling_score=?,
                tech_status=?, content_genre=?, vertical_category=?,
                percentile_tech=?, percentile_mkt=?,
                job_status=?, finished_at=?,
                detection_status=?, product_series=?, product_label=?,
                final_score=?, creator_score=?, overall_score=?, risk_score=?,
                recommendation=?, memo=?, evidence=?, content_types=?,
                views=CASE WHEN views=0 THEN ? ELSE views END,
                likes=CASE WHEN likes=0 THEN ? ELSE likes END,
                comments=CASE WHEN comments=0 THEN ? ELSE comments END,
                shares=CASE WHEN shares=0 THEN ? ELSE shares END,
                favorites=CASE WHEN favorites=0 THEN ? ELSE favorites END
            WHERE id=?
        """, (
            va_json,
            tech_sc, mkt_sc,
            brand_exp, story_sc,
            tech_stat, genre, vertical,
            pct.get("percentile_tech", 0), pct.get("percentile_mkt", 0),
            "done", now,
            detection_status,
            product_match.get("series", ""),
            product_match.get("label", ""),
            final_score, creator_score, overall_score, risk_score,
            recommendation, memo, evidence_json, content_types_json,
            views, likes, comments, shares, favorites,
            submission_id,
        ))
        conn.commit()

        # ── 自动发积分 ──
        if detection_status == "confirmed" and final_score > 0:
            try:
                from app.services.rewards.points import auto_award_points
                handle = task.job.handle or ""
                pts = auto_award_points(submission_id, handle, final_score)
                logger.info("worker write points awarded | submission_id=%s | points=%s", submission_id, pts.get("points", 0))
            except Exception as e:
                logger.warning("worker write points error | submission_id=%s | error=%s", submission_id, e)

        logger.info(
            "worker write done | submission_id=%s | status=%s | product=%s | final=%s | creator=%s | tech=%.1f | mkt=%.1f",
            submission_id,
            detection_status,
            product_match.get("label", "?"),
            final_score,
            creator_score,
            tech_sc,
            mkt_sc,
        )

    def write_rejection(self, task: VideoTask) -> None:
        """
        Fix 5: GPT 预筛判定无关内容时调用。
        不写 video_analysis / 评分字段，只更新 detection_status + memo。
        让管理员在后台能看到"GPT 自动拒绝"而不是一条空记录。
        """
        submission_id = task.job.submission_id
        gpt_payload   = task.results.get("gpt_prefilter", {}).get("payload", {})
        reason = (
            f"GPT prefilter auto-rejected: "
            f"viltrox_likely={gpt_payload.get('viltrox_likely', False)}, "
            f"confidence={gpt_payload.get('confidence', 'none')}"
        )
        conn = self._get_conn()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute("""
            UPDATE submissions SET
                detection_status = 'not_detected',
                recommendation   = 'Auto-rejected by GPT prefilter',
                memo             = COALESCE(memo, '') || ?,
                job_status       = 'done',
                finished_at      = ?
            WHERE id = ?
        """, (f" [{reason}]", now, submission_id))
        conn.commit()
        logger.info("worker prefilter rejected persisted | submission_id=%s | reason=%s", submission_id, reason)


# ──────────────────────────────────────────────
# 编排器（核心）
# 修复了 video_orchestrator.py 的 4 个问题：
#   1. 重试改为 create_task 异步延迟，不阻塞 worker
#   2. 串行预筛 → 并行主分析（节省 API 成本）
#   3. shutdown 时把 PROCESSING 任务标回 QUEUED
#   4. on_event 改为 lifespan（见下方 setup_lifespan）
# ──────────────────────────────────────────────
class VideoOrchestrator:
    def __init__(
        self,
        policy:   AnalyzerPolicy,
        db_writer: DBWriter,
        workers:  int = MAX_WORKERS,
    ) -> None:
        self.policy    = policy
        self.db_writer = db_writer
        self.store     = TaskStore()
        self.queue:    asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._workers        = workers
        self._tasks:         List[asyncio.Task] = []
        self._delayed_tasks: set               = set()  # Fix 3: 延迟重试句柄
        self._started        = False

    # ── 生命周期 ──────────────────────────────
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._workers):
            self._tasks.append(asyncio.create_task(self._worker_loop(i + 1)))
        logger.info("orchestrator started | workers=%s", self._workers)

    async def stop(self) -> None:
        """
        修复：取消 worker 前先把所有 PROCESSING 任务标回 QUEUED
        避免重启后任务永远卡在 PROCESSING 状态
        """
        for t in self.store._tasks.values():
            if t.status == TaskStatus.PROCESSING:
                t.status = TaskStatus.QUEUED
                t.started_at = None
        for dt in list(self._delayed_tasks):   # Fix 3: 取消所有挂起的重试
            dt.cancel()
        self._delayed_tasks.clear()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False
        logger.info("orchestrator stopped")

    # ── 外部提交接口（替换旧的 _pending_analyses.put）──
    async def enqueue(self, job: VideoJobInput) -> str:
        """
        在 /api/audit 末尾调用，替换原来的:
            await _pending_analyses.put({submission_id, url, title, handle, platform})
        """
        task_id = str(uuid.uuid4())
        task = VideoTask(task_id=task_id, job=job)
        await self.store.put(task)
        await self.store.set_status(task_id, TaskStatus.QUEUED)
        await self.queue.put(task_id)
        logger.info(
            "orchestrator enqueued | submission_id=%s | task=%s | platform=%s | queue_depth=%s",
            job.submission_id,
            task_id[:8],
            job.platform,
            self.queue.qsize(),
        )
        return task_id

    # ── Worker 循环 ───────────────────────────
    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            task_id = await self.queue.get()
            try:
                await self._run_single(task_id, worker_id)
            except asyncio.CancelledError:
                # Fix 2: task_done() 只在 finally 里调一次
                # 原来在这里还调一次会导致 "task_done() called too many times"
                raise
            except Exception as e:
                logger.exception("worker loop unexpected error | worker_id=%s | error=%s", worker_id, e)
            finally:
                self.queue.task_done()  # 唯一调用点

    # ── 单任务执行 ────────────────────────────
    async def _run_single(self, task_id: str, worker_id: int = 0) -> None:
        task = await self.store.get(task_id)
        job  = task.job
        await self.store.set_status(task_id, TaskStatus.PROCESSING)
        logger.info(
            "worker processing | worker_id=%s | submission_id=%s | platform=%s | source=%s",
            worker_id,
            job.submission_id,
            job.platform,
            job.url[:60] if job.url else "upload",
        )

        # Mark submission as processing in DB (legacy reads still accept "running")
        try:
            await asyncio.to_thread(self.db_writer.mark_running, job.submission_id)
        except Exception as e:
            logger.warning("worker mark_running error | worker_id=%s | submission_id=%s | error=%s", worker_id, job.submission_id, e)

        # ── GPT 预筛 (仅当有文字内容时) ──
        plan = self.policy.get_plan(task)
        if plan.prefilter and not job.gpt_already_done:
            pre_result = await plan.prefilter.analyze(task)
            await self.store.add_result(task_id, pre_result)

            if pre_result.skip_next:
                await self.store.set_status(task_id, TaskStatus.PREFILTER_REJECTED)
                try:
                    await asyncio.to_thread(self.db_writer.write_rejection, task)
                except Exception as e:
                    logger.warning("worker rejection write error | worker_id=%s | submission_id=%s | error=%s", worker_id, job.submission_id, e)
                logger.info("worker prefilter rejected | worker_id=%s | submission_id=%s", worker_id, job.submission_id)
                return

        # ── 完整审计管线 ──
        try:
            from app.services.audit.pipeline import perform_full_audit, AuditContext

            ctx = AuditContext(
                compute_weighted_fn=self.db_writer._compute_weighted,
                update_benchmark_fn=self.db_writer._update_benchmark,
                get_vertical_fn=self.db_writer._get_vertical,
                apply_learned_weights_fn=self.db_writer._apply_learned,
            )

            result = await perform_full_audit(job, ctx)

            # ── 写回 DB ──
            from app.db.repositories.submissions import finalize_submission
            await asyncio.to_thread(finalize_submission, job.submission_id, result)

            # ── 自动发积分 ──
            scores = result.get("scores", {})
            final_score = scores.get("final_score", 0)
            detection_status = result.get("detection_status", "not_detected")

            if detection_status == "confirmed" and final_score > 0:
                try:
                    from app.services.rewards.points import auto_award_points
                    pts = auto_award_points(job.submission_id, job.handle or "", final_score)
                    logger.info("worker points awarded | worker_id=%s | submission_id=%s | points=%s", worker_id, job.submission_id, pts.get("points", 0))
                except Exception as e:
                    logger.warning("worker points award error | worker_id=%s | submission_id=%s | error=%s", worker_id, job.submission_id, e)

            await self.store.set_status(task_id, TaskStatus.DONE)

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)[:300]}"
            logger.warning("worker pipeline error | worker_id=%s | submission_id=%s | error=%s", worker_id, job.submission_id, error_msg)

            # 重试 or 失败
            retry_count = await self.store.bump_retry(task_id)
            if retry_count <= MAX_RETRIES:
                await self.store.set_status(task_id, TaskStatus.RETRYING)
                delay = RETRY_BASE_SEC * retry_count
                logger.info(
                    "worker retry scheduled | worker_id=%s | submission_id=%s | retry=%s/%s | delay=%.1fs",
                    worker_id,
                    job.submission_id,
                    retry_count,
                    MAX_RETRIES,
                    delay,
                )
                dt = asyncio.create_task(self._delayed_requeue(task_id, delay))
                self._delayed_tasks.add(dt)
                dt.add_done_callback(self._delayed_tasks.discard)
            else:
                await self.store.set_status(task_id, TaskStatus.FAILED)
                try:
                    await asyncio.to_thread(self.db_writer.mark_failed, job.submission_id, error_msg)
                except Exception as db_err:
                    logger.warning(
                        "worker mark_failed error | worker_id=%s | submission_id=%s | error=%s",
                        worker_id,
                        job.submission_id,
                        db_err,
                    )
                logger.error(
                    "worker failed terminally | worker_id=%s | submission_id=%s | retries=%s",
                    worker_id,
                    job.submission_id,
                    MAX_RETRIES,
                )

    async def _delayed_requeue(self, task_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.store.set_status(task_id, TaskStatus.QUEUED)
        await self.queue.put(task_id)

    # ── 状态查询（给 /health 端点用）─────────
    def health(self) -> Dict[str, Any]:
        return {
            "workers":     self._workers,
            "queue_depth": self.queue.qsize(),
            "task_stats":  self.store.stats(),
            "started":     self._started,
        }


# ──────────────────────────────────────────────
# 工厂函数
# 在 main.py 里调用，传入真实函数，构建编排器
#
# 用法（main.py 顶部）：
#
#   from worker import build_orchestrator, VideoJobInput
#
#   orchestrator = build_orchestrator(
#       gemini_fn    = analyze_youtube_with_gemini,
#       gpt_fn       = gpt_prefilter_caption,
#       claude_fn    = analyze_text_content,
#       get_conn_fn  = get_conn,
#       compute_weighted_fn     = compute_weighted_scores,
#       update_benchmark_fn     = update_genre_benchmark,
#       get_vertical_fn         = get_vertical,
#       apply_learned_weights_fn = apply_learned_weights,
#   )
# ──────────────────────────────────────────────
def build_orchestrator(
    gemini_fn:                Callable,
    gpt_fn:                   Callable,
    claude_fn:                Callable,
    get_conn_fn:              Callable,
    compute_weighted_fn:      Callable,
    update_benchmark_fn:      Callable,
    get_vertical_fn:          Callable,
    apply_learned_weights_fn: Callable,
    workers: int = MAX_WORKERS,
) -> VideoOrchestrator:
    policy = AnalyzerPolicy(
        gpt_analyzer    = GPTPrefilterAnalyzer(gpt_fn),
        gemini_analyzer = GeminiAnalyzer(gemini_fn),
        claude_analyzer = ClaudeAnalyzer(claude_fn),
    )
    db_writer = DBWriter(
        get_conn_fn              = get_conn_fn,
        compute_weighted_fn      = compute_weighted_fn,
        update_benchmark_fn      = update_benchmark_fn,
        get_vertical_fn          = get_vertical_fn,
        apply_learned_weights_fn = apply_learned_weights_fn,
    )
    return VideoOrchestrator(policy=policy, db_writer=db_writer, workers=workers)


# ──────────────────────────────────────────────
# Lifespan helper（在 main.py 的 lifespan 里调用）
#
# from contextlib import asynccontextmanager
#
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     await orchestrator.start()
#     yield
#     await orchestrator.stop()
#
# app = FastAPI(..., lifespan=lifespan)
# ──────────────────────────────────────────────
