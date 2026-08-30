"""Injected runtime for YouTube Gemini direct and File API execution paths.

The File API slow path (download → upload → analyze → cleanup) lives verbatim
in the sibling module ``gemini_video_youtube_fileapi``; this class keeps a thin
``run_file_api_path`` delegate so its call surface is unchanged.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.ai.analyzers import gemini_video_youtube_fileapi as _fileapi


def new_youtube_result() -> dict[str, Any]:
    return {
        "analyzed": False,
        "method": "gemini_youtube",
        "content_summary": "",
        "content_genre": "",
        "content_topic": "",
        "timestamps": [],
        "competitor_mentions": [],
        "why_compelling": "",
        "hook_analysis": "",
        "target_audience": "",
        "production_quality": "",
        "camera_body": None,
        "viltrox_lens": None,
        "other_lens": None,
        "viltrox_detected": False,
        "viltrox_products_all": [],
        "marketing_potential": "",
        "marketing_notes": "",
        "llm_attempts": [],
        "cost_authority": "llm_production_google_generate_content_v1",
        "error": None,
    }


@dataclass(frozen=True)
class YouTubeRuntimeHooks:
    client: Any
    genai_types: Any
    logger: Any
    provider_pressure_exhausted: type[Exception]
    final_v1_cache_config: Callable[..., Any]
    generate_json_with_recovery: Callable[..., Any]
    is_provider_pressure_error: Callable[..., bool]
    mark_attempt_failed: Callable[..., Any]
    retry_after_context_cache_error: Callable[..., bool]
    stamp_analyzer_model_identity: Callable[..., Any]
    stage_add: Callable[..., int]
    video_generate_config: Callable[..., Any]
    apply_final_v1_result: Callable[..., Any]
    apply_v2_result: Callable[..., Any]
    apply_legacy_result: Callable[..., Any]
    should_switch_model: Callable[[Exception], bool]
    build_prompts: Callable[..., tuple[str, str]]
    canonical_youtube_url: Callable[[str], tuple[str, str]]
    redact_secrets: Callable[..., str]
    subprocess_run: Callable[..., Any]
    path_exists: Callable[[str], bool]
    path_getsize: Callable[[str], int]
    path_join: Callable[[str, str], str]
    sys_executable: str
    ytdlp_proxy: str
    ytdlp_max_height: Callable[[], int]
    ytdlp_download_format: Callable[[int], str]
    ytdlp_cookies_args: Callable[[], list[str]]
    download_timeout_seconds: int
    subtitle_grace_seconds: float
    subtitle_slow_path_wait_seconds: float


@dataclass
class YouTubeAnalysisRuntime:
    result: dict[str, Any]
    url: str
    title: str
    profile_ctx: str
    schema_key: str
    performance_context: dict[str, Any] | None
    llm_context: dict[str, Any] | None
    models: list[str]
    attempt_total: int
    scope_passes: Callable[[str], bool]
    subtitles: Any
    hooks: YouTubeRuntimeHooks
    subtitle_raw: str = ""
    gemini_file: Any = None
    diagnostics: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.diagnostics = self.result.setdefault("diagnostics", {})

    @property
    def is_v2(self) -> bool:
        return self.schema_key == "v2"

    @property
    def is_final_v1(self) -> bool:
        return self.schema_key == "final_v1"

    def collect_subtitles(self, timeout_seconds: float) -> str:
        if not self.subtitles.done:
            wait_started = time.monotonic()
            self.subtitle_raw = self.subtitles.collect(timeout_seconds)
            self.hooks.stage_add(self.result, "subtitles", wait_started)
        else:
            self.subtitle_raw = self.subtitles.text
        self.result["subtitle_chars"] = len(self.subtitle_raw or "")
        return self.subtitle_raw

    def finish_subtitle_diagnostics(self) -> None:
        timings = (
            self.result.get("stage_timings_ms")
            if isinstance(self.result.get("stage_timings_ms"), dict)
            else {}
        )
        if self.subtitles.done:
            timings["subtitles"] = max(
                int(timings.get("subtitles") or 0),
                int(self.subtitles.elapsed_ms),
            )
            self.result["stage_timings_ms"] = timings
        elif "subtitles" not in timings:
            timings["subtitles"] = 0
            self.result["stage_timings_ms"] = timings
        self.result["subtitle_chars"] = len(self.subtitle_raw or "")
        self.diagnostics["subtitles"] = self.subtitles.diagnostics(
            used=bool(self.subtitle_raw)
        )

    def prompts(self) -> tuple[str, str]:
        return self.hooks.build_prompts(
            self.schema_key,
            title=self.title,
            profile_ctx=self.profile_ctx,
            subtitle_raw=self.subtitle_raw,
            performance_context=self.performance_context,
        )

    def apply_parsed(
        self,
        parsed: dict[str, Any],
        usage_metadata: dict[str, Any],
        *,
        method_prefix: str,
        model_name: str,
    ) -> None:
        method = f"{method_prefix}_{model_name}"
        self.hooks.stamp_analyzer_model_identity(
            self.result,
            self.models,
            model_name,
            self.diagnostics,
        )
        if self.is_final_v1:
            self.hooks.apply_final_v1_result(
                self.result,
                parsed,
                method=method,
                model=model_name,
                usage_metadata=usage_metadata,
                subtitle_used=bool(self.subtitle_raw),
            )
            self.hooks.logger.info(
                f"{method_prefix}_final_v1_success",
                extra={
                    "model": model_name,
                    "timestamps": len(self.result.get("timestamps") or []),
                },
            )
            return
        if self.is_v2:
            self.hooks.apply_v2_result(
                self.result,
                parsed,
                method=method,
                model=model_name,
                usage_metadata=usage_metadata,
                subtitle_used=bool(self.subtitle_raw),
            )
            self.hooks.logger.info(
                f"{method_prefix}_v2_success",
                extra={
                    "model": model_name,
                    "timestamps": len(self.result.get("timestamps") or []),
                },
            )
            return
        scored = self.hooks.apply_legacy_result(
            self.result,
            parsed,
            method=method,
            model=model_name,
            usage_metadata=usage_metadata,
        )
        self.hooks.logger.info(
            f"{method_prefix}_success",
            extra={
                "model": model_name,
                "genre": scored["genre"],
                "vertical": scored["vertical"],
                "timestamps": len(self.result["timestamps"]),
                "brand_score": scored["ws"]["brand_exposure_score"],
                "story_score": scored["ws"]["storytelling_score"],
            },
        )

    async def run(self) -> dict[str, Any]:
        if "youtu.be" in self.url or "youtube.com" in self.url:
            if await self.run_direct_path():
                return self.result
        await self.run_file_api_path()
        return self.result

    async def run_direct_path(self) -> bool:
        direct_url, direct_video_id = self.hooks.canonical_youtube_url(self.url)
        direct_diag: dict[str, Any] = {
            "attempted": True,
            "success": False,
            "attempts": [],
            "fallback_reason": "",
            "url": direct_url,
            "url_canonicalized": bool(direct_video_id) and direct_url != self.url,
        }
        self.result["youtube_direct"] = direct_diag
        try:
            self.hooks.logger.info(
                "gemini_fast_path_start",
                extra={
                    "url": direct_url,
                    "canonicalized": direct_diag["url_canonicalized"],
                },
            )
            done, succeeded, fast_path_err = await self._run_direct_model_chain(
                direct_url,
                direct_diag,
            )
            if done:
                direct_diag["success"] = succeeded
                self.finish_subtitle_diagnostics()
                return True
            if fast_path_err and self.hooks.is_provider_pressure_error(fast_path_err):
                direct_diag["fallback_reason"] = f"provider_pressure: {fast_path_err}"
                self.hooks.logger.warning(
                    "gemini_fast_path_provider_pressure_abort",
                    extra={"error": fast_path_err[:120]},
                )
                raise self.hooks.provider_pressure_exhausted(
                    f"provider_pressure(all models tried): {fast_path_err}"
                )
            direct_diag["fallback_reason"] = fast_path_err or "no_model_succeeded"
            self.hooks.logger.warning("gemini_fast_path_fallback_to_download")
        except self.hooks.provider_pressure_exhausted:
            self.finish_subtitle_diagnostics()
            raise
        except Exception as fast_err:
            direct_diag["fallback_reason"] = (
                f"fast_path_exception: {str(fast_err)[:200]}"
            )
            self.hooks.logger.warning(
                "gemini_fast_path_exception",
                extra={"error": str(fast_err)},
            )
        return False

    async def _run_direct_model_chain(
        self,
        direct_url: str,
        direct_diag: dict[str, Any],
    ) -> tuple[bool, bool, str | None]:
        direct_plan = list(enumerate(self.models, start=1))
        direct_cache_retried: set[str] = set()
        direct_pos = 0
        fast_path_err: str | None = None
        while direct_pos < len(direct_plan):
            attempt_index, model_name = direct_plan[direct_pos]
            direct_pos += 1
            if not self.scope_passes("youtube_direct_attempt"):
                return True, False, fast_path_err
            attempt_started = time.monotonic()
            cache_info: dict[str, Any] = {}
            try:
                await self._direct_attempt(
                    model_name,
                    attempt_index,
                    direct_url,
                    direct_diag,
                    cache_info,
                    attempt_started,
                )
                return True, True, fast_path_err
            except Exception as error:
                self.hooks.mark_attempt_failed(self.diagnostics)
                fast_path_err = str(error)[:200]
                self._record_direct_failure(
                    direct_diag,
                    model_name,
                    fast_path_err,
                    attempt_started,
                )
                self.hooks.logger.warning(
                    "gemini_fast_path_model_failed",
                    extra={"model": model_name, "error": fast_path_err[:80]},
                )
                if self.hooks.retry_after_context_cache_error(
                    error,
                    cache_info,
                    model_name,
                    direct_cache_retried,
                ):
                    direct_plan.insert(direct_pos, (attempt_index, model_name))
                    continue
                if not self.hooks.should_switch_model(error):
                    direct_diag["chain_stop_reason"] = (
                        f"{type(error).__name__}: {fast_path_err[:120]}"
                    )
                    break
        return False, False, fast_path_err

    async def _direct_attempt(
        self,
        model_name: str,
        attempt_index: int,
        direct_url: str,
        direct_diag: dict[str, Any],
        cache_info: dict[str, Any],
        attempt_started: float,
    ) -> None:
        cache_config = None
        if self.is_final_v1:
            cache_setup_started = time.monotonic()
            cache_config, fetched_info = self.hooks.final_v1_cache_config(model_name)
            cache_info.update(fetched_info)
            self.hooks.stage_add(self.result, "cache_setup", cache_setup_started)
        self.collect_subtitles(self.hooks.subtitle_grace_seconds)
        prompt, final_full_prompt = self.prompts()
        request_prompt = (
            final_full_prompt if self.is_final_v1 and not cache_config else prompt
        )
        request_config = self.hooks.video_generate_config(model_name, cache_config)

        def analyze_direct() -> Any:
            contents = [
                self.hooks.genai_types.Part(
                    file_data=self.hooks.genai_types.FileData(file_uri=direct_url)
                ),
                request_prompt,
            ]
            return self.hooks.generate_json_with_recovery(
                model_name=model_name,
                contents=contents,
                config=request_config,
                prompt=request_prompt,
                performance_context=self.performance_context,
                llm_context=self.llm_context,
                subphase="youtube_uri_fast_generation",
                attempt_index=attempt_index,
                attempt_total=self.attempt_total,
                attempt_log=self.result["llm_attempts"],
                diagnostics=self.diagnostics,
            )

        parsed, usage_metadata = await asyncio.to_thread(analyze_direct)
        direct_diag["attempts"].append(
            {
                "model": model_name,
                "ok": True,
                "error": "",
                "elapsed_ms": self.hooks.stage_add(
                    self.result,
                    "youtube_direct",
                    attempt_started,
                ),
            }
        )
        self.apply_parsed(
            parsed,
            usage_metadata,
            method_prefix="gemini_direct",
            model_name=model_name,
        )
        if self.is_final_v1:
            self.result["context_cache"] = cache_info

    def _record_direct_failure(
        self,
        direct_diag: dict[str, Any],
        model_name: str,
        fast_path_err: str,
        attempt_started: float,
    ) -> None:
        last_attempt = direct_diag["attempts"][-1] if direct_diag["attempts"] else None
        if (
            last_attempt
            and last_attempt.get("model") == model_name
            and last_attempt.get("ok")
        ):
            last_attempt["ok"] = False
            last_attempt["error"] = fast_path_err
            return
        direct_diag["attempts"].append(
            {
                "model": model_name,
                "ok": False,
                "error": fast_path_err,
                "elapsed_ms": self.hooks.stage_add(
                    self.result,
                    "youtube_direct",
                    attempt_started,
                ),
            }
        )

    async def run_file_api_path(self) -> None:
        await _fileapi.run_file_api_path(self)


__all__ = [
    "YouTubeAnalysisRuntime",
    "YouTubeRuntimeHooks",
    "new_youtube_result",
]
