from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from psycopg._queries import PostgresQuery
from psycopg.adapt import Transformer

from app.domains.kol import profile_basics, search_inventory_refresh as refresh
from app.domains.kol import search_sessions, url_deep_crawl, url_deep_crawl_queue
from app.domains.ops import scheduler_registry
from app.db.connection_sql_translation import translate_sql_dialect
from app.services.scheduler import jobs_tasks
from app.workers import apify_jobs_worker_handlers as worker_handlers


ROOT = Path(__file__).resolve().parents[1]


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        self.executed.append((sql, params))
        return _Result(self.rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _SqliteWorkerConnection:
    """Small psycopg-compatible adapter for durable worker outcome tests."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @contextmanager
    def transaction(self):
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def cursor(self, **_kwargs: Any):
        conn = self._conn

        class _Cursor:
            def __init__(self) -> None:
                self._cursor = conn.cursor()

            def __enter__(self):
                return self

            def __exit__(self, *_args: Any) -> bool:
                self._cursor.close()
                return False

            def execute(self, sql: str, params: tuple[Any, ...] = ()):
                translated = (
                    str(sql)
                    .replace("%s::jsonb", "?")
                    .replace("%s", "?")
                    .replace("NOW()", "CURRENT_TIMESTAMP")
                )
                return self._cursor.execute(translated, params)

            def fetchone(self):
                return self._cursor.fetchone()

        return _Cursor()

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        sqlite_params = tuple(
            value.isoformat() if isinstance(value, datetime) else value
            for value in params
        )
        return self._conn.execute(sql, sqlite_params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


def test_candidate_selection_prioritises_missing_and_stale_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn(
        [
            {
                "id": 7,
                "handle": "portrait-maker",
                "platform": "youtube",
                "profile_url": "https://www.youtube.com/@portrait-maker",
                "display_name": "Portrait Maker",
                "last_seen_at": None,
                "latest_video_at": None,
                "last_ready_refresh_at": None,
                "last_refresh_attempt_at": None,
            }
        ]
    )
    monkeypatch.setattr(
        refresh,
        "table_exists",
        lambda name: name in {
            "vkpi_kol_pool",
            "vkpi_kol_video_evidence",
            "vkpi_kol_url_deep_crawl_runs",
            "apify_jobs",
        },
    )
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)

    result = refresh.select_refresh_candidates(
        50,
        as_of=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert result[0]["kol_pool_id"] == 7
    assert result[0]["priority_reasons"] == ["never_profile_refreshed", "no_video_evidence"]
    sql, params = conn.executed[0]
    assert "latest_ready_refresh" in sql
    assert "latest_inventory_attempt" in sql
    assert "payload ->> 'source'" in sql
    assert "%" not in sql
    assert "p.duplicate_of_id IS NULL" in sql
    assert "published_at_norm" in sql
    assert "CAST(posted_at AS TIMESTAMPTZ)" in sql
    assert "publish_date" in sql
    assert refresh.REFRESH_SOURCE in params
    assert "maintenance_refresh_%" in params
    translated = translate_sql_dialect(sql)
    pg_query = PostgresQuery(Transformer())
    pg_query.convert(translated, params)
    assert pg_query.query
    assert params[-2:] == (refresh.SCAN_PAGE_SIZE, 0)


def test_candidate_selection_without_video_table_is_pg_parse_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn([])
    monkeypatch.setattr(
        refresh,
        "table_exists",
        lambda name: name
        in {"vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "apify_jobs"},
    )
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)

    assert refresh.select_refresh_candidates(1) == []

    sql, params = conn.executed[0]
    assert "%" not in sql
    assert "maintenance_refresh_%" in params
    translated = translate_sql_dialect(sql)
    pg_query = PostgresQuery(Transformer())
    pg_query.convert(translated, params)
    assert pg_query.query


def test_maintenance_gate_block_is_durable_without_retry_and_remains_reselectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqlite_conn = sqlite3.connect(":memory:")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL,
            platform TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            display_name TEXT,
            last_seen_at TEXT,
            duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_url_deep_crawl_runs (
            kol_pool_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY,
            job_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        """
    )
    payload = {
        "url": "https://www.youtube.com/@creator",
        "kol_pool_id": 7,
        "source": refresh.REFRESH_SOURCE,
        "maintenance_refresh": True,
    }
    sqlite_conn.execute(
        """
        INSERT INTO vkpi_kol_pool
            (id, handle, platform, profile_url, display_name)
        VALUES (7, '@creator', 'youtube', 'https://www.youtube.com/@creator', 'Creator')
        """
    )
    sqlite_conn.execute(
        """
        INSERT INTO apify_jobs
            (id, job_type, payload, status, attempts, created_at)
        VALUES (11, ?, ?, 'running', 2, '2026-09-04T11:59:00+00:00')
        """,
        (refresh.JOB_TYPE, json.dumps(payload)),
    )
    sqlite_conn.commit()
    conn = _SqliteWorkerConnection(sqlite_conn)

    monkeypatch.setattr(
        "app.core.release_validation.release_validation_active",
        lambda: True,
    )
    monkeypatch.setattr(worker_handlers, "_resolve_job_staff", lambda *_args: {})
    monkeypatch.setattr(worker_handlers, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: pytest.fail("release-fenced maintenance must not call provider"),
    )
    monkeypatch.setattr(
        worker_handlers.deep_crawl_worker,
        "record_monitor_terminal",
        lambda *_args, **_kwargs: None,
    )

    # A terminal maintenance decision returns normally, so the outer worker
    # never enters its exception retry path.  The outcome write deliberately
    # does not touch attempts.
    worker_handlers._process_kol_profile_deep_crawl(
        conn,
        {"id": 11, "attempts": 2},
        payload,
    )

    persisted = sqlite_conn.execute(
        "SELECT status, attempts, last_error FROM apify_jobs WHERE id=11"
    ).fetchone()
    assert dict(persisted) == {
        "status": "blocked",
        "attempts": 2,
        "last_error": "maintenance_refresh_release_validation_fenced",
    }

    available_tables = {
        "vkpi_kol_pool",
        "vkpi_kol_url_deep_crawl_runs",
        "apify_jobs",
    }
    monkeypatch.setattr(refresh, "table_exists", available_tables.__contains__)
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    candidates = refresh.select_refresh_candidates(
        1,
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    assert [candidate["kol_pool_id"] for candidate in candidates] == [7]

    # The exemption is intentionally narrow: a recent non-terminal attempt
    # still applies the cooldown and prevents immediate reselection.
    sqlite_conn.execute(
        "UPDATE apify_jobs SET status='queued', last_error=NULL WHERE id=11"
    )
    sqlite_conn.commit()
    assert refresh.select_refresh_candidates(
        1,
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    ) == []
    sqlite_conn.close()


def test_daily_refresh_is_bounded_idempotent_and_queue_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {"kol_pool_id": 1, "handle": "one", "platform": "youtube", "profile_url": "https://x/1", "priority_reasons": ["no_video_evidence"]},
        {"kol_pool_id": 2, "handle": "two", "platform": "youtube", "profile_url": "https://x/2", "priority_reasons": ["video_evidence_stale"]},
    ]
    conn = _Conn([{"used": 0}])
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        refresh,
        "select_refresh_candidates",
        lambda _limit, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        refresh,
        "table_exists",
        lambda name: name in {"apify_jobs", refresh.DAILY_SLOT_TABLE},
    )
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(
        refresh,
        "_reserve_daily_job_slots",
        lambda *_args, **_kwargs: {
            "reservation_token": "token",
            "reserved_slots": [1, 2],
            "used_before": 0,
            "used_after_reservation": 2,
            "hard_limit": 5,
        },
    )
    monkeypatch.setattr(refresh, "_bind_daily_job_slot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(refresh, "_release_daily_job_slots", lambda *_args, **_kwargs: 1)

    def enqueue(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"url": url, **kwargs})
        return {"status": "queued" if kwargs["kol_pool_id"] == 1 else "already_queued", "job_id": 99}

    monkeypatch.setattr(refresh.url_deep_crawl, "enqueue_profile_deep_crawl_job", enqueue)
    result = refresh.enqueue_daily_refresh(
        50,
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "ok",
        "task_key": "kol_profile_incremental_refresh",
        "job_type": "kol_profile_deep_crawl",
        "candidate_count": 2,
        "selected_candidate_count": 2,
        "daily_limit": 5,
        "run_limit": 5,
        "default_canary_limit": 5,
        "daily_cap_unit": "new_maintenance_jobs_not_provider_calls",
        "daily_cap_notice": "5 maintenance jobs are not 5 provider calls; one job may perform multiple provider calls",
        "daily_used": 0,
        "daily_reserved_before_run": 0,
        "remaining_before_run": 5,
        "reservation_slots_granted": 2,
        "batch_date": "2026-09-04",
        "budget_timezone": "America/New_York",
        "budget_window_start": "2026-09-04T04:00:00+00:00",
        "budget_window_end": "2026-09-05T04:00:00+00:00",
        "selection_status": "not_run",
        "selection_scanned_rows": 0,
        "selection_invalid_profile_urls": 0,
        "selection_scan_exhausted": False,
        "refresh_source": "kol_search_inventory_daily",
        "refresh_mode": "account_deep_one_post_no_followups",
        "provider_calls_performed": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": True,
        "queued": 1,
        "already_queued": 1,
        "failed": 0,
        "reservation_slots_released": 1,
        "reservation_slots_held": 1,
    }
    assert len(calls) == 2
    assert calls[0]["max_posts"] == 1
    assert calls[0]["mode"] == "account_deep"
    assert calls[0]["source"] == refresh.REFRESH_SOURCE
    assert calls[0]["queue_lane"] == "batch"
    assert calls[0]["suppress_final_v1"] is True
    assert calls[0]["suppress_contact_followup"] is True
    assert calls[0]["suppress_profile_followups"] is True
    assert calls[0]["maintenance_refresh"] is True
    assert calls[0]["maintenance_batch_date"] == "2026-09-04"


