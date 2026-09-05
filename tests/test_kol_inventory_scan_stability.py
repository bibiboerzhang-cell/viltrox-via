from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.kol import search_inventory_refresh as refresh
from app.domains.kol import search_inventory_scan_state as state


NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Inventory:
    def __init__(self, rows):
        self.rows = rows
        self.offsets = []
        self.cache = sqlite3.connect(":memory:")
        self.cache.row_factory = sqlite3.Row
        self.cache.execute(
            "CREATE TABLE persistent_cache (cache_key TEXT PRIMARY KEY, "
            "value_json TEXT, expires_at TEXT, created_at TEXT)"
        )

    def execute(self, sql, params=()):
        if "persistent_cache" in sql:
            values = tuple(v.isoformat() if isinstance(v, datetime) else v for v in params)
            return self.cache.execute(sql, values)
        if "COUNT(*) AS used" in sql:
            return Rows([{"used": 0}])
        limit, offset = params[-2:]
        self.offsets.append(offset)
        return Rows(self.rows[offset:offset + limit])

    def commit(self):
        self.cache.commit()

    def rollback(self):
        self.cache.rollback()


def profile(index: int, *, valid: bool = False) -> dict[str, Any]:
    return {
        "id": index,
        "platform": "youtube",
        "profile_url": (
            f"https://www.youtube.com/@person-{index}"
            if valid else f"https://www.youtube.com/watch?v=bad{index}"
        ),
    }


