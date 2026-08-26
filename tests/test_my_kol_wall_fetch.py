"""内容墙「去查最新内容」的报价 / 确认边界 / 防连点 / 诚实状态机(2026-08-25)。

最重要的三条,其余用例围着它们转:

1. **点之前算得出账单。** ``planned_count`` 就是「这次要去几个账号取内容」,而且是
   服务端算的——共享账号、刚取过的、超上限的各自记账,一个都不静默丢。
2. **报价与派活必须绑定。** ``plan_hash`` / ``expected_count`` 对不上一条都不派(409)。
   没有这道,「确认框写 3 个、实际派 30 个」是完全可能发生的。
3. **诚实状态机。** 没派出去就不许说派出去了;同 URL 已有在途任务时如实归入
   ``already_queued``(并入),不冒充成新派的一次。

零 provider:入队器一律 monkeypatch 成记账假件,测试全程不花一分钱。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import my_kol_wall_fetch as wall_fetch
from app.domains.kol import my_kol_wall_fetch_plan as wall_plan


# ── 假库:按 SQL 特征分派,只回答本车道用到的四条 SELECT,零真实 IO ──────────


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(
        self,
        *,
        pools: list[dict[str, Any]],
        crawled_recently: list[int] | None = None,
        queued_recently: list[int] | None = None,
        daily_used: int = 0,
    ) -> None:
        self.pools = pools
        self.crawled_recently = crawled_recently or []
        self.queued_recently = queued_recently or []
        self.daily_used = daily_used
        self.seen: list[str] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.seen.append(sql)
        if "FROM vkpi_kol_pool kp" in sql:
            single = int(params[4] or 0)
            rows = [row for row in self.pools if not single or int(row["kol_pool_id"]) == single]
            return _FakeCursor([dict(row) for row in rows])
        if "FROM vkpi_kol_url_deep_crawl_runs" in sql:
            return _FakeCursor([{"kol_pool_id": pool_id} for pool_id in self.crawled_recently])
        if "COUNT(*) AS used" in sql:
            return _FakeCursor([{"used": self.daily_used}])
        if "FROM apify_jobs" in sql:
            return _FakeCursor([{"kol_pool_id": str(pool_id)} for pool_id in self.queued_recently])
        if "FROM vkpi_kol_pool WHERE id=?" in sql:
            pool_id = int(params[0])
            row = next((item for item in self.pools if int(item["kol_pool_id"]) == pool_id), None)
            return _FakeCursor([{"profile_url": row["profile_url"]}] if row else [])
        raise AssertionError(f"unexpected SQL: {sql}")


STAFF = {"id": 10, "user_id": 110, "role": "member"}

POOLS = [
    {"kol_pool_id": 1, "display_name": "甲", "platform": "youtube", "profile_url": "https://www.youtube.com/@a"},
    {"kol_pool_id": 2, "display_name": "乙", "platform": "tiktok", "profile_url": "https://www.tiktok.com/@b"},
    {"kol_pool_id": 3, "display_name": "丙", "platform": "instagram", "profile_url": "https://www.instagram.com/c/"},
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VKPI_WALL_FETCH_PER_CLICK",
        "VKPI_WALL_FETCH_DAILY_MAX",
        "VKPI_WALL_FETCH_COOLDOWN_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _stub_side_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    """写围栏与预算表都有各自的测试,这里只需要它们是确定的。"""

    monkeypatch.setattr(
        wall_plan.my_kol_paid_action_access,
        "target_write_context",
        lambda conn, *, kol_pool_id, staff: (
            {"can_run_paid_actions": True, "reason": "owned_favorite"}
            if int(kol_pool_id) != 3
            else {"can_run_paid_actions": False, "reason": "my_kol_paid_action_write_forbidden"}
        ),
    )
    monkeypatch.setattr(
        wall_plan,
        "_budget_headroom",
        lambda: {"configured": True, "usage_ratio": 0.25, "hard_stopped": False},
    )


def _plan(conn: _FakeConn, **kwargs: Any) -> dict[str, Any]:
    return wall_plan.plan_wall_fetch(conn, staff=STAFF, staff_scope_id=10, **kwargs)


# ── ① 报价计算 ────────────────────────────────────────────────────────────


def test_quote_counts_every_account_and_books_each_skip_separately():
    conn = _FakeConn(pools=POOLS, crawled_recently=[2])
    plan = _plan(conn, days=7)

    # 3 个候选:1 号可取;2 号刚取过;3 号是共享的。一个都没有静默消失。
    assert plan["candidates_total"] == 3
    assert plan["planned_count"] == 1
    assert [item["kol_pool_id"] for item in plan["planned"]] == [1]
    assert plan["skipped_counts"]["recently_fetched"] == 1
    assert plan["skipped_counts"]["shared_readonly"] == 1
    assert plan["skipped"]["shared_readonly"][0]["reason"] == "my_kol_paid_action_write_forbidden"
    # 报价的语义硬承诺:follow-up 全关,计量单位是真实取数次数(不是「一个账号一次」)。
    assert plan["followups_suppressed"] is True
    assert plan["posts_per_account"] == wall_plan.WINDOW_POSTS == 12
    assert "fetch_per_account" not in plan  # 旧口径「各取一次」是假话,不许复活


def test_quote_never_widens_the_existing_post_ceiling():
    """时间范围**不会**让我们去平台取更多条——「全部时间」也只是最近这一批。"""

    conn = _FakeConn(pools=POOLS)
    for days in (0, 7, 15, 30):
        plan = _plan(_FakeConn(pools=POOLS), days=days)
        assert plan["window"]["max_posts"] == 12
    assert wall_plan.window_spec(0)["since"] == ""
    assert wall_plan.window_spec(7)["since"]
    assert wall_plan.window_spec(7)["max_posts"] == 12
    assert conn.seen == []


def test_quote_marks_window_exactness_per_platform():
    """YouTube/TikTok 是平台侧按发布时间截取;Instagram 只能取最近内容,两档不许混为一谈。"""

    plan = _plan(_FakeConn(pools=POOLS), days=30)
    by_platform = {item["platform"]: item["window_exactness"] for item in plan["planned"]}
    assert by_platform["youtube"] == "date_pushdown"
    assert by_platform["tiktok"] == "date_pushdown"
    assert plan["window"]["exactness_counts"]["date_pushdown"] == 2
    # 近似档必须有独立标签,措辞里不许出现「精确/筛选」这类会被读成真过滤的词。
    approx = plan["window"]["exactness_labels"]["recent_only"]
    assert "不认发布时间" in approx


def test_instagram_window_is_reported_as_approximate_not_as_a_filter():
    """IG 账号资料抓取器没有日期字段——报价必须单独标一档,不许糊成「按时间筛选」。"""

    ig_pools = [
        {"kol_pool_id": 7, "display_name": "丁", "platform": "instagram", "profile_url": "https://www.instagram.com/d/"},
    ]
    plan = _plan(_FakeConn(pools=ig_pools), days=7)
    assert [item["window_exactness"] for item in plan["planned"]] == ["recent_only"]
    assert plan["window"]["exactness_counts"] == {"recent_only": 1}


def test_quote_counts_real_platform_calls_not_one_per_account():
    """一次 YouTube 取数在平台侧是两次(账号资料 + 内容列表)。笼统写「各一次」= 少报一半。"""

    plan = _plan(_FakeConn(pools=POOLS), days=0)
    calls = plan["fetch_calls"]
    # 名单:1 号 youtube(2 次)+ 2 号 tiktok(1 次);3 号是共享的,没进名单。
    assert plan["planned_count"] == 2
    assert calls["total"] == 3
    assert calls["by_platform"]["youtube"] == {
        "accounts": 1,
        "per_account": 2,
        "per_account_max": 2,
        "calls": 2,
    }
    assert calls["by_platform"]["tiktok"]["per_account"] == 1


def test_instagram_fallback_is_reported_as_a_higher_ceiling_not_hidden():
    """IG 账号资料空返回会兜底再取一次 → 上限比下限高一次,两个数都要报。"""

    ig_pools = [
        {"kol_pool_id": 7, "display_name": "丁", "platform": "instagram", "profile_url": "https://www.instagram.com/d/"},
    ]
    calls = _plan(_FakeConn(pools=ig_pools), days=0)["fetch_calls"]
    assert calls["total"] == 1
    assert calls["max_total"] == 2


def test_candidate_set_drops_merged_duplicates_like_the_wall_does():
    """内容墙每条 SQL 都带 duplicate_of_id IS NULL;报价漏了就会为墙上看不见的账号花钱。"""

    conn = _FakeConn(pools=POOLS)
    _plan(conn, days=0)
    candidate_sql = next(sql for sql in conn.seen if "FROM vkpi_kol_pool kp" in sql)
    assert "kp.duplicate_of_id IS NULL" in candidate_sql


def test_quote_applies_daily_and_per_click_caps_with_separate_books(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VKPI_WALL_FETCH_PER_CLICK", "1")
    monkeypatch.setenv("VKPI_WALL_FETCH_DAILY_MAX", "10")
    pools = [
        {"kol_pool_id": i, "display_name": f"号{i}", "platform": "tiktok", "profile_url": f"https://www.tiktok.com/@k{i}"}
        for i in (1, 2, 4, 5)
    ]
    plan = _plan(_FakeConn(pools=pools, daily_used=8), days=0)
    # 日剩余 2 先切(4 → 2),再按单次上限 1 切(2 → 1)。两刀各自记账。
    assert plan["planned_count"] == 1
    assert plan["skipped_counts"]["daily_cap"] == 2
    assert plan["skipped_counts"]["per_click_cap"] == 1
    assert plan["limits"] == {
        "per_click": 1,
        "daily": 10,
        "daily_used": 8,
        "daily_left": 2,
        "cooldown_hours": 6,
    }


def test_env_caps_cannot_exceed_the_hard_ceilings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VKPI_WALL_FETCH_PER_CLICK", "9999")
    monkeypatch.setenv("VKPI_WALL_FETCH_DAILY_MAX", "9999")
    assert wall_plan.per_click_cap() == 30
    assert wall_plan.daily_cap() == 120


def test_quote_sql_stays_pg_compatible_and_reads_the_real_cooldown_sources():
    conn = _FakeConn(pools=POOLS)
    _plan(conn, days=7)
    joined = "\n".join(conn.seen)
    assert "make_interval" in joined
    assert " LIKE " not in joined.upper()
    assert "%" not in joined
    assert "strpos(" in joined
    assert "COUNT(*) AS used" in joined
    # 冷却读两个真源;刻意不读 prod 全 NULL 的 last_scrape_at。
    assert "vkpi_kol_url_deep_crawl_runs" in joined
    assert "last_scrape_at" not in joined


# ── ② 二次确认边界 ────────────────────────────────────────────────────────


def test_all_favorites_always_requires_confirmation_single_account_does_not():
    batch = _plan(_FakeConn(pools=POOLS), days=0, kol_pool_id=0)
    assert batch["requires_confirmation"] is True

    single = _plan(_FakeConn(pools=POOLS), days=0, kol_pool_id=2)
    assert single["requires_confirmation"] is False
    assert single["planned_count"] == 1


def test_all_favorites_requires_confirmation_even_when_only_one_account_is_planned():
    """「全部」的语义是操作员没有逐个看过名单——算出 1 个也照样弹框。"""

    plan = _plan(_FakeConn(pools=POOLS, crawled_recently=[2]), days=0, kol_pool_id=0)
    assert plan["planned_count"] == 1
    assert plan["requires_confirmation"] is True


# ── ③ 报价指纹绑定 + 幂等防连点 ───────────────────────────────────────────


def _dispatch(conn: _FakeConn, monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]], status: str = "queued"):
    from app.domains.kol import url_deep_crawl

    def fake_enqueue(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return {"status": status, "job_id": 900 + len(calls)}

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", fake_enqueue)


def test_plan_hash_mismatch_dispatches_nothing(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    conn = _FakeConn(pools=POOLS)
    with pytest.raises(wall_fetch.WallFetchError) as caught:
        wall_fetch.run_wall_fetch(
            conn, staff=STAFF, staff_scope_id=10, days=0, plan_hash="stale-hash", expected_count=3
        )
    assert caught.value.code == "wall_fetch_plan_drifted"
    assert caught.value.status_code == 409
    assert calls == []


def test_expected_count_mismatch_dispatches_nothing(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    conn = _FakeConn(pools=POOLS)
    plan = _plan(_FakeConn(pools=POOLS), days=0)
    with pytest.raises(wall_fetch.WallFetchError) as caught:
        wall_fetch.run_wall_fetch(
            conn,
            staff=STAFF,
            staff_scope_id=10,
            days=0,
            plan_hash=plan["plan_hash"],
            expected_count=plan["planned_count"] + 1,
        )
    assert caught.value.code == "wall_fetch_plan_drifted"
    assert calls == []


def test_missing_plan_hash_is_refused_before_any_spend(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    with pytest.raises(wall_fetch.WallFetchError) as caught:
        wall_fetch.run_wall_fetch(_FakeConn(pools=POOLS), staff=STAFF, staff_scope_id=10, days=0)
    assert caught.value.code == "wall_fetch_plan_required"
    assert calls == []


def test_dispatch_reuses_the_shared_enqueuer_with_all_followups_suppressed(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    plan = _plan(_FakeConn(pools=POOLS), days=7)
    result = wall_fetch.run_wall_fetch(
        _FakeConn(pools=POOLS),
        staff=STAFF,
        staff_scope_id=10,
        days=7,
        plan_hash=plan["plan_hash"],
        expected_count=plan["planned_count"],
    )
    assert result["counts"]["queued"] == plan["planned_count"] == 2
    for call in calls:
        # 三个 follow-up 全关:忘一个就从 1 次取数翻成 4 个付费动作。
        assert call["suppress_final_v1"] is True
        assert call["suppress_contact_followup"] is True
        assert call["suppress_profile_followups"] is True
        assert call["enforce_target_write"] is True
        assert call["mode"] == "account_deep"
        assert call["max_posts"] == 12
        assert call["source"] == wall_plan.WALL_FETCH_SOURCE
        assert call["queue_lane"] == "batch"
        assert call["since_iso"]
    # 共享账号一次都没有被派出去。
    assert {call["kol_pool_id"] for call in calls} == {1, 2}


def test_single_account_dispatch_uses_the_interactive_lane(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    plan = _plan(_FakeConn(pools=POOLS), days=0, kol_pool_id=1)
    wall_fetch.run_wall_fetch(
        _FakeConn(pools=POOLS),
        staff=STAFF,
        staff_scope_id=10,
        kol_pool_id=1,
        days=0,
        plan_hash=plan["plan_hash"],
        expected_count=1,
    )
    assert [call["queue_lane"] for call in calls] == ["interactive"]
    assert calls[0]["since_iso"] == ""


def test_repeat_click_is_reported_as_merged_not_as_a_fresh_dispatch(monkeypatch: pytest.MonkeyPatch):
    """同 URL 已有在途任务 → 既有幂等把它并入那一条。回执必须如实说「并入」。"""

    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls, status="already_queued")
    plan = _plan(_FakeConn(pools=POOLS), days=0, kol_pool_id=1)
    result = wall_fetch.run_wall_fetch(
        _FakeConn(pools=POOLS),
        staff=STAFF,
        staff_scope_id=10,
        kol_pool_id=1,
        days=0,
        plan_hash=plan["plan_hash"],
        expected_count=1,
    )
    assert result["counts"] == {"planned": 1, "queued": 0, "already_queued": 1, "failed": 0}
    assert result["queued"] == []
    assert result["already_queued"][0]["job_id"] == 901


def test_cooldown_blocks_the_second_click_after_a_job_landed(monkeypatch: pytest.MonkeyPatch):
    """幂等只挡在途;结果落地后靠冷却挡住「再点一次就是真花钱」。"""

    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=POOLS), monkeypatch, calls)
    # 本车道刚派过 1 号(结果还没回来)+ 2 号刚取成功过 → 两个都在冷却里。
    conn = _FakeConn(pools=POOLS, crawled_recently=[2], queued_recently=[1])
    plan = _plan(conn, days=0)
    assert plan["planned_count"] == 0
    result = wall_fetch.run_wall_fetch(
        _FakeConn(pools=POOLS, crawled_recently=[2], queued_recently=[1]),
        staff=STAFF,
        staff_scope_id=10,
        days=0,
        plan_hash=plan["plan_hash"],
        expected_count=0,
    )
    assert result["status"] == "nothing_to_fetch"
    assert calls == []


# ── ④ 诚实状态机 ─────────────────────────────────────────────────────────


def test_nothing_to_fetch_never_claims_a_dispatch(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    _dispatch(_FakeConn(pools=[]), monkeypatch, calls)
    plan = _plan(_FakeConn(pools=[]), days=0)
    result = wall_fetch.run_wall_fetch(
        _FakeConn(pools=[]),
        staff=STAFF,
        staff_scope_id=10,
        days=0,
        plan_hash=plan["plan_hash"],
        expected_count=0,
    )
    assert result["status"] == "nothing_to_fetch"
    assert result["queued"] == [] and result["already_queued"] == [] and result["failed"] == []
    assert calls == []


def test_one_account_failing_does_not_silently_shrink_the_receipt(monkeypatch: pytest.MonkeyPatch):
    from app.domains.kol import url_deep_crawl

    calls: list[int] = []

    def flaky(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(int(kwargs["kol_pool_id"]))
        if int(kwargs["kol_pool_id"]) == 2:
            raise RuntimeError("provider_input_rejected")
        return {"status": "queued", "job_id": 777}

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", flaky)
    plan = _plan(_FakeConn(pools=POOLS), days=0)
    result = wall_fetch.run_wall_fetch(
        _FakeConn(pools=POOLS),
        staff=STAFF,
        staff_scope_id=10,
        days=0,
        plan_hash=plan["plan_hash"],
        expected_count=plan["planned_count"],
    )
    assert calls == [1, 2]
    assert result["counts"] == {"planned": 2, "queued": 1, "already_queued": 0, "failed": 1}
    assert result["failed"][0]["reason"] == "RuntimeError"


def test_quote_is_read_only_and_touches_no_provider(monkeypatch: pytest.MonkeyPatch):
    from app.domains.kol import url_deep_crawl

    def bomb(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("quote must never enqueue")

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", bomb)
    conn = _FakeConn(pools=POOLS)
    plan = _plan(conn, days=15)
    assert plan["planned_count"] == 2
    assert all(sql.strip().upper().startswith("SELECT") for sql in conn.seen)


# ── ⑤ 报价指纹只随「会改变花多少」的输入变化 ────────────────────────────


def test_plan_hash_changes_with_window_and_roster_but_is_stable_otherwise():
    seven = _plan(_FakeConn(pools=POOLS), days=7)["plan_hash"]
    seven_again = _plan(_FakeConn(pools=POOLS), days=7)["plan_hash"]
    thirty = _plan(_FakeConn(pools=POOLS), days=30)["plan_hash"]
    shrunk = _plan(_FakeConn(pools=POOLS, crawled_recently=[2]), days=7)["plan_hash"]
    assert seven == seven_again
    assert seven != thirty
    assert seven != shrunk


# ── ⑥ 门面无内部术语(后端产出的文案位) ──────────────────────────────────


def test_server_supplied_labels_carry_no_internal_jargon():
    plan = _plan(_FakeConn(pools=POOLS), days=7)
    surfaced = " ".join(
        [
            plan["window_label"],
            plan["scope_label"],
            *plan["window"]["exactness_labels"].values(),
        ]
    ).lower()
    for banned in ("provider", "actor", "apify", "crawl", "job", "worker", "llm", "gemini", "队列", "作业"):
        assert banned not in surfaced


# ── ⑦ 路由层:报价 GET 纯读、派活 POST 的错误映射 ──────────────────────────


def test_plan_route_is_registered_read_only_and_the_post_route_is_not():
    """新 GET 漏登记 release_validation 白名单 = 部署验收 503(此坑已踩过两次)。"""

    from app.api.routers import vkpi_my_kol_wall_fetch as route_mod
    from app.core.release_validation import _reviewed_read_only_get

    paths = {route.path: sorted(route.methods) for route in route_mod.router.routes}
    assert paths == {
        "/api/admin/vkpi/my-kol/wall-fetch/plan": ["GET"],
        "/api/admin/vkpi/my-kol/wall-fetch/status": ["GET"],
        "/api/admin/vkpi/my-kol/wall-fetch": ["POST"],
    }
    assert _reviewed_read_only_get("/api/admin/vkpi/my-kol/wall-fetch/plan", None) is True
    assert _reviewed_read_only_get("/api/admin/vkpi/my-kol/wall-fetch/status", None) is True
    # 派活口刻意不登记:它就该被发布围栏挡住。
    assert _reviewed_read_only_get("/api/admin/vkpi/my-kol/wall-fetch", None) is False


def test_post_route_maps_plan_drift_to_409_with_a_readable_code(monkeypatch: pytest.MonkeyPatch):
    from fastapi import HTTPException

    from app.api.routers import vkpi_my_kol_wall_fetch as route_mod

    monkeypatch.setattr(route_mod, "get_conn", lambda: _FakeConn(pools=POOLS))

    def drift(*_args: Any, **_kwargs: Any) -> None:
        raise wall_fetch.WallFetchError("wall_fetch_plan_drifted", 409, {"plan": {"planned_count": 2}})

    monkeypatch.setattr(route_mod.my_kol_wall_fetch, "run_wall_fetch", drift)
    with pytest.raises(HTTPException) as caught:
        route_mod.my_kol_wall_fetch_endpoint(
            body={"days": 0, "plan_hash": "stale", "expected_count": 3},
            staff_id=None,
            staff=STAFF,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "wall_fetch_plan_drifted"
    # 漂了之后把新报价一并带回去,前端可以直接重画确认框。
    assert caught.value.detail["plan"]["planned_count"] == 2


def test_plan_route_returns_the_quote_and_never_enqueues(monkeypatch: pytest.MonkeyPatch):
    from app.api.routers import vkpi_my_kol_wall_fetch as route_mod
    from app.domains.kol import url_deep_crawl

    def bomb(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the quote route must never enqueue")

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", bomb)
    monkeypatch.setattr(route_mod, "get_conn", lambda: _FakeConn(pools=POOLS))
    body = route_mod.my_kol_wall_fetch_plan_endpoint(days=7, kol_pool_id=None, staff_id=None, staff=STAFF)
    assert body["planned_count"] == 2
    assert body["requires_confirmation"] is True
    assert "scope_context" in body


# ── ⑧ 派单结局回读(HIGH 3:2026-08-24 线上 P0 的同型复发,派完必须能读回结局) ──


class _JobsConn:
    """只回答回读那一条 SELECT 的假库;顺带把 SQL 与参数留给断言。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.seen: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.seen.append((sql, params))
        assert "FROM apify_jobs" in sql
        return _FakeCursor([dict(row) for row in self.rows])


