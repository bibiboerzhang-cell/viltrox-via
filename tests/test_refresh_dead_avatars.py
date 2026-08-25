"""scripts/ops/refresh_dead_avatars.py 的行为门。

重点覆盖四件会花钱或会说谎的事:干跑必须零入队、分类口径正确、``--limit``
真的夹住预算、探活拿不准时保守归类为存活(绝不因为一次网络异常去多抓一次)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "refresh_dead_avatars.py"
SPEC = importlib.util.spec_from_file_location("refresh_dead_avatars", SCRIPT)
assert SPEC and SPEC.loader
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)


# 签名 URL 一律带一个绝不允许出现在输出里的哨兵串。
LIVE_SIGNED_URL = "https://p16-sign.tiktokcdn.com/a.jpeg?x-expires=4102444800&sig=must-never-leak"
EXPIRED_SIGNED_URL = "https://scontent.cdninstagram.com/a.jpg?oe=5F5E0FF0&sig=must-never-leak"
EXPIRED_TIKTOK_URL = "https://p16-sign.tiktokcdn.com/b.jpeg?x-expires=1600000000&sig=must-never-leak"
STABLE_URL = "https://yt3.ggpht.com/stable-avatar?token=must-never-leak"
FOREIGN_HOST_URL = "https://internal.example.com/admin/avatar.png"


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    """只回放本测试给定的行;顺序/过滤/LIMIT 都按真 SQL 的语义模拟。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        actor: dict[str, Any] | None = None,
    ) -> None:
        self.rows = list(rows or [])
        self.actor = actor
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeResult:
        self.calls.append((sql, tuple(params)))
        if "FROM staff" in sql:
            return FakeResult([self.actor] if self.actor else [])
        selected = [row for row in self.rows if str(row.get("avatar_url") or "")]
        if len(params) == 4:
            platform = str(params[2])
            selected = [
                row for row in selected if str(row.get("platform") or "").lower() == platform
            ]
        selected.sort(key=lambda row: (-int(row.get("followers") or 0), int(row["id"])))
        return FakeResult(selected[: int(params[-1])])


def _row(
    pool_id: int,
    avatar_url: str,
    *,
    platform: str = "tiktok",
    followers: int = 1000,
    profile_url: str | None = None,
    duplicate_of_id: int | None = None,
) -> dict[str, Any]:
    return {
        "id": pool_id,
        "platform": platform,
        "handle": f"creator{pool_id}",
        "profile_url": profile_url if profile_url is not None else f"https://www.tiktok.com/@creator{pool_id}",
        "avatar_url": avatar_url,
        "followers": followers,
        "duplicate_of_id": duplicate_of_id,
    }


def _owner_actor(staff_id: int = 7) -> dict[str, Any]:
    return {
        "id": staff_id,
        "user_id": 42,
        "role": "owner",
        "is_owner": 1,
        "active": 1,
        "suspended_at": None,
        "user_status": "active",
    }


class EnqueueSpy:
    def __init__(self, status: str = "queued") -> None:
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"status": self.status, "job_id": 900 + len(self.calls)}


def _dead_probe(_url: str) -> str:
    return "dead"


def _alive_probe(_url: str) -> str:
    return "alive"


def _run(conn: FakeConnection, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "staff_id": 7,
        "probe_fn": _dead_probe,
        "fresh_fn": lambda _pool_id: False,
        "fence_active_fn": lambda: False,
    }
    params.update(kwargs)
    return refresh.run(conn, **params)


# ── 分类 ──────────────────────────────────────────────────────────


def test_stable_host_is_skipped_and_never_probed() -> None:
    """稳定直连(youtube ggpht)不是本车道的活,也不该花一次探活。"""

    probed: list[str] = []

    def probe(url: str) -> str:
        probed.append(url)
        return "dead"

    classified = refresh.classify_rows([_row(1, STABLE_URL, platform="youtube")], probe_fn=probe)

    assert probed == []
    assert classified[0]["verdict"] == "skipped"
    assert classified[0]["reason"] == "stable_host"


def test_expired_signature_is_dead_without_probing() -> None:
    """URL 自带的过期时间戳已过 = 证据确凿,不必再花一次 HEAD。"""

    probed: list[str] = []

    def probe(url: str) -> str:
        probed.append(url)
        return "alive"

    classified = refresh.classify_rows(
        [_row(1, EXPIRED_SIGNED_URL, platform="instagram"), _row(2, EXPIRED_TIKTOK_URL)],
        probe_fn=probe,
    )

    assert probed == []
    assert [item["verdict"] for item in classified] == ["dead", "dead"]
    assert {item["reason"] for item in classified} == {"policy_expired"}


