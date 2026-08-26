"""车道 2:在线发现的**分页**与**多轮**,以及不许多轮把时间/钱线性放大的两道闸。

守的都是「行为」不是「实现」:
1. 分页真的翻页 —— 第二轮送出去的 pageToken 必须是第一轮回来的那个;
2. ``exhausted`` 基于**真的没有下一页**(actor 无游标 / 真到最后一页),
   绝不因为「跑过一轮了」就宣布耗尽;被轮次闸拦下时 exhausted 必须是 False;
3. 总耗时上限生效(整体 deadline 不够就不开新一轮);
4. 每日预算闸拦得住,读不到台账时**拒绝**额外付费轮(钱的方向失败安全);
5. IG 不被多轮放大成本 —— 默认只有 YouTube 参与第 2 轮起的翻页。

绝对红线:本文件不碰任何质量口径(新鲜度天数 / required_terms / 器材证据 / 粉丝下限),
只测供给侧轮次;零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_rounds as rounds
from app.domains.kol import profile_online_qualification
from app.services.intelligence import account_scan_service, account_search_discovery


# ── ① 分页:YouTube 严格视频路真的翻页 ─────────────────────────────────────────
def _fake_youtube_crawler(monkeypatch: pytest.MonkeyPatch, pages: dict[str | None, dict[str, Any]]):
    """按 pageToken 发不同页的假 crawler;记录每次 search 请求的参数。"""
    calls: list[dict[str, Any]] = []

    class FakeCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "search":
                calls.append(dict(params))
                return {"provider_status": "ok", **pages[params.get("pageToken")]}
            return {
                "provider_status": "ok",
                "items": [
                    {
                        "id": cid,
                        "snippet": {"customUrl": f"@{cid.lower()}"},
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


def _yt_page(channel: str, next_token: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "items": [{
            "id": {"videoId": f"vid-{channel}"},
            "snippet": {
                "channelId": f"UC-{channel}",
                "channelTitle": channel,
                "title": f"{channel} lens test",
                "publishedAt": "2026-08-20T00:00:00Z",
            },
        }],
    }
    if next_token:
        payload["nextPageToken"] = next_token
    return payload


def test_youtube_second_round_sends_the_first_round_page_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_youtube_crawler(monkeypatch, {
        None: _yt_page("alpha", "TOKEN-P2"),
        "TOKEN-P2": _yt_page("beta", None),
    })

    first = asyncio.run(account_search_discovery._youtube_data_api_strict_video_search(
        "viltrox lens review", safe_limit=1,
    ))
    assert first is not None
    md = first["metadata"]
    # 第一页:真给了 nextPageToken → 如实说还有下一页,并把游标交出来。
    assert md["pagination_supported"] is True
    assert md["has_more"] is True
    cursor = md["next_page_cursor"]
    assert set(cursor.values()) == {"TOKEN-P2"}

    second = asyncio.run(account_search_discovery._youtube_data_api_strict_video_search(
        "viltrox lens review", safe_limit=1, page_cursor=cursor,
    ))
    assert second is not None
    # ——第二轮送出去的 pageToken 必须正是第一轮回来的那个(分页真的发生)。
    assert calls[0].get("pageToken") is None
    assert calls[-1]["pageToken"] == "TOKEN-P2"
    # 翻到的是**新**频道,不是把第一页又抓一遍。
    assert second["items"][0]["channel_id"] == "UC-beta"
    # 最后一页没有 nextPageToken → 诚实说没有下一页(这才是 exhausted 的依据)。
    assert second["metadata"]["has_more"] is False
    assert second["metadata"]["next_page_cursor"] == {}


def test_instagram_leg_reports_no_pagination_instead_of_faking_a_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IG hashtag actor 的输入 schema 里没有 offset/cursor —— 只许如实上报,不许编游标。"""
    actor_calls: list[str] = []

    async def fake_run_actor(actor_id: str, payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        actor_calls.append(actor_id)
        if "hashtag" in actor_id:
            return [{"ownerUsername": "shooter", "caption": "portrait photography", "url": "https://instagram.com/p/1"}]
        return [{"username": "shooter", "followersCount": 42000}]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)

    result = asyncio.run(account_scan_service.search_platform_content(
        "instagram", "portrait photography", max_results=5,
        # 就算调用方硬塞游标,这条腿也不会假装能翻页。
        page_cursor={"anything": "not-a-real-cursor"},
    ))
    md = result["metadata"]
    assert md["pagination_supported"] is False
    assert md["has_more"] is False
    assert md["pagination_unsupported_reason"] == "actor_input_schema_has_no_cursor"
    assert "next_page_cursor" not in md
    # 而且没有因为「多轮」就多烧一次 hashtag actor:本次仍是 hashtag + profile 各一次。
    assert actor_calls == ["apify/instagram-hashtag-scraper", "apify/instagram-profile-scraper"]


