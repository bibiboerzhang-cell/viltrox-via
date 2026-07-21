"""CN 平台(bilibili/抖音/小红书)「仅视频分析」通道测试。

覆盖:URL 识别(A)、actor 输出归一(B)、worker 分析链关键分支(C:降级/官方号/
不建档红线)、dry-run 计划与 HTTP defer 判定(D)。全部 hermetic:不碰真库、
不碰 provider、不碰 LLM。
"""
from __future__ import annotations

import pytest

from app.domains.kol.url_deep_crawl import (
    CN_VIDEO_ANALYSIS_PLATFORMS,
    SUPPORTED_PLATFORMS,
    classify_url,
    dry_run_url_deep_crawl,
)
from app.services.scraping import apify_cn


# ---------------------------------------------------------------- A. URL 识别


@pytest.mark.parametrize(
    ("url", "platform", "video_id"),
    [
        ("https://www.bilibili.com/video/BV11jKm6bE61", "bilibili", "BV11jKm6bE61"),
        ("https://m.bilibili.com/video/av170001", "bilibili", "av170001"),
        ("https://b23.tv/abc123XY", "bilibili", "abc123XY"),
        ("https://www.douyin.com/video/7534679152504376595", "douyin", "7534679152504376595"),
        ("https://www.iesdouyin.com/share/video/7534679152504376595/", "douyin", "7534679152504376595"),
        ("https://v.douyin.com/iF8yXbAq/", "douyin", "iF8yXbAq"),
        (
            "https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=ABdR&xsec_source=pc_feed",
            "xiaohongshu",
            "64608fa90000000027003d64",
        ),
        (
            "https://www.xiaohongshu.com/discovery/item/6a06c9360000000036001d5a?xsec_token=X",
            "xiaohongshu",
            "6a06c9360000000036001d5a",
        ),
        ("http://xhslink.com/o/6oKW7wkJf09", "xiaohongshu", "6oKW7wkJf09"),
    ],
)
def test_cn_video_urls_classify_as_video(url, platform, video_id):
    classified = classify_url(url)

    assert classified.url_type == "video"
    assert classified.platform == platform
    assert classified.video_id == video_id
    assert classified.confidence == "cn_video_pattern"


def test_cn_video_url_keeps_query_in_normalized_url_for_xsec_token():
    url = "https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=ABdR&xsec_source=pc_feed"

    classified = classify_url(url)

    assert "xsec_token=ABdR" in classified.normalized_url


def test_cn_profile_urls_stay_unknown_with_honest_video_only_confidence():
    for url in (
        "https://space.bilibili.com/67063436",
        "https://www.douyin.com/user/MS4wLjABAAAA",
        "https://www.xiaohongshu.com/user/profile/5657ed837c5bb8258f1dbb45",
    ):
        classified = classify_url(url)

        assert classified.url_type == "unknown"
        assert classified.platform in CN_VIDEO_ANALYSIS_PLATFORMS
        assert classified.confidence == "cn_platform_video_only"


def test_cn_platforms_do_not_leak_into_supported_pool_platforms():
    assert not (CN_VIDEO_ANALYSIS_PLATFORMS & SUPPORTED_PLATFORMS)
    # 回归:三大海外平台判定不受影响。
    assert classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ").platform == "youtube"
    assert classify_url("https://www.instagram.com/p/DYw3UWUCJ_6/").platform == "instagram"
    assert classify_url("https://www.tiktok.com/@user/video/7300000000000000000").platform == "tiktok"


# ------------------------------------------------------- B. actor 输出归一化


def test_normalize_bilibili_item_maps_metadata_and_native_id():
    item = {
        "bvid": "BV1S6Kr6mEgi",
        "title": "《 双 枪 牛 仔 》",
        "desc": "-",
        "nickname": "IPx的粉红豹",
        "view": 3005478,
        "like": 236523,
        "reply": 590,
        "share": 1000,
        "duration": 62.5,
        "createTime": 1784896475,
        "img": "http://i0.hdslb.com/cover.jpg",
        "avatarUri": "http://i0.hdslb.com/face.jpg",
        "url": "https://www.bilibili.com/video/BV1S6Kr6mEgi",
        "videoUrl": "https://upos-sz.bilivideo.com/x-1-192.mp4?e=1",
        "audioUrl": "https://upos-sz.bilivideo.com/x-1-30280.m4s?e=1",
        "errMsg": "",
    }

    normalized = apify_cn._normalize_bilibili(item, "https://www.bilibili.com/video/BV1S6Kr6mEgi", "run1")
    metadata = normalized["metadata"]

    assert normalized["native_video_id"] == "BV1S6Kr6mEgi"
    assert metadata["platform"] == "bilibili"
    assert metadata["media_kind"] == "video"
    assert metadata["view_count"] == 3005478
    assert metadata["comment_count"] == 590
    assert metadata["duration_seconds"] == 62
    assert metadata["publish_date"].startswith("2026-")
    assert normalized["creator"]["display_name"] == "IPx的粉红豹"