def test_live_signature_uses_head_probe_verdict() -> None:
    """签名还在有效期内时,死活只能靠 HEAD 说了算。"""

    alive = refresh.classify_rows([_row(1, LIVE_SIGNED_URL)], probe_fn=_alive_probe)[0]
    dead = refresh.classify_rows([_row(1, LIVE_SIGNED_URL)], probe_fn=_dead_probe)[0]

    assert (alive["verdict"], alive["reason"]) == ("alive", "head_200")
    assert (dead["verdict"], dead["reason"]) == ("dead", "head_not_200")


def test_inconclusive_probe_is_conservatively_kept_alive() -> None:
    """拿不准算存活:网络抖动不该换来一次真金白银的重抓。"""

    classified = refresh.classify_rows([_row(1, LIVE_SIGNED_URL)], probe_fn=lambda _url: "unknown")

    assert classified[0]["verdict"] == "unknown"
    assert classified[0]["reason"] == "probe_inconclusive"
    plan = refresh.build_plan(classified, limit=50)
    assert plan["plan"] == []


def test_probe_refuses_non_allowlisted_host_without_network() -> None:
    """库里一行脏 URL 不能指挥本进程去请求任意主机。"""

    assert refresh.probe_host_allowed(LIVE_SIGNED_URL) is True
    assert refresh.probe_host_allowed(FOREIGN_HOST_URL) is False
    assert refresh.probe_host_allowed("file:///etc/passwd") is False
    # 未过白名单时直接返回保守结论,不发任何请求。
    assert refresh.probe_avatar(FOREIGN_HOST_URL, timeout=0.1) == "unknown"


def test_missing_avatar_is_reported_not_silently_dropped() -> None:
    classified = refresh.classify_rows([_row(1, "")], probe_fn=_dead_probe)

    assert classified[0]["verdict"] == "skipped"
    assert classified[0]["reason"] == "missing"


# ── 优先级与预算 ──────────────────────────────────────────────────


def test_plan_orders_by_followers_desc_so_big_accounts_are_saved_first() -> None:
    classified = refresh.classify_rows(
        [
            _row(1, EXPIRED_TIKTOK_URL, followers=5_000),
            _row(2, EXPIRED_TIKTOK_URL, followers=9_000_000),
            _row(3, EXPIRED_TIKTOK_URL, followers=120_000),
        ],
        probe_fn=_dead_probe,
    )

    plan = refresh.build_plan(classified, limit=50)["plan"]

    assert [item["pool_id"] for item in plan] == [2, 3, 1]
    assert [item["follower_band"] for item in plan] == ["1M+", "100K-1M", "1K-10K"]


def test_limit_caps_the_plan_and_records_the_remainder_honestly() -> None:
    rows = [_row(index, EXPIRED_TIKTOK_URL, followers=index * 1000) for index in range(1, 6)]
    classified = refresh.classify_rows(rows, probe_fn=_dead_probe)

    bundle = refresh.build_plan(classified, limit=2)

    assert [item["pool_id"] for item in bundle["plan"]] == [5, 4]
    assert [item["plan_reason"] for item in bundle["skipped"]] == ["over_limit"] * 3


def test_limit_rejects_out_of_range_instead_of_silently_widening() -> None:
    with pytest.raises(ValueError):
        refresh.normalize_limit(0)
    with pytest.raises(ValueError):
        refresh.normalize_limit(refresh.MAX_LIMIT + 1)
    with pytest.raises(ValueError):
        refresh.normalize_concurrency(refresh.MAX_PROBE_CONCURRENCY + 1)


def test_unqueueable_rows_are_skipped_with_a_named_reason() -> None:
    classified = refresh.classify_rows(
        [
            _row(1, EXPIRED_TIKTOK_URL, duplicate_of_id=99),
            _row(2, EXPIRED_TIKTOK_URL, platform="weibo"),
            _row(3, EXPIRED_TIKTOK_URL, profile_url=""),
            _row(4, EXPIRED_TIKTOK_URL),
        ],
        probe_fn=_dead_probe,
    )

    bundle = refresh.build_plan(classified, limit=50)

    assert [item["pool_id"] for item in bundle["plan"]] == [4]
    assert {item["plan_reason"] for item in bundle["skipped"]} == {
        "duplicate_row",
        "unsupported_platform",
        "profile_url_missing",
    }


