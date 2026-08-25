"""车道 2:在线段提速(A2 IG 富化后置 + A3 腿级 deadline)与 B3 每平台上限。

守三件事(都是「行为」而非「实现」):
1. 超时的腿诚实降级成「本轮该平台无供给」,且**不污染其余腿**;
2. IG 富化只发给前置闸的存活者,且候选集合一条不少(前置闸只省钱、不删人);
3. 每平台上限:operator 的 {平台: 上限} 覆盖生效,YT 50 / IG·TT 20,缺省沿用旧标量。

prod 实测依据(a05e48dd3,vkpi_ai_cost_ledger × vkpi_kol_search_sessions,9 个会话)
写在 profile_discovery_supply 模块头,这里只守代码行为。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_provider as provider
from app.domains.kol import profile_discovery_supply as supply
from app.services.intelligence import account_scan_service, account_search_instagram


# ── A3 腿级 deadline ────────────────────────────────────────────────────────────
def test_leg_deadline_default_and_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(supply.LEG_DEADLINE_ENV, raising=False)
    monkeypatch.delenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", raising=False)
    assert supply.leg_deadline_seconds("instagram") == supply.LEG_DEADLINE_DEFAULT_SECONDS

    monkeypatch.setenv(supply.LEG_DEADLINE_ENV, "40")
    assert supply.leg_deadline_seconds("tiktok") == 40.0
    monkeypatch.setenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", "75")
    assert supply.leg_deadline_seconds("instagram") == 75.0
    assert supply.leg_deadline_seconds("tiktok") == 40.0

    # 配错 env 不许把腿掐死成 0 秒 —— 失败方向必须安全(退回下一级)。
    monkeypatch.setenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", "0")
    assert supply.leg_deadline_seconds("instagram") == 40.0
    monkeypatch.setenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", "not-a-number")
    assert supply.leg_deadline_seconds("instagram") == 40.0


def test_timed_out_leg_degrades_honestly_without_poisoning_other_legs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(supply.LEG_DEADLINE_ENV, "10")
    monkeypatch.setenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", "0.05")
    started: list[str] = []

    async def _leg(platform: str) -> dict[str, Any]:
        started.append(platform)
        if platform == "instagram":
            await asyncio.sleep(5)  # 永远追不上 0.05s 的 deadline
            return {"platform": platform, "status": "done", "annotated": [{"handle": "late"}]}
        return {"platform": platform, "status": "done", "annotated": [{"handle": platform}]}

    outcomes = asyncio.run(supply.run_all_legs(["youtube", "instagram", "tiktok"], _leg))

    assert started == ["youtube", "instagram", "tiktok"]
    by_platform = {row["platform"]: row for row in outcomes}
    # 超时腿:诚实说「本轮无供给」,不是「搜到 0 条」;error=True 让整体 status 落到 partial。
    ig = by_platform["instagram"]
    assert ig["status"] == "deadline_exceeded"
    assert ig["annotated"] == [] and ig["error"] is True
    assert ig["deadline_exceeded"] is True and ig["deadline_seconds"] == 0.05
    # 不假装超时=省钱:provider run 仍在跑并照常计费。
    assert ig["provider_run_still_billed"] is True
    # 其余腿完全不受影响。
    assert by_platform["youtube"]["annotated"] == [{"handle": "youtube"}]
    assert by_platform["tiktok"]["annotated"] == [{"handle": "tiktok"}]


def test_timed_out_leg_is_cancelled_and_not_leaked(monkeypatch: pytest.MonkeyPatch) -> None:
    """被甩掉的腿必须真的 cancel 掉,而且不留「exception was never retrieved」噪声。"""
    monkeypatch.setenv(supply.LEG_DEADLINE_ENV, "0.05")
    seen: dict[str, Any] = {}

    async def _slow(platform: str) -> dict[str, Any]:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
        return {"platform": platform, "status": "done", "annotated": []}

    async def _drive() -> list[Any]:
        out = await supply.run_all_legs(["instagram"], _slow)
        await asyncio.sleep(0.05)  # 给 cancel 回调跑完的机会
        return out

    outcomes = asyncio.run(_drive())
    assert outcomes[0]["status"] == "deadline_exceeded"
    assert seen.get("cancelled") is True
    assert not supply._ORPHANED_LEGS  # 完成后自行摘除,不无限增长


def test_leg_accounting_is_empty_for_healthy_legs() -> None:
    assert supply.leg_accounting({"platform": "youtube", "status": "done"}) == {}
    assert supply.leg_accounting(None) == {}
    hit = supply.leg_accounting(supply.leg_no_supply("instagram", 25.0))
    assert hit == {
        "deadline_exceeded": True,
        "deadline_seconds": 25.0,
        "provider_run_still_billed": True,
    }


# ── A2 IG 富化后置 ─────────────────────────────────────────────────────────────
def test_instagram_enrich_only_targets_gate_survivors(monkeypatch: pytest.MonkeyPatch) -> None:
    """富化只发给存活者,但候选一条不少 —— 前置闸只省配额,绝不删人。"""
    actor_calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_run_actor(actor_id: str, payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        actor_calls.append((actor_id, dict(payload)))
        if "hashtag" in actor_id:
            return [
                {"ownerUsername": "streetshooter", "caption": "portrait photography", "url": "https://instagram.com/p/1"},
                # B&H 一类零售/经销身份 → _is_discovery_garbage 命中,富化前就该被判死。
                {"ownerUsername": "bhphotovideo", "caption": "camera store deal", "url": "https://instagram.com/p/2"},
                {"ownerUsername": "lensgirl", "caption": "lens review", "url": "https://instagram.com/p/3"},
            ]
        return [
            {"username": "streetshooter", "fullName": "Street Shooter", "followersCount": 45200, "biography": "Documentary photographer"},
            {"username": "lensgirl", "fullName": "Lens Girl", "followersCount": 800},
        ]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)

    gate = supply.build_enrich_doom_gate(exclude_chinese=True, neg_terms=[])
    result = asyncio.run(
        account_scan_service.search_platform_content(
            "instagram", "portrait photography", max_results=5, enrich_prefilter=gate
        )
    )

    # 富化 actor 只收到两个存活者,零售号没花配额。
    assert actor_calls[1][1]["usernames"] == ["streetshooter", "lensgirl"]
    # 但候选集合一条不少:被前置闸判死的那条照旧返回,交给下游同一条闸处理。
    assert [item["handle"] for item in result["items"]] == ["streetshooter", "bhphotovideo", "lensgirl"]
    md = result["metadata"]
    assert md["profile_enrich_prefiltered"] == 1
    assert md["profile_enrich_requested"] == 2
    assert md["profile_enriched"] == 2
    assert "profile_enrich_degraded" not in md  # 做满了就不该有降级标记


def test_instagram_enrich_skipped_when_deadline_budget_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """hashtag 阶段吃光预算 → 诚实跳过富化(候选照常返回、followers 未知),而不是拖爆整条腿。"""
    actor_calls: list[str] = []

    async def fake_run_actor(actor_id: str, payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        actor_calls.append(actor_id)
        if "hashtag" in actor_id:
            await asyncio.sleep(0.2)  # 模拟 hashtag 阶段把预算吃光
            return [{"ownerUsername": "solo", "caption": "photography", "url": "https://instagram.com/p/9"}]
        raise AssertionError("profile enrich must not run when the budget is exhausted")

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)
    monkeypatch.setenv(account_search_instagram.INSTAGRAM_ENRICH_MIN_BUDGET_SECONDS_ENV, "5")

    result = asyncio.run(
        account_scan_service.search_platform_content(
            "instagram", "photography", max_results=5, deadline_seconds=0.25
        )
    )

    assert actor_calls == ["apify/instagram-hashtag-scraper"]
    assert result["status"] == "done"
    assert result["items"][0]["handle"] == "solo"
    assert "followers" not in result["items"][0]  # 未知就是未知,绝不杜撰
    assert result["metadata"]["profile_enrich_degraded"] == "deadline_budget_exhausted"


def test_instagram_enrich_prefilter_failure_falls_open() -> None:
    """闸自身炸了 → 全部富化(退回旧行为),绝不静默把人筛没。"""
    rows = [{"ownerUsername": "a"}, {"ownerUsername": "b"}]

    def _explodes(_probe: dict[str, Any]) -> bool:
        raise RuntimeError("gate is broken")

    names, dropped = account_search_instagram.instagram_enrich_targets(rows, _explodes)
    assert names == ["a", "b"] and dropped == 0

    names, dropped = account_search_instagram.instagram_enrich_targets(rows, None)
    assert names == ["a", "b"] and dropped == 0


def test_enrich_doom_gate_excludes_anti_monotone_camera_signal() -> None:
    """相机信号常只写在 bio 里,而 bio 恰恰是富化才拿得到的 —— 前置闸绝不许拿它判死。"""
    gate = supply.build_enrich_doom_gate(exclude_chinese=True, neg_terms=[])
    probe = account_search_instagram.instagram_prefilter_probe(
        {"ownerUsername": "quiet_shooter", "caption": "☀️"}
    )
    assert gate(probe) is False  # 零相机信号也不判死,留给富化后的主环判


def test_enrich_doom_gate_matches_the_monotone_gates() -> None:
    gate = supply.build_enrich_doom_gate(exclude_chinese=True, neg_terms=["cinema"])
    probe = account_search_instagram.instagram_prefilter_probe
    assert gate(probe({"ownerUsername": "bhphotovideo", "caption": "deal"})) is True   # garbage
    assert gate(probe({"ownerUsername": "viltrox.usa", "caption": "new lens"})) is True  # 自有品牌
    # 静态高精词表(人工维护的明确非视觉品类)照旧判死。
    assert gate(probe({"ownerUsername": "normal_guy", "caption": "casino night"})) is True
    # persona 的 avoid_types 已由 A1 降级为排序扣分(_is_hard_avoid 不再据此丢人),
    # 前置闸自动跟随同一份口径 —— 这里守的正是「前置闸绝不比主环更狠」。
    assert gate(probe({"ownerUsername": "normal_guy", "caption": "cinema rig"})) is False
    assert gate(probe({"ownerUsername": "normal_guy", "caption": "35mm portrait"})) is False


def test_enrich_doom_gate_never_drops_more_than_the_main_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前置闸与主环共用同一批 discovery_filters 函数,任何一方口径变了都一起变。

    这里用 A1 留的应急回滚开关做一次真实联动验证:开关一开,persona 负词在主环恢复
    丢弃语义,前置闸必须同步恢复 —— 不许出现「主环放行、前置闸偷偷杀掉」的漂移。
    """
    from app.domains.kol import discovery_filters

    probe = account_search_instagram.instagram_prefilter_probe(
        {"ownerUsername": "normal_guy", "caption": "cinema rig"}
    )
    gate = supply.build_enrich_doom_gate(exclude_chinese=True, neg_terms=["cinema"])
    assert gate(probe) is discovery_filters._is_hard_avoid(probe, ["cinema"])

    monkeypatch.setenv(discovery_filters.PERSONA_AVOID_DROP_ENV, "1")
    assert gate(probe) is discovery_filters._is_hard_avoid(probe, ["cinema"]) is True