# ── ② 游标收敛 + 第 2 轮腿选择 ─────────────────────────────────────────────────
def _platform_results(youtube_more: bool, instagram_more: bool = False) -> list[dict[str, Any]]:
    return [
        {"platform": "youtube", "metadata": {
            "pagination_supported": True,
            "has_more": youtube_more,
            "next_page_cursor": {"q1": "TOKEN-P2"} if youtube_more else {},
        }},
        {"platform": "instagram", "metadata": {
            # 假设未来某天 IG actor 真支持了分页 —— 也要 env 显式放行才会参与多轮。
            "pagination_supported": instagram_more,
            "has_more": instagram_more,
            "next_page_cursor": {"tag": "IG-CURSOR"} if instagram_more else None,
        }},
        {"platform": "tiktok", "metadata": {"pagination_supported": False, "has_more": False}},
    ]


def test_pagination_state_only_trusts_provider_reported_next_pages() -> None:
    state = rounds.pagination_state(_platform_results(youtube_more=True))
    assert state["has_more"] is True
    assert state["next_page_cursors"] == {"youtube": {"q1": "TOKEN-P2"}}
    assert state["next_cursor"]["has_more"] == {"youtube": True, "instagram": False, "tiktok": False}

    done = rounds.pagination_state(_platform_results(youtube_more=False))
    assert done["has_more"] is False
    assert done["next_page_cursors"] == {}


def test_round_two_runs_only_paginated_legs_so_instagram_cost_is_not_amplified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(rounds.PAGINATED_PLATFORMS_ENV, raising=False)
    cursor = rounds.pagination_state(_platform_results(youtube_more=True, instagram_more=True))["next_cursor"]
    legs = rounds.platforms_for_round(2, ["youtube", "instagram", "tiktok"], cursor)
    # 即使 IG 自称还有下一页,默认名单里没有它 → 第 2 轮绝不再烧 IG hashtag actor。
    assert legs == ["youtube"]
    forecast = rounds.round_cost_forecast(legs, round_no=2, per_platform_limit=50)
    assert forecast["apify_runs"] == 0
    assert forecast["estimated_usd"] == 0.0
    assert forecast["youtube_api_calls"] == rounds.YOUTUBE_API_CALLS_PER_ROUND

    # 显式改 env 才可能把 IG 拉进多轮(那时钱闸就会开始起作用)。
    monkeypatch.setenv(rounds.PAGINATED_PLATFORMS_ENV, "youtube,instagram")
    assert rounds.platforms_for_round(2, ["youtube", "instagram", "tiktok"], cursor) == ["youtube", "instagram"]


def test_round_two_stops_when_no_leg_has_a_next_page() -> None:
    cursor = rounds.pagination_state(_platform_results(youtube_more=False))["next_cursor"]
    assert rounds.platforms_for_round(2, ["youtube", "instagram", "tiktok"], cursor) == []


def test_cost_forecast_reports_how_many_fetches_this_round_costs() -> None:
    """跑前必须能报出「这一次要花多少次抓取」——三条腿全上时的真账。"""
    forecast = rounds.round_cost_forecast(
        ["youtube", "instagram", "tiktok"], round_no=1,
        per_platform_limits={"youtube": 50, "instagram": 20, "tiktok": 20},
    )
    assert forecast["apify_runs"] == 3          # IG hashtag + IG profile + TT
    assert forecast["youtube_api_calls"] == 4   # ≤3 个 search.list 变体 + 1 次 channels.list
    # prod 只读复测(2026-08-25):IG $0.571+$0.049、TT $0.032、YT $0 → 单轮 ≈ $0.65
    assert forecast["estimated_usd"] == pytest.approx(0.652, abs=0.01)
    assert forecast["by_platform"]["youtube"]["requested_limit"] == 50
    assert forecast["by_platform"]["instagram"]["requested_limit"] == 20


# ── ③ 多轮:真跑第二轮,exhausted 基于真的没有下一页 ───────────────────────────
def _candidate(index: int) -> dict[str, Any]:
    return {
        "platform": "youtube",
        "handle": f"creator{index}",
        "channel_id": f"UC{index:022d}",
        "display_name": f"Creator {index}",
        "channel_url": f"https://www.youtube.com/channel/UC{index:022d}",
        "followers": 50_000,
    }