def test_daily_refresh_closes_source_scoped_local_calendar_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn([{"used": refresh.MAX_DAILY_LIMIT}])
    monkeypatch.setattr(
        refresh,
        "table_exists",
        lambda name: name in {"apify_jobs", refresh.DAILY_SLOT_TABLE},
    )
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(
        refresh,
        "select_refresh_candidates",
        lambda _limit, **_kwargs: pytest.fail("candidate query must not run after the daily cap"),
    )

    as_of = datetime(2026, 9, 4, 7, 20, tzinfo=timezone.utc)
    result = refresh.enqueue_daily_refresh(as_of=as_of)

    assert result["status"] == "budget_exhausted"
    assert result["daily_used"] == refresh.MAX_DAILY_LIMIT
    assert result["remaining_before_run"] == 0
    assert result["queued"] == 0
    _, params = conn.executed[0]
    assert params[-2].isoformat() == "2026-09-04T04:00:00+00:00"
    assert params[-1].isoformat() == "2026-09-05T04:00:00+00:00"


def test_calendar_budget_resets_even_when_runs_are_less_than_24_hours_apart() -> None:
    previous = datetime(2026, 9, 3, 7, 20, 5, tzinfo=timezone.utc)
    current = datetime(2026, 9, 4, 7, 20, 0, tzinfo=timezone.utc)

    previous_start, previous_end, previous_batch = refresh._calendar_day_bounds(previous)
    current_start, current_end, current_batch = refresh._calendar_day_bounds(current)

    assert previous_batch == "2026-09-03"
    assert current_batch == "2026-09-04"
    assert previous_end == current_start
    assert previous_start.isoformat() == "2026-09-03T04:00:00+00:00"
    assert current_end.isoformat() == "2026-09-05T04:00:00+00:00"