def _job(job_id: int, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": status,
        "last_error": extra.get("last_error", ""),
        "last_error_category": extra.get("last_error_category", ""),
        "kol_pool_id": str(extra.get("kol_pool_id", job_id)),
    }


def test_readback_reports_blocked_jobs_as_stopped_never_as_landed():
    """被拦死的活必须现形。这一条就是那次 17 分钟假进度的回归闸。"""

    conn = _JobsConn([_job(11, "blocked", last_error_category="authorization"), _job(12, "done")])
    out = wall_fetch.read_dispatch_outcomes(conn, job_ids=[11, 12], staff_scope_id=10)
    states = {item["job_id"]: item["state"] for item in out["items"]}
    assert states == {11: "stopped", 12: "landed"}
    assert out["counts"] == {"waiting": 0, "landed": 1, "stopped": 1, "unknown": 0}
    reason = next(item["reason_human"] for item in out["items"] if item["job_id"] == 11)
    assert "权限" in reason


def test_readback_never_leaks_raw_machine_codes_to_the_facade():
    """未映射的机器码归入统一兜底句,不许原样打到门面上(红线 5)。"""

    conn = _JobsConn([_job(21, "failed", last_error="ZeroDivisionError: totally_unmapped_code_x")])
    out = wall_fetch.read_dispatch_outcomes(conn, job_ids=[21], staff_scope_id=10)
    reason = out["items"][0]["reason_human"]
    assert "totally_unmapped_code_x" not in reason
    assert reason == wall_fetch.UNKNOWN_STOP_REASON