def configure(monkeypatch, conn):
    monkeypatch.setattr(refresh, "SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(refresh, "MAX_SCAN_ROWS", 4)
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(refresh, "table_exists", lambda name: name != "vkpi_kol_video_evidence")


def test_next_run_resumes_past_unusable_prefix_without_raising_daily_cap(monkeypatch):
    conn = Inventory([*(profile(i) for i in range(1, 5)), profile(5, valid=True)])
    configure(monkeypatch, conn)
    starts = []
    monkeypatch.setattr(refresh, "_reserve_daily_job_slots", lambda *a, **k: {
        "reserved_slots": [1], "reservation_token": "fixture", "used_before": 0,
    })
    monkeypatch.setattr(refresh, "_bind_daily_job_slot", lambda *a, **k: None)
    monkeypatch.setattr(refresh.url_deep_crawl, "enqueue_profile_deep_crawl_job",
                        lambda url, **kw: starts.append(url) or {"status": "queued", "job_id": 1})
    first = refresh.enqueue_daily_refresh(as_of=NOW)
    assert first["status"] == "selection_exhausted"
    assert first["selection_next_offset"] == 4
    assert first["queued"] == 0
    second = refresh.enqueue_daily_refresh(as_of=NOW)
    assert second["selection_start_offset"] == 4
    assert second["selection_next_offset"] == 0
    assert second["queued"] == 1
    assert second["daily_limit"] == 5
    assert conn.offsets == [0, 2, 4]
    assert starts == ["https://www.youtube.com/@person-5"]
    conn.cache.close()


def test_stale_offset_wraps_in_same_bounded_scan(monkeypatch):
    conn = Inventory([profile(1, valid=True)])
    configure(monkeypatch, conn)
    diagnostics, progress = {}, {}
    rows = refresh.select_refresh_candidates(
        1, as_of=NOW, start_offset=500, diagnostics=diagnostics, progress=progress,
    )
    assert [row["kol_pool_id"] for row in rows] == [1]
    assert conn.offsets == [500, 0]
    assert diagnostics["scanned_rows"] == 1
    assert progress["next_offset"] == 0
    conn.cache.close()


def test_malformed_url_does_not_abort_other_profiles(monkeypatch):
    malformed = profile(1)
    malformed["profile_url"] = "https://[bad/profile"
    conn = Inventory([malformed, profile(2, valid=True)])
    configure(monkeypatch, conn)
    diagnostics = {}
    rows = refresh.select_refresh_candidates(1, as_of=NOW, diagnostics=diagnostics)
    assert [row["kol_pool_id"] for row in rows] == [2]
    assert diagnostics["invalid_profile_urls"] == 1
    assert diagnostics["scanned_rows"] <= refresh.MAX_SCAN_ROWS
    conn.cache.close()


def test_unexpected_classifier_failure_is_not_silently_skipped(monkeypatch):
    conn = Inventory([profile(1, valid=True)])
    configure(monkeypatch, conn)

    def broken(_url):
        raise RuntimeError("unexpected classifier defect")

    monkeypatch.setattr(refresh.url_deep_crawl, "classify_url", broken)
    with pytest.raises(RuntimeError, match="unexpected classifier defect"):
        refresh.select_refresh_candidates(1, as_of=NOW)
    conn.cache.close()


@pytest.mark.parametrize("value", [-1, None, "bad", float("inf"), 10_000_001])
def test_invalid_cursor_is_bounded(value):
    assert state.bounded_offset(value) == 0


def test_cursor_corruption_and_expiry_restart_without_authorizing_work():
    conn = Inventory([])
    assert state.save_offset(conn, 42, as_of=NOW) == "saved"
    assert state.load_offset(conn, as_of=NOW) == (42, "loaded")
    conn.cache.execute("UPDATE persistent_cache SET value_json='[invalid'")
    assert state.load_offset(conn, as_of=NOW) == (0, "invalid")
    conn.cache.execute("UPDATE persistent_cache SET value_json=?, expires_at=?",
                       (json.dumps({"next_offset": 42}), "2026-09-01T00:00:00+00:00"))
    assert state.load_offset(conn, as_of=NOW) == (0, "missing")
    conn.cache.close()


@pytest.mark.parametrize("operation", ["load", "save"])
def test_cursor_store_failure_rolls_back_and_is_visible(operation):
    class Broken:
        rollbacks = 0

        def execute(self, *args):
            raise RuntimeError("database unavailable")

        def rollback(self):
            self.rollbacks += 1

    conn = Broken()
    result = state.load_offset(conn, as_of=NOW) if operation == "load" else state.save_offset(conn, 2, as_of=NOW)
    assert result == ((0, "unavailable") if operation == "load" else "unavailable")
    assert conn.rollbacks == 1


@pytest.mark.pg
def test_real_postgres_scan_resume_and_cache_storage(pg_dsn, monkeypatch):
    import psycopg
    from app.db.connection import PostgresCompatConnection

    with psycopg.connect(pg_dsn, autocommit=True) as raw:
        # Session-local tables shadow any fixture schema. The PG fixture also
        # rejects databases whose name is not explicitly disposable/test.
        for statement in (
            "CREATE TEMP TABLE persistent_cache (cache_key TEXT PRIMARY KEY, "
            "value_json TEXT, expires_at TIMESTAMPTZ, created_at TIMESTAMPTZ)",
            "CREATE TEMP TABLE vkpi_kol_pool (id BIGINT PRIMARY KEY, handle TEXT, "
            "platform TEXT, profile_url TEXT, display_name TEXT, last_seen_at TIMESTAMPTZ, "
            "duplicate_of_id BIGINT)",
            "CREATE TEMP TABLE vkpi_kol_url_deep_crawl_runs (kol_pool_id BIGINT, "
            "status TEXT, dry_run BOOLEAN, created_at TIMESTAMPTZ)",
            "CREATE TEMP TABLE apify_jobs (id BIGINT, job_type TEXT, payload JSONB, "
            "status TEXT, last_error TEXT, created_at TIMESTAMPTZ)",
            "CREATE TEMP TABLE vkpi_kol_search_inventory_daily_slots ("
            "batch_date DATE, slot_no SMALLINT CHECK (slot_no BETWEEN 1 AND 5), "
            "reservation_token TEXT, job_id BIGINT, updated_at TIMESTAMPTZ, "
            "PRIMARY KEY (batch_date,slot_no))",
        ):
            raw.execute(statement)
        for index in range(1, 6):
            item = profile(index, valid=index == 5)
            raw.execute(
                "INSERT INTO vkpi_kol_pool (id, platform, profile_url) VALUES (%s,%s,%s)",
                (item["id"], item["platform"], item["profile_url"]),
            )
        conn = PostgresCompatConnection(raw)
        configure(monkeypatch, conn)
        starts = []
        monkeypatch.setattr(refresh.url_deep_crawl, "enqueue_profile_deep_crawl_job",
                            lambda url, **kw: starts.append(url) or {"status": "queued", "job_id": 1})
        first = refresh.enqueue_daily_refresh(as_of=NOW)
        second = refresh.enqueue_daily_refresh(as_of=NOW)
        assert first["status"] == "selection_exhausted"
        assert first["selection_next_offset"] == 4
        assert second["selection_start_offset"] == 4
        assert second["selection_next_offset"] == 0
        assert second["queued"] == 1
        assert starts == ["https://www.youtube.com/@person-5"]
        assert state.load_offset(conn, as_of=NOW) == (0, "loaded")
        assert raw.execute("SELECT COUNT(*) AS n FROM vkpi_kol_search_inventory_daily_slots").fetchone()[0] == 1
