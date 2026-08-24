from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.domains.kol.pool_read_avatar_hydration import (
    RAW_PROFILE_AVATAR_EXTRACTOR_VERSION,
    _capability_negative_predicate,
    bounded_profile_avatar_urls,
    raw_profile_avatar_capability,
)


ROOT = Path(__file__).resolve().parents[1]


def _profile_payload(url: str, *, kind: str = "youtube#channel") -> str:
    return json.dumps({
        "profile": {"items": [{
            "kind": kind,
            "snippet": {"thumbnails": {"high": {"url": url}}},
        }]},
    })


def test_capability_matches_strict_profile_paths_and_rejects_content_paths() -> None:
    assert raw_profile_avatar_capability({"profile": {"avatar_url": "https://img/profile"}}) is True
    assert raw_profile_avatar_capability(_profile_payload("https://img/channel")) is True
    assert raw_profile_avatar_capability(_profile_payload("https://img/video", kind="youtube#video")) is False
    assert raw_profile_avatar_capability({
        "videos": [{"authorMeta": {"profilePicUrlHD": "https://img/content-author"}}],
    }) is False
    assert raw_profile_avatar_capability({"profile": {}}) is False
    assert raw_profile_avatar_capability("not-json") is None
    assert raw_profile_avatar_capability([]) is None


def _capability_conn(*, with_298: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    capability = (
        ", raw_profile_avatar_present INTEGER, raw_profile_avatar_extracted_at TEXT, "
        "raw_profile_avatar_extractor_version TEXT, last_scrape_at TEXT, updated_at TEXT"
        if with_298 else ""
    )
    conn.execute(
        "CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, avatar_url TEXT, "
        f"raw_platform_data TEXT{capability})"
    )
    return conn


def test_fresh_false_capability_skips_raw_but_stale_true_null_and_old_version_fail_open() -> None:
    conn = _capability_conn()
    for pool_id in range(1, 7):
        conn.execute(
            "INSERT INTO vkpi_kol_pool VALUES (?, '', ?, ?, ?, ?, ?, ?)",
            (
                pool_id,
                _profile_payload(f"https://img/{pool_id}"),
                0 if pool_id != 3 else 1,
                "2026-08-02T00:00:00Z" if pool_id != 2 else "2026-08-01T00:00:00Z",
                "old" if pool_id == 5 else RAW_PROFILE_AVATAR_EXTRACTOR_VERSION,
                "2026-08-01T00:00:00Z" if pool_id != 2 else "2026-08-02T00:00:00Z",
                "2026-08-03T00:00:00Z" if pool_id == 6 else "2026-08-01T00:00:00Z",
            ),
        )
    conn.execute("UPDATE vkpi_kol_pool SET raw_profile_avatar_present=NULL WHERE id=4")
    conn.commit()

    avatars = bounded_profile_avatar_urls(conn, [1, 2, 3, 4, 5, 6])

    assert avatars == {pool_id: f"https://img/{pool_id}" for pool_id in (2, 3, 4, 5, 6)}


def test_old_sqlite_and_postgres_schemas_fail_open_without_298_columns() -> None:
    sqlite_conn = _capability_conn(with_298=False)
    sqlite_conn.execute(
        "INSERT INTO vkpi_kol_pool VALUES (1, '', ?)",
        (_profile_payload("https://img/legacy"),),
    )
    assert bounded_profile_avatar_urls(sqlite_conn, [1]) == {1: "https://img/legacy"}

    class Result:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[dict[str, Any]]:
            return self.rows

    class PostgresCompatConnection:
        def __init__(self) -> None:
            self.hydration_sql = ""

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Result:
            if "information_schema.columns" in sql:
                assert params == ("vkpi_kol_pool",)
                return Result([{"column_name": "id"}, {"column_name": "raw_platform_data"}])
            self.hydration_sql = sql
            assert params == (1,)
            return Result([{"id": 1, "raw_profile_avatar_url": "https://img/pg-legacy"}])

    pg_conn = PostgresCompatConnection()
    assert _capability_negative_predicate(pg_conn) == ("", ())
    assert bounded_profile_avatar_urls(pg_conn, [1]) == {1: "https://img/pg-legacy"}
    assert "raw_profile_avatar_present" not in pg_conn.hydration_sql

    class FailingPostgresCompatConnection:
        def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> Result:
            raise RuntimeError("information schema temporarily unavailable")

    assert _capability_negative_predicate(FailingPostgresCompatConnection()) == ("", ())


def test_migration_298_is_additive_reversible_and_never_stores_an_avatar_url() -> None:
    up = (ROOT / "migrations/298_vkpi_kol_pool_raw_profile_avatar_capability.sql").read_text()
    down = (ROOT / "migrations/298_vkpi_kol_pool_raw_profile_avatar_capability_down.sql").read_text()
    for column in (
        "raw_profile_avatar_present",
        "raw_profile_avatar_extracted_at",
        "raw_profile_avatar_extractor_version",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in up
        assert f"DROP COLUMN IF EXISTS {column}" in down
    assert "raw_profile_avatar_url" not in up
    assert "298_vkpi_kol_pool_raw_profile_avatar_capability.sql" in down
