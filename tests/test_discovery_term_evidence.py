"""证据埋点车道:让「用了什么词、各自产出多少」变成可 SELECT 的事实(2026-08-27)。

本文件守的是**证据**,不是实现细节。四样必须落库:
  ① 本次实际用了哪几条检索词(真发出去的,不是预报常量);
  ② 产品锚是什么、来自哪条路径;
  ③ 相关性判定时手里有几个字段;
  ④ 每条检索词各自产出几个合格新人、烧掉多少配额。
外加把「每轮固定按 301 预报、实际 201」的 50% 高估修掉,并让真实消耗可对账。

**单元测试绿不算完成**,所以这里有一条端到端:真的跑一遍 pipeline 的在线严格腿,
断言诊断落库那一次拿到的 patch 里四样齐全、且每条词的配额与真实调用次数对得上。

绝对红线:本文件不碰任何质量口径(新鲜度天数 / required_terms / 器材证据 / 粉丝下限 /
检测器阈值),零触 viltrox_fit_score / rule_v0,零新增 Apify 调用。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_evidence as ev
from app.domains.kol import profile_discovery_rounds as rounds
from app.services.intelligence import account_search_discovery


# ── 配额记账 v2:独立 Search Queries + combined quota ────────────────────────
def test_youtube_usage_keeps_search_and_combined_quota_separate() -> None:
    one = rounds.youtube_usage_forecast(1)
    assert one == {
        "youtube_search_calls": 1,
        "youtube_combined_quota_units": 2,
        "youtube_api_calls": 3,
    }
    five = rounds.youtube_usage_forecast(5)
    assert five["youtube_search_calls"] == 5
    assert five["youtube_combined_quota_units"] == 2
    assert five["youtube_api_calls"] == 7
    assert rounds.youtube_usage_forecast(99)["youtube_search_calls"] == rounds.YOUTUBE_QUERY_VARIANTS_CEILING
    assert rounds.youtube_quota_units(1) == 2
    assert rounds.YOUTUBE_QUOTA_UNITS_PER_ROUND == 2


def test_forecast_uses_the_variant_count_so_the_round_gate_reads_a_real_number() -> None:
    two = rounds.round_cost_forecast(["youtube"], round_no=2, youtube_query_variants=2)
    assert two["youtube_search_calls"] == 2
    assert two["youtube_combined_quota_units"] == 2
    assert two["youtube_quota_units"] == 2
    assert two["youtube_quota_units_deprecated"] is True
    assert two["youtube_api_calls"] == 4
    assert two["estimated_usd"] == 0.0            # YouTube 腿零 Apify 花费,口径不变
    # 不传变体数 = 旧行为逐字不变(三条腿全上的既有账不许被这刀改动)。
    legacy = rounds.round_cost_forecast(
        ["youtube", "instagram", "tiktok"], round_no=1,
        per_platform_limits={"youtube": 50, "instagram": 20, "tiktok": 20},
    )
    assert legacy["youtube_search_calls"] == 3
    assert legacy["youtube_combined_quota_units"] == 2
    assert legacy["youtube_api_calls"] == 5
    assert legacy["apify_runs"] == 3
    assert legacy["estimated_usd"] == pytest.approx(0.652, abs=0.01)


def test_round_plan_record_reports_both_actual_buckets_against_forecast() -> None:
    forecasts = [
        rounds.round_cost_forecast(["youtube"], round_no=index, youtube_query_variants=3)
        for index in (1, 2, 3)
    ]
    record = rounds.round_plan_record(
        forecasts=forecasts, provider_rounds=3,
        actual_search_calls=6, actual_combined_quota_units=6,
        actual_youtube_api_calls=12, actual_apify_runs=0,
    )
    assert record["youtube_search_calls_total"] == 9
    assert record["youtube_search_calls_actual"] == 6
    assert record["youtube_combined_quota_units_total"] == 6
    assert record["youtube_combined_quota_units_actual"] == 6
    assert record["youtube_api_calls_total"] == 15
    assert record["youtube_api_calls_actual"] == 12
    assert record["youtube_quota_units_total"] == 6
    assert record["youtube_quota_units_deprecated"] is True
    assert record["apify_runs_actual"] == 0
    # 没有实际值时不拿预报冒充实际(诚实空态,读端能分辨)。
    blind = rounds.round_plan_record(forecasts=forecasts, provider_rounds=3)
    assert blind["youtube_quota_units_actual"] is None
    assert blind["quota_forecast_delta_units"] is None


def test_forecast_tracks_whatever_the_term_builder_actually_plans() -> None:
    """预报必须跟着**真实**变体数走,不写死 —— 检索词车道换词表时不许再回到 301 虚报。

    这里刻意不钉具体词数(那是检索词车道的口径,不是记账车道的):只钉「预报 = 真实
    变体数 × 100 + 1」这条恒等式,以及空 query 不虚报任何配额。
    """
    for query in ("Viltrox lens review", "Viltrox AF 135mm f1.8 LAB review Sony E mount portrait"):
        planned = ev.planned_youtube_variants(query)
        assert 1 <= planned <= rounds.YOUTUBE_QUERY_VARIANTS_CEILING
        forecast = rounds.round_cost_forecast(
            ["youtube"], round_no=1, youtube_query_variants=planned,
        )
        assert forecast["youtube_search_calls"] == planned
        assert forecast["youtube_combined_quota_units"] == 2
        assert forecast["youtube_api_calls"] == planned + 2
    assert ev.planned_youtube_variants("") == 0


# ── 写端契约:候选到底带不带检索词溯源标 ─────────────────────────────────────
def _fake_youtube_crawler(monkeypatch: pytest.MonkeyPatch, per_variant: int = 1) -> list[dict[str, Any]]:
    """每条变体各回 ``per_variant`` 条视频;记录每次 search 请求的参数。"""
    calls: list[dict[str, Any]] = []

    class FakeCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "search":
                calls.append(dict(params))
                # 频道 id 按**整条**检索词区分:不同变体必须给出不同频道,否则会被
                # seen_channels 去重掉,测试就测不到「装满提前 break」那条路径了。
                tag = f"q{len(calls)}"
                return {
                    "provider_status": "ok",
                    "items": [
                        {
                            "id": {"videoId": f"vid-{tag}-{index}"},
                            "snippet": {
                                "channelId": f"UC-{tag}-{index}",
                                "channelTitle": f"{tag}{index}",
                                "title": f"{tag} lens test {index}",
                                "publishedAt": "2026-08-20T00:00:00Z",
                            },
                        }
                        for index in range(per_variant)
                    ],
                }
            return {
                "provider_status": "ok",
                "items": [
                    {
                        "id": cid,
                        "snippet": {"customUrl": f"@{cid.lower()}", "country": "US"},
                        "statistics": {"subscriberCount": "50000"},
                    }
                    for cid in str(params.get("id") or "").split(",")
                    if cid
                ],
            }

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    from app.platform.industry_crawlers import youtube_crawler

    monkeypatch.setattr(youtube_crawler, "YouTubeCrawler", FakeCrawler)
    return calls


_LONG_QUERY = "young creators shooting portraits with the Viltrox AF 135mm f1.8 LAB Sony"


def test_youtube_candidates_carry_the_search_term_that_found_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一轮发多条变体、按 channelId 合并之后就分不清了 —— 溯源必须在候选身上。"""
    _fake_youtube_crawler(monkeypatch, per_variant=1)
    result = asyncio.run(account_search_discovery.search_platform_content(
        "youtube", _LONG_QUERY, market="US", max_results=9, strict_evidence=True,
    ))
    items = result["items"]
    assert items, "假 crawler 必须回候选,否则本测试什么都没证明"
    tags = {item[ev.CANDIDATE_TERM_KEY] for item in items}
    assert all(tags), "每条候选都要带检索词溯源标"
    # 真发出去的那几条词 = metadata 里的 provider_queries,溯源标只能是它们中的一个。
    assert tags.issubset(set(result["metadata"]["provider_queries"]))


