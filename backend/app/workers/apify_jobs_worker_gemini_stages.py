"""final_v1 视频链阶段计时(零 LLM 成本)+ 任务级诊断落库。

背景(2026-08 剖面):vkpi_analysis_cache.result.cost.latency_ms 只有「总耗时」,
下载 / R2 / File API 上传 / Gemini 调用 / 落库 / 派生入队 各占多少全是盲区,
提速刀无从排序。本模块提供:

* ``StageClock`` —— worker 侧阶段计时器(media_resolve / download / analyzer_subprocess /
  r2_warm / cost_record / persist / followups)。
* ``merged_stage_timings(raw)`` —— 合并子进程分析器阶段(``raw["stage_timings_ms"]``:
  subtitles / youtube_direct / download / upload / file_active_wait / cache_setup /
  generation / cleanup)与 worker 侧阶段,写进 shaped 结果 ``cost.stage_timings_ms``。
* ``persist_job_stage_diagnostics`` —— 把落库/派生入队之后才知道的阶段时间写进
  ``apify_jobs.payload.diagnostics``(单条 UPDATE,失败只 warning,绝不影响已 done 的任务)。

红线:本模块零 fit 写、零 LLM 调用、不改 final_v1 六层内容契约(只在 cost 下追加诊断键)。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.logging import get_logger
from app.workers.apify_jobs_worker_helpers import _json, _redact_sensitive_text


logger = get_logger(__name__)

# 分析器子进程里的阶段名(gemini_video*.py 的 _stage_add 写入)。
ANALYZER_STAGE_KEYS = (
    "subtitles",
    "youtube_direct",
    "download",
    "upload",
    "file_active_wait",
    "cache_setup",
    "generation",
    "cleanup",
)
# worker 进程里的阶段名(本模块 StageClock 写入)。
WORKER_STAGE_KEYS = (
    "media_resolve",
    "worker_download",
    "analyzer_subprocess",
    "r2_warm",
    "cost_record",
    "persist",
    "followups",
)


class StageClock:
    """累加式阶段计时器;同名阶段多次进入累加(与分析器 _stage_add 口径一致)。"""

    def __init__(self) -> None:
        self.timings: dict[str, int] = {}
        self.started_monotonic = time.monotonic()

    def add(self, stage: str, started_monotonic: float) -> int:
        elapsed_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))
        self.timings[stage] = int(self.timings.get(stage) or 0) + elapsed_ms
        return elapsed_ms

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.add(name, started)

    def total_ms(self) -> int:
        return max(0, int((time.monotonic() - self.started_monotonic) * 1000))


def merged_stage_timings(raw: dict[str, Any] | None, worker: dict[str, int] | None = None) -> dict[str, int]:
    """分析器阶段 + worker 阶段合并成一张平表(键不重叠;非法值丢弃)。"""

    merged: dict[str, int] = {}
    analyzer = (raw or {}).get("stage_timings_ms") if isinstance(raw, dict) else None
    for source in (analyzer, (raw or {}).get("worker_stage_timings_ms") if isinstance(raw, dict) else None, worker):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            try:
                merged[str(key)] = int(value)
            except (TypeError, ValueError):
                continue
    return merged


def redact_diagnostics(value: Any, *, depth: int = 0) -> Any:
    """递归脱敏诊断结构里的字符串(代理 userinfo / token / bearer),深度封顶防循环。"""

    if depth > 6:
        return None
    if isinstance(value, str):
        return _redact_sensitive_text(value, limit=600)
    if isinstance(value, dict):
        return {str(k): redact_diagnostics(v, depth=depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, list):
        return [redact_diagnostics(v, depth=depth + 1) for v in value[:32]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def analyzer_failure_diagnostics(raw: dict[str, Any] | None, *, platform: str, error: str) -> dict[str, Any]:
    """失败路径要落 payload.diagnostics 的子集:错误 + 直链尝试 + 下载诊断(全部脱敏)。"""

    raw = raw if isinstance(raw, dict) else {}
    # 优化波 B:分析器/子进程自带的 diagnostics(truncation / retries / subtitles /
    # chain_stop_reason / child_stderr_tail ...)原样并入;固定键优先,不被覆盖。
    analyzer_diag = raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {}
    merged: dict[str, Any] = {str(k): v for k, v in analyzer_diag.items()}
    merged.update(
        {
            "platform": platform,
            "method": str(raw.get("method") or ""),
            "error": str(error or "")[:300],
            "youtube_direct": raw.get("youtube_direct"),
            "download_diagnostics": raw.get("download_diagnostics"),
            "subtitle_chars": raw.get("subtitle_chars"),
        }
    )
    return redact_diagnostics(merged)


def record_final_v1_outcome_diagnostics(
    conn: Any,
    *,
    job_id: int,
    raw: dict[str, Any] | None,
    clock: "StageClock",
    platform: str,
    error: str = "",
) -> bool:
    """成功/失败共用的一行落库入口:阶段耗时 + 直链/下载诊断(失败时附 error)。"""

    raw = raw if isinstance(raw, dict) else {}
    extra = analyzer_failure_diagnostics(raw, platform=platform, error=error)
    extra["total_ms"] = clock.total_ms()
    extra["outcome"] = "failed" if error else "done"
    return persist_job_stage_diagnostics(
        conn,
        job_id=int(job_id),
        stage_timings_ms=merged_stage_timings(raw, clock.timings),
        extra=extra,
    )


def persist_job_stage_diagnostics(
    conn: Any,
    *,
    job_id: int,
    stage_timings_ms: dict[str, int],
    extra: dict[str, Any] | None = None,
) -> bool:
    """把阶段耗时写进 apify_jobs.payload.diagnostics(覆盖同名键,保留其余 payload)。

    只在任务已 done/failed 之后调用;任何异常只 warning 返回 False——诊断永远不能把
    已完成的分析变成失败。
    """

    diagnostics: dict[str, Any] = {
        "stage_timings_ms": {str(k): int(v) for k, v in (stage_timings_ms or {}).items()},
        "stage_timings_version": 1,
    }
    if isinstance(extra, dict):
        diagnostics.update(redact_diagnostics(extra))
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE apify_jobs
                    SET payload = COALESCE(payload, '{}'::jsonb)
                        || jsonb_build_object(
                             'diagnostics',
                             COALESCE(payload->'diagnostics', '{}'::jsonb) || %s::jsonb
                           )
                    WHERE id=%s
                    """,
                    (_json(diagnostics), int(job_id)),
                )
        return True
    except Exception as exc:
        logger.warning(
            "final_v1 stage diagnostics persist skipped | job_id=%s exception_type=%s",
            job_id,
            type(exc).__name__,
        )
        return False
