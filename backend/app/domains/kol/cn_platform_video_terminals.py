"""Terminal result builders for the CN platform video runtime.

Extracted verbatim from ``cn_platform_video_runtime._CNPlatformVideoRuntime``
(class-LOC 460→≤400 ratchet wave). Each function receives the live runtime and
assembles one terminal payload — replay hits, official-channel short-circuit,
media degradation, budget block, and analysis finalization — exactly as the
former methods did, progress emissions and checkpoints included.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    from app.domains.kol.cn_platform_video_runtime import _CNPlatformVideoRuntime


def pre_provider_replay_result(
    rt: "_CNPlatformVideoRuntime",
    replay: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not replay:
        return None
    shaped = replay.get("result") or {}
    cached_meta = shaped.get("video_metadata") if isinstance(shaped.get("video_metadata"), dict) else {}
    cached_creator = (
        shaped.get("creator_identity")
        if isinstance(shaped.get("creator_identity"), dict)
        else {}
    )
    replay_video_url = rt._cached_media_url(rt.canonical_id)
    rt.emit_progress("resolve_video", "ready", reason="cached_analysis")
    rt.emit_progress("identify_creator", "ready")
    rt.current = rt.hooks.progress(
        rt.current,
        "cache_media",
        "ready" if replay_video_url else "skipped",
        reason="" if replay_video_url else "media_cache_not_ready",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    rt.current = rt.hooks.progress(
        rt.current,
        "ai_analysis",
        "ready",
        overall="ready",
        base_status="ready",
        reason="cached_analysis",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    rt.checkpoint()
    return rt.hooks.terminal_result(
        platform=rt.platform,
        video_id=rt.canonical_id,
        metadata=cached_meta or None,
        creator=cached_creator or None,
        cached_video_url=replay_video_url,
        ai_analysis=rt.hooks.ai_state("ready", "cached_analysis", allowed=False),
        cn_analysis=rt.hooks.compact_analysis(shaped),
        resolution_progress=rt.current,
        provider_calls_performed=False,
        llm_calls_performed=False,
        analysis_cache_id=rt.hooks.int_or_none(replay.get("cache_id")),
    )


def official_terminal(rt: "_CNPlatformVideoRuntime", official: Any) -> dict[str, Any]:
    return {
        "status": "official_channel_video",
        "operation": "cn_platform_video_analysis",
        "official_channel": official,
        "creator_identity": rt.creator,
        "video_metadata": rt.metadata,
        "video_flow": {
            "status": "official_channel_video",
            "operation": "cn_platform_video_analysis",
            "message": "官方自有账号的视频：不建人选档案，也不做深度分析，仅保留视频基础数据。",
            "viltrox_fit_score_untouched": True,
        },
        "ai_analysis": rt.hooks.ai_state("skipped", "official_channel_video"),
        "resolution_progress": rt.current,
        "provider_calls_performed": True,
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }


def post_provider_replay_result(
    rt: "_CNPlatformVideoRuntime",
) -> dict[str, Any] | None:
    cached_analysis = rt.hooks.load_ready_analysis(rt.platform, rt.video_id)
    rt.cached_video_url = rt._cached_media_url(rt.video_id)
    if not cached_analysis:
        return None
    shaped = cached_analysis.get("result") or {}
    rt.current = rt.hooks.progress(
        rt.current,
        "cache_media",
        "ready" if rt.cached_video_url else "skipped",
        reason="" if rt.cached_video_url else "media_cache_not_ready",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    rt.current = rt.hooks.progress(
        rt.current,
        "ai_analysis",
        "ready",
        overall="ready",
        base_status="ready",
        reason="cached_analysis",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    rt.checkpoint()
    cached_metadata = shaped.get("video_metadata") if isinstance(shaped.get("video_metadata"), dict) else None
    cached_creator = shaped.get("creator_identity") if isinstance(shaped.get("creator_identity"), dict) else None
    return rt.hooks.terminal_result(
        platform=rt.platform,
        video_id=rt.video_id,
        metadata=rt.metadata or cached_metadata,
        creator=rt.creator or cached_creator,
        cached_video_url=rt.cached_video_url,
        ai_analysis=rt.hooks.ai_state("ready", "cached_analysis", allowed=False),
        cn_analysis=rt.hooks.compact_analysis(shaped),
        resolution_progress=rt.current,
        provider_calls_performed=True,
        llm_calls_performed=False,
        analysis_cache_id=rt.hooks.int_or_none(cached_analysis.get("cache_id")),
    )


def media_degraded_result(rt: "_CNPlatformVideoRuntime", reason: str) -> dict[str, Any]:
    rt.current = rt.hooks.progress(rt.current, "cache_media", "failed", reason=reason[:200])
    rt.hooks.emit(rt.progress_callback, rt.current)
    rt.current = rt.hooks.progress(
        rt.current,
        "ai_analysis",
        "skipped",
        overall="partial",
        base_status="ready",
        reason="media_unavailable_metadata_only",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    return rt.hooks.terminal_result(
        platform=rt.platform,
        video_id=rt.video_id,
        metadata=rt.metadata,
        creator=rt.creator,
        cached_video_url=None,
        ai_analysis=rt.hooks.ai_state("skipped", "media_unavailable_metadata_only"),
        cn_analysis=None,
        resolution_progress=rt.current,
        provider_calls_performed=True,
        llm_calls_performed=False,
        media_degraded=True,
        media_degraded_reason=reason[:240],
    )


def budget_blocked_result(
    rt: "_CNPlatformVideoRuntime",
    budget: dict[str, Any],
) -> dict[str, Any] | None:
    if budget.get("allowed"):
        return None
    gate_reason = rt.hooks.text(budget.get("reason")) or "provider_calls_blocked"
    rt.current = rt.hooks.progress(
        rt.current,
        "ai_analysis",
        "skipped",
        overall="ready",
        base_status="ready",
        reason="ai_disabled",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    return rt.hooks.terminal_result(
        platform=rt.platform,
        video_id=rt.video_id,
        metadata=rt.metadata,
        creator=rt.creator,
        cached_video_url=rt.cached_video_url,
        ai_analysis=rt.hooks.ai_state(
            "not_requested",
            "ai_disabled",
            gate_reason=gate_reason,
        ),
        cn_analysis=None,
        resolution_progress=rt.current,
        provider_calls_performed=True,
        llm_calls_performed=False,
    )


def finalize_analysis(rt: "_CNPlatformVideoRuntime", raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw.get("analyzed"):
        reason = rt.hooks.text((raw or {}).get("error")) or "gemini_not_analyzed"
        rt.current = rt.hooks.progress(
            rt.current,
            "ai_analysis",
            "failed",
            overall="partial",
            base_status="ready",
            reason=reason[:200],
        )
        rt.hooks.emit(rt.progress_callback, rt.current)
        return rt.hooks.terminal_result(
            platform=rt.platform,
            video_id=rt.video_id,
            metadata=rt.metadata,
            creator=rt.creator,
            cached_video_url=rt.cached_video_url,
            ai_analysis=rt.hooks.ai_state("failed", reason[:200], allowed=True),
            cn_analysis=None,
            resolution_progress=rt.current,
            provider_calls_performed=True,
            llm_calls_performed=True,
        )
    return _store_successful_analysis(rt, raw)


def _store_successful_analysis(
    rt: "_CNPlatformVideoRuntime",
    raw: dict[str, Any],
) -> dict[str, Any]:
    shaped = rt.hooks.shape_final_v1(
        raw=raw,
        platform=rt.platform,
        video_id=rt.video_id,
        content_url=rt.content_url,
        metadata=rt.metadata,
        creator=rt.creator,
    )
    rt.checkpoint()
    cache_id = rt.hooks.store_analysis(
        platform=rt.platform,
        video_id=rt.video_id,
        shaped=shaped,
        model=rt.hooks.text(raw.get("model") or raw.get("method")),
        triggered_by=rt.triggered_by,
    )
    rt.current = rt.hooks.progress(
        rt.current,
        "ai_analysis",
        "ready",
        overall="ready",
        base_status="ready",
        reason="",
    )
    rt.hooks.emit(rt.progress_callback, rt.current)
    return rt.hooks.terminal_result(
        platform=rt.platform,
        video_id=rt.video_id,
        metadata=rt.metadata,
        creator=rt.creator,
        cached_video_url=rt.cached_video_url,
        ai_analysis=rt.hooks.ai_state("ready", "cn_platform_video_analysis", allowed=True),
        cn_analysis=rt.hooks.compact_analysis(shaped),
        resolution_progress=rt.current,
        provider_calls_performed=True,
        llm_calls_performed=True,
        analysis_cache_id=cache_id,
    )


__all__ = [
    "budget_blocked_result",
    "finalize_analysis",
    "media_degraded_result",
    "official_terminal",
    "post_provider_replay_result",
    "pre_provider_replay_result",
]
