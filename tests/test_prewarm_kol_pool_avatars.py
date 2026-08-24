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


def _row(pool_id: int, avatar_url: str) -> dict[str, Any]:
    return {
        "id": pool_id,
        "avatar_url": avatar_url,
        "raw_platform_data": {},
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
        (11, "https://yt3.googleusercontent.com/avatar.jpg", "durable"),
        (12, "https://scontent.cdninstagram.com/avatar.jpg?oe=00000001", "expired"),
        (13, "", "missing"),
        (14, "https://i.ytimg.com/vi/not-an-avatar/hqdefault.jpg", "invalid"),
        (15, "https://unapproved.example/avatar.jpg?expires=4102444800", "durable"),
    ],
)
def test_non_ephemeral_rows_are_skipped_without_cache_call(
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


def test_process_exit_code_is_nonzero_for_blocked_or_failed_rows() -> None:
    assert prewarm._exit_code([{"status": "cached"}, {"status": "skipped"}]) == 0
    assert prewarm._exit_code([{"status": "failed"}]) == 1
    assert prewarm._exit_code([{"status": "failed"}, {"status": "blocked"}]) == 2