def _collect(fetch_batch, *, round_gate=None, max_rounds=3) -> dict[str, Any]:
    return asyncio.run(profile_online_qualification.collect_strict_online_candidates(
        query_text="portrait lighting",
        policy=profile_online_qualification.online_policy(market="US"),
        local_canonical_keys=set(),
        fetch_batch=fetch_batch,
        enroll_candidate=lambda raw: {"kol_pool_id": 90_000 + abs(hash(raw.get("handle"))) % 1000},
        candidate_budget=60,
        max_provider_rounds=max_rounds,
        round_gate=round_gate,
    ))


def test_multi_round_pages_until_the_provider_really_has_no_next_page() -> None:
    seen_cursors: list[Any] = []

    async def fetch_batch(*, round_no: int, limit: int, cursor: Any) -> dict[str, Any]:
        seen_cursors.append(cursor)
        if round_no == 1:
            return {
                "status": "ready",
                "new_creators": [_candidate(i) for i in range(5)],
                "next_cursor": {"page_cursors": {"youtube": {"q": "P2"}}, "has_more": {"youtube": True}},
                "has_more": True,
            }
        return {
            "status": "ready",
            "new_creators": [_candidate(100 + i) for i in range(5)],
            "next_cursor": {},
            "has_more": False,
        }

    result = _collect(fetch_batch)
    # 多轮真的跑了(不再是死代码),并且第 2 轮拿到了第 1 轮交回来的游标。
    assert result["provider_rounds"] == 2
    assert seen_cursors[0] is None
    assert seen_cursors[1] == {"page_cursors": {"youtube": {"q": "P2"}}, "has_more": {"youtube": True}}
    assert result["evaluated_count"] == 10
    # 第二轮 provider 说没有下一页了 → 这才叫 exhausted。
    assert result["exhausted"] is True
    assert result["round_gate"]["stopped_by"] is None


def test_round_cap_is_not_reported_as_exhausted() -> None:
    """轮数用完但 provider 还有下一页 → exhausted 必须是 False,原因如实说是轮数用完。"""
    async def fetch_batch(*, round_no: int, limit: int, cursor: Any) -> dict[str, Any]:
        return {
            "status": "ready",
            "new_creators": [_candidate(round_no * 100 + i) for i in range(3)],
            "next_cursor": {"page_cursors": {"youtube": {"q": f"P{round_no + 1}"}}},
            "has_more": True,
        }

    result = _collect(fetch_batch, max_rounds=2)
    assert result["provider_rounds"] == 2
    assert result["exhausted"] is False
    assert result["shortfall_reasons"]["provider_round_budget_exhausted"] > 0


def test_gate_denied_round_is_never_reported_as_exhausted() -> None:
    """被时间/钱闸拦下 ≠ 翻完了。exhausted 保持 False,终止原因用闸给的机器码。"""
    calls: list[int] = []

    async def fetch_batch(*, round_no: int, limit: int, cursor: Any) -> dict[str, Any]:
        calls.append(round_no)
        return {
            "status": "ready",
            "new_creators": [_candidate(i) for i in range(4)],
            "next_cursor": {"page_cursors": {"youtube": {"q": "P2"}}},
            "has_more": True,
        }

    def gate(round_no: int) -> dict[str, Any]:
        return {"allowed": False, "reason": "daily_budget_exhausted", "forecast": {"round_no": round_no}}

    result_gated = _collect(fetch_batch, round_gate=gate)
    assert calls == [1]  # 闸在发 provider **之前**拦下,第 2 轮一次抓取都没发出去
    assert result_gated["provider_rounds"] == 1
    assert result_gated["exhausted"] is False
    assert result_gated["round_gate"]["stopped_by"] == "daily_budget_exhausted"
    assert result_gated["shortfall_reasons"]["daily_budget_exhausted"] > 0
    assert len(result_gated["round_gate"]["verdicts"]) == 1


# ── ④ 轮次闸:时间、钱、无进展 ────────────────────────────────────────────────
def _gate(**kwargs: Any):
    defaults: dict[str, Any] = {
        "legs_for_round": lambda _round: ["youtube"],
        "per_platform_limit": 50,
        "spend_reader": lambda: {"available": True, "spend_usd": 0.0, "run_count": 0},
    }
    defaults.update(kwargs)
    return rounds.build_round_gate(**defaults)


def test_total_deadline_stops_a_new_round() -> None:
    import time as _time

    allowed = _gate(deadline_seconds=120.0)(2)
    assert allowed["allowed"] is True

    stale = _gate(
        deadline_seconds=10.0,
        started_monotonic=_time.monotonic() - 9.9,   # 只剩 0.1s,不够跑一轮
    )(2)
    assert stale["allowed"] is False
    assert stale["reason"] == "online_deadline_exhausted"
    assert stale["seconds_left"] < rounds.MIN_ROUND_BUDGET_SECONDS


