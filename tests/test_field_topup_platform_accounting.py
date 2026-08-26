"""车道 1 的**按平台记账**契约(用户裁决 3,2026-08-25)。

用户裁令是三条腿(YouTube / TikTok / Instagram)全开,但要求按平台分别记账,
理由写得很直白:prod 实测 Instagram 两项填充率都是 0.0(n=145),混在一个总数里
那条 0 产出的腿永远看不见。跑一周后要拿真实数据决定关不关某条腿。

所以这个文件钉三件事:

1. **三条腿默认齐全**,且总开关仍然默认 OFF —— 记账变详细不等于自动开始花钱。
2. **计划期**按平台报得出「这个平台要花几次抓取、按实测率预计换回几个字段」。
3. **入队后**按平台报得出「真排了几个队」,并且这份记账进得了会话诊断。

外加真实产出回读(``profile_field_topup_yield``)的两条口径:只认本车道自己排的队;
分母为 0 时产出率是 ``None`` 而不是 0.0 —— 「没抓过」和「抓了没补上」不能混成一个结论。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import profile_field_topup_enqueue as topup
from app.domains.kol import profile_field_topup_yield as yield_readback


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, *, pool_rows: list[dict[str, Any]]) -> None:
        self.pool_rows = pool_rows
        self.seen: list[str] = []

    def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
        self.seen.append(sql)
        assert sql.strip().upper().startswith("SELECT"), "本车道只允许 SELECT 探测"
        if "FROM vkpi_kol_pool" in sql:
            wanted = {int(value) for value in params if isinstance(value, int)}
            rows = [row for row in self.pool_rows if int(row["id"]) in wanted]
            rows.sort(key=lambda row: (-(row.get("followers") or -1), row["id"]))
            return _FakeCursor(rows)
        if "vkpi_kol_url_deep_crawl_runs" in sql:
            return _FakeCursor([])
        if "COUNT(*) AS used" in sql:
            return _FakeCursor([{"used": 0}])
        if "FROM apify_jobs" in sql:
            return _FakeCursor([])
        raise AssertionError(f"unexpected sql: {sql[:80]}")


_URL = {
    "youtube": "https://www.youtube.com/@h{id}",
    "tiktok": "https://www.tiktok.com/@h{id}",
    "instagram": "https://www.instagram.com/h{id}",
}


def _row(pool_id: int, followers: int, platform: str) -> dict[str, Any]:
    return {
        "id": pool_id,
        "platform": platform,
        "handle": f"h{pool_id}",
        "profile_url": _URL[platform].format(id=pool_id),
        "followers": followers,
        "country": "",
        "language": "",
    }


def _candidate(pool_id: int, platform: str) -> dict[str, Any]:
    return {
        "kol_pool_id": pool_id,
        "handle": f"h{pool_id}",
        "platform": platform,
        "missing_fields": ["country", "language"],
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VKPI_FIELD_TOPUP_ENABLED",
        "VKPI_FIELD_TOPUP_PER_SEARCH",
        "VKPI_FIELD_TOPUP_DAILY_MAX",
        "VKPI_FIELD_TOPUP_COOLDOWN_HOURS",
        "VKPI_FIELD_TOPUP_PLATFORMS",
    ):
        monkeypatch.delenv(name, raising=False)


def _bind(monkeypatch: pytest.MonkeyPatch, conn: _FakeConn) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(topup, "get_conn", lambda: conn)

    def _fake_enqueue(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return {"status": "queued", "job_id": 900 + len(calls)}

    from app.domains.kol import url_deep_crawl

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", _fake_enqueue)
    return calls


# ── 1. 三条腿全开,但闸仍然默认关 ────────────────────────────────────────────


def test_all_three_legs_are_enabled_by_default_but_the_gate_stays_off() -> None:
    """用户裁决「三条腿全开」= 白名单齐全;但花钱的总开关一如既往默认 OFF。"""

    assert topup.DEFAULT_PLATFORMS == ("youtube", "tiktok", "instagram")
    settings = topup.topup_settings()
    assert settings["platforms"] == ["instagram", "tiktok", "youtube"]
    assert settings["enabled"] is False, "补数据总开关绝不许在代码里默认武装"


def test_instagram_measured_zero_rate_is_kept_visible_not_dropped() -> None:
    """Instagram 实测 0 产出,但**不能**因此把它从实测表里删掉。

    删掉会让它落进 ``unmeasured`` 桶,看起来像「还没测过」;留着 0.0 才是如实,
    也正是用户要用来做决定的那个数。
    """

    assert topup.MEASURED_FILL_RATE["instagram"] == {"country": 0.0, "language": 0.0}
    assert set(topup.MEASURED_FILL_RATE) == set(topup.DEFAULT_PLATFORMS)


# ── 2. 计划期按平台报账单 ────────────────────────────────────────────────────


def test_plan_reports_fetches_and_expected_fields_per_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每个平台各自报「要花几次抓取」与「预计补上几个字段」。"""

    conn = _FakeConn(
        pool_rows=[
            _row(1, 9000, "youtube"),
            _row(2, 8000, "tiktok"),
            _row(3, 7000, "instagram"),
        ]
    )
    _bind(monkeypatch, conn)
    plan = topup.plan_field_topup(
        [_candidate(1, "youtube"), _candidate(2, "tiktok"), _candidate(3, "instagram")]
    )

    by_platform = plan["by_platform"]
    assert set(by_platform) == {"youtube", "tiktok", "instagram"}
    for platform in ("youtube", "tiktok", "instagram"):
        assert by_platform[platform]["fetches"] == 1
        assert by_platform[platform]["eligible"] == 1

    # 预期字段数 = 该平台实测率之和(两个字段都缺)。
    assert by_platform["youtube"]["expected_fields"] == {"country": 0.81, "language": 0.09}
    assert by_platform["tiktok"]["expected_fields"] == {"country": 0.14, "language": 0.29}
    # 这一条就是用户要看见的那个数:花了钱,预期产出 0。
    assert by_platform["instagram"]["expected_fields"] == {"country": 0.0, "language": 0.0}
    assert by_platform["instagram"]["expected_fields_total"] == 0.0
    assert by_platform["instagram"]["measured_fill_rate"] == {"country": 0.0, "language": 0.0}