def test_reported_quota_matches_the_calls_actually_made(monkeypatch: pytest.MonkeyPatch) -> None:
    """装满提前 break 时只发 2 条 → 配额 201,与真实调用次数对得上(不是 301)。"""
    calls = _fake_youtube_crawler(monkeypatch, per_variant=1)
    result = asyncio.run(account_search_discovery.search_platform_content(
        "youtube", _LONG_QUERY, market="US", max_results=2, strict_evidence=True,
    ))
    search_calls = [row for row in calls if "q" in row]
    used = result["metadata"]["provider_queries"]
    assert len(search_calls) == len(used) == 2, "装满 2 条就该停,第 3 条变体不该发"
    assert result["metadata"]["youtube_search_calls"] == len(used) == 2
    assert result["metadata"]["youtube_combined_quota_units"] == 2
    assert result["metadata"]["youtube_api_calls"] == 4
    assert result["metadata"]["quota_units_deprecated"] is True


def test_real_provider_path_keeps_the_term_tag_all_the_way_to_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接不进生产链路的埋点等于没做:这里跑**真的** discover_new_creators。

    候选要穿过 annotate_platform_items / 身份归一 / 品牌官号闸 / 相机信号闸 / 触达闸,
    溯源标必须一路活着,``observe_round`` 才能给出 per_item 归因。假的只有 HTTP 那一层。
    """
    from app.domains.kol.profile_discovery_provider import discover_new_creators

    class FakeCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "search":
                tag = str(params.get("q") or "").split()[0]
                return {"provider_status": "ok", "items": [
                    {
                        "id": {"videoId": f"v-{tag}-{index}"},
                        "snippet": {
                            "channelId": f"UC{tag}{index:018d}"[:24],
                            "channelTitle": f"{tag}{index}",
                            "title": f"{tag} viltrox lens review camera test {index}",
                            "publishedAt": "2026-08-20T00:00:00Z",
                        },
                    }
                    for index in range(3)
                ]}
            return {"provider_status": "ok", "items": [
                {
                    "id": cid,
                    "snippet": {"customUrl": f"@{cid.lower()}", "country": "US",
                                "description": "camera lens reviewer"},
                    "statistics": {"subscriberCount": "50000"},
                }
                for cid in str(params.get("id") or "").split(",") if cid
            ]}

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    from app.platform.industry_crawlers import youtube_crawler

    monkeypatch.setattr(youtube_crawler, "YouTubeCrawler", FakeCrawler)
    result = asyncio.run(discover_new_creators(
        query_text=_LONG_QUERY, platforms=["youtube"], market="US",
        limit=20, per_platform_limit=20, auto_enroll=False,
    ))
    assert result["new_creators"], "闸门全过之后必须还有人,否则本测试什么都没证明"
    observation = ev.observe_round(
        round_no=1,
        platform_results=result["platform_results"],
        candidates=result["new_creators"],
    )
    leg = observation["legs"][0]
    assert leg["attribution"] == "per_item"
    assert leg["candidates_untagged"] == 0
    assert sum(leg["candidates_by_term"].values()) == len(result["new_creators"])
    assert set(leg["candidates_by_term"]) <= set(leg["terms"])
    assert leg["youtube_search_calls_actual"] == leg["term_count"]
    assert leg["youtube_combined_quota_units_actual"] == 2
    assert leg["youtube_api_calls_actual"] == leg["term_count"] + 2
    # 落库的用词是**真发出去的**那几条(带市场后缀),不是 payload 里的原句。
    assert _LONG_QUERY not in leg["terms"]
