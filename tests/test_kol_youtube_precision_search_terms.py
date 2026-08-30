"""精准检索词(品牌+品类窄词并联)——2026-08-27「搜出来一堆再筛选 → 精准搜索」车道。

守的是**本机真链路实测**结论,不是口味(实测表在 account_search_terms 模块头):
- 无锚泛词("photography gear review")21 人过相关闸、**0 人**过 8 道严格闸 → 每条词必须带锚;
- 有锚但无意图词("Viltrox prime lens")出货率 2.4% → 每条词必须带 review/test;
- 锚到型号级("Viltrox AF 135mm f1.8 LAB")语料已抓干、0 产出 → 光圈/系列词必须被剥掉;
- 同 query 重跑返回逐条相同、0 产出 → 抓干的词必须被跳过,不许重发第一页。

红线自证:本文件零触任何质量判据(新鲜度/粉丝下限/器材证据/检测器阈值),只测检索词与记账。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from app.services.intelligence import account_search_discovery as discovery
from app.services.intelligence import account_search_terms as terms
from app.services.intelligence.account_search_youtube_metrics import (
    youtube_sample_video_ids,
    youtube_video_statistics,
)

PERSONA = "young content creators Sony E-mount 135mm portrait prime lens review photography video"
MODEL_QUERY = "Viltrox AF 135mm f1.8 LAB Sony E mount portrait creators"
_INTENT_WORDS = {"review", "test"}


def test_every_precision_term_carries_an_anchor_and_an_intent_word() -> None:
    ladder = terms.youtube_precision_terms(PERSONA)
    assert len(ladder) == 6
    for row in ladder:
        assert row["anchor"], row
        assert row["anchor_source"] in {
            "own_brand_category", "mount_category", "use_case_category",
            "focal_family_category", "peer_brand_category",
        }
        assert row["term"].split()[-1] in _INTENT_WORDS, row
        # 锚必须真的出现在词里 —— 记账里写了锚、词里没有,就是假记账。
        assert row["anchor"].split()[0].lower() in row["term"].lower()


def test_prospect_anchors_lead_and_own_brand_trails() -> None:
    """用户裁决「要找的是 135 的**潜在**用户,不是已经有 135 的」的落点。

    自有品牌词捞回的人手上已经有我们的镜头 —— 出货率再高也不是要找的人,排到最后。
    代价比预期小:实测最高产的 ``Sony lens review``(26.0%,harness 真链路第一页)
    本来就在潜在用户档里,旧排 tier 6 被 cap 砍掉,从来没发出去过、也就没人测过它。
    """
    ladder = terms.youtube_precision_terms(PERSONA)
    assert ladder[0]["anchor_source"] == "peer_brand_category"
    assert ladder[0]["term"] == "Sony lens review"
    # 自有品牌两条必须垫底 —— 它们一旦回到头位就会把整轮配额吃光(装满即停)。
    assert [row["anchor_source"] for row in ladder[-2:]] == ["own_brand_category"] * 2
    prospect = [r["anchor_source"] for r in ladder if r["anchor_source"] != "own_brand_category"]
    assert prospect == ["peer_brand_category", "focal_family_category",
                        "use_case_category", "mount_category"]


def test_model_level_tokens_are_stripped_and_recorded() -> None:
    signals = terms.query_anchor_signals(MODEL_QUERY)
    assert signals["dropped_model_tokens"] == ["f1.8", "lab"]
    # 焦段家族是允许的锚,不算型号 token。
    assert signals["focal_family"] == "135mm"
    for row in terms.youtube_precision_terms(MODEL_QUERY):
        lowered = row["term"].lower()
        assert "f1.8" not in lowered and "lab" not in lowered
        # 品牌与焦段绝不同时出现在一条词里 —— 那就是型号级检索词。
        assert not ("viltrox" in lowered and "135mm" in lowered)


def test_focal_length_implies_lens_category() -> None:
    """整句只写 135mm 没写 lens:不推品类的话会掉回旧 5 词块,第一条就是型号级检索词。"""
    assert terms.query_anchor_signals(MODEL_QUERY)["category"] == "lens"
    assert terms._youtube_search_query_variants(MODEL_QUERY, max_variants=6)[0] == "Sony lens review"


def test_non_gear_query_falls_back_to_legacy_chunks_unchanged() -> None:
    """认不出器材品类 = 这不是器材检索 → 整条精准路径让位,零回归。"""
    plain = "vegan cooking channels in germany"
    assert terms.youtube_precision_terms(plain) == []
    assert terms._youtube_search_query_variants(plain) == [plain]
    assert terms._youtube_search_query_variants("street photographer") == ["street photographer"]
    assert terms._youtube_search_query_variants("") == []


def test_short_operator_query_keeps_its_own_words_first() -> None:
    """operator 在 lens_monitor / kol_ops 手打的短词是显式意图,不许被改写掉。"""
    variants = terms._youtube_search_query_variants("Viltrox 135mm lens review", max_variants=5)
    assert variants[0] == "Viltrox 135mm lens review"
    assert "Viltrox lens review" in variants


def test_terms_are_deterministic_because_page_cursors_are_keyed_by_term() -> None:
    assert terms.youtube_precision_terms(PERSONA) == terms.youtube_precision_terms(PERSONA)


def test_no_precision_term_is_a_bare_generic() -> None:
    """实测唯一 0 产出的形状:泛词 + 意图词、零产品锚。它绝不许被造出来。"""
    generated = {row["term"].lower() for row in terms.youtube_precision_terms(PERSONA)}
    assert "photography gear review" not in generated
    assert "camera gear review" not in generated


class _FakeCrawler:
    """只回 search/channels 两种响应的假 crawler;记录每次实发的 q 与 pageToken。"""

    api_key = "test-key"

    def __init__(self, pages: dict[str, str | None]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, dict(params)))
        if endpoint == "videos":
            return {"provider_status": "ok", "items": [
                {
                    "id": video_id,
                    "statistics": {"viewCount": "1200", "likeCount": "40"},
                    "contentDetails": {"duration": "PT1M30S"},
                }
                for video_id in str(params.get("id") or "").split(",") if video_id
            ]}
        if endpoint == "channels":
            return {"provider_status": "ok", "items": [
                {"id": cid, "snippet": {"title": "creator", "customUrl": f"@{cid[-4:]}"},
                 "statistics": {"subscriberCount": "5000"}}
                for cid in str(params.get("id") or "").split(",") if cid
            ]}
        query = str(params.get("q") or "")
        return {
            "provider_status": "ok",
            "nextPageToken": self.pages.get(query) or "",
            "items": [{
                "id": {"videoId": f"vid-{query[:6]}-{len(self.calls)}"},
                "snippet": {
                    "channelId": f"UC-{query[:8]}-{len(self.calls)}",
                    "channelTitle": "creator",
                    "description": "on-camera flash setup for motorsport photography",
                },
            }],
        }

    @staticmethod
    def _should_use_apify_fallback(payload: dict[str, Any]) -> bool:
        return False


@pytest.fixture()
def fake_crawler(monkeypatch: pytest.MonkeyPatch):
    holder: dict[str, _FakeCrawler] = {}

    def install(pages: dict[str, str | None]) -> _FakeCrawler:
        crawler = _FakeCrawler(pages)
        holder["crawler"] = crawler
        import app.platform.industry_crawlers.youtube_crawler as yt

        monkeypatch.setattr(yt, "YouTubeCrawler", lambda *a, **k: crawler)
        return crawler

    return install


def _run(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(discovery._youtube_data_api_strict_video_search(PERSONA, **kwargs))


def test_term_ledger_records_anchor_quota_and_exhaustion(fake_crawler) -> None:
    crawler = fake_crawler({"Sony lens review": "TOKEN-2"})
    result = _run(safe_limit=3)
    meta = result["metadata"]
    ledger = meta["term_ledger"]
    assert [row["term"] for row in ledger][:2] == ["Sony lens review", "135mm lens review"]
    assert ledger[0]["anchor_source"] == "peer_brand_category"
    assert ledger[0]["exhausted"] is False
    assert ledger[1]["exhausted"] is True  # 假 crawler 只给第一条词发了 nextPageToken
    # v2 keeps Search Queries and combined quota in separate buckets.
    searches = sum(1 for endpoint, _ in crawler.calls if endpoint == "search")
    assert meta["youtube_search_calls"] == searches
    assert meta["youtube_combined_quota_units"] == 2
    assert meta["youtube_api_calls"] == searches + 2
    assert meta["quota_units"] == 2
    assert meta["quota_units_deprecated"] is True
    assert sum(row["youtube_search_calls"] for row in ledger) == searches
    assert meta["query_anchor_signals"]["category"] == "lens"


def test_exhausted_term_is_skipped_next_round_instead_of_refetching_page_one(fake_crawler) -> None:
    """同 query 重跑无效的落点:抓干的词进游标当哨兵,下一轮 0 配额跳过。

    ``safe_limit=50`` 是刻意的:让第一轮把 6 条词**全部**发一遍,第二轮就没有「没发过的
    词」可优先了 —— 否则轮转会先发未发过的词,那样即便哨兵完全失灵,抓干的词也照样不会
    出现在第二轮里,测试就证明不了哨兵在起作用(只证明了轮转在起作用)。
    """
    first = fake_crawler({"Sony lens review": "TOKEN-2"})
    cursor = _run(safe_limit=50)["metadata"]["next_page_cursor"]
    assert cursor["Sony lens review"] == "TOKEN-2"
    exhausted = {t for t, tok in cursor.items() if tok == terms.TERM_EXHAUSTED_TOKEN}
    assert "135mm lens review" in exhausted
    assert len(cursor) == 6  # 6 条词这一轮全发过了,第二轮无「未发过」可优先

    second = fake_crawler({"Sony lens review": ""})
    meta = _run(safe_limit=50, page_cursor=cursor)["metadata"]
    issued = [params.get("q") for endpoint, params in second.calls if endpoint == "search"]
    assert not (exhausted & set(issued))  # 哨兵生效:抓干的词一条都没再发
    assert "Sony lens review" in issued   # 没抓干的词照常续页,不是整体停摆
    assert first is not second
    skipped = [row for row in meta["term_ledger"] if row.get("skipped") == "exhausted_previous_round"]
    assert skipped and all(row["quota_units"] == 0 for row in skipped)


def test_has_more_is_false_once_every_term_is_exhausted(fake_crawler) -> None:
    """全部词都抓干 → 诚实说没有下一页;哨兵不许被当成「还有游标」。"""
    fake_crawler({})
    meta = _run(safe_limit=50)["metadata"]
    assert meta["has_more"] is False
    assert set(meta["next_page_cursor"].values()) == {terms.TERM_EXHAUSTED_TOKEN}


def test_targeted_query_cell_is_sent_exactly_once_without_brand_ladder(fake_crawler) -> None:
    """Prospective-growth cells belong to the planner, not the legacy term expander."""

    exact = "motorsport photographer on-camera flash tutorial"
    crawler = fake_crawler({exact: "NEXT"})
    result = asyncio.run(
        discovery._youtube_data_api_strict_video_search(
            exact,
            safe_limit=12,
            exact_query=True,
        )
    )

    issued = [params.get("q") for endpoint, params in crawler.calls if endpoint == "search"]
    assert issued == [exact]
    assert all("viltrox" not in str(query).lower() for query in issued)
    assert result["metadata"]["query_mode"] == "exact_query_cell"
    assert result["metadata"]["term_ledger"][0]["anchor_source"] == "query_cell_exact"
    assert result["items"][0]["sample_description"] == "on-camera flash setup for motorsport photography"


def test_strict_youtube_keeps_lifetime_scale_display_only_and_enriches_exact_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class MetricsCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append((endpoint, dict(params)))
            if endpoint == "search":
                return {
                    "provider_status": "ok",
                    "items": [{
                        "id": {"videoId": "video-1"},
                        "snippet": {
                            "channelId": "UC-metrics",
                            "channelTitle": "Race Photographer",
                            "title": "Race-day speedlight workflow",
                            "description": "motorsport photography with an on-camera flash",
                            "publishedAt": "2026-08-20T12:00:00Z",
                        },
                    }],
                }
            if endpoint == "videos":
                assert params["part"] == "statistics,contentDetails"
                assert params["id"] == "video-1"
                return {
                    "provider_status": "ok",
                    "items": [{
                        "id": "video-1",
                        "statistics": {"viewCount": "42000", "likeCount": "2100"},
                        "contentDetails": {"duration": "PT2M5S"},
                    }],
                }
            assert endpoint == "channels"
            return {
                "provider_status": "ok",
                "items": [{
                    "id": "UC-metrics",
                    "snippet": {"customUrl": "@racephotographer"},
                    "statistics": {
                        "subscriberCount": "50000",
                        "videoCount": "200",
                        "viewCount": "5000000",
                    },
                }],
            }

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", MetricsCrawler)
    result = asyncio.run(discovery._youtube_data_api_strict_video_search(
        "motorsport photographer on-camera flash",
        safe_limit=10,
        exact_query=True,
    ))

    assert calls[0][0] == "search"
    assert {endpoint for endpoint, _params in calls[1:]} == {"channels", "videos"}
    item = result["items"][0]
    assert "avg_views" not in item
    assert item["channel_lifetime_views"] == 5_000_000
    assert item["channel_public_video_count"] == 200
    assert item["channel_lifetime_views_per_public_video"] == 25_000
    assert item["representative_video_views"] == 42_000
    assert item["representative_video_likes"] == 2_100
    assert "representative_video_comments" not in item
    assert item["representative_video_published_at"] == "2026-08-20T12:00:00Z"
    assert item["representative_video_duration"] == "PT2M5S"
    assert item["representative_video_duration_seconds"] == 125
    assert item["activation_sample_count"] == 1
    assert item["activation_metrics_source"] == "youtube_data_api.videos.list"
    assert item["activation_metrics_scope"] == "exact_query_hit_45d"
    assert item["claim_status"] == "descriptive_only"
    assert result["metadata"]["youtube_search_calls"] == 1
    assert result["metadata"]["youtube_combined_quota_units"] == 2
    assert result["metadata"]["youtube_api_calls"] == 3


def test_repeated_channel_hits_become_three_sample_activation_without_extra_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class MultiVideoCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append(endpoint)
            if endpoint == "search":
                return {
                    "provider_status": "ok",
                    "items": [
                        {
                            "id": {"videoId": f"v{index}"},
                            "snippet": {
                                "channelId": "UC-same",
                                "channelTitle": "Working Creator",
                                "title": f"Flash workflow {index}",
                                "description": "motorsport photographer speedlight tutorial",
                                "publishedAt": f"2026-08-2{index}T12:00:00Z",
                            },
                        }
                        for index in range(1, 4)
                    ],
                }
            if endpoint == "channels":
                return {
                    "provider_status": "ok",
                    "items": [{
                        "id": "UC-same",
                        "snippet": {"customUrl": "@workingcreator"},
                        "statistics": {"subscriberCount": "100000"},
                    }],
                }
            ids = str(params["id"]).split(",")
            return {
                "provider_status": "ok",
                "items": [
                    {
                        "id": video_id,
                        "statistics": {
                            "viewCount": str(10_000 * index),
                            "likeCount": str(500 * index),
                            "commentCount": str(50 * index),
                        },
                        "contentDetails": {"duration": "PT2M"},
                    }
                    for index, video_id in enumerate(ids, start=1)
                ],
            }

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", MultiVideoCrawler)
    result = asyncio.run(discovery._youtube_data_api_strict_video_search(
        "motorsport photographer speedlight tutorial", exact_query=True,
    ))

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["activation_sample_count"] == 3
    assert item["activation_metrics_scope"] == "exact_query_hits_45d_aggregate"
    assert item["activation_evidence_status"] == "observed_multi_sample"
    assert item["avg_views"] == 20_000
    assert item["avg_likes"] == 1_000
    assert item["avg_comments"] == 100
    assert item["views_per_follower"] == pytest.approx(0.2)
    assert item["activation_metric_sample_counts"] == {
        "avg_views": 3,
        "engagement": 3,
        "views_per_follower": 3,
        "comments_per_follower": 3,
    }
    assert len(item["recent_videos"]) == 3
    assert calls.count("search") == calls.count("channels") == calls.count("videos") == 1
    assert result["metadata"]["youtube_api_calls"] == 3
    assert result["metadata"]["activation_multi_sample_candidates"] == 1
    assert result["metadata"]["activation_pending_candidates"] == 0


def test_expanded_variants_cannot_count_the_same_video_three_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class DuplicateVideoCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append(endpoint)
            if endpoint == "search":
                return {"provider_status": "ok", "items": [{
                    "id": {"videoId": "v-same"},
                    "snippet": {
                        "channelId": "UC-same",
                        "channelTitle": "Creator",
                        "title": f"Result for {params['q']}",
                        "publishedAt": "2026-08-20T00:00:00Z",
                    },
                }]}
            if endpoint == "channels":
                return {"provider_status": "ok", "items": [{
                    "id": "UC-same", "snippet": {},
                    "statistics": {"subscriberCount": "100000"},
                }]}
            return {"provider_status": "ok", "items": [{
                "id": "v-same",
                "statistics": {"viewCount": "50000", "likeCount": "1000", "commentCount": "50"},
                "contentDetails": {},
            }]}

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", DuplicateVideoCrawler)
    monkeypatch.setattr(discovery, "_youtube_search_query_variants", lambda *_args, **_kwargs: ["q1", "q2", "q3"])
    result = asyncio.run(discovery._youtube_data_api_strict_video_search(
        "motorsport flash", exact_query=False,
    ))

    item = result["items"][0]
    assert calls.count("search") == 3
    assert item["activation_sample_count"] == 1
    assert item["activation_metrics_scope"] == "expanded_query_hit_45d"
    assert item["activation_query_mode"] == "expanded_ladder"
    assert item["recent_videos"][0]["video_id"] == "v-same"


def test_representative_identity_follows_the_first_video_with_observed_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PartialStatsCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, _params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "search":
                return {"provider_status": "ok", "items": [
                    {"id": {"videoId": "v-missing"}, "snippet": {
                        "channelId": "UC1", "channelTitle": "Creator", "title": "Missing stats",
                    }},
                    {"id": {"videoId": "v-observed"}, "snippet": {
                        "channelId": "UC1", "channelTitle": "Creator", "title": "Observed stats",
                        "description": "real workflow", "publishedAt": "2026-08-22T00:00:00Z",
                    }},
                ]}
            if endpoint == "channels":
                return {"provider_status": "ok", "items": [{
                    "id": "UC1", "snippet": {}, "statistics": {"subscriberCount": "90000"},
                }]}
            return {"provider_status": "ok", "items": [{
                "id": "v-observed", "statistics": {"viewCount": "8000"}, "contentDetails": {},
            }]}

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", PartialStatsCrawler)
    result = asyncio.run(discovery._youtube_data_api_strict_video_search("camera workflow", exact_query=True))
    item = result["items"][0]
    assert item["video_id"] == "v-observed"
    assert item["source_url"].endswith("v-observed")
    assert item["sample_title"] == "Observed stats"
    assert item["representative_video_views"] == 8000


def test_channel_and_video_enrichment_start_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)

    class SearchCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, _params: dict[str, Any]) -> dict[str, Any]:
            assert endpoint == "search"
            return {
                "provider_status": "ok",
                "items": [{
                    "id": {"videoId": "v1"},
                    "snippet": {"channelId": "UC1", "channelTitle": "Creator"},
                }],
            }

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    def channel_enrich(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        barrier.wait(timeout=1)
        return {"UC1": {"subscribers": 50_000}}

    def video_enrich(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        barrier.wait(timeout=1)
        return {"v1": {"representative_video_views": 5_000}}

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", SearchCrawler)
    monkeypatch.setattr(discovery, "_youtube_channel_statistics", channel_enrich)
    monkeypatch.setattr(discovery, "youtube_video_statistics", video_enrich)

    result = asyncio.run(discovery._youtube_data_api_strict_video_search(
        "food photographer camera gear", exact_query=True,
    ))
    assert result["items"][0]["followers"] == 50_000
    assert result["items"][0]["representative_video_views"] == 5_000


def test_videos_list_failure_keeps_candidate_pending_without_query_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FailingVideoCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append(endpoint)
            if endpoint == "search":
                return {"provider_status": "ok", "items": [{
                    "id": {"videoId": "v1"},
                    "snippet": {"channelId": "UC1", "channelTitle": "Creator"},
                }]}
            if endpoint == "channels":
                return {"provider_status": "ok", "items": [{
                    "id": "UC1", "snippet": {}, "statistics": {"subscriberCount": "70000"},
                }]}
            return {"provider_status": "error", "items": []}

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", FailingVideoCrawler)
    result = asyncio.run(discovery._youtube_data_api_strict_video_search(
        "motorsport photographer on-camera flash", exact_query=True,
    ))
    assert calls[0] == "search"
    assert set(calls[1:]) == {"channels", "videos"}
    assert len(result["items"]) == 1
    assert result["items"][0]["activation_evidence_status"] == "provider_error"
    assert "representative_video_views" not in result["items"][0]
    assert result["metadata"]["video_enrichment_status"] == "provider_error"


def test_video_statistics_caps_fifty_ids_in_one_batch_and_keeps_missing_null() -> None:
    calls: list[dict[str, Any]] = []

    class BatchCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            assert endpoint == "videos"
            calls.append(dict(params))
            ids = str(params["id"]).split(",")
            return {"provider_status": "ok", "items": [{
                "id": video_id,
                "statistics": {"viewCount": "0"},
                "contentDetails": {},
            } for video_id in ids]}

    result = youtube_video_statistics(BatchCrawler(), [f"v{i}" for i in range(70)])
    assert len(calls) == 1
    assert len(calls[0]["id"].split(",")) == 50
    assert len(result) == 50
    assert result["v0"]["representative_video_views"] == 0
    assert "representative_video_likes" not in result["v0"]
    assert "representative_video_comments" not in result["v0"]


def test_fifty_id_budget_gives_every_channel_one_then_completes_top_ranked() -> None:
    rows = [
        {
            "_channel_video_samples": [
                {"id": {"videoId": f"c{channel}-v{video}"}}
                for video in range(1, 4)
            ]
        }
        for channel in range(1, 26)
    ]

    ids = youtube_sample_video_ids(rows, limit=50)

    assert len(ids) == 50
    assert all(f"c{channel}-v1" in ids for channel in range(1, 26))
    assert all(f"c{channel}-v3" in ids for channel in range(1, 13))
    assert "c13-v3" not in ids


def test_exact_query_never_silently_falls_back_to_paid_apify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoKeyCrawler:
        api_key = ""

    class ScanService:
        @staticmethod
        def provider_ready() -> bool:
            return True

        @staticmethod
        async def _run_actor(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("exact QueryCell must not reach Apify")

    import app.platform.industry_crawlers.youtube_crawler as yt

    monkeypatch.setattr(yt, "YouTubeCrawler", NoKeyCrawler)
    monkeypatch.setattr(discovery, "_scan_service", lambda: ScanService)
    result = asyncio.run(discovery.search_platform_content(
        "youtube", "food photographer on-camera flash",
        strict_evidence=True, exact_query=True,
    ))
    assert result["status"] == "provider_unavailable"
    assert result["metadata"]["fallback_policy"] == "disabled_unforecast_provider_switch"
