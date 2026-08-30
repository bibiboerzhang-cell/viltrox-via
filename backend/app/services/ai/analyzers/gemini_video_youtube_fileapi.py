"""File API slow path for the YouTube Gemini runtime (download → upload → analyze).

Extracted verbatim from ``gemini_video_youtube_runtime.YouTubeAnalysisRuntime``
(class-LOC 682→≤400 ratchet wave). Every function receives the live runtime and
mutates its ``result``/``diagnostics`` exactly as the former methods did; the
runtime keeps a thin ``run_file_api_path`` delegate so callers see no change.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
import traceback
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    from app.services.ai.analyzers.gemini_video_youtube_runtime import (
        YouTubeAnalysisRuntime,
    )


async def run_file_api_path(runtime: "YouTubeAnalysisRuntime") -> None:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = runtime.hooks.path_join(tmpdir, "gemini_video.mp4")
            if not runtime.scope_passes("youtube_download"):
                return
            downloaded_bytes = await _download(runtime, tmp_path)
            if downloaded_bytes < 1000:
                return
            file_size_mb = downloaded_bytes / 1024 / 1024
            runtime.hooks.logger.info(
                "gemini_fileapi_upload_start",
                extra={"size_mb": round(file_size_mb, 1)},
            )
            if not runtime.scope_passes("file_api_upload"):
                return
            if not await _upload(runtime, tmp_path):
                return
            if not await _wait_until_active(runtime):
                return
            runtime.hooks.logger.info(
                "gemini_fileapi_active",
                extra={"uri": runtime.gemini_file.uri},
            )
            if not getattr(runtime.gemini_file, "uri", None):
                runtime.result["error"] = (
                    "Gemini file ACTIVE but uri is empty — cannot call generate_content"
                )
                runtime.hooks.logger.warning(
                    "gemini_fileapi_empty_uri",
                    extra={"file_name": runtime.gemini_file.name},
                )
                return
            runtime.collect_subtitles(runtime.hooks.subtitle_slow_path_wait_seconds)
            await _run_file_model_chain(runtime)
    except Exception as error:
        runtime.result["error"] = str(error)
        runtime.hooks.logger.exception("gemini_analysis_failed")
    finally:
        runtime.finish_subtitle_diagnostics()
        await _cleanup_file(runtime)


async def _download(runtime: "YouTubeAnalysisRuntime", tmp_path: str) -> int:
    runtime.hooks.logger.info(
        "gemini_fileapi_download_start",
        extra={"url": runtime.url},
    )
    download_max_height = runtime.hooks.ytdlp_max_height()
    command = [
        runtime.hooks.sys_executable,
        "-m",
        "yt_dlp",
        "-f",
        runtime.hooks.ytdlp_download_format(download_max_height),
        "--merge-output-format",
        "mp4",
        "-o",
        tmp_path,
        "--no-playlist",
        "--quiet",
    ]
    if runtime.hooks.ytdlp_proxy:
        command += ["--proxy", runtime.hooks.ytdlp_proxy]
    command += runtime.hooks.ytdlp_cookies_args()
    command.append(runtime.url)
    download_started = time.monotonic()
    process = await asyncio.to_thread(
        lambda: runtime.hooks.subprocess_run(
            command,
            capture_output=True,
            timeout=runtime.hooks.download_timeout_seconds,
        )
    )
    download_ms = runtime.hooks.stage_add(
        runtime.result,
        "download",
        download_started,
    )
    downloaded_bytes = (
        int(runtime.hooks.path_getsize(tmp_path))
        if runtime.hooks.path_exists(tmp_path)
        else 0
    )
    stderr_raw = getattr(process, "stderr", b"") if process is not None else b""
    if isinstance(stderr_raw, bytes):
        stderr_text = stderr_raw.decode("utf-8", errors="ignore")
    else:
        stderr_text = str(stderr_raw or "")
    stderr_text = runtime.hooks.redact_secrets(stderr_text, limit=600)
    runtime.result["download_diagnostics"] = {
        "tool": "yt-dlp",
        "returncode": (
            getattr(process, "returncode", None) if process is not None else None
        ),
        "elapsed_ms": download_ms,
        "bytes": downloaded_bytes,
        "proxy": bool(runtime.hooks.ytdlp_proxy),
        "max_height": download_max_height,
        "cookies": "--cookies" in command,
        "stderr_tail": stderr_text,
    }
    if downloaded_bytes < 1000:
        runtime.result["error"] = (
            "yt-dlp video download failed for Gemini analysis"
        )
        runtime.hooks.logger.warning(
            "gemini_fileapi_download_failed",
            extra={"url": runtime.url, "stderr_tail": stderr_text[-300:]},
        )
    return downloaded_bytes


async def _upload(runtime: "YouTubeAnalysisRuntime", tmp_path: str) -> bool:
    upload_started = time.monotonic()
    try:
        runtime.gemini_file = await asyncio.to_thread(
            lambda: runtime.hooks.client.files.upload(
                file=tmp_path,
                config={"mime_type": "video/mp4"},
            )
        )
    except Exception as upload_error:
        runtime.hooks.stage_add(runtime.result, "upload", upload_started)
        runtime.result["error"] = (
            f"Gemini File API upload failed: {upload_error}"
        )
        runtime.hooks.logger.warning(
            "gemini_fileapi_upload_failed",
            extra={"error": str(upload_error)},
        )
        return False
    runtime.hooks.stage_add(runtime.result, "upload", upload_started)
    if not runtime.gemini_file or not getattr(runtime.gemini_file, "name", None):
        runtime.result["error"] = "Gemini upload returned empty file object"
        runtime.hooks.logger.warning(
            "gemini_fileapi_upload_invalid_file",
            extra={"file": str(runtime.gemini_file)},
        )
        return False
    runtime.hooks.logger.info(
        "gemini_fileapi_upload_complete",
        extra={
            "file_name": runtime.gemini_file.name,
            "uri": runtime.gemini_file.uri,
        },
    )
    return True


async def _wait_until_active(runtime: "YouTubeAnalysisRuntime") -> bool:
    active_wait_started = time.monotonic()
    state = ""
    for poll_attempt in range(30):
        try:
            runtime.gemini_file = await asyncio.to_thread(
                lambda: runtime.hooks.client.files.get(name=runtime.gemini_file.name)
            )
        except Exception as poll_error:
            runtime.hooks.stage_add(
                runtime.result,
                "file_active_wait",
                active_wait_started,
            )
            runtime.result["error"] = (
                "files.get() 404 during polling — upload may have failed: "
                f"{poll_error}"
            )
            runtime.hooks.logger.warning(
                "gemini_fileapi_poll_error",
                extra={
                    "attempt": poll_attempt,
                    "error": str(poll_error),
                },
            )
            return False
        state = getattr(
            runtime.gemini_file.state,
            "name",
            str(runtime.gemini_file.state),
        )
        runtime.hooks.logger.info(
            "gemini_fileapi_poll",
            extra={"attempt": poll_attempt + 1, "state": state},
        )
        if state == "ACTIVE":
            break
        if state == "FAILED":
            runtime.hooks.stage_add(
                runtime.result,
                "file_active_wait",
                active_wait_started,
            )
            runtime.result["error"] = (
                f"Gemini file processing FAILED (state={state}). Try re-uploading."
            )
            runtime.hooks.logger.warning(
                "gemini_fileapi_processing_failed",
                extra={"attempt": poll_attempt + 1, "state": state},
            )
            return False
        await asyncio.sleep(3)
    else:
        runtime.hooks.stage_add(
            runtime.result,
            "file_active_wait",
            active_wait_started,
        )
        runtime.result["error"] = (
            f"Gemini file ACTIVE timeout after 90s (final state={state})"
        )
        runtime.hooks.logger.warning(
            "gemini_fileapi_poll_timeout",
            extra={"state": state},
        )
        return False
    runtime.hooks.stage_add(
        runtime.result,
        "file_active_wait",
        active_wait_started,
    )
    return True


async def _run_file_model_chain(runtime: "YouTubeAnalysisRuntime") -> None:
    last_err = ""
    file_plan = list(enumerate(runtime.models, start=1))
    file_cache_retried: set[str] = set()
    file_pos = 0
    while file_pos < len(file_plan):
        model_offset, model_name = file_plan[file_pos]
        file_pos += 1
        if not runtime.scope_passes("file_api_attempt"):
            return
        attempt_started = time.monotonic()
        cache_info: dict[str, Any] = {}
        try:
            await _file_attempt(
                runtime,
                model_name,
                model_offset,
                cache_info,
                attempt_started,
            )
            break
        except Exception as error:
            runtime.hooks.stage_add(runtime.result, "generation", attempt_started)
            runtime.hooks.mark_attempt_failed(runtime.diagnostics)
            last_err = str(error)
            runtime.hooks.logger.warning(
                "gemini_fileapi_model_failed",
                extra={
                    "model": model_name,
                    "error": str(error)[:80],
                    "traceback_tail": traceback.format_exc()[-500:],
                },
            )
            if runtime.hooks.retry_after_context_cache_error(
                error,
                cache_info,
                model_name,
                file_cache_retried,
            ):
                file_plan.insert(file_pos, (model_offset, model_name))
                continue
            if not runtime.hooks.should_switch_model(error):
                runtime.diagnostics["chain_stop_reason"] = (
                    f"{type(error).__name__}: {last_err[:120]}"
                )
                break
    if not runtime.result["analyzed"]:
        runtime.result["error"] = last_err


async def _file_attempt(
    runtime: "YouTubeAnalysisRuntime",
    model_name: str,
    model_offset: int,
    cache_info: dict[str, Any],
    attempt_started: float,
) -> None:
    cache_config = None
    if runtime.is_final_v1:
        cache_setup_started = time.monotonic()
        cache_config, fetched_info = runtime.hooks.final_v1_cache_config(model_name)
        cache_info.update(fetched_info)
        runtime.hooks.stage_add(runtime.result, "cache_setup", cache_setup_started)
    prompt, final_full_prompt = runtime.prompts()
    request_prompt = (
        final_full_prompt if runtime.is_final_v1 and not cache_config else prompt
    )
    request_config = runtime.hooks.video_generate_config(model_name, cache_config)

    def analyze_file() -> Any:
        contents = [
            runtime.hooks.genai_types.Part.from_uri(
                file_uri=runtime.gemini_file.uri,
                mime_type="video/mp4",
            ),
            request_prompt,
        ]
        return runtime.hooks.generate_json_with_recovery(
            model_name=model_name,
            contents=contents,
            config=request_config,
            prompt=request_prompt,
            performance_context=runtime.performance_context,
            llm_context=runtime.llm_context,
            subphase="youtube_file_fallback_generation",
            attempt_index=len(runtime.models) + model_offset,
            attempt_total=runtime.attempt_total,
            attempt_log=runtime.result["llm_attempts"],
            diagnostics=runtime.diagnostics,
        )

    parsed, usage_metadata = await asyncio.to_thread(analyze_file)
    runtime.hooks.stage_add(runtime.result, "generation", attempt_started)
    runtime.apply_parsed(
        parsed,
        usage_metadata,
        method_prefix="gemini_fileapi",
        model_name=model_name,
    )
    if runtime.is_final_v1:
        runtime.result["context_cache"] = cache_info


async def _cleanup_file(runtime: "YouTubeAnalysisRuntime") -> None:
    file_to_delete = (
        getattr(runtime.gemini_file, "name", None) if runtime.gemini_file else None
    )
    if not file_to_delete:
        return
    cleanup_started = time.monotonic()
    try:
        await asyncio.to_thread(
            lambda: runtime.hooks.client.files.delete(name=file_to_delete)
        )
        runtime.hooks.logger.info(
            "gemini_fileapi_deleted",
            extra={"file_name": file_to_delete},
        )
    except Exception as delete_error:
        runtime.hooks.logger.warning(
            "gemini_fileapi_delete_skipped",
            extra={"error": str(delete_error)},
        )
    runtime.hooks.stage_add(runtime.result, "cleanup", cleanup_started)


__all__ = ["run_file_api_path"]