def test_database_slots_enforce_hard_daily_job_cap_and_release_unused() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_kol_search_inventory_daily_slots (
            batch_date TEXT NOT NULL,
            slot_no INTEGER NOT NULL CHECK (slot_no BETWEEN 1 AND 5),
            reservation_token TEXT NOT NULL,
            job_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (batch_date, slot_no)
        )
        """
    )

    first = refresh._reserve_daily_job_slots(
        conn,
        batch_date="2026-09-04",
        requested=30,
        actual_jobs=0,
    )
    second = refresh._reserve_daily_job_slots(
        conn,
        batch_date="2026-09-04",
        requested=30,
        actual_jobs=0,
    )

    assert len(first["reserved_slots"]) == 5
    assert len(second["reserved_slots"]) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_search_inventory_daily_slots"
    ).fetchone()[0] == refresh.MAX_DAILY_LIMIT

    released = refresh._release_daily_job_slots(
        conn,
        batch_date="2026-09-04",
        reservation_token=first["reservation_token"],
        slot_numbers=first["reserved_slots"][:5],
    )
    third = refresh._reserve_daily_job_slots(
        conn,
        batch_date="2026-09-04",
        requested=10,
        actual_jobs=0,
    )

    assert released == 5
    assert len(third["reserved_slots"]) == 5
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_search_inventory_daily_slots"
    ).fetchone()[0] == refresh.MAX_DAILY_LIMIT
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO vkpi_kol_search_inventory_daily_slots
                (batch_date, slot_no, reservation_token)
            VALUES ('2026-09-04', 6, 'must-fail')
            """
        )
    conn.close()