def test_daily_budget_gate_blocks_a_paid_extra_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(rounds.PAGINATED_PLATFORMS_ENV, "youtube,instagram")
    paid = _gate(
        legs_for_round=lambda _round: ["instagram"],
        per_platform_limit=20,
        budget_usd=5.0,
        spend_reader=lambda: {"available": True, "spend_usd": 4.90, "run_count": 9},
    )(2)
    # 今日已花 $4.90 + 本轮预估 $0.62 > $5 上限 → 拦。
    assert paid["allowed"] is False
    assert paid["reason"] == "daily_budget_exhausted"
    assert paid["forecast"]["estimated_usd"] == pytest.approx(0.62, abs=0.01)
    assert paid["today_spend_usd"] == 4.9

    ok = _gate(
        legs_for_round=lambda _round: ["instagram"],
        per_platform_limit=20,
        budget_usd=5.0,
        spend_reader=lambda: {"available": True, "spend_usd": 1.0, "run_count": 2},
    )(2)
    assert ok["allowed"] is True


def test_unreadable_ledger_denies_paid_rounds_but_free_rounds_still_run() -> None:
    """钱的方向失败必须安全:读不到台账 → 付费轮一律拒;YouTube 这种零花费轮照跑。"""
    blind = {"available": False, "spend_usd": 0.0, "run_count": 0}
    denied = _gate(legs_for_round=lambda _round: ["instagram"], spend_reader=lambda: blind)(2)
    assert denied["allowed"] is False and denied["reason"] == "daily_budget_unreadable"

    free = _gate(legs_for_round=lambda _round: ["youtube"], spend_reader=lambda: blind)(2)
    assert free["allowed"] is True
    assert free["spend_checked"] is False        # 零花费的轮次根本不必去读台账
    assert free["forecast"]["estimated_usd"] == 0.0


def test_gate_stops_when_the_previous_round_yielded_nothing() -> None:
    empty = _gate(progress_reader=lambda: 0)(2)
    assert empty["allowed"] is False and empty["reason"] == "no_progress_last_round"
    assert _gate(progress_reader=lambda: 7)(2)["allowed"] is True


def test_gate_stops_when_no_leg_can_paginate() -> None:
    verdict = _gate(legs_for_round=lambda _round: [])(2)
    assert verdict["allowed"] is False and verdict["reason"] == "no_paginated_leg_left"


def test_env_overrides_fail_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(rounds.ONLINE_DEADLINE_ENV, "0")
    assert rounds.online_deadline_seconds() == rounds.ONLINE_DEADLINE_DEFAULT_SECONDS
    monkeypatch.setenv(rounds.DAILY_BUDGET_ENV, "not-a-number")
    assert rounds.daily_budget_usd() == rounds.DAILY_BUDGET_DEFAULT_USD
    monkeypatch.setenv(rounds.PAGINATED_PLATFORMS_ENV, "  , ,")
    assert rounds.paginated_platforms() == frozenset(rounds.PAGINATED_PLATFORMS_DEFAULT)
    monkeypatch.setenv(rounds.DAILY_BUDGET_ENV, "2.5")
    assert rounds.daily_budget_usd() == 2.5


# ── ⑤ 今日花费只读台账 ────────────────────────────────────────────────────────
def test_daily_spend_reads_only_and_fails_closed() -> None:
    captured: dict[str, Any] = {}

    class _Row(dict):
        pass

    class FakeConn:
        def execute(self, sql: str, params: tuple[Any, ...]):
            captured["sql"] = sql
            captured["params"] = params

            class _Cursor:
                @staticmethod
                def fetchone():
                    return _Row({"spend_usd": "1.2345", "run_count": 3})
            return _Cursor()

    spend = rounds.daily_discovery_spend_usd(conn=FakeConn())
    assert spend == {"available": True, "spend_usd": 1.2345, "run_count": 3, "since": spend["since"]}
    sql = captured["sql"].strip().upper()
    assert sql.startswith("SELECT")                 # 只读,别的什么也不做
    assert " LIKE " not in sql                      # compat:禁 LIKE
    assert "AS SPEND_USD" in sql and "AS RUN_COUNT" in sql   # compat:聚合列必须有别名
    assert captured["params"][1:] == rounds.LEDGER_ACTORS

    class BrokenConn:
        def execute(self, *_args: Any, **_kwargs: Any):
            raise RuntimeError("ledger table missing")

    blind = rounds.daily_discovery_spend_usd(conn=BrokenConn())
    assert blind["available"] is False and blind["spend_usd"] == 0.0
