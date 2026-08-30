"""Characterization locks for the CN platform video durable orchestration.

These tests intentionally freeze externally observable ordering as well as the
terminal projection.  The provider, downloader, R2 cache and LLM are all
hermetic doubles; no network, database or model call is made.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.kol import cn_platform_video as cn
from app.domains.kol.provider_job_access import ProviderJobAccessError
from app.domains.kol import url_deep_crawl
from app.domains.kol import video_url_resolver
from app.domains.media import cache as media_cache
from app.platform import apify_budget
from app.services.media import video_download
from app.services.scraping import apify_cn


URL = "https://www.bilibili.com/video/BV1S6Kr6mEgi"
VIDEO_ID = "BV1S6Kr6mEgi"


def _scraped() -> dict[str, Any]:
    return {
        "ok": True,
        "platform": "bilibili",
        "provider_status": "ok",
        "error": None,
        "metadata": {
            "platform": "bilibili",
            "media_kind": "video",
            "content_url": URL,
            "title": "样例视频",
            "channel_name": "样例UP主",
            "scrape_status": "success",
        },
        "creator": {
            "platform": "bilibili",
            "display_name": "样例UP主",
            "handle": "sample",
            "profile_url": "https://space.bilibili.com/1",
        },
        "native_video_id": VIDEO_ID,
        "direct_video_url": "https://cdn.example/video.mp4",
        "audio_url": "",
        "apify_run_id": "run-characterization",
    }


def _stable(value: Any) -> Any:
    """Keep volatile fields present while making the projection hash stable."""

    if isinstance(value, dict):
        return {
            key: "<volatile>" if key in {"generated_at", "updated_at"} else _stable(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def _digest(value: Any) -> str:
    canonical = json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _progress_event(snapshot: dict[str, Any]) -> str:
    current = str(snapshot.get("current_step") or "")
    steps = snapshot.get("steps") if isinstance(snapshot.get("steps"), list) else []
    step = next((item for item in steps if isinstance(item, dict) and item.get("key") == current), {})
    return f"progress:{current}:{step.get('status')}:{snapshot.get('status')}"


def _install_common(
    monkeypatch: pytest.MonkeyPatch,
    trace: list[str],
    *,
    scraped: dict[str, Any] | None = None,
) -> None:
    monkeypatch.setattr(
        apify_budget,
        "current_apify_execution_context",
        lambda: trace.append("durable_context") or object(),
    )
    monkeypatch.setattr(
        cn,
        "_expand_cn_short_link",
        lambda value: trace.append("expand_short_link") or value,
    )
    monkeypatch.setattr(
        video_url_resolver,
        "find_official_channel_match",
        lambda _creator: trace.append("official_match") or None,
    )
    if scraped is not None:
        monkeypatch.setattr(
            apify_cn,
            "scrape_cn_platform_video",
            lambda _platform, _url: trace.append("provider_scrape") or scraped,
        )


def test_pre_provider_cache_replay_freezes_zero_cost_order_and_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    _install_common(monkeypatch, trace)
    replay = {
        "cache_id": 15187,
        "model": "gemini-3.6-flash",
        "result": {
            "video_metadata": {"platform": "bilibili", "title": "缓存标题"},
            "creator_identity": {"platform": "bilibili", "display_name": "缓存UP主"},
            "layer1_visual_content": {"content_summary": "缓存摘要", "content_genre": "教程"},
            "layer6_flags_and_scores": {"scores": {"content_quality_score": 91}},
            "model": "gemini-3.6-flash",
            "generated_at": "2026-08-29T00:00:00Z",
        },
    }
    monkeypatch.setattr(
        cn,
        "_load_ready_cn_analysis",
        lambda platform, video_id: trace.append(f"analysis_cache_read:{platform}:{video_id}") or replay,
    )
    monkeypatch.setattr(
        apify_cn,
        "scrape_cn_platform_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider must stay untouched")),
    )
    monkeypatch.setattr(
        media_cache,
        "cached_video_url_for_item",
        lambda platform, video_id: trace.append(f"media_cache_read:{platform}:{video_id}")
        or "/api/vkpi-media/video-cache/cached",
    )

    result = cn.run_cn_platform_video_for_job(
        {"url": URL},
        progress_callback=lambda progress: trace.append(_progress_event(progress)),
        authorization_checkpoint=lambda: trace.append("authorization_checkpoint"),
    )

    assert trace == [
        "durable_context",
        "progress:resolve_video:running:running",
        "authorization_checkpoint",
        "expand_short_link",
        "authorization_checkpoint",
        f"analysis_cache_read:bilibili:{VIDEO_ID}",
        f"media_cache_read:bilibili:{VIDEO_ID}",
        "progress:resolve_video:ready:running",
        "progress:identify_creator:ready:running",
        "progress:cache_media:ready:running",
        "progress:ai_analysis:ready:ready",
        "authorization_checkpoint",
    ]
    assert _digest(result) == "2da8f8edec6b118b3bd58393c139f9289d7efc9bc63b7b2a8f8b5957c925e222"
    assert result["provider_calls_performed"] is False
    assert result["llm_calls_performed"] is False


def test_full_success_freezes_proxy_fallback_r2_llm_order_and_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    _install_common(monkeypatch, trace, scraped=_scraped())
    monkeypatch.setenv("VKPI_CN_MEDIA_PROXY", "http://proxy.example:10000")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"characterization")
    download_attempt = 0

    def download(_url: str, _outdir: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal download_attempt
        download_attempt += 1
        trace.append("download_proxy" if kwargs.get("proxy_url") else "download_direct")
        if download_attempt == 1:
            return {"success": False, "path": None, "error": "proxy_timeout"}
        return {"success": True, "path": str(video_path)}

    def load_analysis(platform: str, video_id: str) -> None:
        trace.append(f"analysis_cache_read:{platform}:{video_id}")
        return None

    monkeypatch.setattr(cn, "_load_ready_cn_analysis", load_analysis)
    monkeypatch.setattr(video_download, "download_direct_video_url", download)
    monkeypatch.setattr(
        media_cache,
        "cache_local_video_file",
        lambda platform, video_id, _path, **_kwargs: trace.append(f"r2_write:{platform}:{video_id}")
        or {"cached": True, "status": "cached", "cached_url": "/fallback"},
    )
    monkeypatch.setattr(
        media_cache,
        "cached_video_url_for_item",
        lambda platform, video_id: trace.append(f"media_cache_read:{platform}:{video_id}")
        or "/api/vkpi-media/video-cache/r2",
    )
    monkeypatch.setattr(
        cn,
        "_cn_budget_gate",
        lambda platform, video_id: trace.append(f"budget_gate:{platform}:{video_id}")
        or {"allowed": True, "reason": ""},
    )
    monkeypatch.setattr(
        cn,
        "_run_cn_gemini_final_v1",
        lambda **_kwargs: trace.append("gemini_final_v1")
        or {
            "analyzed": True,
            "model": "gemini-3.6-flash",
            "video_analysis_final_v1": {
                "layer1_visual_content": {"content_summary": "样例摘要", "content_genre": "教程"},
                "layer6_flags_and_scores": {
                    "scores": {"content_quality_score": 92, "viewer_heart_score": 88}
                },
            },
        },
    )
    monkeypatch.setattr(
        cn,
        "_store_cn_analysis",
        lambda **_kwargs: trace.append("analysis_cache_write") or 991,
    )

    result = cn.run_cn_platform_video_for_job(
        {"url": URL, "triggered_by_user_id": 77},
        progress_callback=lambda progress: trace.append(_progress_event(progress)),
        authorization_checkpoint=lambda: trace.append("authorization_checkpoint"),
    )

    assert trace == [
        "durable_context",
        "progress:resolve_video:running:running",
        "authorization_checkpoint",
        "expand_short_link",
        "authorization_checkpoint",
        f"analysis_cache_read:bilibili:{VIDEO_ID}",
        "authorization_checkpoint",
        "provider_scrape",
        "authorization_checkpoint",
        "progress:resolve_video:ready:running",
        "progress:identify_creator:running:running",
        "official_match",
        "progress:identify_creator:ready:running",
        f"analysis_cache_read:bilibili:{VIDEO_ID}",
        f"media_cache_read:bilibili:{VIDEO_ID}",
        "progress:cache_media:running:running",
        "authorization_checkpoint",
        "download_proxy",
        "authorization_checkpoint",
        "authorization_checkpoint",
        "download_direct",
        "authorization_checkpoint",
        "authorization_checkpoint",
        f"r2_write:bilibili:{VIDEO_ID}",
        "authorization_checkpoint",
        f"media_cache_read:bilibili:{VIDEO_ID}",
        "progress:cache_media:ready:running",
        "progress:ai_analysis:running:running",
        "authorization_checkpoint",
        f"budget_gate:bilibili:{VIDEO_ID}",
        "authorization_checkpoint",
        "gemini_final_v1",
        "authorization_checkpoint",
        "authorization_checkpoint",
        "analysis_cache_write",
        "progress:ai_analysis:ready:ready",
    ]
    assert _digest(result) == "a033262bc83d5cb6df658ced68263249865e98f6e3cd2b05bdff81b8a5fc308c"
    assert result["business_tables_written"] is True
    assert result["provider_calls_performed"] is True
    assert result["llm_calls_performed"] is True


def test_r2_authorization_failure_propagates_without_budget_or_llm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    _install_common(monkeypatch, trace, scraped=_scraped())
    monkeypatch.delenv("VKPI_CN_MEDIA_PROXY", raising=False)
    monkeypatch.delenv("YTDLP_PROXY", raising=False)
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"characterization")
    monkeypatch.setattr(cn, "_load_ready_cn_analysis", lambda *_args: None)
    monkeypatch.setattr(media_cache, "cached_video_url_for_item", lambda *_args: None)
    monkeypatch.setattr(
        video_download,
        "download_direct_video_url",
        lambda *_args, **_kwargs: {"success": True, "path": str(video_path)},
    )
    monkeypatch.setattr(
        media_cache,
        "cache_local_video_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProviderJobAccessError("provider_job_permission_revoked", 403)
        ),
    )
    monkeypatch.setattr(
        cn,
        "_cn_budget_gate",
        lambda *_args: (_ for _ in ()).throw(AssertionError("budget must not run")),
    )
    monkeypatch.setattr(
        cn,
        "_run_cn_gemini_final_v1",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("LLM must not run")),
    )

    with pytest.raises(ProviderJobAccessError) as raised:
        cn.run_cn_platform_video_for_job({"url": URL})

    assert raised.value.code == "provider_job_permission_revoked"