def test_normalize_douyin_item_extracts_native_id_from_url():
    item = {
        "title": "雨天开车除雾",
        "nickname": "懂车小彬",
        "playCount": 0,
        "diggCount": 10858,
        "commentCount": 300,
        "shareCount": 50,
        "followerCount": 354498,
        "duration": 67.756,
        "createTime": 1754304200,
        "url": "https://www.douyin.com/video/7534679152504376595",
        "videoUrl": "https://www.douyin.com/aweme/v1/play/?file_id=x",
        "audioUrl": "",
        "avatarUri": "https://p3.douyinpic.com/x.jpeg",
        "errMsg": "",
    }

    normalized = apify_cn._normalize_douyin(item, "https://v.douyin.com/short/", "run2")

    assert normalized["native_video_id"] == "7534679152504376595"
    assert normalized["metadata"]["like_count"] == 10858
    assert normalized["creator"]["followers"] == 354498


def test_normalize_xiaohongshu_item_maps_note_fields_and_profile_url():
    item = {
        "noteid": "64608fa90000000027003d64",
        "noteType": "video",
        "title": "普通女孩的家",
        "desc": "小时候午后阳光的感觉",
        "userName": "小青柑",
        "userid": "5657ed837c5bb8258f1dbb45",
        "likedCount": 13000,
        "commentCount": 417,
        "shareCount": 12,
        "duration": 28.099,
        "createTime": 1684049833,
        "img": "http://sns-webpic.xhscdn.com/cover.jpg",
        "imgs": ["http://sns-webpic.xhscdn.com/1.jpg"],
        "avatar": "https://sns-avatar.xhscdn.com/a.jpg",
        "url": "https://www.xiaohongshu.com/explore/64608fa90000000027003d64",
        "videoUrl": "http://sns-video-zl.xhscdn.com/stream/x.mp4?sign=1",
        "audioUrl": "",
        "errMsg": "",
    }

    normalized = apify_cn._normalize_xiaohongshu(item, "https://www.xiaohongshu.com/explore/64608fa90000000027003d64", "run3")

    assert normalized["native_video_id"] == "64608fa90000000027003d64"
    assert normalized["metadata"]["media_kind"] == "video"
    assert normalized["metadata"]["like_count"] == 13000
    assert normalized["creator"]["profile_url"].endswith("/user/profile/5657ed837c5bb8258f1dbb45")


def test_xiaohongshu_image_note_is_honest_image_kind():
    item = {
        "noteid": "6a06c9360000000036001d5a",
        "noteType": "normal",
        "title": "图文笔记",
        "userName": "某博主",
        "userid": "abc",
        "imgs": ["http://sns-webpic.xhscdn.com/1.jpg"],
        "videoUrl": "",
        "errMsg": "",
    }

    normalized = apify_cn._normalize_xiaohongshu(item, "https://www.xiaohongshu.com/explore/6a06c9360000000036001d5a", "run4")

    assert normalized["metadata"]["media_kind"] == "image"


def test_cn_video_actor_ids_default_to_apple_yang_family(monkeypatch):
    for env in ("APIFY_BILIBILI_VIDEO_ACTOR_ID", "APIFY_DOUYIN_VIDEO_ACTOR_ID", "APIFY_XIAOHONGSHU_VIDEO_ACTOR_ID"):
        monkeypatch.delenv(env, raising=False)

    assert apify_cn.cn_video_actor_id("bilibili") == "apple_yang/bilibili-video-audio-downloader"
    assert apify_cn.cn_video_actor_id("douyin") == "apple_yang/douyin-video-audio-downloader"
    assert apify_cn.cn_video_actor_id("xiaohongshu") == "apple_yang/rednote-video-audio-downloader"
    monkeypatch.setenv("APIFY_DOUYIN_VIDEO_ACTOR_ID", "someone/else")
    assert apify_cn.cn_video_actor_id("douyin") == "someone/else"