def test_limit_50_and_repeated_runs_cannot_exceed_five_daily_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqlite_conn = sqlite3.connect(":memory:")
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_conn.executescript(
        """
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY,
            job_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_search_inventory_daily_slots (
            batch_date TEXT NOT NULL,
            slot_no INTEGER NOT NULL CHECK (slot_no BETWEEN 1 AND 5),
            reservation_token TEXT NOT NULL,
            job_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (batch_date, slot_no)
        );
        """
    )
    conn = _SqliteWorkerConnection(sqlite_conn)
    candidates = [
        {
            "kol_pool_id": index,
            "profile_url": f"https://www.youtube.com/@creator-{index}",
        }
        for index in range(1, 9)
    ]
    requested_limits: list[int] = []
    queued_jobs: list[int] = []

    def select(limit: int, **_kwargs: Any) -> list[dict[str, Any]]:
        requested_limits.append(limit)
        # Deliberately violate the selector contract: the allocator must still
        # prevent a caller or future selector bug from exceeding the hard cap.
        return candidates

    def enqueue(_url: str, **kwargs: Any) -> dict[str, Any]:
        job_id = len(queued_jobs) + 1
        queued_jobs.append(int(kwargs["kol_pool_id"]))
        return {"status": "queued", "job_id": job_id}

    monkeypatch.setattr(refresh, "table_exists", lambda _name: True)
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(refresh, "select_refresh_candidates", select)
    monkeypatch.setattr(
        refresh.url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        enqueue,
    )

    first = refresh.enqueue_daily_refresh(
        50,
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )
    second = refresh.enqueue_daily_refresh(
        50,
        as_of=datetime(2026, 9, 4, 13, tzinfo=timezone.utc),
    )

    assert requested_limits == [5, 5]
    assert first["run_limit"] == 5
    assert first["daily_limit"] == 5
    assert first["queued"] == 5
    assert second["status"] == "budget_exhausted"
    assert second["queued"] == 0
    assert len(queued_jobs) == 5
    assert sqlite_conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_search_inventory_daily_slots"
    ).fetchone()[0] == 5
    sqlite_conn.close()


