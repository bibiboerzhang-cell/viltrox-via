"""
services/ai/analyzers/gemini_video_youtube.py — analyze_youtube_with_gemini 整簇

从 gemini_video.py 搬出。模块级常量/类/纯函数（ProviderPressureExhausted /
_is_provider_pressure_error / final_v1_gemini_models / _final_v1_cache_config /
_generate_json_with_recovery）仍住 gemini_video.py，本模块用函数内 lazy import 取用以避免
循环依赖（gemini_video.py 在加载时 re-export 本函数）。

优化波 B(2026-08-23):
* F2 字幕抓取(yt-dlp ~3.5s)改为守护线程并行,直链首次 Gemini 调用前最多等
  ``GEMINI_VIDEO_SUBTITLE_GRACE_SEC``(默认 1.5s),晚到不等;慢路(下载+File API)天然晚,
  等 ``GEMINI_VIDEO_SUBTITLE_SLOW_PATH_WAIT_SEC``(默认 10s)。
* F1 截断续写 / C10 SDK 重试账 / C4 链换节判据全部经 gemini_video._generate_json_with_recovery
  与 gemini_video_recovery.should_switch_model,三条生成路径口径一致。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from app.core.gemini_models import DEFAULT_VIDEO_GEMINI_MODEL
from app.core.logging import get_logger
from app.domains.costs.budget_guard_errors import redact_secrets
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

from app.services.scraping.ytdlp import YTDLP_AVAILABLE, YTDLP_PROXY, fetch_youtube_subtitles
from app.services.scoring.creator import get_creator_profile

from app.services.ai.analyzers.gemini_video_results import (
    _apply_final_v1_result,
    _apply_v2_result,
)
from app.services.ai.analyzers.gemini_video_legacy_result import _apply_legacy_result
from app.services.ai.analyzers.gemini_video_recovery import should_switch_model
from app.services.ai.analyzers.gemini_video_prompts import (
    _video_final_v1_dynamic_prompt,
    _video_final_v1_prompt,
    _video_v2_prompt,
)
from app.services.ai.analyzers.gemini_video_legacy_prompt import _video_legacy_prompt
from app.services.ai.analyzers.gemini_video_youtube_runtime import (
    YouTubeAnalysisRuntime,
    YouTubeRuntimeHooks,
    new_youtube_result,
)

logger = get_logger(__name__)

GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)
_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PATH_ROUTES = frozenset({"shorts", "embed", "live", "v"})


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default)) or default))
    except ValueError:
        return default


# F2:直链首次生成前最多等字幕这么久(0 = 从不等);慢路上传完后的等待上限。
GEMINI_VIDEO_SUBTITLE_GRACE_SECONDS = _env_float("GEMINI_VIDEO_SUBTITLE_GRACE_SEC", 1.5)
GEMINI_VIDEO_SUBTITLE_SLOW_PATH_WAIT_SECONDS = _env_float("GEMINI_VIDEO_SUBTITLE_SLOW_PATH_WAIT_SEC", 10.0)


class _SubtitleFetch:
    """守护线程里跑 fetch_youtube_subtitles;``collect(timeout)`` 有界等待,晚到不等。

    通过模块全局名解析 ``fetch_youtube_subtitles``(worker 子进程 skip_subtitles 补丁与单测
    monkeypatch 都打在模块属性上)。不用 asyncio 线程池:asyncio.run 退出时会 join 默认
    executor,子进程就得白等 yt-dlp 结束才能把结果打给父进程。
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.text = ""
        self.elapsed_ms = 0
        self.error = ""
        self._done = threading.Event()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="gemini-youtube-subtitles", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.text = str(fetch_youtube_subtitles(self.url) or "")
        except Exception as exc:  # 字幕永远是锦上添花,失败不影响分析
            self.error = f"{type(exc).__name__}: {str(exc)[:120]}"
            self.text = ""
        finally:
            self.elapsed_ms = max(0, int((time.monotonic() - self._started) * 1000))
            self._done.set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    def collect(self, timeout_seconds: float) -> str:
        """等到 ``timeout_seconds``(自抓取启动起算)为止;到点没来就返回空串。"""

        remaining = max(0.0, float(timeout_seconds) - (time.monotonic() - self._started))
        if not self._done.is_set() and remaining > 0:
            self._done.wait(remaining)
        return self.text if self._done.is_set() else ""

    def diagnostics(self, *, used: bool) -> dict[str, Any]:
        if not self._done.is_set():
            # 收尾只给 50ms 余量(秒级抓取不等;瞬时完成的抓取不漏记)
            self._done.wait(0.05)
        return {
            "parallel": True,
            "done": self.done,
            "elapsed_ms": self.elapsed_ms if self.done else max(0, int((time.monotonic() - self._started) * 1000)),
            "chars": len(self.text) if self.done else 0,
            "used": bool(used),
            "error": self.error,
        }