def test_recently_crawled_rows_are_not_paid_for_twice() -> None:
    classified = refresh.classify_rows(
        [_row(1, EXPIRED_TIKTOK_URL), _row(2, EXPIRED_TIKTOK_URL)],
        probe_fn=_dead_probe,
    )

    bundle = refresh.build_plan(classified, limit=50, fresh_fn=lambda pool_id: pool_id == 1)

    assert [item["pool_id"] for item in bundle["plan"]] == [2]
    assert [item["plan_reason"] for item in bundle["skipped"]] == ["recently_crawled"]


# ── 干跑 / 真跑 ───────────────────────────────────────────────────


def test_dry_run_enqueues_nothing_but_still_prices_the_work() -> None:
    conn = FakeConnection([_row(index, EXPIRED_TIKTOK_URL) for index in range(1, 4)])
    spy = EnqueueSpy()
    priced: list[dict[str, Any]] = []

    summary = _run(conn, apply_mode=False, enqueue_fn=spy, cost_reporter=priced.append)

    assert spy.calls == []
    assert summary["dry_run"] is True
    assert summary["mode"] == "dry_run"
    assert summary["enqueued"] == 0
    assert summary["results"] == []
    assert summary["planned_refetch"] == 3
    assert summary["planned_provider_crawls"] == 3
    assert priced and priced[0]["planned_provider_crawls"] == 3
    assert refresh.exit_code(summary) == 0
    # actor 从未被读过:干跑不该碰权限表,也不该有任何写路径。
    assert not any("FROM staff" in sql for sql, _params in conn.calls)


def test_apply_enqueues_through_the_existing_deep_crawl_queue() -> None:
    conn = FakeConnection(
        [_row(1, EXPIRED_TIKTOK_URL, followers=500_000), _row(2, EXPIRED_TIKTOK_URL, followers=900)],
        actor=_owner_actor(),
    )
    spy = EnqueueSpy()

    summary = _run(conn, apply_mode=True, limit=10, enqueue_fn=spy)

    assert summary["enqueued"] == 2
    assert summary["failed"] == 0
    assert [call["kol_pool_id"] for call in spy.calls] == [1, 2]
    assert [call["url"] for call in spy.calls] == [
        "https://www.tiktok.com/@creator1",
        "https://www.tiktok.com/@creator2",
    ]
    assert all(call["staff"]["id"] == 7 for call in spy.calls)
    assert refresh.exit_code(summary) == 0


def test_apply_respects_the_limit_it_printed() -> None:
    conn = FakeConnection(
        [_row(index, EXPIRED_TIKTOK_URL, followers=index * 10) for index in range(1, 6)],
        actor=_owner_actor(),
    )
    spy = EnqueueSpy()

    summary = _run(conn, apply_mode=True, limit=2, enqueue_fn=spy)

    assert len(spy.calls) == 2
    assert summary["planned_provider_crawls"] == 2
    assert summary["plan_skipped_reasons"] == {"over_limit": 3}


def test_enqueue_failure_is_surfaced_not_swallowed() -> None:
    conn = FakeConnection([_row(1, EXPIRED_TIKTOK_URL)], actor=_owner_actor())

    class Boom(RuntimeError):
        code = "my_kol_video_write_forbidden"

    def exploding(**_kwargs: Any) -> dict[str, Any]:
        raise Boom("https://p16-sign.tiktokcdn.com/a.jpeg?sig=must-never-leak")

    summary = _run(conn, apply_mode=True, enqueue_fn=exploding)

    assert summary["status"] == "failed"
    assert summary["failed"] == 1
    assert summary["results"][0]["reason"] == "my_kol_video_write_forbidden"
    assert "must-never-leak" not in repr(summary)
    assert refresh.exit_code(summary) == 1


def test_unexpected_enqueue_status_fails_closed() -> None:
    conn = FakeConnection([_row(1, EXPIRED_TIKTOK_URL)], actor=_owner_actor())

    summary = _run(conn, apply_mode=True, enqueue_fn=EnqueueSpy(status="nope"))

    assert summary["status"] == "failed"
    assert summary["results"][0]["reason"] == "unexpected_enqueue_status:nope"


# ── 围栏 ──────────────────────────────────────────────────────────