def test_candidate_selection_pages_past_invalid_legacy_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = [
        {
            "id": index,
            "handle": f"bad-{index}",
            "platform": "youtube",
            "profile_url": f"https://www.youtube.com/watch?v=bad{index}",
            "display_name": "bad",
            "last_seen_at": None,
            "latest_video_at": None,
            "last_ready_refresh_at": None,
            "last_refresh_attempt_at": None,
        }
        for index in (1, 2)
    ]
    valid = {
        "id": 3,
        "handle": "good",
        "platform": "youtube",
        "profile_url": "https://www.youtube.com/@good",
        "display_name": "Good",
        "last_seen_at": None,
        "latest_video_at": None,
        "last_ready_refresh_at": None,
        "last_refresh_attempt_at": None,
    }

    class _PagedConn(_Conn):
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            self.executed.append((sql, params))
            return _Result(invalid if params[-1] == 0 else [valid])

    conn = _PagedConn()
    monkeypatch.setattr(refresh, "SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(refresh, "MAX_SCAN_ROWS", 4)
    monkeypatch.setattr(refresh, "table_exists", lambda _name: True)
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    diagnostics: dict[str, Any] = {}

    result = refresh.select_refresh_candidates(1, diagnostics=diagnostics)

    assert [item["kol_pool_id"] for item in result] == [3]
    assert diagnostics == {
        "status": "ok",
        "scanned_rows": 3,
        "invalid_profile_urls": 2,
        "scan_exhausted": False,
        "scan_limit": 4,
    }
    assert [params[-1] for _, params in conn.executed] == [0, 2]


def test_missing_schema_and_query_failure_are_not_reported_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(refresh, "table_exists", lambda name: name != "apify_jobs")
    with pytest.raises(refresh.RefreshSelectionUnavailable, match="required_tables_missing:apify_jobs"):
        refresh.select_refresh_candidates()

    class _BrokenConn(_Conn):
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
            raise RuntimeError("sql broke")

    monkeypatch.setattr(refresh, "table_exists", lambda _name: True)
    monkeypatch.setattr(refresh, "get_conn", lambda: _BrokenConn())
    with pytest.raises(refresh.RefreshSelectionUnavailable, match="candidate_query_failed"):
        refresh.select_refresh_candidates()


def test_enqueue_failure_rolls_back_before_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        {"kol_pool_id": 1, "profile_url": "https://www.youtube.com/@one"},
        {"kol_pool_id": 2, "profile_url": "https://www.youtube.com/@two"},
    ]
    conn = _Conn([{"used": 0}])
    monkeypatch.setattr(
        refresh,
        "table_exists",
        lambda name: name in {"apify_jobs", refresh.DAILY_SLOT_TABLE},
    )
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(
        refresh,
        "_reserve_daily_job_slots",
        lambda *_args, **_kwargs: {
            "reservation_token": "token",
            "reserved_slots": [1, 2],
            "used_before": 0,
            "used_after_reservation": 2,
            "hard_limit": 5,
        },
    )
    monkeypatch.setattr(refresh, "_bind_daily_job_slot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        refresh,
        "select_refresh_candidates",
        lambda _limit, **_kwargs: candidates,
    )

    def enqueue(_url: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["kol_pool_id"] == 1:
            raise RuntimeError("transaction aborted")
        return {"status": "queued", "job_id": 2}

    monkeypatch.setattr(refresh.url_deep_crawl, "enqueue_profile_deep_crawl_job", enqueue)
    result = refresh.enqueue_daily_refresh(
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    )

    assert conn.rollbacks == 1
    assert result["status"] == "partial"
    assert result["queued"] == 1
    assert result["failed"] == 1


def test_maintenance_worker_skips_sessions_media_and_marks_profile_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def execute(body: dict[str, Any]) -> dict[str, Any]:
        captured.update(body)
        return {"status": "ready"}

    monkeypatch.setattr(url_deep_crawl, "dry_run_url_deep_crawl", execute)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_maintenance_refresh_execution_block_reason",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_revalidate_maintenance_target_fence",
        lambda _payload, **_kwargs: {"kol_pool_id": 7},
    )
    monkeypatch.setattr(
        search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: pytest.fail("maintenance refresh must not create a search session"),
    )
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "get_conn",
        lambda: pytest.fail("maintenance refresh must not warm media"),
    )

    result = url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.youtube.com/@creator",
            "kol_pool_id": 7,
            "source": refresh.REFRESH_SOURCE,
            "maintenance_refresh": True,
            "maintenance_target_fence": {
                "version": 1,
                "kind": "kol_search_inventory_daily",
                "kol_pool_id": 7,
            },
            "suppress_contact_followup": True,
            "suppress_profile_followups": True,
            "max_posts": 1,
        }
    )

    assert result["status"] == "ready"
    assert captured["maintenance_refresh"] is True
    assert captured["max_posts"] == 1
    assert captured["suppress_final_v1"] is True
    assert captured["suppress_profile_followups"] is True
    assert captured["suppress_contact_acquisition"] is True
    assert captured["suppress_avatar_landing"] is True