def test_readback_counts_unread_jobs_as_unknown_not_as_finished():
    """读不到就是读不到——绝不能算进「已取回」。"""

    conn = _JobsConn([_job(31, "queued")])
    out = wall_fetch.read_dispatch_outcomes(conn, job_ids=[31, 32], staff_scope_id=10)
    assert out["counts"] == {"waiting": 1, "landed": 0, "stopped": 0, "unknown": 1}
    assert out["unknown_job_ids"] == [32]


def test_readback_is_read_only_and_fenced_to_this_lane_and_this_staff():
    conn = _JobsConn([])
    wall_fetch.read_dispatch_outcomes(conn, job_ids=[41, 41, 42], staff_scope_id=10)
    sql, params = conn.seen[0]
    assert sql.strip().upper().startswith("SELECT")
    assert "%" not in sql and " LIKE " not in sql.upper()
    # 只认本车道自己派的活 + 非管理层只看自己派的。
    assert wall_plan.WALL_FETCH_SOURCE in params
    assert "kol_profile_deep_crawl" in params
    assert 10 in params and "10" in params
    # 重复 id 去重后才进 SQL。
    assert params[:2] == (41, 42)


def test_readback_for_a_manager_drops_the_per_staff_fence_but_keeps_the_lane_fence():
    conn = _JobsConn([])
    wall_fetch.read_dispatch_outcomes(conn, job_ids=[51], staff_scope_id=10, scoped=False)
    _sql, params = conn.seen[0]
    assert 0 in params and "0" in params
    assert wall_plan.WALL_FETCH_SOURCE in params