def test_plan_by_platform_exists_even_on_early_return_paths() -> None:
    """没有候选时也要有 ``by_platform`` 键(空字典),调用方不必到处判 None。"""

    plan = topup.plan_field_topup([])
    assert plan["status"] == "no_candidates"
    assert plan["by_platform"] == {}


# ── 3. 入队后按平台报实际发生 ────────────────────────────────────────────────


def test_enqueue_reports_realized_counts_per_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开闸真入队后,每个平台各自报「真排了几个队」,并与预期并排。"""

    monkeypatch.setenv("VKPI_FIELD_TOPUP_ENABLED", "1")
    conn = _FakeConn(
        pool_rows=[
            _row(1, 9000, "youtube"),
            _row(2, 8000, "youtube"),
            _row(3, 7000, "instagram"),
        ]
    )
    calls = _bind(monkeypatch, conn)
    result = topup.enqueue_field_topup_for_candidates(
        candidates=[
            _candidate(1, "youtube"),
            _candidate(2, "youtube"),
            _candidate(3, "instagram"),
        ],
        session_id=42,
    )

    assert result["status"] == "ok"
    assert len(calls) == 3
    by_platform = result["by_platform"]
    assert by_platform["youtube"]["queued"] == 2
    assert by_platform["youtube"]["fetches"] == 2
    assert by_platform["instagram"]["queued"] == 1
    # 实际与预期同处一行:instagram 花了 1 次抓取、预期补上 0 个字段。
    assert by_platform["instagram"]["expected_fields_total"] == 0.0
    assert by_platform["youtube"]["errors"] == 0


def test_enqueue_by_platform_counts_errors_separately() -> None:
    """报错单独成列 —— 「抓了没补上」与「压根没抓成」是两回事,不许合并。"""

    realized = topup.enqueue_by_platform(
        [
            {"platform": "youtube", "status": "queued"},
            {"platform": "youtube", "status": "error"},
            {"platform": "tiktok", "status": "already_queued"},
            {"platform": "", "status": "queued"},
        ]
    )
    assert realized["youtube"] == {
        "fetches": 2, "queued": 1, "already_queued": 0, "errors": 1,
    }
    assert realized["tiktok"]["already_queued"] == 1
    # 平台缺失不静默丢,归进 unknown 桶。
    assert realized["unknown"]["queued"] == 1


# ── 4. 真实产出回读的口径 ────────────────────────────────────────────────────


def test_yield_readback_only_counts_this_lane_and_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回读只认本车道自己排的队,且全程只有 SELECT。"""

    seen: list[tuple[str, Any]] = []

    class _Conn:
        def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
            seen.append((sql, params))
            assert sql.strip().upper().startswith("SELECT"), "回读必须零写"
            return _FakeCursor(
                [
                    {
                        "platform": "youtube",
                        "fetches": 10,
                        "country_filled": 8,
                        "language_filled": 1,
                        "any_filled": 8,
                    },
                    {
                        "platform": "instagram",
                        "fetches": 5,
                        "country_filled": 0,
                        "language_filled": 0,
                        "any_filled": 0,
                    },
                ]
            )

    monkeypatch.setattr(yield_readback, "get_conn", lambda: _Conn())
    result = yield_readback.field_topup_yield_by_platform(window_days=7)

    assert result["status"] == "ok"
    assert result["window_days"] == 7
    assert result["fetches"] == 15
    # 只认自己的 source,否则别的管线补的字段会让本车道产出率虚高。
    sql, params = seen[0]
    assert "payload ->> 'source' = ?" in sql
    assert params[0] == topup.TOPUP_SOURCE

    youtube = result["by_platform"]["youtube"]
    assert youtube["fill_rate"]["country"] == 0.8
    assert youtube["fill_rate"]["language"] == 0.1
    # 用户要的那一行:花了 5 次抓取,一个字段都没补上。
    instagram = result["by_platform"]["instagram"]
    assert instagram["fetches"] == 5
    assert instagram["filled_now"] == {"country": 0, "language": 0}
    assert instagram["fill_rate_any"] == 0.0


def test_yield_rate_is_none_when_nothing_was_fetched() -> None:
    """分母为 0 时产出率必须是 None。

    返回 0.0 会把「这条腿还没跑过」显示成「这条腿产出为零」,用户可能据此关掉一条
    其实还没验证过的腿 —— 这正是本次要避免的误判。
    """

    assert yield_readback._rate(0, 0) is None
    assert yield_readback._rate(0, 5) == 0.0


def test_yield_window_is_clamped_and_fails_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        def execute(self, sql: str, params: Any = ()) -> _FakeCursor:
            raise RuntimeError("pg down")

    monkeypatch.setattr(yield_readback, "get_conn", lambda: _Boom())
    result = yield_readback.field_topup_yield_by_platform(window_days=9999)
    # 读不出来就诚实说读不出来,绝不返回一个看起来像「0 产出」的空账。
    assert result["status"] == "probe_failed"
    assert result["by_platform"] == {}
    assert result["window_days"] == yield_readback.MAX_WINDOW_DAYS