# ------------------------------------------------ C. worker 分析链关键分支


def _base_payload(url: str) -> dict:
    return {"url": url, "source_url": url, "queue_lane": "interactive"}


def _install_cn_flow_mocks(monkeypatch, *, scraped: dict, budget_allowed: bool = True):
    """给 run_cn_platform_video_for_job 装 hermetic 桩:零 provider/LLM/DB。"""
    from app.domains.kol import cn_platform_video as module

    monkeypatch.setattr(
        "app.platform.apify_budget.current_apify_execution_context",
        lambda: ("task", 1),
    )
    monkeypatch.setattr(
        "app.services.scraping.apify_cn.scrape_cn_platform_video",
        lambda platform, url, **kwargs: scraped,
    )
    monkeypatch.setattr(module, "_expand_cn_short_link", lambda url: url)
    monkeypatch.setattr(module, "_load_ready_cn_analysis", lambda platform, video_id: None)
    monkeypatch.setattr(
        module, "_store_cn_analysis",
        lambda **kwargs: 991,
    )
    monkeypatch.setattr(
        module, "_cn_budget_gate",
        lambda platform, video_id: {"allowed": budget_allowed, "reason": "" if budget_allowed else "budget_blocked"},
    )
    monkeypatch.setattr(
        "app.domains.kol.video_url_resolver.find_official_channel_match",
        lambda identity: None,
    )
    return module


def _scraped_ok(platform: str = "bilibili") -> dict:
    return {
        "ok": True,
        "platform": platform,
        "provider_status": "ok",
        "error": None,
        "metadata": {
            "platform": platform,
            "media_kind": "video",
            "content_url": "https://www.bilibili.com/video/BV1S6Kr6mEgi",
            "title": "样例视频",
            "channel_name": "样例UP主",
            "scrape_status": "success",
        },
        "creator": {"platform": platform, "display_name": "样例UP主", "handle": "", "profile_url": ""},
        "native_video_id": "BV1S6Kr6mEgi",
        "direct_video_url": "https://upos-sz.bilivideo.com/x.mp4",
        "audio_url": "",
        "apify_run_id": "run-x",
    }


def test_cn_flow_media_download_failure_degrades_to_metadata_only(monkeypatch):
    module = _install_cn_flow_mocks(monkeypatch, scraped=_scraped_ok())
    monkeypatch.setattr(
        "app.services.media.video_download.download_direct_video_url",
        lambda url, outdir, **kwargs: {"success": False, "path": None, "error": "http_403"},
    )

    result = module.run_cn_platform_video_for_job(_base_payload("https://www.bilibili.com/video/BV1S6Kr6mEgi"))

    assert result["status"] == "cn_platform_video"
    assert result["media_degraded"] is True
    assert result["ai_analysis"]["state"] == "skipped"
    assert result["ai_analysis"]["reason"] == "media_unavailable_metadata_only"
    assert result["kol_pool_id"] is None
    assert result["viltrox_fit_score_untouched"] is True
    assert result["llm_calls_performed"] is False


def test_cn_flow_no_direct_url_image_note_degrades_honestly(monkeypatch):
    scraped = _scraped_ok("xiaohongshu")
    scraped["direct_video_url"] = ""
    scraped["metadata"]["media_kind"] = "image"
    module = _install_cn_flow_mocks(monkeypatch, scraped=scraped)

    result = module.run_cn_platform_video_for_job(
        _base_payload("https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=T")
    )

    assert result["status"] == "cn_platform_video"
    assert result["media_degraded"] is True
    assert result["media_degraded_reason"] == "note_has_no_video_image_only"


