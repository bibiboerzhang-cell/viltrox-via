"""Runtime orchestration for the CN platform video analysis-only flow.

The public facade and its patchable policy hooks stay in ``cn_platform_video``.
This module owns only the stateful durable-worker choreography so each failure
boundary remains small enough to audit independently.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.kol import cn_platform_video_terminals as _terminals
from app.domains.kol.provider_job_access import ProviderJobAccessError
from app.domains.kol.url_deep_crawl_helpers import CN_VIDEO_ANALYSIS_PLATFORMS


logger = get_logger(__name__)


@dataclass(frozen=True)
class CNPlatformVideoHooks:
    """Call-time dependencies supplied by the public, monkeypatchable facade."""

    classify_url: Callable[[str], Any]
    emit: Callable[..., dict[str, Any]]
    progress: Callable[..., dict[str, Any]]
    find_official_channel_match: Callable[[dict[str, Any]], Any]
    initial_progress: Callable[[], dict[str, Any]]
    current_apify_execution_context: Callable[[], Any]
    scrape_cn_platform_video: Callable[[str, str], dict[str, Any]]
    expand_short_link: Callable[[str], str]
    load_ready_analysis: Callable[[str, str], dict[str, Any] | None]
    store_analysis: Callable[..., int | None]
    budget_gate: Callable[[str, str], dict[str, Any]]
    run_gemini: Callable[..., dict[str, Any]]
    shape_final_v1: Callable[..., dict[str, Any]]
    compact_analysis: Callable[[dict[str, Any]], dict[str, Any]]
    terminal_result: Callable[..., dict[str, Any]]
    ai_state: Callable[..., dict[str, Any]]
    int_or_none: Callable[[Any], int | None]
    text: Callable[[Any], str]


class _CNPlatformVideoRuntime:
    """One durable execution with explicit side-effect boundaries."""

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        staff: dict[str, Any] | None,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        authorization_checkpoint: Callable[[], Any] | None,
        hooks: CNPlatformVideoHooks,
    ) -> None:
        self.payload = payload
        self.progress_callback = progress_callback
        self.authorization_checkpoint = authorization_checkpoint
        self.hooks = hooks
        self.classified = hooks.classify_url(hooks.text(payload.get("url") or payload.get("source_url")))
        if (
            self.classified.url_type != "video"
            or self.classified.platform not in CN_VIDEO_ANALYSIS_PLATFORMS
        ):
            raise ValueError("cn_platform_video requires a recognized CN platform video URL")
        if hooks.current_apify_execution_context() is None:
            raise RuntimeError("durable_execution_context_required")

        self.platform = self.classified.platform
        self.triggered_by = (staff or {}).get("user_id") or payload.get("triggered_by_user_id")
        current = payload.get("video_url_resolution")
        self.current = current if isinstance(current, dict) else hooks.initial_progress()
        self.source_url = self.classified.normalized_url
        self.canonical_id = self.classified.video_id
        self.metadata: dict[str, Any] = {}
        self.creator: dict[str, Any] = {}
        self.video_id = self.classified.video_id
        self.direct_video_url = ""
        self.content_url = self.source_url
        self.cached_video_url: str | None = None

    def checkpoint(self) -> None:
        if self.authorization_checkpoint:
            self.authorization_checkpoint()

    def emit_progress(self, step: str, status: str, **kwargs: Any) -> None:
        self.current = self.hooks.emit(
            self.progress_callback,
            self.hooks.progress(self.current, step, status, **kwargs),
        )

    def run(self) -> dict[str, Any]:
        replay = self._begin_resolution()
        cached_result = _terminals.pre_provider_replay_result(self, replay)
        if cached_result is not None:
            return cached_result

        self.checkpoint()
        self._scrape_provider()
        official_result = self._official_channel_result()
        if official_result is not None:
            return official_result

        cached_result = _terminals.post_provider_replay_result(self)
        if cached_result is not None:
            return cached_result

        degraded_result = self._begin_media_cache()
        if degraded_result is not None:
            return degraded_result
        return self._download_cache_and_analyze()

    def _begin_resolution(self) -> dict[str, Any] | None:
        self.emit_progress("resolve_video", "running")
        self.checkpoint()
        self.source_url = self.hooks.expand_short_link(self.classified.normalized_url)
        self.checkpoint()
        if self.source_url != self.classified.normalized_url:
            expanded = self.hooks.classify_url(self.source_url)
            if expanded.platform == self.platform and expanded.video_id:
                self.canonical_id = expanded.video_id
        return self.hooks.load_ready_analysis(self.platform, self.canonical_id)

    def _cached_media_url(self, video_id: str) -> str | None:
        try:
            from app.domains.media.cache import cached_video_url_for_item

            return self.hooks.text(cached_video_url_for_item(self.platform, video_id)) or None
        except Exception:
            logger.debug("cn video cached url lookup failed", exc_info=True)
            return None

    def _scrape_provider(self) -> None:
        scraped = self.hooks.scrape_cn_platform_video(self.platform, self.source_url)
        self.checkpoint()
        if not scraped.get("ok"):
            raise RuntimeError(
                "cn_video_resolve_failed:"
                f"{self.hooks.text(scraped.get('provider_status'))}:"
                f"{self.hooks.text(scraped.get('error'))[:160]}"
            )
        self.metadata = scraped.get("metadata") if isinstance(scraped.get("metadata"), dict) else {}
        self.creator = scraped.get("creator") if isinstance(scraped.get("creator"), dict) else {}
        self.video_id = self.hooks.text(scraped.get("native_video_id")) or self.classified.video_id
        self.direct_video_url = self.hooks.text(scraped.get("direct_video_url"))
        self.content_url = self.hooks.text(self.metadata.get("content_url")) or self.source_url
        self.emit_progress("resolve_video", "ready")

    def _official_channel_result(self) -> dict[str, Any] | None:
        self.emit_progress("identify_creator", "running")
        official = self.hooks.find_official_channel_match(self.creator)
        if official:
            self.current = self.hooks.progress(
                self.current,
                "cache_media",
                "skipped",
                reason="official_channel_video",
            )
            self.hooks.emit(self.progress_callback, self.current)
            self.current = self.hooks.progress(
                self.current,
                "ai_analysis",
                "skipped",
                overall="ready",
                base_status="ready",
                reason="official_channel_video",
            )
            self.hooks.emit(self.progress_callback, self.current)
            self.checkpoint()
            return _terminals.official_terminal(self, official)
        creator_status = "ready" if self.hooks.text(self.creator.get("display_name")) else "skipped"
        self.emit_progress(
            "identify_creator",
            creator_status,
            reason="" if creator_status == "ready" else "creator_display_unavailable",
        )
        return None

    def _begin_media_cache(self) -> dict[str, Any] | None:
        self.emit_progress("cache_media", "running")
        if self.direct_video_url:
            return None
        reason = (
            "note_has_no_video_image_only"
            if self.hooks.text(self.metadata.get("media_kind")) == "image"
            else "actor_returned_no_video_url"
        )
        return _terminals.media_degraded_result(self, reason)

    def _download(self, tmpdir: str) -> dict[str, Any]:
        from app.services.media.video_download import download_direct_video_url

        cn_proxy = (os.getenv("VKPI_CN_MEDIA_PROXY") or os.getenv("YTDLP_PROXY") or "").strip()
        cn_socket = int(os.getenv("VKPI_CN_MEDIA_SOCKET_TIMEOUT_SEC", "60"))
        cn_total = int(os.getenv("VKPI_CN_MEDIA_TOTAL_TIMEOUT_SEC", "300"))
        self.checkpoint()
        download = download_direct_video_url(
            self.direct_video_url,
            tmpdir,
            referer=self.content_url,
            socket_timeout_sec=cn_socket,
            total_timeout_sec=cn_total,
            proxy_url=cn_proxy,
        )
        self.checkpoint()
        if (not download.get("success")) and cn_proxy:
            self.checkpoint()
            download = download_direct_video_url(
                self.direct_video_url,
                tmpdir,
                referer=self.content_url,
                socket_timeout_sec=cn_socket,
                total_timeout_sec=cn_total,
            )
            self.checkpoint()
        return download

    def _warm_media_cache(self, video_path: str) -> None:
        try:
            from app.domains.media.cache import cache_local_video_file, cached_video_url_for_item

            self.checkpoint()
            warm = cache_local_video_file(
                self.platform,
                self.video_id,
                video_path,
                source_url=self.content_url,
            )
            self.checkpoint()
            if warm.get("cached") or warm.get("status") == "cached":
                self.cached_video_url = (
                    self.hooks.text(cached_video_url_for_item(self.platform, self.video_id))
                    or self.hooks.text(warm.get("cached_url"))
                    or None
                )
        except ProviderJobAccessError:
            raise
        except Exception:
            logger.warning(
                "cn video r2 warm failed platform=%s id=%s",
                self.platform,
                self.video_id,
                exc_info=True,
            )

    def _download_cache_and_analyze(self) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="vkpi-cn-video-") as tmpdir:
            download = self._download(tmpdir)
            if not download.get("success") or not download.get("path"):
                reason = self.hooks.text(download.get("error")) or f"direct_video_download_failed:{self.platform}"
                return _terminals.media_degraded_result(self, reason)
            self._warm_media_cache(str(download["path"]))
            self.emit_progress(
                "cache_media",
                "ready" if self.cached_video_url else "skipped",
                reason="" if self.cached_video_url else "media_cache_not_ready",
            )
            self.emit_progress("ai_analysis", "running")
            self.checkpoint()
            budget = self.hooks.budget_gate(self.platform, self.video_id)
            blocked_result = _terminals.budget_blocked_result(self, budget)
            if blocked_result is not None:
                return blocked_result
            self.checkpoint()
            raw = self.hooks.run_gemini(
                video_path=str(download["path"]),
                title=self.hooks.text(self.metadata.get("title")),
                creator_name=self.hooks.text(self.creator.get("display_name")),
                platform=self.platform,
                video_id=self.video_id,
                triggered_by=self.triggered_by,
            )
            self.checkpoint()
        return _terminals.finalize_analysis(self, raw)


def execute_cn_platform_video(
    payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    authorization_checkpoint: Callable[[], Any] | None,
    hooks: CNPlatformVideoHooks,
) -> dict[str, Any]:
    """Create and execute one isolated durable runtime."""

    return _CNPlatformVideoRuntime(
        payload,
        staff=staff,
        progress_callback=progress_callback,
        authorization_checkpoint=authorization_checkpoint,
        hooks=hooks,
    ).run()