# ── B3 每平台上限 ──────────────────────────────────────────────────────────────
def test_per_platform_limit_override_contract() -> None:
    limits = {"youtube": 50, "instagram": 20, "tiktok": 20}
    assert supply.resolve_platform_limit("youtube", 20, limits) == 50
    assert supply.resolve_platform_limit("instagram", 20, limits) == 20
    assert supply.resolve_platform_limit("tiktok", 20, limits) == 20
    # 缺覆盖 / 非 dict → 沿用标量(旧行为逐字不变)。
    assert supply.resolve_platform_limit("facebook", 20, limits) == 20
    assert supply.resolve_platform_limit("youtube", 20, None) == 20
    # 永远夹在 [1, 50]。
    assert supply.resolve_platform_limit("youtube", 20, {"youtube": 999}) == 50
    assert supply.resolve_platform_limit("youtube", 0, None) == 1


def test_sanitize_platform_limits_drops_garbage() -> None:
    assert supply.sanitize_platform_limits({"YouTube": " 50 ", "tiktok": 999, "x": 0, "ig": "nope"}) == {
        "youtube": 50,
        "tiktok": 50,
    }
    assert supply.sanitize_platform_limits(None) == {}
    assert supply.sanitize_platform_limits("50") == {}


def test_provider_requests_per_platform_limits_and_reports_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """端到端:provider 按每平台上限向 provider 层要量,并把生效值如实报出来。"""
    monkeypatch.setenv(supply.LEG_DEADLINE_ENV, "30")
    asked: dict[str, int] = {}

    async def fake_search(platform: str, _query: str, **kwargs: Any) -> dict[str, Any]:
        asked[platform] = int(kwargs.get("max_results") or 0)
        return {"status": "done", "items": [], "metadata": {}}

    monkeypatch.setattr(provider, "search_platform_content", fake_search)

    result = asyncio.run(
        provider.discover_new_creators(
            query_text="camera reviewer",
            platforms=["youtube", "instagram", "tiktok"],
            per_platform_limit=20,
            per_platform_limits={"youtube": 50, "instagram": 20, "tiktok": 20},
            auto_enroll=False,
        )
    )

    assert asked == {"youtube": 50, "instagram": 20, "tiktok": 20}
    assert result["per_platform_limits"] == {"youtube": 50, "instagram": 20, "tiktok": 20}
    assert result["per_platform_limit"] == 20  # 标量兜底仍然如实透出
    assert {row["platform"]: row["requested_limit"] for row in result["platform_results"]} == {
        "youtube": 50, "instagram": 20, "tiktok": 20,
    }
    assert result["counts"]["deadline_exceeded_platforms"] == 0