def test_cn_flow_budget_blocked_is_honest_ai_disabled(monkeypatch, tmp_path):
    module = _install_cn_flow_mocks(monkeypatch, scraped=_scraped_ok(), budget_allowed=False)
    video_file = tmp_path / "direct_video.mp4"
    video_file.write_bytes(b"0" * 2048)
    monkeypatch.setattr(
        "app.services.media.video_download.download_direct_video_url",
        lambda url, outdir, **kwargs: {"success": True, "path": str(video_file)},
    )
    monkeypatch.setattr(
        "app.domains.media.cache.cache_local_video_file",
        lambda *args, **kwargs: {"cached": True, "status": "cached", "cached_url": "/api/vkpi-media/video-cache/x"},
    )
    monkeypatch.setattr(
        "app.domains.media.cache.cached_video_url_for_item",
        lambda platform, video_id: "/api/vkpi-media/video-cache/x",
    )

    result = module.run_cn_platform_video_for_job(_base_payload("https://www.bilibili.com/video/BV1S6Kr6mEgi"))

    assert result["status"] == "cn_platform_video"
    assert result["ai_analysis"]["state"] == "not_requested"
    assert result["ai_analysis"]["reason"] == "ai_disabled"
    assert result["cached_video_url"] == "/api/vkpi-media/video-cache/x"
    assert result["llm_calls_performed"] is False


def test_cn_flow_success_stores_analysis_and_never_touches_pool(monkeypatch, tmp_path):
    module = _install_cn_flow_mocks(monkeypatch, scraped=_scraped_ok())
    video_file = tmp_path / "direct_video.mp4"
    video_file.write_bytes(b"0" * 2048)
    monkeypatch.setattr(
        "app.services.media.video_download.download_direct_video_url",
        lambda url, outdir, **kwargs: {"success": True, "path": str(video_file)},
    )
    monkeypatch.setattr(
        "app.domains.media.cache.cache_local_video_file",
        lambda *args, **kwargs: {"cached": True, "status": "cached", "cached_url": "/api/vkpi-media/video-cache/x"},
    )
    monkeypatch.setattr(
        "app.domains.media.cache.cached_video_url_for_item",
        lambda platform, video_id: "/api/vkpi-media/video-cache/x",
    )
    monkeypatch.setattr(
        module,
        "_run_cn_gemini_final_v1",
        lambda **kwargs: {
            "analyzed": True,
            "model": "gemini-2.5-flash",
            "video_analysis_final_v1": {
                "layer1_visual_content": {"content_summary": "样例摘要", "content_genre": "搞笑"},
                "layer6_flags_and_scores": {"scores": {"content_quality_score": 92, "viewer_heart_score": 88}},
            },
        },
    )

    result = module.run_cn_platform_video_for_job(_base_payload("https://www.bilibili.com/video/BV1S6Kr6mEgi"))

    assert result["status"] == "cn_platform_video"
    assert result["analysis_cache_id"] == 991
    assert result["cn_analysis"]["content_summary"] == "样例摘要"
    assert result["cn_analysis"]["scores"] == {"content_quality": 92.0, "viewer_heart": 88.0}
    assert result["ai_analysis"]["state"] == "ready"
    # 红线:绝不建档、绝不触 fit。
    assert result["kol_pool_id"] is None
    assert result["evidence_id"] is None
    assert result["viltrox_fit_score_untouched"] is True
    steps = {s["key"]: s["status"] for s in result["resolution_progress"]["steps"]}
    assert steps == {
        "resolve_video": "ready",
        "identify_creator": "ready",
        "cache_media": "ready",
        "ai_analysis": "ready",
    }


def test_cn_flow_replays_cached_analysis_without_provider_or_llm(monkeypatch):
    module = _install_cn_flow_mocks(monkeypatch, scraped=_scraped_ok())

    def _boom(*args, **kwargs):  # provider 不允许被碰
        raise AssertionError("provider must not be called on cached replay")

    monkeypatch.setattr("app.services.scraping.apify_cn.scrape_cn_platform_video", _boom)
    monkeypatch.setattr(
        module,
        "_load_ready_cn_analysis",
        lambda platform, video_id: {
            "cache_id": 15187,
            "model": "gemini-2.5-flash",
            "result": {
                "video_metadata": {"platform": "bilibili", "title": "缓存标题"},
                "creator_identity": {"platform": "bilibili", "display_name": "缓存UP主"},
                "layer1_visual_content": {"content_summary": "缓存摘要"},
                "layer6_flags_and_scores": {"scores": {}},
            },
        },
    )
    monkeypatch.setattr(
        "app.domains.media.cache.cached_video_url_for_item",
        lambda platform, video_id: "/api/vkpi-media/video-cache/x",
    )

    result = module.run_cn_platform_video_for_job(_base_payload("https://www.bilibili.com/video/BV1S6Kr6mEgi"))

    assert result["status"] == "cn_platform_video"
    assert result["provider_calls_performed"] is False
    assert result["llm_calls_performed"] is False
    assert result["analysis_cache_id"] == 15187
    assert result["cn_analysis"]["content_summary"] == "缓存摘要"
    assert result["video_metadata"]["title"] == "缓存标题"


