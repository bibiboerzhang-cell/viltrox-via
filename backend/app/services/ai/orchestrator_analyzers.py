"""Analyzer types and policy for the video orchestrator."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    PARTIAL = "partial_done"
    DONE = "done"
    FAILED = "failed"
    RETRYING = "retrying"
    PREFILTER_REJECTED = "prefilter_rejected"


@dataclass
class VideoJobInput:
    """Calling-side payload for queued video analysis."""
    submission_id: int
    url: str
    title: str = ""
    handle: str = ""
    platform: str = ""
    profile: str = "standard"
    caption: str = ""
    scraped_text: str = ""
    og_image: str = ""
    gpt_already_done: bool = False
    user_id: Optional[int] = None
    user_handle: str = ""
    linked_handles: Dict[str, str] = field(default_factory=dict)
    uploaded_video: Optional[Dict[str, Any]] = None
    hints: Dict[str, bool] = field(default_factory=dict)
    metrics: Dict[str, int] = field(default_factory=dict)


@dataclass
class VideoTask:
    task_id: str
    job: VideoJobInput
    status: TaskStatus = TaskStatus.QUEUED
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class AnalyzerResult:
    provider: str
    ok: bool
    payload: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    latency_ms: int = 0
    skip_next: bool = False


class BaseAnalyzer:
    name: str = "base"

    async def analyze(self, task: VideoTask) -> AnalyzerResult:
        raise NotImplementedError


class GPTPrefilterAnalyzer(BaseAnalyzer):
    name = "gpt_prefilter"

    def __init__(self, fn: Callable) -> None:
        self._fn = fn

    async def analyze(self, task: VideoTask) -> AnalyzerResult:
        t0 = time.time()
        job = task.job
        try:
            result = await asyncio.to_thread(self._fn, job.title, job.caption, job.platform)
            latency = int((time.time() - t0) * 1000)
            is_spam_or_unrelated = (
                not result.get("viltrox_likely", True)
                and result.get("confidence") in ("none", "low")
            )
            return AnalyzerResult(
                provider=self.name,
                ok=True,
                payload=result,
                latency_ms=latency,
                skip_next=is_spam_or_unrelated,
            )
        except Exception as e:
            return AnalyzerResult(
                provider=self.name,
                ok=False,
                error=str(e),
                latency_ms=int((time.time() - t0) * 1000),
            )


class GeminiAnalyzer(BaseAnalyzer):
    name = "gemini"

    def __init__(self, fn: Callable) -> None:
        self._fn = fn

    async def analyze(self, task: VideoTask) -> AnalyzerResult:
        t0 = time.time()
        job = task.job
        if job.platform not in ("YouTube", ""):
            return AnalyzerResult(
                provider=self.name,
                ok=True,
                payload={"skipped": True, "reason": f"platform={job.platform} not supported by Gemini"},
                latency_ms=0,
            )
        try:
            result = await asyncio.wait_for(
                self._fn(job.url, job.title, job.handle),
                timeout=700.0,
            )
            ok = result.get("analyzed", False)
            return AnalyzerResult(
                provider=self.name,
                ok=ok,
                payload=result,
                error="" if ok else result.get("error", "not analyzed"),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except asyncio.TimeoutError:
            return AnalyzerResult(
                provider=self.name,
                ok=False,
                error="Gemini File API timed out (700s limit) - worker slot released",
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return AnalyzerResult(
                provider=self.name,
                ok=False,
                error=str(e),
                latency_ms=int((time.time() - t0) * 1000),
            )


class ClaudeAnalyzer(BaseAnalyzer):
    name = "claude"

    def __init__(self, fn: Callable) -> None:
        self._fn = fn

    async def analyze(self, task: VideoTask) -> AnalyzerResult:
        t0 = time.time()
        job = task.job
        try:
            result = await asyncio.to_thread(
                self._fn,
                job.title,
                job.caption,
                job.url,
                job.platform,
                job.scraped_text,
                job.og_image,
            )
            ok = result.get("analyzed", False)
            return AnalyzerResult(
                provider=self.name,
                ok=ok,
                payload=result,
                error="" if ok else result.get("error", "not analyzed"),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return AnalyzerResult(
                provider=self.name,
                ok=False,
                error=str(e),
                latency_ms=int((time.time() - t0) * 1000),
            )


@dataclass
class AnalyzerPlan:
    prefilter: Optional[BaseAnalyzer]
    main_analyzers: List[BaseAnalyzer]


class AnalyzerPolicy:
    def __init__(
        self,
        gpt_analyzer: GPTPrefilterAnalyzer,
        gemini_analyzer: GeminiAnalyzer,
        claude_analyzer: ClaudeAnalyzer,
    ) -> None:
        self.gpt = gpt_analyzer
        self.gemini = gemini_analyzer
        self.claude = claude_analyzer

    def get_plan(self, task: VideoTask) -> AnalyzerPlan:
        platform = task.job.platform
        profile = task.job.profile
        has_no_text = not task.job.title.strip() and not task.job.caption.strip()
        is_upload = (
            platform == "Uploaded Video"
            or (task.job.uploaded_video and task.job.uploaded_video.get("path"))
        )
        skip_prefilter = is_upload or has_no_text
        if profile == "economy":
            return AnalyzerPlan(prefilter=self.gpt, main_analyzers=[self.claude])
        if profile == "premium" or skip_prefilter:
            return AnalyzerPlan(prefilter=None, main_analyzers=[self.gpt, self.gemini, self.claude])
        return AnalyzerPlan(prefilter=self.gpt, main_analyzers=[self.gpt, self.gemini, self.claude])