def test_maintenance_worker_rechecks_release_fence_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.release_validation.release_validation_active",
        lambda: True,
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: pytest.fail("release-fenced maintenance must not call provider"),
    )

    result = url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.youtube.com/@creator",
            "kol_pool_id": 7,
            "maintenance_refresh": True,
        }
    )

    assert result == {
        "status": "maintenance_refresh_release_validation_fenced",
        "reason": "maintenance_refresh_release_validation_fenced",
        "provider_calls_performed": False,
        "llm_calls_performed": False,
        "viltrox_fit_score_untouched": True,
    }


def test_maintenance_worker_rechecks_scheduler_switch_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _Conn([{"enabled": False}])
    monkeypatch.setattr(
        "app.core.release_validation.release_validation_active",
        lambda: False,
    )
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_maintenance_refresh_batch_block_reason",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: pytest.fail("disabled maintenance must not call provider"),
    )

    result = url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.youtube.com/@creator",
            "kol_pool_id": 7,
            "maintenance_refresh": True,
        }
    )

    assert result["status"] == "maintenance_refresh_task_disabled"
    assert result["provider_calls_performed"] is False


@pytest.mark.parametrize(
    ("batch_date", "expected"),
    [
        (None, "maintenance_refresh_batch_invalid"),
        ("2026-02-30", "maintenance_refresh_batch_invalid"),
        ("2026-09-03", "maintenance_refresh_batch_expired"),
        ("2026-09-05", "maintenance_refresh_batch_future"),
        ("2026-09-04", ""),
    ],
)
def test_maintenance_batch_date_fence_uses_new_york_calendar(
    batch_date: str | None,
    expected: str,
) -> None:
    payload = {"maintenance_batch_date": batch_date} if batch_date is not None else {}
    assert url_deep_crawl_queue._maintenance_refresh_batch_block_reason(
        payload,
        as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    ) == expected


def test_maintenance_batch_date_fence_handles_new_york_dst_boundary() -> None:
    assert url_deep_crawl_queue._maintenance_refresh_batch_block_reason(
        {"maintenance_batch_date": "2026-03-07"},
        as_of=datetime(2026, 3, 8, 4, 59, tzinfo=timezone.utc),
    ) == ""
    assert url_deep_crawl_queue._maintenance_refresh_batch_block_reason(
        {"maintenance_batch_date": "2026-03-08"},
        as_of=datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc),
    ) == ""


@pytest.mark.parametrize(
    ("batch_date", "expected"),
    [
        (None, "maintenance_refresh_batch_invalid"),
        ("not-a-date", "maintenance_refresh_batch_invalid"),
        ("2000-01-01", "maintenance_refresh_batch_expired"),
        ("2999-01-01", "maintenance_refresh_batch_future"),
    ],
)
def test_force_enable_cannot_run_invalid_or_cross_day_maintenance_backlog(
    monkeypatch: pytest.MonkeyPatch,
    batch_date: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "app.core.release_validation.release_validation_active",
        lambda: False,
    )
    monkeypatch.setenv("OPS_SCHEDULER_FORCE_ENABLE", "1")
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda _body: pytest.fail("invalid maintenance backlog must not call provider"),
    )
    payload: dict[str, Any] = {
        "url": "https://www.youtube.com/@creator",
        "kol_pool_id": 7,
        "maintenance_refresh": True,
    }
    if batch_date is not None:
        payload["maintenance_batch_date"] = batch_date

    result = url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)

    assert result["status"] == expected
    assert result["provider_calls_performed"] is False