def test_release_validation_fence_blocks_every_enqueue() -> None:
    conn = FakeConnection([_row(1, EXPIRED_TIKTOK_URL)], actor=_owner_actor())
    spy = EnqueueSpy()

    summary = _run(conn, apply_mode=True, enqueue_fn=spy, fence_active_fn=lambda: True)

    assert spy.calls == []
    assert summary["status"] == "blocked"
    assert summary["reason"] == "release_validation_fenced"
    assert refresh.exit_code(summary) == 2


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        (None, "actor_not_found"),
        ({**_owner_actor(), "active": 0}, "actor_inactive"),
        ({**_owner_actor(), "suspended_at": "2026-01-01T00:00:00Z"}, "actor_inactive"),
        ({**_owner_actor(), "user_status": "disabled"}, "actor_inactive"),
        (
            {**_owner_actor(), "is_owner": 0, "role": "readonly", "permissions_json": "{}"},
            "actor_permission_missing",
        ),
    ],
)
def test_apply_fails_closed_when_the_actor_cannot_write(actor: Any, expected: str) -> None:
    conn = FakeConnection([_row(1, EXPIRED_TIKTOK_URL)], actor=actor)
    spy = EnqueueSpy()

    summary = _run(conn, apply_mode=True, enqueue_fn=spy)

    assert spy.calls == []
    assert summary["status"] == "blocked"
    assert summary["reason"] == expected
    assert refresh.exit_code(summary) == 2


def test_active_boolean_read_back_as_int_is_accepted() -> None:
    """compat 下 BOOLEAN 读回是 int 1/0,不能用 ``is True`` 判活。"""

    conn = FakeConnection([], actor={**_owner_actor(), "active": 1})
    actor, error = refresh.load_actor(conn, 7)

    assert error == ""
    assert actor is not None and int(actor["id"]) == 7


# ── 报表 ──────────────────────────────────────────────────────────


def test_summary_reports_platform_and_follower_band_distribution() -> None:
    conn = FakeConnection([
        _row(1, EXPIRED_SIGNED_URL, platform="instagram", followers=2_000_000),
        _row(2, EXPIRED_TIKTOK_URL, platform="tiktok", followers=50_000),
        _row(3, STABLE_URL, platform="youtube", followers=10),
    ])

    summary = _run(conn, apply_mode=False)

    assert summary["classification"] == {"dead": 2, "skipped": 1}
    assert summary["by_platform"]["instagram"] == {"dead": 1}
    assert summary["by_platform"]["youtube"] == {"skipped": 1}
    assert summary["by_follower_band"]["1M+"] == {"dead": 1}
    assert summary["planned_by_follower_band"] == {"1M+": 1, "10K-100K": 1}


def test_alive_rows_are_handed_back_to_the_existing_prewarm_script() -> None:
    conn = FakeConnection([_row(index, LIVE_SIGNED_URL) for index in range(1, 4)])

    summary = _run(conn, apply_mode=False, probe_fn=_alive_probe)

    handoff = summary["prewarm_handoff"]
    assert handoff["alive_pool_ids"] == 3
    assert handoff["first_batch_pool_ids"] == [1, 2, 3]
    assert "prewarm_kol_pool_avatars.py" in handoff["first_batch_command"]
    assert summary["planned_refetch"] == 0


def test_scan_cap_downgrades_the_verdict_instead_of_lying() -> None:
    original = refresh.MAX_SCAN_ROWS
    refresh.MAX_SCAN_ROWS = 2
    try:
        conn = FakeConnection([_row(index, EXPIRED_TIKTOK_URL) for index in range(1, 6)])
        summary = _run(conn, apply_mode=False)
    finally:
        refresh.MAX_SCAN_ROWS = original

    assert summary["scan_cap_exceeded"] is True
    assert summary["status"] == "partial"
    assert summary["rows_scanned"] == 2
    assert refresh.exit_code(summary) == 1


def test_output_never_renders_avatar_urls_or_signatures() -> None:
    conn = FakeConnection(
        [_row(1, EXPIRED_SIGNED_URL, platform="instagram"), _row(2, LIVE_SIGNED_URL)],
        actor=_owner_actor(),
    )

    summary = _run(conn, apply_mode=True, enqueue_fn=EnqueueSpy())

    rendered = repr(summary)
    assert "must-never-leak" not in rendered
    assert "cdninstagram" not in rendered
    assert "tiktokcdn" not in rendered


def test_platform_filter_narrows_the_scan() -> None:
    conn = FakeConnection([
        _row(1, EXPIRED_SIGNED_URL, platform="instagram"),
        _row(2, EXPIRED_TIKTOK_URL, platform="tiktok"),
    ])

    summary = _run(conn, apply_mode=False, platform="instagram")

    assert summary["rows_scanned"] == 1
    assert summary["by_platform"] == {"instagram": {"dead": 1}}
    with pytest.raises(ValueError):
        refresh.normalize_platform("weibo")