def test_readback_without_job_ids_never_touches_the_database():
    conn = _JobsConn([])
    out = wall_fetch.read_dispatch_outcomes(conn, job_ids=[], staff_scope_id=10)
    assert conn.seen == []
    assert out["counts"] == {"waiting": 0, "landed": 0, "stopped": 0, "unknown": 0}


def test_status_route_parses_ids_and_stays_read_only(monkeypatch: pytest.MonkeyPatch):
    from app.api.routers import vkpi_my_kol_wall_fetch as route_mod

    conn = _JobsConn([_job(61, "running")])
    monkeypatch.setattr(route_mod, "get_conn", lambda: conn)
    body = route_mod.my_kol_wall_fetch_status_endpoint(job_ids="61, x, 62", staff_id=None, staff=STAFF)
    assert body["counts"]["waiting"] == 1
    assert body["unknown_job_ids"] == [62]


# ── ⑨ 时间窗真下推(MEDIUM 7:since 此前是签名里的死参数,全仓无人传) ─────────


class _RecordingCrawler:
    def __init__(self) -> None:
        self.profile_calls: list[dict[str, Any]] = []
        self.video_calls: list[dict[str, Any]] = []

    def crawl_channel_profile(self, target: str, *, channel_id: str = "", max_posts: int = 12, **kwargs: Any):
        self.profile_calls.append({"target": target, "max_posts": max_posts, **kwargs})
        return {"items": [{"id": "UC-x"}], "provider_status": "ok", "sync_status": "ok"}

    def crawl_channel_videos(self, channel_id: str, *, max_results: int = 25, **kwargs: Any):
        self.video_calls.append({"channel_id": channel_id, "max_results": max_results, **kwargs})
        return {"items": [], "provider_status": "ok", "sync_status": "ok"}


