from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ops" / "prewarm_kol_pool_avatars.py"
SPEC = importlib.util.spec_from_file_location("prewarm_kol_pool_avatars", SCRIPT)
assert SPEC and SPEC.loader
prewarm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prewarm)


LIVE_SECRET_URL = (
    "https://scontent.cdninstagram.com/profile/avatar.jpg"
    "?oe=FFFFFFFF&signed_secret=must-never-leak"
)
STABLE_SECRET_URL = "https://yt3.ggpht.com/profile-avatar?token=stable-must-never-leak"


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeResult:
        self.calls.append((sql, tuple(params)))
        selected = set(params)
        return FakeResult([row for row in self.rows if int(row["id"]) in selected])


class FakeSessionConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeResult:
        self.calls.append((sql, tuple(params)))
        session_ids = {int(value) for value in params[:-1]}
        limit = int(params[-1])
        creator_types = {
            "existing_kol",
            "new_creator",
            "online_qualified_candidate",
            "recall_candidate",
        }
        selected = [
            row
            for row in self.rows
            if int(row["session_id"]) in session_ids
            and str(row["item_type"]) in creator_types
        ]
        return FakeResult(selected[:limit])


def _row(pool_id: int, avatar_url: str) -> dict[str, Any]:
    return {
        "id": pool_id,
        "avatar_url": avatar_url,
        "raw_platform_data": {},
    }


def _session_row(
    row_id: int,
    payload: Any,
    *,
    session_id: int = 1143,
    item_type: str = "new_creator",
) -> dict[str, Any]:
    return {
        "id": row_id,
        "session_id": session_id,
        "item_type": item_type,
        "payload_json": payload if isinstance(payload, str) else json.dumps(payload),
    }


def test_dry_run_never_calls_cache_and_queries_only_explicit_ids() -> None:
    conn = FakeConnection([_row(7, LIVE_SECRET_URL), _row(8, LIVE_SECRET_URL), _row(99, LIVE_SECRET_URL)])
    called: list[str] = []

    result = prewarm.run(
        conn,
        pool_ids=[8, 7],
        execute=False,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )

    assert result == [
        {"pool_id": 8, "status": "eligible", "reason": "dry_run"},
        {"pool_id": 7, "status": "eligible", "reason": "dry_run"},
    ]
    assert called == []
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "FROM vkpi_kol_pool" in sql
    assert params == (8, 7)
    assert 99 not in params


def test_execute_calls_cache_only_for_live_allowlisted_ephemeral_avatar() -> None:
    conn = FakeConnection([_row(3, LIVE_SECRET_URL)])
    called: list[str] = []

    result = prewarm.run(
        conn,
        pool_ids=[3],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {
            "status": "cached",
            "url": "/api/vkpi-media/image-cache/" + "a" * 64,
        },
        fence_active_fn=lambda: False,
    )

    assert called == [LIVE_SECRET_URL]
    assert result == [
        {"pool_id": 3, "status": "cached", "reason": "cache_hit_or_fetched"}
    ]


def test_supplied_id_count_has_a_hard_cap_before_deduplication() -> None:
    with pytest.raises(ValueError, match="at most 50"):
        prewarm.normalize_pool_ids([1] * 51)
    assert prewarm.normalize_pool_ids([2, 2, 3]) == [2, 3]


def test_supplied_session_count_has_a_small_cap_before_deduplication() -> None:
    with pytest.raises(ValueError, match="at most 5"):
        prewarm.normalize_session_ids([1] * 6)
    with pytest.raises(ValueError, match="positive"):
        prewarm.normalize_session_ids([0])
    assert prewarm.normalize_session_ids([1143, 1143, 1144]) == [1143, 1144]


def test_output_schema_cannot_leak_source_or_cache_urls(capsys: pytest.CaptureFixture[str]) -> None:
    conn = FakeConnection([_row(5, LIVE_SECRET_URL)])

    result = prewarm.run(
        conn,
        pool_ids=[5],
        execute=True,
        cache_image_fn=lambda _url: {
            "status": "failed",
            "reason": "network error " + LIVE_SECRET_URL,
            "url": "https://cache.example/private?token=also-secret",
        },
        fence_active_fn=lambda: False,
    )
    prewarm.out_json(result, ensure_ascii=False, sort_keys=True)
    rendered = capsys.readouterr().out

    assert set(result[0]) == {"pool_id", "status", "reason"}
    assert json.loads(rendered) == result
    assert LIVE_SECRET_URL not in rendered
    assert "must-never-leak" not in rendered
    assert "also-secret" not in rendered
    assert "network error" not in rendered