def test_profile_materialization_suppression_skips_avatar_and_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_basics, "_record_creator_identity_alias", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(profile_basics, "_commit", lambda _db: None)
    monkeypatch.setattr(
        profile_basics,
        "_land_profile_avatar",
        lambda *_args, **_kwargs: pytest.fail("maintenance refresh must not land avatar"),
    )
    monkeypatch.setattr(
        "app.domains.kol.contact_acquisition_queue.enqueue_contact_acquisition",
        lambda *_args, **_kwargs: pytest.fail("maintenance refresh must not enqueue contacts"),
    )

    result = profile_basics._finalize_profile_write(
        object(),
        target_id=7,
        requested_identity={},
        canonical_match=False,
        commit_write=True,
        planned_values={"avatar_url": "https://example.test/avatar.jpg"},
        normalized={"platform": "youtube", "handle": "creator"},
        existing=None,
        avatar_landing_budget=None,
        suppress_contact_acquisition=True,
        suppress_avatar_landing=True,
    )

    assert result == {}


def test_scheduler_job_calls_queue_only_refresher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records: list[dict[str, Any]] = []
    monkeypatch.setattr(jobs_tasks, "_scheduler_task_enabled", lambda key: key == refresh.TASK_KEY)
    monkeypatch.setattr("app.core.release_validation.release_validation_active", lambda: False)
    monkeypatch.setattr(refresh, "enqueue_daily_refresh", lambda: {"status": "ok", "candidate_count": 3, "queued": 3, "already_queued": 0, "failed": 0})
    monkeypatch.setattr(jobs_tasks, "_record_scheduler_run", lambda key, **values: records.append({"key": key, **values}))

    result = asyncio.run(jobs_tasks.job_kol_profile_incremental_refresh())

    assert result and result["queued"] == 3
    assert records == [{
        "key": refresh.TASK_KEY,
        "ok": False,
        "error": "status=queued; awaiting_downstream_completion",
        "status": "blocked",
    }]


def test_migration_wires_one_bounded_daily_refresh_without_fake_cost_cap() -> None:
    sql = (ROOT / "migrations/310_vkpi_kol_search_refresh_scheduler.sql").read_text()
    assert "'kol_profile_incremental_refresh'" in sql
    assert "FALSE" in sql
    assert "enabled=FALSE" in sql.replace(" ", "")
    assert "enabled=TRUE" not in sql.replace(" ", "")
    assert "max_daily_runs" in sql
    assert "max_daily_cost_cents" in sql
    assert "idx_apify_jobs_kol_search_inventory_source_created" in sql
    assert "payload ->> 'source'='kol_search_inventory_daily'" in sql
    assert "CREATE TABLE IF NOT EXISTS vkpi_kol_search_inventory_daily_slots" in sql
    assert "PRIMARY KEY (batch_date, slot_no)" in sql
    assert "CHECK (slot_no BETWEEN 1 AND 5)" in sql
    assert "5 jobs do not equal 5 provider calls" in sql
    assert "Intentionally force OFF on upgrade" in sql
    assert "BEGIN;" not in sql and "COMMIT;" not in sql


def test_standard_local_scheduler_allowlists_daily_inventory_refresh() -> None:
    supervisor = (ROOT / "scripts/ops/local_stack_supervisor.sh").read_text()

    assert "SCHEDULER_ALLOWLIST=\"kol_profile_incremental_refresh," in supervisor


def test_registry_exposes_paid_future_execution_truth() -> None:
    row = scheduler_registry._row_to_dict(
        {
            "id": 1,
            "task_key": refresh.TASK_KEY,
            "label": "KOL 搜索库存每日增量刷新",
            "enabled": False,
            "risk_level": "medium",
        }
    )

    assert row["execution_wired"] is True
    assert row["paid_execution"] is True
    assert "最多 5 个维护刷新任务" in row["enable_warning"]
    assert "5 个维护任务不等于 5 次外部 provider 调用" in row["enable_warning"]
    assert "不会立即运行" in row["enable_warning"]
    assert row["toggle_effect"] == "future_scheduler_runs_only"


def test_migration_down_restores_forward_ledger_eligibility() -> None:
    sql = (ROOT / "migrations/310_vkpi_kol_search_refresh_scheduler_down.sql").read_text()

    assert "DROP INDEX IF EXISTS idx_apify_jobs_kol_search_inventory_source_created" in sql
    assert "DROP TABLE IF EXISTS vkpi_kol_search_inventory_daily_slots" in sql
    assert "DELETE FROM schema_migrations" in sql
    assert "310_vkpi_kol_search_refresh_scheduler.sql" in sql