def test_cn_flow_official_channel_video_skips_analysis(monkeypatch):
    module = _install_cn_flow_mocks(monkeypatch, scraped=_scraped_ok())
    monkeypatch.setattr(
        "app.domains.kol.video_url_resolver.find_official_channel_match",
        lambda identity: {"id": 3, "platform": "bilibili", "handle": "viltrox"},
    )

    result = module.run_cn_platform_video_for_job(_base_payload("https://www.bilibili.com/video/BV1S6Kr6mEgi"))

    assert result["status"] == "official_channel_video"
    assert result["ai_analysis"]["state"] == "skipped"
    assert result["llm_calls_performed"] is False


def test_cn_flow_scrape_failure_raises_honest_error(monkeypatch):
    scraped = {
        "ok": False,
        "provider_status": "no_items",
        "error": "xiaohongshu_link_token_expired_or_note_unavailable",
        "metadata": {},
        "creator": {},
        "native_video_id": "",
        "direct_video_url": "",
    }
    module = _install_cn_flow_mocks(monkeypatch, scraped=scraped)

    with pytest.raises(RuntimeError, match="cn_video_resolve_failed:no_items:xiaohongshu_link_token_expired"):
        module.run_cn_platform_video_for_job(
            _base_payload("https://www.xiaohongshu.com/explore/64608fa90000000027003d64?xsec_token=T")
        )


# ---------------------------------------------- D. dry-run 计划 / HTTP defer


def test_dry_run_cn_video_returns_analysis_only_plan():
    result = dry_run_url_deep_crawl({"url": "https://www.bilibili.com/video/BV11jKm6bE61"})

    assert result["url_type"] == "video"
    assert result["platform"] == "bilibili"
    assert result["in_pool"] is False
    assert result["video_flow"]["status"] == "cn_platform_video_planned"
    assert result["video_flow"]["cn_platform_video"] is True
    assert result["profile_flow"]["status"] == "not_applicable"
    assert result["next_action"]["code"] == "cn_platform_video"
    assert result["safety"]["provider_calls_performed"] is False


def test_dry_run_cn_profile_url_is_video_only_hint():
    result = dry_run_url_deep_crawl({"url": "https://space.bilibili.com/67063436"})

    assert result["url_type"] == "unknown"
    assert result["next_action"]["code"] == "cn_platform_video_only"


def test_http_defer_condition_accepts_cn_video(monkeypatch):
    from app.api.routers import vkpi_kol_pool_search as router_module

    seen: dict = {}

    def fake_dry_run(body):
        return dry_run_url_deep_crawl({"url": body["url"]})

    def fake_ensure(**kwargs):
        seen["ensure"] = kwargs
        return {"id": 77}

    def fake_enqueue(url, **kwargs):
        seen["enqueue"] = {"url": url, **kwargs}
        return {
            "status": "queued",
            "job_id": 9001,
            "job_type": "video_url_resolve",
            "write_db": True,
            "provider_calls_performed": False,
            "resolution_progress": {"status": "queued"},
            "ai_analysis": {"state": "waiting_for_evidence"},
        }

    def fake_attach(session_id, result):
        seen["attach"] = {"session_id": session_id}
        return {"session_id": session_id, "items": []}

    monkeypatch.setattr(router_module.kol_url_deep_crawl, "dry_run_url_deep_crawl", fake_dry_run)
    monkeypatch.setattr(router_module.kol_search_sessions, "ensure_session_for_result", fake_ensure)
    monkeypatch.setattr(router_module.kol_url_deep_crawl, "enqueue_video_url_resolve_job", fake_enqueue)
    monkeypatch.setattr(router_module.kol_search_sessions, "attach_url_result", fake_attach)

    result = router_module._run_url_deep_crawl(
        {"url": "https://www.douyin.com/video/7534679152504376595", "execute": True},
        staff={"id": 1, "user_id": 1},
        default_defer_profile=True,
        default_create_session=True,
        default_source="test",
    )

    assert seen["enqueue"]["url"] == "https://www.douyin.com/video/7534679152504376595"
    assert result["deferred_to_queue"] is True
    assert result["video_flow"]["operation"] == "video_url_resolve_queue"