def _basics(monkeypatch: pytest.MonkeyPatch, platform: str, since: str) -> _RecordingCrawler:
    from app.domains.kol import url_deep_crawl_execute as execute

    crawler = _RecordingCrawler()
    monkeypatch.setattr(execute, "_crawler_for", lambda _platform: crawler)
    classified = type("C", (), {"platform": platform, "channel_id": ""})()
    execute._crawl_profile_basics(classified, target="creator", max_posts=12, since=since)
    return crawler


def test_youtube_window_is_pushed_down_to_the_platform(monkeypatch: pytest.MonkeyPatch):
    """确认框承诺了时间范围,YouTube 分支就必须真把日期下推,不许只在嘴上承诺。"""

    crawler = _basics(monkeypatch, "youtube", "2026-08-18")
    assert crawler.video_calls[0]["since"] == "2026-08-18"


def test_youtube_without_a_window_behaves_exactly_as_before(monkeypatch: pytest.MonkeyPatch):
    crawler = _basics(monkeypatch, "youtube", "")
    assert crawler.video_calls[0]["since"] == ""


def test_tiktok_window_is_pushed_down_to_the_platform(monkeypatch: pytest.MonkeyPatch):
    crawler = _basics(monkeypatch, "tiktok", "2026-08-18")
    assert crawler.profile_calls[0]["since"] == "2026-08-18"