def _subtitle_context(subtitle_raw: str) -> str:
    if not subtitle_raw:
        return ""
    return (
        "\n\n=== 字幕时间轴（真实时间戳，优先用这个定位事件）===\n"
        + subtitle_raw
        + "\n=== 字幕结束 ===\n"
        "时间戳规则：timestamps 里的 time 字段必须来自上面字幕里的真实时间点，"
        "不允许猜测或等间隔填写。"
    )


def _build_prompts(
    schema_key: str,
    *,
    title: str,
    profile_ctx: str,
    subtitle_raw: str,
    performance_context: dict[str, Any] | None,
) -> tuple[str, str]:
    """按当下拿到的字幕构造 (prompt, final_full_prompt);final_full_prompt 只对 final_v1 非空。"""

    subtitle_ctx = _subtitle_context(subtitle_raw)
    if schema_key == "final_v1":
        prompt = _video_final_v1_dynamic_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
        full = _video_final_v1_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_used=bool(subtitle_raw),
            performance_context=performance_context,
        )
        return prompt, full
    if schema_key == "v2":
        return (
            _video_v2_prompt(
                title=title,
                profile_ctx=profile_ctx,
                subtitle_ctx=subtitle_ctx,
                subtitle_used=bool(subtitle_raw),
                performance_context=performance_context,
            ),
            "",
        )
    return (
        _video_legacy_prompt(
            title=title,
            profile_ctx=profile_ctx,
            subtitle_ctx=subtitle_ctx,
            subtitle_raw=subtitle_raw,
        ),
        "",
    )


def _ytdlp_max_height() -> int:
    """慢路下载分辨率上限(默认 720 行为不变)。Gemini 视频帧按固定 token/帧降采样,480p 对分析输入
    几乎无差而下载+File API 上传字节量 ~2-3x 更小;是否降档由运维用 env 决定,代码默认不动。"""

    try:
        value = int(os.environ.get("GEMINI_VIDEO_YTDLP_MAX_HEIGHT", "720") or "720")
    except ValueError:
        value = 720
    return max(240, min(1080, value))


def _ytdlp_download_format(max_height: int) -> str:
    return f"best[ext=mp4][height<={max_height}]/18/best[height<={max_height}]/best"


def _ytdlp_cookies_args() -> list[str]:
    """YTDLP_COOKIES_FILE 指向存在的 cookies.txt 时透传 --cookies(治「Sign in to confirm you're
    not a bot」类下载失败);未设/文件不存在 → 空,行为不变。"""

    path = str(os.environ.get("YTDLP_COOKIES_FILE", "") or "").strip()
    if path and os.path.isfile(path):
        return ["--cookies", path]
    return []