def test_provider_marks_deadline_exceeded_leg_in_platform_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(supply.LEG_DEADLINE_ENV, "10")
    monkeypatch.setenv(supply.LEG_DEADLINE_PLATFORM_ENV_PREFIX + "INSTAGRAM", "0.05")

    async def fake_search(platform: str, _query: str, **_kwargs: Any) -> dict[str, Any]:
        if platform == "instagram":
            await asyncio.sleep(5)
        return {"status": "done", "items": [], "metadata": {}}

    monkeypatch.setattr(provider, "search_platform_content", fake_search)

    result = asyncio.run(
        provider.discover_new_creators(
            query_text="camera reviewer",
            platforms=["youtube", "instagram"],
            auto_enroll=False,
        )
    )

    rows = {row["platform"]: row for row in result["platform_results"]}
    assert rows["instagram"]["deadline_exceeded"] is True
    assert rows["instagram"]["provider_run_still_billed"] is True
    assert rows["instagram"]["returned"] == 0
    assert "deadline_exceeded" not in rows["youtube"]
    assert result["counts"]["deadline_exceeded_platforms"] == 1
    # 超时进 errors → 整体不许冒充 ready。
    assert any(err["platform"] == "instagram" for err in result["errors"])
    assert result["status"] in {"partial", "failed"}
