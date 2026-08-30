"""Injected runtime for YouTube Gemini direct and File API execution paths."""
from __future__ import annotations

import asyncio
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable


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
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = self.hooks.path_join(tmpdir, "gemini_video.mp4")
                if not self.scope_passes("youtube_download"):
                    return
                downloaded_bytes = await self._download(tmp_path)
                if downloaded_bytes < 1000:
                    return
                file_size_mb = downloaded_bytes / 1024 / 1024
                self.hooks.logger.info(
                    "gemini_fileapi_upload_start",
                    extra={"size_mb": round(file_size_mb, 1)},
                )
                if not self.scope_passes("file_api_upload"):
                    return
                if not await self._upload(tmp_path):
                    return
                if not await self._wait_until_active():
                    return
                self.hooks.logger.info(
                    "gemini_fileapi_active",
                    extra={"uri": self.gemini_file.uri},
                )
                if not getattr(self.gemini_file, "uri", None):
                    self.result["error"] = (
                        "Gemini file ACTIVE but uri is empty — cannot call generate_content"
                    )
                    self.hooks.logger.warning(
                        "gemini_fileapi_empty_uri",
                        extra={"file_name": self.gemini_file.name},
                    )
                    return
                self.collect_subtitles(self.hooks.subtitle_slow_path_wait_seconds)
                await self._run_file_model_chain()
        except Exception as error:
            self.result["error"] = str(error)
            self.hooks.logger.exception("gemini_analysis_failed")
        finally:
            self.finish_subtitle_diagnostics()
            await self._cleanup_file()

    async def _download(self, tmp_path: str) -> int:
        self.hooks.logger.info(
            "gemini_fileapi_download_start",
            extra={"url": self.url},
        )
        download_max_height = self.hooks.ytdlp_max_height()
        command = [
            self.hooks.sys_executable,
            "-m",
            "yt_dlp",
            "-f",
            self.hooks.ytdlp_download_format(download_max_height),
            "--merge-output-format",
            "mp4",
            "-o",
            tmp_path,
            "--no-playlist",
            "--quiet",
        ]
        if self.hooks.ytdlp_proxy:
            command += ["--proxy", self.hooks.ytdlp_proxy]
        command += self.hooks.ytdlp_cookies_args()
        command.append(self.url)
        download_started = time.monotonic()
        process = await asyncio.to_thread(
            lambda: self.hooks.subprocess_run(
                command,
                capture_output=True,
                timeout=self.hooks.download_timeout_seconds,
            )
        )
        download_ms = self.hooks.stage_add(
            self.result,
            "download",
            download_started,
        )
        downloaded_bytes = (
            int(self.hooks.path_getsize(tmp_path))
            if self.hooks.path_exists(tmp_path)
            else 0
        )
        stderr_raw = getattr(process, "stderr", b"") if process is not None else b""
        if isinstance(stderr_raw, bytes):
            stderr_text = stderr_raw.decode("utf-8", errors="ignore")
        else:
            stderr_text = str(stderr_raw or "")
        stderr_text = self.hooks.redact_secrets(stderr_text, limit=600)
        self.result["download_diagnostics"] = {
            "tool": "yt-dlp",
            "returncode": (
                getattr(process, "returncode", None) if process is not None else None
            ),
            "elapsed_ms": download_ms,
            "bytes": downloaded_bytes,
            "proxy": bool(self.hooks.ytdlp_proxy),
            "max_height": download_max_height,
            "cookies": "--cookies" in command,
            "stderr_tail": stderr_text,
        }
        if downloaded_bytes < 1000:
            self.result["error"] = (
                "yt-dlp video download failed for Gemini analysis"
            )
            self.hooks.logger.warning(
                "gemini_fileapi_download_failed",
                extra={"url": self.url, "stderr_tail": stderr_text[-300:]},
            )
        return downloaded_bytes

    async def _upload(self, tmp_path: str) -> bool:
        upload_started = time.monotonic()
        try:
            self.gemini_file = await asyncio.to_thread(
                lambda: self.hooks.client.files.upload(
                    file=tmp_path,
                    config={"mime_type": "video/mp4"},
                )
            )
        except Exception as upload_error:
            self.hooks.stage_add(self.result, "upload", upload_started)
            self.result["error"] = (
                f"Gemini File API upload failed: {upload_error}"
            )
            self.hooks.logger.warning(
                "gemini_fileapi_upload_failed",
                extra={"error": str(upload_error)},
            )
            return False
        self.hooks.stage_add(self.result, "upload", upload_started)
        if not self.gemini_file or not getattr(self.gemini_file, "name", None):
            self.result["error"] = "Gemini upload returned empty file object"
            self.hooks.logger.warning(
                "gemini_fileapi_upload_invalid_file",
                extra={"file": str(self.gemini_file)},
            )
            return False
        self.hooks.logger.info(
            "gemini_fileapi_upload_complete",
            extra={
                "file_name": self.gemini_file.name,
                "uri": self.gemini_file.uri,
            },
        )
        return True

    async def _wait_until_active(self) -> bool:
        active_wait_started = time.monotonic()
        state = ""
        for poll_attempt in range(30):
            try:
                self.gemini_file = await asyncio.to_thread(
                    lambda: self.hooks.client.files.get(name=self.gemini_file.name)
                )
            except Exception as poll_error:
                self.hooks.stage_add(
                    self.result,
                    "file_active_wait",
                    active_wait_started,
                )
                self.result["error"] = (
                    "files.get() 404 during polling — upload may have failed: "
                    f"{poll_error}"
                )
                self.hooks.logger.warning(
                    "gemini_fileapi_poll_error",
                    extra={
                        "attempt": poll_attempt,
                        "error": str(poll_error),
                    },
                )
                return False
            state = getattr(
                self.gemini_file.state,
                "name",
                str(self.gemini_file.state),
            )
            self.hooks.logger.info(
                "gemini_fileapi_poll",
                extra={"attempt": poll_attempt + 1, "state": state},
            )
            if state == "ACTIVE":
                break
            if state == "FAILED":
                self.hooks.stage_add(
                    self.result,
                    "file_active_wait",
                    active_wait_started,
                )
                self.result["error"] = (
                    f"Gemini file processing FAILED (state={state}). Try re-uploading."
                )
                self.hooks.logger.warning(
                    "gemini_fileapi_processing_failed",
                    extra={"attempt": poll_attempt + 1, "state": state},
                )
                return False
            await asyncio.sleep(3)
        else:
            self.hooks.stage_add(
                self.result,
                "file_active_wait",
                active_wait_started,
            )
            self.result["error"] = (
                f"Gemini file ACTIVE timeout after 90s (final state={state})"
            )
            self.hooks.logger.warning(
                "gemini_fileapi_poll_timeout",
                extra={"state": state},
            )
            return False
        self.hooks.stage_add(
            self.result,
            "file_active_wait",
            active_wait_started,
        )
        return True

    async def _run_file_model_chain(self) -> None:
        last_err = ""
        file_plan = list(enumerate(self.models, start=1))
        file_cache_retried: set[str] = set()
        file_pos = 0
        while file_pos < len(file_plan):
            model_offset, model_name = file_plan[file_pos]
            file_pos += 1
            if not self.scope_passes("file_api_attempt"):
                return
            attempt_started = time.monotonic()
            cache_info: dict[str, Any] = {}
            try:
                await self._file_attempt(
                    model_name,
                    model_offset,
                    cache_info,
                    attempt_started,
                )
                break
            except Exception as error:
                self.hooks.stage_add(self.result, "generation", attempt_started)
                self.hooks.mark_attempt_failed(self.diagnostics)
                last_err = str(error)
                self.hooks.logger.warning(
                    "gemini_fileapi_model_failed",
                    extra={
                        "model": model_name,
                        "error": str(error)[:80],
                        "traceback_tail": traceback.format_exc()[-500:],
                    },
                )
                if self.hooks.retry_after_context_cache_error(
                    error,
                    cache_info,
                    model_name,
                    file_cache_retried,
                ):
                    file_plan.insert(file_pos, (model_offset, model_name))
                    continue
                if not self.hooks.should_switch_model(error):
                    self.diagnostics["chain_stop_reason"] = (
                        f"{type(error).__name__}: {last_err[:120]}"
                    )
                    break
        if not self.result["analyzed"]:
            self.result["error"] = last_err

    async def _file_attempt(
        self,
        model_name: str,
        model_offset: int,
        cache_info: dict[str, Any],
        attempt_started: float,
    ) -> None:
        cache_config = None
        if self.is_final_v1:
            cache_setup_started = time.monotonic()
            cache_config, fetched_info = self.hooks.final_v1_cache_config(model_name)
            cache_info.update(fetched_info)
            self.hooks.stage_add(self.result, "cache_setup", cache_setup_started)
        prompt, final_full_prompt = self.prompts()
        request_prompt = (
            final_full_prompt if self.is_final_v1 and not cache_config else prompt
        )
        request_config = self.hooks.video_generate_config(model_name, cache_config)

        def analyze_file() -> Any:
            contents = [
                self.hooks.genai_types.Part.from_uri(
                    file_uri=self.gemini_file.uri,
                    mime_type="video/mp4",
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
                subphase="youtube_file_fallback_generation",
                attempt_index=len(self.models) + model_offset,
                attempt_total=self.attempt_total,
                attempt_log=self.result["llm_attempts"],
                diagnostics=self.diagnostics,
            )

        parsed, usage_metadata = await asyncio.to_thread(analyze_file)
        self.hooks.stage_add(self.result, "generation", attempt_started)
        self.apply_parsed(
            parsed,
            usage_metadata,
            method_prefix="gemini_fileapi",
            model_name=model_name,
        )
        if self.is_final_v1:
            self.result["context_cache"] = cache_info

    async def _cleanup_file(self) -> None:
        file_to_delete = (
            getattr(self.gemini_file, "name", None) if self.gemini_file else None
        )
        if not file_to_delete:
            return
        cleanup_started = time.monotonic()
        try:
            await asyncio.to_thread(
                lambda: self.hooks.client.files.delete(name=file_to_delete)
            )
            self.hooks.logger.info(
                "gemini_fileapi_deleted",
                extra={"file_name": file_to_delete},
            )
        except Exception as delete_error:
            self.hooks.logger.warning(
                "gemini_fileapi_delete_skipped",
                extra={"error": str(delete_error)},
            )
        self.hooks.stage_add(self.result, "cleanup", cleanup_started)


__all__ = [
    "YouTubeAnalysisRuntime",
    "YouTubeRuntimeHooks",
    "new_youtube_result",
]