@pytest.mark.parametrize(
    ("pool_id", "avatar_url", "reason"),
    [
        (12, "https://scontent.cdninstagram.com/avatar.jpg?oe=00000001", "expired"),
        (13, "", "missing"),
        (14, "https://i.ytimg.com/vi/not-an-avatar/hqdefault.jpg", "invalid"),
        (15, "https://unapproved.example/avatar.jpg?expires=4102444800", "not_allowlisted"),
    ],
)
def test_noneligible_rows_are_skipped_without_cache_call(
    pool_id: int,
    avatar_url: str,
    reason: str,
) -> None:
    called: list[str] = []
    result = prewarm.run(
        FakeConnection([_row(pool_id, avatar_url)]),
        pool_ids=[pool_id],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )

    assert result == [{"pool_id": pool_id, "status": "skipped", "reason": reason}]
    assert called == []


def test_stable_allowlisted_external_avatar_is_eligible_for_real_local_durability() -> None:
    avatar = "https://yt3.ggpht.com/profile-avatar"
    called: list[str] = []

    dry = prewarm.run(
        FakeConnection([_row(11, avatar)]),
        pool_ids=[11],
        execute=False,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )
    assert dry == [{"pool_id": 11, "status": "eligible", "reason": "dry_run"}]
    assert called == []

    applied = prewarm.run(
        FakeConnection([_row(11, avatar)]),
        pool_ids=[11],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )
    assert applied == [{"pool_id": 11, "status": "cached", "reason": "cache_hit_or_fetched"}]
    assert called == [avatar]


def test_release_validation_fence_refuses_execute_before_query_or_cache() -> None:
    conn = FakeConnection([_row(21, LIVE_SECRET_URL)])
    called: list[str] = []

    result = prewarm.run(
        conn,
        pool_ids=[21],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: True,
    )

    assert result == [
        {"pool_id": 21, "status": "blocked", "reason": "release_validation_fenced"}
    ]
    assert conn.calls == []
    assert called == []


def test_session_dry_run_is_explicit_bounded_aggregate_only_and_provider_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    expired = "https://scontent.cdninstagram.com/avatar.jpg?oe=00000001"
    unapproved = "https://unapproved.example/avatar.jpg?token=unapproved-secret"
    rows = [
        _session_row(1, {"avatar_url": STABLE_SECRET_URL, "name": "private-name"}),
        _session_row(2, {"avatar_url": STABLE_SECRET_URL, "handle": "private-handle"}),
        _session_row(3, {"avatar_url": LIVE_SECRET_URL}),
        _session_row(4, {"avatar_url": expired}),
        _session_row(5, {"avatar_url": unapproved}),
        _session_row(6, "not-json"),
        _session_row(7, {"name": "private-no-avatar"}),
        _session_row(8, {"creator": {"avatar_url": "https://yt3.ggpht.com/nested-secret"}}),
        _session_row(9, {"avatar_url": "https://yt3.ggpht.com/video-item"}, item_type="url_video"),
        _session_row(10, {"avatar_url": "https://yt3.ggpht.com/other-session"}, session_id=2000),
    ]
    conn = FakeSessionConnection(rows)
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=False,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )
    prewarm.out_json(summary, ensure_ascii=False, sort_keys=True)
    rendered = capsys.readouterr().out

    assert summary["status"] == "dry_run"
    assert summary["sessions_requested"] == 1
    assert summary["sessions_found"] == 1
    assert summary["creator_items_scanned"] == 8
    assert summary["avatar_references"] == 5
    assert summary["unique_avatar_urls"] == 4
    assert summary["duplicate_avatar_references"] == 1
    assert summary["eligible_urls"] == 2
    assert summary["skipped_urls"] == 2
    assert summary["invalid_payloads"] == 1
    assert summary["provider_calls_performed"] is False
    assert summary["business_db_writes"] == 0
    assert called == []

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "SELECT session_id, item_type, payload_json" in " ".join(sql.split())
    assert "SELECT *" not in sql.upper()
    assert "raw_platform_data" not in sql
    assert "name" not in sql.lower()
    assert "handle" not in sql.lower()
    assert params == (1143, prewarm.MAX_SESSION_CREATOR_ITEMS + 1)

    assert json.loads(rendered) == summary
    for private_value in (
        "1143",
        "private-name",
        "private-handle",
        "stable-must-never-leak",
        "must-never-leak",
        "unapproved-secret",
        "nested-secret",
    ):
        assert private_value not in rendered


