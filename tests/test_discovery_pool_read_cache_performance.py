from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import pool_common
from app.domains.kol.pool_read_projection import prepare_pool_read_selection
from app.domains.kol.pool_read_projection_cache import clear_pool_read_selection_cache
from app.domains.kol.pool_read_response_cache import (
    cached_pool_avatar_ids,
    restore_cached_pool_avatars,
)


def test_global_postgres_selection_never_selects_raw_provider_payload() -> None:
    class Result:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        def fetchone(self) -> dict[str, Any] | None:
            return self.rows[0] if self.rows else None

        def fetchall(self) -> list[dict[str, Any]]:
            return self.rows

    class PostgresCompatConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> Result:
            self.statements.append(sql)
            if "row_count" in sql and "max_updated_at" in sql:
                return Result([{
                    "row_count": 1,
                    "max_id": 1,
                    "max_updated_at": "2026-08-24",
                    "duplicate_rows": 0,
                }])
            if "vkpi_kol_search_session_items" in sql:
                return Result([])
            return Result([{
                "id": 1,
                "platform": "youtube",
                "handle": "creator",
                "profile_url": "https://youtube.com/@creator",
                "display_name": "Creator",
                "avatar_url": "",
                "bio": "",
                "duplicate_of_id": None,
                "avg_views": None,
                "engagement_rate": None,
                "viltrox_fit_score": None,
                "raw_profile_avatar_url": None,
            }])

    conn = PostgresCompatConnection()
    clear_pool_read_selection_cache()

    selection = prepare_pool_read_selection(
        conn,
        clause="WHERE duplicate_of_id IS NULL",
        params=(),
    )

    assert selection.visible_ids == frozenset({1})
    assert not any("raw_platform_data" in sql for sql in conn.statements)
    clear_pool_read_selection_cache()


def test_shared_pool_cache_redacts_ephemeral_url_and_restores_request_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict[str, Any], int]] = []
    monkeypatch.setattr(
        pool_common,
        "cache_set",
        lambda key, value, *, ttl: writes.append((key, value, ttl)),
    )
    signed = "https://signed.example/avatar"

    result = pool_common._kol_pool_cache_store(
        "pool:test",
        {"items": [{
            "id": 7101,
            "avatar_url": signed,
            "avatar_url_status": "ephemeral",
            "raw_profile_avatar_url": signed,
        }]},
    )

    assert result["items"][0]["avatar_url"] == signed
    assert result["cache"] == {
        "hit": False,
        "ttl_sec": 30,
        "stored": True,
        "ephemeral_avatar_urls_stored": 0,
        "ephemeral_avatar_templates": 1,
    }
    assert [(key, ttl) for key, _value, ttl in writes] == [("pool:test", 30)]
    cached = writes[0][1]
    assert cached["items"][0]["avatar_url"] == ""
    assert cached["items"][0]["avatar_url_status"] == "missing"
    assert "raw_profile_avatar_url" not in cached["items"][0]
    assert cached_pool_avatar_ids(cached) == frozenset({7101})

    restored = restore_cached_pool_avatars(
        cached,
        {
            7101: {
                "avatar_url": signed,
                "avatar_url_status": "ephemeral",
                "avatar_upstream_status": "ephemeral",
                "avatar_url_source": "pool_avatar_url",
                "avatar_fallback": "",
            }
        },
    )
    assert restored["items"][0]["avatar_url"] == signed
    assert restored["items"][0]["avatar_url_status"] == "ephemeral"
    assert cached_pool_avatar_ids(restored) == frozenset()