def canonical_youtube_url(url: str) -> tuple[str, str]:
    """把任意形态的 YouTube 链接规范成 ``https://www.youtube.com/watch?v=<id>``。

    剖面坐实(2026-08,隔离库 vkpi_llm_calls):直链 33 次失败里 18 次是带 ``&t=314s`` /
    ``&pp=ygU...`` 等附加参数的 watch 链接,Gemini file_uri 约 2.5s 秒拒;成功的 47 次零带参。
    秒拒后整条任务掉进 yt-dlp 下载 + File API 慢路(p50 98s→269s,或干脆下载失败)。
    返回 ``(canonical_url, video_id)``;解析不出 11 位 id 时原样返回 ``(url, "")``,不改行为。
    """

    raw = str(url or "").strip()
    if not raw:
        return raw, ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw, ""
    host = (parsed.hostname or "").lower().rstrip(".")
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    parts = [part for part in parsed.path.split("/") if part]
    lowered = [part.lower() for part in parts]
    video_id = ""
    if host == "youtu.be" and parts:
        video_id = parts[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if lowered[:1] == ["watch"]:
            video_id = str((parse_qs(parsed.query).get("v") or [""])[0] or "")
        elif len(parts) >= 2 and lowered[0] in _YOUTUBE_PATH_ROUTES:
            video_id = parts[1]
    video_id = video_id.strip()
    if not _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        return raw, ""
    return f"https://www.youtube.com/watch?v={video_id}", video_id


async def analyze_youtube_with_gemini(
    url: str,
    title: str,
    creator_handle: str = "",
    *,
    schema_version: str = "legacy",
    performance_context: dict[str, Any] | None = None,
    final_v1_models: list[str] | str | None = None,
    models: list[str] | str | None = None,
    llm_context: dict[str, Any] | None = None,
    authorization_checkpoint: Callable[[str], None] | None = None,
) -> dict:
    """Analyze a YouTube video through the direct URI and File API paths.

    Public module dependencies are captured at call time so existing worker
    patches for provider availability, subtitles, yt-dlp, files and path
    probes remain authoritative.
    """
    from app.services.ai.analyzers.gemini_video import (
        ProviderPressureExhausted,
        _final_v1_cache_config,
        _generate_json_with_recovery,
        _is_provider_pressure_error,
        _mark_attempt_failed,
        _retry_after_context_cache_error,
        _scope_guard,
        _stamp_analyzer_model_identity,
        _stage_add,
        _video_generate_config,
        final_v1_gemini_models,
    )

    result = new_youtube_result()
    if not GEMINI_AVAILABLE or not gemini_client:
        result["error"] = "Gemini not available"
        return result
    if not YTDLP_AVAILABLE:
        result["error"] = "yt-dlp not available for download"
        return result

    profile_ctx = ""
    if creator_handle:
        profile = get_creator_profile(creator_handle)
        if profile.get("viltrox_lenses"):
            profile_ctx = (
                f"\n创作者历史使用过: {', '.join(profile['viltrox_lenses'][:3])}"
            )

    scope_passes = _scope_guard(result, authorization_checkpoint)
    if not scope_passes("youtube_subtitles"):
        return result
    subtitles = _SubtitleFetch(url)
    schema_key = str(schema_version or "").strip().lower()
    is_final_v1 = schema_key == "final_v1"
    gemini_models = [DEFAULT_VIDEO_GEMINI_MODEL]
    if models is not None:
        gemini_models = final_v1_gemini_models(models)
    elif is_final_v1:
        gemini_models = final_v1_gemini_models(final_v1_models)
    attempt_total = len(gemini_models) * (
        2 if "youtu.be" in url or "youtube.com" in url else 1
    )

    hooks = YouTubeRuntimeHooks(
        client=gemini_client,
        genai_types=genai_types,
        logger=logger,
        provider_pressure_exhausted=ProviderPressureExhausted,
        final_v1_cache_config=_final_v1_cache_config,
        generate_json_with_recovery=_generate_json_with_recovery,
        is_provider_pressure_error=_is_provider_pressure_error,
        mark_attempt_failed=_mark_attempt_failed,
        retry_after_context_cache_error=_retry_after_context_cache_error,
        stamp_analyzer_model_identity=_stamp_analyzer_model_identity,
        stage_add=_stage_add,
        video_generate_config=_video_generate_config,
        apply_final_v1_result=_apply_final_v1_result,
        apply_v2_result=_apply_v2_result,
        apply_legacy_result=_apply_legacy_result,
        should_switch_model=should_switch_model,
        build_prompts=_build_prompts,
        canonical_youtube_url=canonical_youtube_url,
        redact_secrets=redact_secrets,
        subprocess_run=subprocess.run,
        path_exists=os.path.exists,
        path_getsize=os.path.getsize,
        path_join=os.path.join,
        sys_executable=sys.executable,
        ytdlp_proxy=YTDLP_PROXY,
        ytdlp_max_height=_ytdlp_max_height,
        ytdlp_download_format=_ytdlp_download_format,
        ytdlp_cookies_args=_ytdlp_cookies_args,
        download_timeout_seconds=GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS,
        subtitle_grace_seconds=GEMINI_VIDEO_SUBTITLE_GRACE_SECONDS,
        subtitle_slow_path_wait_seconds=GEMINI_VIDEO_SUBTITLE_SLOW_PATH_WAIT_SECONDS,
    )
    runtime = YouTubeAnalysisRuntime(
        result=result,
        url=url,
        title=title,
        profile_ctx=profile_ctx,
        schema_key=schema_key,
        performance_context=performance_context,
        llm_context=llm_context,
        models=gemini_models,
        attempt_total=attempt_total,
        scope_passes=scope_passes,
        subtitles=subtitles,
        hooks=hooks,
    )
    return await runtime.run()