def test_session_execute_caches_only_allowlisted_live_external_avatars() -> None:
    conn = FakeSessionConnection([
        _session_row(1, {"avatar_url": STABLE_SECRET_URL}),
        _session_row(2, {"avatar_url": LIVE_SECRET_URL}),
        _session_row(3, {"avatar_url": "https://unapproved.example/avatar.jpg"}),
    ])
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {
            "status": "cached",
            "url": "/api/vkpi-media/image-cache/" + "b" * 64,
        },
        fence_active_fn=lambda: False,
    )

    assert called == [STABLE_SECRET_URL, LIVE_SECRET_URL]
    assert summary["status"] == "ok"
    assert summary["eligible_urls"] == 2
    assert summary["cached_urls"] == 2
    assert summary["skipped_urls"] == 1
    assert summary["failed_urls"] == 0


def test_session_execute_fence_refuses_before_query_or_cache() -> None:
    conn = FakeSessionConnection([_session_row(1, {"avatar_url": STABLE_SECRET_URL})])
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: True,
    )

    assert summary["status"] == "blocked"
    assert summary["reason"] == "release_validation_fenced"
    assert conn.calls == []
    assert called == []
    assert prewarm._session_exit_code(summary) == 2


def test_session_execute_rechecks_fence_before_each_cache_mutation() -> None:
    conn = FakeSessionConnection([
        _session_row(1, {"avatar_url": STABLE_SECRET_URL}),
        _session_row(2, {"avatar_url": LIVE_SECRET_URL}),
    ])
    fence_checks = iter((False, False, True))
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: next(fence_checks),
    )

    assert called == [STABLE_SECRET_URL]
    assert summary["status"] == "blocked"
    assert summary["cached_urls"] == 1
    assert summary["blocked_urls"] == 1
    assert summary["reason"] == "release_validation_fenced"


def test_session_url_cap_blocks_before_any_cache_mutation() -> None:
    rows = [
        _session_row(index, {"avatar_url": f"https://yt3.ggpht.com/profile-{index}"})
        for index in range(1, prewarm.MAX_SESSION_AVATAR_URLS + 2)
    ]
    conn = FakeSessionConnection(rows)
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )

    assert summary["status"] == "blocked"
    assert summary["reason"] == "avatar_url_cap_exceeded"
    assert summary["unique_avatar_urls"] == prewarm.MAX_SESSION_AVATAR_URLS + 1
    assert summary["url_cap_exceeded"] is True
    assert summary["avatar_scan_complete"] is False
    assert called == []


def test_session_item_scan_cap_blocks_before_payload_inspection_or_cache() -> None:
    rows = [
        _session_row(index, "not-json")
        for index in range(1, prewarm.MAX_SESSION_CREATOR_ITEMS + 2)
    ]
    conn = FakeSessionConnection(rows)
    called: list[str] = []

    summary = prewarm.run_sessions(
        conn,
        session_ids=[1143],
        execute=True,
        cache_image_fn=lambda url: called.append(url) or {"status": "cached"},
        fence_active_fn=lambda: False,
    )

    assert summary["status"] == "blocked"
    assert summary["reason"] == "creator_item_scan_cap_exceeded"
    assert summary["creator_items_scanned"] == prewarm.MAX_SESSION_CREATOR_ITEMS + 1
    assert summary["item_scan_cap_exceeded"] is True
    assert summary["avatar_scan_complete"] is False
    assert summary["invalid_payloads"] == 0
    assert called == []


def test_make_read_only_uses_verified_postgres_handle_without_sqlite_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Raw:
        read_only = False

    class Conn:
        _raw = Raw()

        def execute(self, _sql: str) -> None:
            raise AssertionError("postgres path must not execute PRAGMA")

    monkeypatch.setattr("app.db.connection.is_postgres_runtime", lambda: True)
    conn = Conn()
    prewarm._make_read_only(conn)
    assert conn._raw.read_only is True


def test_make_read_only_verifies_sqlite_query_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def fetchone(self) -> tuple[int]:
            return (1,)

    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str) -> Result:
            self.sql.append(sql)
            return Result()

    monkeypatch.setattr("app.db.connection.is_postgres_runtime", lambda: False)
    conn = Conn()
    prewarm._make_read_only(conn)
    assert conn.sql == ["PRAGMA query_only=ON", "PRAGMA query_only"]


def test_process_exit_code_is_nonzero_for_blocked_or_failed_rows() -> None:
    assert prewarm._exit_code([{"status": "cached"}, {"status": "skipped"}]) == 0
    assert prewarm._exit_code([{"status": "failed"}]) == 1
    assert prewarm._exit_code([{"status": "failed"}, {"status": "blocked"}]) == 2
