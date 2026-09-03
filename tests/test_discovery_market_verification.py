"""在线新发现的「市场 vs 国家」核实(2026-09-02 T 车道实测钉两条)。

实测:在线新发现入池 ``country=''``,而会话项的 ``market`` 直接盖成查询里的 ``US``——没核实过;
同市场优先也不存在(有 market 时 UK/KR/HU 照样排前面)。

本测试钉死 provider 层的两条契约:
1. ``country`` 只在平台自报可得时补(YT ``snippet.country`` / TT ``authorMeta.region`` /
   IG 商家地址),取不到就**不带**——``market`` 照写查询市场但配 ``market_status`` 说清
   verified / unverified / mismatch,绝不用 market 冒充 country;
2. 有 market 时同市场优先:verified → unverified → mismatch 稳定分区,核实档之后的行打
   ``market_backfill=True``(「同市场不够,回填的」);没 market 时一字不动。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.intelligence import account_scan_service
from app.services.intelligence import account_search_discovery as discovery
from app.services.intelligence import account_search_terms as terms


# ── 纯函数:市场码 / 单条核实 ────────────────────────────────────────────────


def test_market_code_normalizes_aliases_and_refuses_global() -> None:
    assert terms.market_code("us") == "US"
    assert terms.market_code("uk") == "GB"
    assert terms.market_code("United Kingdom") == "GB"
    assert terms.market_code("global") == ""
    assert terms.market_code("") == ""
    assert terms.market_code(None) == ""


@pytest.mark.parametrize(
    ("country", "market", "expected"),
    [
        ("US", "us", terms.MARKET_STATUS_VERIFIED),
        ("uk", "GB", terms.MARKET_STATUS_VERIFIED),
        ("GB", "us", terms.MARKET_STATUS_MISMATCH),
        ("", "us", terms.MARKET_STATUS_UNVERIFIED),
        (None, "us", terms.MARKET_STATUS_UNVERIFIED),
        ("US", "", ""),
        ("US", "global", ""),
    ],
)
def test_market_verification_three_states(country: Any, market: Any, expected: str) -> None:
    assert terms.market_verification(country, market) == expected


# ── 纯函数:打标(country 只认平台字段;market 配核实状态)─────────────────────


def _yt_raw(channel_id: str) -> dict[str, Any]:
    return {
        "id": {"channelId": channel_id},
        "snippet": {"channelTitle": f"Creator {channel_id}", "description": "lens reviews"},
    }


def test_youtube_platform_country_is_kept_and_market_is_marked_mismatch() -> None:
    items = terms._youtube_data_api_normalize(
        [_yt_raw("UC-de"), _yt_raw("UC-unknown")],
        "viltrox lens", "US", "youtube-data-api/search.list", 5,
        stats_by_id={"UC-de": {"country": "DE", "subscribers": 5000, "custom_url": "@decreator"}},
    )
    rows = terms.annotate_market_verification(items, "us")

    de, unknown = rows
    assert de["country"] == "DE"
    assert de["country_source"] == "platform_profile"
    assert de["market"] == "US"
    assert de["market_source"] == terms.MARKET_SOURCE_QUERY
    assert de["market_status"] == terms.MARKET_STATUS_MISMATCH

    # 平台没给国家 → 不编:没有 country 键,market 只能是 unverified。
    assert "country" not in unknown
    assert unknown["market"] == "US"
    assert unknown["market_status"] == terms.MARKET_STATUS_UNVERIFIED


def test_annotate_without_market_only_fills_platform_country() -> None:
    rows = terms.annotate_market_verification(
        [{"handle": "a"}, {"handle": "b", "country": "JP"}],
        "",
        country_hints={"a": "US"},
    )
    assert rows[0]["country"] == "US"
    assert rows[0]["country_source"] == "platform_profile"
    assert "market_status" not in rows[0]
    assert "market" not in rows[0]
    # 候选已带国家 → 线索不覆盖。
    assert rows[1]["country"] == "JP"
    assert "country_source" not in rows[1]


def test_annotate_never_overwrites_existing_country_with_hint_or_market() -> None:
    rows = terms.annotate_market_verification(
        [{"handle": "jpcreator", "country": "JP"}], "US", country_hints={"jpcreator": "US"},
    )
    assert rows[0]["country"] == "JP"
    assert rows[0]["market_status"] == terms.MARKET_STATUS_MISMATCH


def test_raw_country_hints_reads_tiktok_region_and_instagram_business_address() -> None:
    tiktok_rows = [
        {"authorMeta": {"name": "@UKVlogger", "region": "GB"}, "text": "x"},
        {"authorMeta": {"name": "noregion"}, "text": "y"},
        {"author": "plainauthor", "region": "KR"},
        "not-a-row",
    ]
    ig_profiles = {
        "shopgal": {
            "username": "shopgal",
            "businessAddress": json.dumps({"city_name": "Austin, Texas", "country_code": "US"}),
        },
        "nobiz": {"username": "nobiz"},
    }
    hints = terms.raw_country_hints(tiktok_rows, ig_profiles)
    assert hints == {"ukvlogger": "GB", "plainauthor": "KR", "shopgal": "US"}


# ── 纯函数:同市场优先 + 回填标 ───────────────────────────────────────────────


def _candidate(handle: str, status: str) -> dict[str, Any]:
    return {"handle": handle, "market_status": status}


def test_prefer_market_items_partitions_stably_and_marks_backfill() -> None:
    rows = [
        _candidate("hu", terms.MARKET_STATUS_MISMATCH),
        _candidate("unknown-1", terms.MARKET_STATUS_UNVERIFIED),
        _candidate("us-1", terms.MARKET_STATUS_VERIFIED),
        _candidate("kr", terms.MARKET_STATUS_MISMATCH),
        _candidate("us-2", terms.MARKET_STATUS_VERIFIED),
        _candidate("unknown-2", terms.MARKET_STATUS_UNVERIFIED),
    ]
    ordered = terms.prefer_market_items(rows, "us")
    assert [row["handle"] for row in ordered] == ["us-1", "us-2", "unknown-1", "unknown-2", "hu", "kr"]
    assert [row.get("market_backfill") for row in ordered] == [None, None, True, True, True, True]
    assert terms.market_verification_summary(ordered) == {
        terms.MARKET_STATUS_VERIFIED: 2,
        terms.MARKET_STATUS_UNVERIFIED: 2,
        terms.MARKET_STATUS_MISMATCH: 2,
    }


def test_prefer_market_items_without_market_keeps_provider_order_untouched() -> None:
    rows = [_candidate("b", terms.MARKET_STATUS_MISMATCH), _candidate("a", terms.MARKET_STATUS_VERIFIED)]
    ordered = terms.prefer_market_items(rows, "")
    assert [row["handle"] for row in ordered] == ["b", "a"]
    assert all("market_backfill" not in row for row in ordered)
    assert terms.prefer_market_items(rows, "global") == rows


# ── 端到端:TikTok 腿(actor 原始行 → 候选 → 打标 → 同市场优先)────────────────


def _tt_row(handle: str, region: str = "") -> dict[str, Any]:
    author: dict[str, Any] = {
        "name": handle,
        "nickName": handle.title(),
        "avatar": f"https://p16-sign.tiktokcdn.com/{handle}.jpeg",
    }
    if region:
        author["region"] = region
    return {
        "authorMeta": author,
        "text": f"viltrox lens field test by {handle}",
        "webVideoUrl": f"https://www.tiktok.com/@{handle}/video/1",
    }


def test_tiktok_leg_reports_platform_country_and_prefers_requested_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_actor(actor_id: str, payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        assert actor_id == "clockworks/free-tiktok-scraper"
        return [_tt_row("ukvlogger", "GB"), _tt_row("nowhere"), _tt_row("usvlogger", "US")]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)

    result = asyncio.run(
        discovery.search_platform_content("tiktok", "viltrox lens", market="us", max_results=5),
    )

    assert result["status"] == "done"
    assert result["market"] == "US"
    assert [item["handle"] for item in result["items"]] == ["usvlogger", "nowhere", "ukvlogger"]
    us, nowhere, uk = result["items"]

    assert us["country"] == "US"
    assert us["country_source"] == "platform_profile"
    assert us["market_status"] == terms.MARKET_STATUS_VERIFIED
    assert "market_backfill" not in us

    assert "country" not in nowhere
    assert nowhere["market_status"] == terms.MARKET_STATUS_UNVERIFIED
    assert nowhere["market_backfill"] is True

    assert uk["country"] == "GB"
    assert uk["market_status"] == terms.MARKET_STATUS_MISMATCH
    assert uk["market_backfill"] is True

    assert {item["market"] for item in result["items"]} == {"US"}
    assert {item["market_source"] for item in result["items"]} == {terms.MARKET_SOURCE_QUERY}


def test_tiktok_leg_without_market_neither_reorders_nor_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_actor(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [_tt_row("ukvlogger", "GB"), _tt_row("usvlogger", "US")]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)

    result = asyncio.run(discovery.search_platform_content("tiktok", "viltrox lens", max_results=5))

    assert [item["handle"] for item in result["items"]] == ["ukvlogger", "usvlogger"]
    assert [item["country"] for item in result["items"]] == ["GB", "US"]
    assert all("market_status" not in item and "market_backfill" not in item for item in result["items"])
