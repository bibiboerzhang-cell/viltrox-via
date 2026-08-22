from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains import content_metric_snapshots as snapshots  # noqa: E402
from app.domains.projects import workflow_evidence  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            content_url TEXT NOT NULL,
            platform TEXT,
            video_title TEXT,
            title TEXT,
            posted_at TEXT,
            publish_date TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            duration_seconds INTEGER,
            thumbnail_url TEXT,
            channel_id TEXT,
            channel_name TEXT,
            scrape_status TEXT,
            scrape_source TEXT,
            scrape_error TEXT,
            scraped_at TEXT,
            metrics_scraped_at TEXT,
            metrics_source TEXT,
            updated_at TEXT
        );
        INSERT INTO vkpi_kol_video_evidence (
            id, content_url, view_count, like_count, comment_count, share_count, updated_at
        ) VALUES (7, 'https://www.youtube.com/watch?v=abc123', 100, 10, 2, 1, '2026-01-01T00:00:00Z');
        """
    )
    snapshots.ensure_sqlite_schema(conn)
    return conn


def test_success_is_idempotent_and_preserves_nullable_metrics() -> None:
    conn = _conn()
    kwargs = {
        "evidence_id": 7,
        "provider": "apify",
        "fetched_at": "2026-08-21T12:00:00+00:00",
        "source_observed_at": "2026-08-21T11:59:59+00:00",
        "views": 150,
        "likes": 11,
        "comments": None,
        "shares": None,
        "run_id": "run-1",
    }

    first = snapshots.record_successful_refresh(conn, **kwargs)
    second = snapshots.record_successful_refresh(conn, **kwargs)
    conn.commit()

    assert first["inserted"] is True
    assert first["latest_updated"] is True
    assert second["inserted"] is False
    assert second["latest_updated"] is False
    assert conn.execute("SELECT COUNT(*) FROM vkpi_content_metric_snapshots").fetchone()[0] == 1
    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert latest["view_count"] == 150
    assert latest["comment_count"] is None
    row = dict(conn.execute("SELECT * FROM vkpi_content_metric_snapshots").fetchone())
    assert row["comments"] is None
    assert row["shares"] is None
    assert json.loads(row["quality_flags"]) == ["partial_metrics"]


def test_same_capture_key_with_different_payload_fails_before_latest_diverges() -> None:
    conn = _conn()
    kwargs = {
        "evidence_id": 7,
        "provider": "apify",
        "fetched_at": "2026-08-21T12:00:00+00:00",
        "source_observed_at": "2026-08-21T12:00:00+00:00",
        "views": 150,
        "likes": 11,
        "run_id": "same-provider-run",
    }
    snapshots.record_successful_refresh(conn, **kwargs)
    conn.commit()

    with pytest.raises(ValueError, match="capture_key payload conflict: views"):
        snapshots.record_successful_refresh(conn, **{**kwargs, "views": 999})

    latest = conn.execute(
        "SELECT view_count, like_count FROM vkpi_kol_video_evidence WHERE id=7"
    ).fetchone()
    assert tuple(latest) == (150, 11)
    stored = conn.execute(
        "SELECT views, likes FROM vkpi_content_metric_snapshots"
    ).fetchall()
    assert [tuple(row) for row in stored] == [(150, 11)]


class _PgPrecisionConn:
    """Mimic app.db.connection._normalize_pg_value: TIMESTAMPTZ reads back at
    whole-second ``...Z`` precision even though the write carried microseconds."""

    _TS_FIELDS = ("fetched_at", "source_observed_at")

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner

    def _truncate(self, row: Any) -> Any:
        keys = getattr(row, "keys", None)
        if row is None or not callable(keys) or not set(self._TS_FIELDS) & set(keys()):
            return row
        mapped = dict(row)
        for field in self._TS_FIELDS:
            parsed = snapshots._parse_timestamp(mapped.get(field))
            if parsed is not None:
                mapped[field] = parsed.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        return mapped

    def execute(self, sql: str, params: Any = ()) -> Any:
        outer = self
        cursor = self._inner.execute(sql, params)

        class _Cursor:
            rowcount = cursor.rowcount
            description = cursor.description

            def fetchone(self) -> Any:
                return outer._truncate(cursor.fetchone())

            def fetchall(self) -> list[Any]:
                return [outer._truncate(row) for row in cursor.fetchall()]

        return _Cursor()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def test_microsecond_fetched_at_survives_postgres_second_precision_readback() -> None:
    """Regression: the worker passes ``_utcnow()`` with microseconds; the PG
    compat layer reads TIMESTAMPTZ back at second precision.  The round-trip
    conflict check must not flag fetched_at/source_observed_at as a payload
    conflict (it previously sent every real refresh job to triage)."""
    conn = _PgPrecisionConn(_conn())
    fetched_at = "2026-08-22T01:13:11.156411+00:00"

    failed = snapshots.record_failed_refresh(
        conn,
        evidence_id=7,
        provider="tiktok",
        fetched_at=fetched_at,
        error_code="provider_exception",
    )
    assert failed["inserted"] is True
    assert failed["snapshot"]["status"] == "failed"

    success = snapshots.record_successful_refresh(
        conn,
        evidence_id=7,
        provider="tiktok",
        fetched_at=fetched_at,
        source_observed_at=fetched_at,
        views=321,
        likes=5,
        run_id="run-micro",
    )
    assert success["inserted"] is True
    assert success["latest_updated"] is True
    # Real payload divergence is still detected at the same precision.
    with pytest.raises(ValueError, match="capture_key payload conflict: views"):
        snapshots.record_successful_refresh(
            conn,
            evidence_id=7,
            provider="tiktok",
            fetched_at=fetched_at,
            source_observed_at=fetched_at,
            views=999,
            likes=5,
            run_id="run-micro",
        )


def test_out_of_order_capture_appends_history_without_rewinding_latest() -> None:
    conn = _conn()
    newer = snapshots.record_successful_refresh(
        conn,
        evidence_id=7,
        provider="apify",
        fetched_at="2026-08-21T13:00:00+00:00",
        source_observed_at="2026-08-21T12:00:00+00:00",
        views=200,
        likes=20,
        run_id="newer-observation",
    )
    conn.commit()
    delayed_older = snapshots.record_successful_refresh(
        conn,
        evidence_id=7,
        provider="apify",
        fetched_at="2026-08-21T14:00:00+00:00",
        source_observed_at="2026-08-21T11:00:00+00:00",
        views=150,
        likes=15,
        run_id="delayed-older-observation",
    )
    conn.commit()

    assert newer["inserted"] is True
    assert newer["latest_updated"] is True
    assert delayed_older["inserted"] is True
    assert delayed_older["latest_updated"] is False
    latest = conn.execute(
        "SELECT view_count, like_count, metrics_scraped_at FROM vkpi_kol_video_evidence WHERE id=7"
    ).fetchone()
    assert tuple(latest) == (200, 20, "2026-08-21T13:00:00+00:00")
    history = conn.execute(
        "SELECT views FROM vkpi_content_metric_snapshots WHERE evidence_id=7 ORDER BY fetched_at"
    ).fetchall()
    assert [row[0] for row in history] == [200, 150]


def test_equal_observation_tie_break_is_stable_across_arrival_order() -> None:
    fixed = {
        "evidence_id": 7,
        "provider": "apify",
        "fetched_at": "2026-08-21T12:00:00+00:00",
        "source_observed_at": "2026-08-21T12:00:00+00:00",
    }
    captures = [("tie-a", 301), ("tie-b", 302)]
    expected_run = max(
        captures,
        key=lambda item: snapshots.make_capture_key(
            **fixed,
            status="success",
            run_id=item[0],
        ),
    )[0]
    expected_views = dict(captures)[expected_run]

    for arrival_order in (captures, list(reversed(captures))):
        conn = _conn()
        for run_id, views in arrival_order:
            snapshots.record_successful_refresh(
                conn,
                **fixed,
                views=views,
                run_id=run_id,
            )
            conn.commit()
        assert conn.execute(
            "SELECT view_count FROM vkpi_kol_video_evidence WHERE id=7"
        ).fetchone()[0] == expected_views


def test_missing_evidence_rolls_back_new_snapshot_to_savepoint() -> None:
    conn = _conn()
    conn.execute("PRAGMA foreign_keys=OFF")

    # The canonical snapshot is deliberately attempted before the latest-value
    # UPDATE.  Disable the fixture FK so the append succeeds and the missing
    # latest row fails afterwards; rollback-to-savepoint must remove that row.
    with pytest.raises(LookupError, match="video evidence not found"):
        snapshots.record_successful_refresh(
            conn,
            evidence_id=999,
            provider="apify",
            fetched_at="2026-08-21T12:00:00+00:00",
            views=1,
            run_id="missing-evidence",
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_content_metric_snapshots WHERE evidence_id=999"
    ).fetchone()[0] == 0


def test_all_missing_cannot_clear_latest_or_create_success() -> None:
    conn = _conn()

    with pytest.raises(ValueError, match="at least one observed metric"):
        snapshots.record_successful_refresh(
            conn,
            evidence_id=7,
            provider="apify",
            fetched_at="2026-08-21T12:00:00+00:00",
        )

    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert (latest["view_count"], latest["like_count"], latest["comment_count"], latest["share_count"]) == (100, 10, 2, 1)
    assert conn.execute("SELECT COUNT(*) FROM vkpi_content_metric_snapshots").fetchone()[0] == 0


def test_failure_appends_truth_without_overwriting_latest() -> None:
    conn = _conn()

    result = snapshots.record_failed_refresh(
        conn,
        evidence_id=7,
        provider="apify",
        fetched_at="2026-08-21T12:00:00+00:00",
        error_code="failed_no_return",
        run_id="run-failed",
    )
    conn.commit()

    assert result["inserted"] is True
    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert (latest["view_count"], latest["like_count"], latest["comment_count"], latest["share_count"]) == (100, 10, 2, 1)
    failed = dict(conn.execute("SELECT * FROM vkpi_content_metric_snapshots").fetchone())
    assert failed["status"] == "failed"
    assert failed["error_code"] == "failed_no_return"
    assert set(json.loads(failed["quality_flags"])) >= {"refresh_failed", "all_metrics_missing"}


def test_workflow_success_updates_latest_and_snapshot_in_one_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(workflow_evidence, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_evidence, "get_conn", lambda: conn)
    monkeypatch.setattr(
        workflow_evidence,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "title": "Fresh title",
            "view_count": 222,
            "like_count": 20,
            "comment_count": 3,
            "share_count": None,
            "scrape_status": "success",
            "scrape_source": "youtube_api",
        },
    )

    result = workflow_evidence.refresh_project_video_evidence_metadata(7)

    assert result["status"] == "success"
    assert result["metric_snapshot"]["inserted"] is True
    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert latest["view_count"] == 222
    snapshot = dict(conn.execute("SELECT * FROM vkpi_content_metric_snapshots").fetchone())
    assert snapshot["status"] == "success"
    assert snapshot["views"] == 222


def test_workflow_provider_failure_keeps_latest_and_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(workflow_evidence, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_evidence, "get_conn", lambda: conn)

    def fail(_url: str) -> dict[str, Any]:
        raise LookupError("provider returned no item")

    monkeypatch.setattr(workflow_evidence, "_fetch_video_metadata", fail)

    with pytest.raises(LookupError, match="provider returned no item"):
        workflow_evidence.refresh_project_video_evidence_metadata(7)

    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert latest["view_count"] == 100
    failed = dict(conn.execute("SELECT * FROM vkpi_content_metric_snapshots").fetchone())
    assert failed["status"] == "failed"
    assert failed["error_code"] == "lookuperror"


def test_workflow_all_missing_is_failed_snapshot_and_keeps_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _conn()
    monkeypatch.setattr(workflow_evidence, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_evidence, "get_conn", lambda: conn)
    monkeypatch.setattr(
        workflow_evidence,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "title": "Provider returned metadata only",
            "view_count": None,
            "like_count": None,
            "comment_count": None,
            "share_count": None,
            "scrape_status": "success",
            "scrape_source": "youtube_api",
        },
    )

    result = workflow_evidence.refresh_project_video_evidence_metadata(7)

    latest = dict(conn.execute("SELECT * FROM vkpi_kol_video_evidence WHERE id=7").fetchone())
    assert (latest["view_count"], latest["like_count"], latest["comment_count"], latest["share_count"]) == (100, 10, 2, 1)
    failed = dict(conn.execute("SELECT * FROM vkpi_content_metric_snapshots").fetchone())
    assert failed["status"] == "failed"
    assert failed["error_code"] == "all_metrics_missing"
    assert result["metric_snapshot"]["inserted"] is True


def test_postgres_fallback_translates_qmark_placeholders() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.sql = ""
            self.params: tuple[Any, ...] = ()

        def execute(self, sql: str, params: tuple[Any, ...]) -> None:
            self.sql = sql
            self.params = params

    class Connection:
        def __init__(self) -> None:
            self.value = Cursor()

        def cursor(self) -> Cursor:
            return self.value

    conn = Connection()
    snapshots._execute(conn, "SELECT * FROM t WHERE a=? AND b=?", (1, 2))
    assert conn.value.sql == "SELECT * FROM t WHERE a=%s AND b=%s"
    assert conn.value.params == (1, 2)


def test_batch_trends_keep_last_success_when_latest_attempt_failed() -> None:
    conn = _conn()
    observations = [
        ("2026-08-14T11:00:00+00:00", 100, "success", None),
        ("2026-08-20T11:00:00+00:00", 150, "success", None),
        ("2026-08-21T12:00:00+00:00", 200, "success", None),
        ("2026-08-21T12:30:00+00:00", None, "failed", "provider_timeout"),
    ]
    for index, (fetched_at, views, status, error_code) in enumerate(observations):
        snapshots.append_snapshot(
            conn,
            evidence_id=7,
            provider="apify",
            fetched_at=fetched_at,
            source_observed_at=fetched_at if status == "success" else None,
            views=views,
            status=status,
            error_code=error_code,
            run_id=f"run-{index}",
        )
    conn.commit()

    trend = snapshots.metric_trends_for_evidence(
        conn,
        [7],
        now=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
    )[7]

    assert trend["last_attempt"]["status"] == "failed"
    assert trend["last_success"]["views"] == 200
    assert trend["sample_count"] == 3
    assert trend["attempt_count"] == 4
    assert trend["views_delta_24h"] == 50
    assert trend["views_delta_7d"] == 100
    assert trend["delta_24h_status"] == "ready"
    assert trend["delta_7d_status"] == "ready"
    assert trend["freshness"] == "fresh"
    assert trend["tracking_status"] == "failed"
    assert {"provider", "run_id", "source_observed_at", "quality_flags", "error_code"}.isdisjoint(
        trend["last_attempt"]
    )


def test_batch_trends_are_one_bounded_query_for_200_evidence() -> None:
    conn = _conn()
    conn.executemany(
        "INSERT INTO vkpi_kol_video_evidence (id, content_url, updated_at) VALUES (?,?,?)",
        [(evidence_id, f"https://example.test/{evidence_id}", "2026-08-21") for evidence_id in range(8, 208)],
    )
    conn.executemany(
        """
        INSERT INTO vkpi_content_metric_snapshots (
            evidence_id, capture_key, provider, fetched_at, views, status, quality_flags
        ) VALUES (?,?,?,?,?,'success','[]')
        """,
        [(evidence_id, f"capture-{evidence_id}", "fixture", "2026-08-21T12:00:00Z", evidence_id) for evidence_id in range(8, 208)],
    )
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    result = snapshots.metric_trends_for_evidence(
        conn,
        range(8, 208),
        now=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
    )

    data_queries = [sql for sql in statements if "WITH ranked AS" in sql]
    assert len(result) == 200
    assert len(data_queries) == 1
    assert all(item["tracking_status"] == "insufficient_history" for item in result.values())


def test_batch_trends_cap_history_and_mark_insufficient_7d_baseline() -> None:
    conn = _conn()
    latest = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for offset in range(85):
        fetched = latest - timedelta(hours=offset)
        snapshots.append_snapshot(
            conn,
            evidence_id=7,
            provider="fixture",
            fetched_at=fetched.isoformat(),
            source_observed_at=fetched.isoformat(),
            views=1000 - offset,
            status="success",
            run_id=f"hour-{offset}",
        )
    conn.commit()

    trend = snapshots.metric_trends_for_evidence(conn, [7], now=latest)[7]

    assert trend["attempt_count"] == 85
    assert trend["sample_count"] == 85
    assert trend["history_capped"] is True
    assert trend["views_delta_24h"] == 24
    assert trend["views_delta_7d"] is None
    assert trend["delta_7d_status"] == "insufficient_history"


def test_weekly_cold_baseline_cannot_masquerade_as_24h_delta() -> None:
    conn = _conn()
    latest = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for run_id, fetched_at, views in (
        ("weekly-baseline", latest - timedelta(days=7), 100),
        ("weekly-latest", latest, 800),
    ):
        snapshots.append_snapshot(
            conn,
            evidence_id=7,
            provider="fixture",
            fetched_at=fetched_at.isoformat(),
            source_observed_at=fetched_at.isoformat(),
            views=views,
            status="success",
            run_id=run_id,
        )
    conn.commit()

    trend = snapshots.metric_trends_for_evidence(conn, [7], now=latest)[7]

    assert trend["views_delta_24h"] is None
    assert trend["delta_24h_status"] == "insufficient_history"
    assert trend["views_delta_7d"] == 700
    assert trend["delta_7d_status"] == "ready"
    assert trend["tracking_status"] == "tracked"


def test_baseline_older_than_window_tolerance_is_not_labeled_fixed_window() -> None:
    conn = _conn()
    latest = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    for run_id, fetched_at, views in (
        ("too-old", latest - timedelta(days=11), 100),
        ("latest", latest, 900),
    ):
        snapshots.append_snapshot(
            conn,
            evidence_id=7,
            provider="fixture",
            fetched_at=fetched_at.isoformat(),
            source_observed_at=fetched_at.isoformat(),
            views=views,
            status="success",
            run_id=run_id,
        )
    conn.commit()

    trend = snapshots.metric_trends_for_evidence(conn, [7], now=latest)[7]

    assert trend["views_delta_24h"] is None
    assert trend["views_delta_7d"] is None
    assert trend["delta_24h_status"] == "insufficient_history"
    assert trend["delta_7d_status"] == "insufficient_history"
    assert trend["tracking_status"] == "insufficient_history"


def test_batch_trends_return_unavailable_when_snapshot_table_is_missing() -> None:
    conn = sqlite3.connect(":memory:")
    result = snapshots.metric_trends_for_evidence(conn, [1, 2])
    assert result[1]["tracking_status"] == "unavailable"
    assert result[2]["freshness"] == "unavailable"
    assert result[1]["views_delta_24h"] is None


def test_batch_trends_mark_last_success_older_than_24h_stale() -> None:
    conn = _conn()
    snapshots.append_snapshot(
        conn,
        evidence_id=7,
        provider="fixture",
        fetched_at="2026-08-19T10:00:00Z",
        source_observed_at="2026-08-19T10:00:00Z",
        views=100,
        status="success",
        run_id="stale-success",
    )
    conn.commit()
    trend = snapshots.metric_trends_for_evidence(
        conn,
        [7],
        now=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
    )[7]
    assert trend["freshness"] == "stale"
    assert trend["tracking_status"] == "stale"
    assert trend["last_success"]["views"] == 100


def test_legacy_current_truth_is_never_a_trend_sample_or_baseline() -> None:
    conn = _conn()
    snapshots.append_snapshot(
        conn,
        evidence_id=7,
        provider="fixture",
        fetched_at="2026-08-20T11:00:00Z",
        source_observed_at="2026-08-20T11:00:00Z",
        views=100,
        status="success",
        run_id="real-baseline",
    )
    snapshots.append_snapshot(
        conn,
        evidence_id=7,
        provider="legacy",
        fetched_at="2026-08-20T12:00:00Z",
        views=190,
        status="legacy_current_only",
        capture_key="legacy-current",
        quality_flags=("legacy_current_only", "not_historical"),
    )
    snapshots.append_snapshot(
        conn,
        evidence_id=7,
        provider="fixture",
        fetched_at="2026-08-21T12:00:00Z",
        source_observed_at="2026-08-21T12:00:00Z",
        views=200,
        status="success",
        run_id="real-latest",
    )
    snapshots.append_snapshot(
        conn,
        evidence_id=7,
        provider="legacy",
        fetched_at="2026-08-21T12:30:00Z",
        views=250,
        status="legacy_current_only",
        capture_key="legacy-newest-current",
        quality_flags=("legacy_current_only", "not_historical"),
    )
    conn.commit()

    trend = snapshots.metric_trends_for_evidence(
        conn,
        [7],
        now=datetime(2026, 8, 21, 13, 0, tzinfo=timezone.utc),
    )[7]

    assert trend["last_success"]["status"] == "legacy_current_only"
    assert trend["last_success"]["views"] == 250
    assert trend["last_success"]["fetched_at"] is None
    assert trend["sample_count"] == 2
    assert trend["views_delta_24h"] == 100
    assert trend["delta_24h_status"] == "ready"
    assert trend["freshness"] == "unavailable"
    assert trend["tracking_status"] == "insufficient_history"


def test_migration_backfill_includes_legacy_null_evidence_type() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "283_vkpi_content_metric_snapshots.sql").read_text()
    assert "COALESCE(e.evidence_type, 'video') = 'video'" in migration
    assert "'legacy_current_only'" in migration
    assert '"not_historical"' in migration

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            evidence_type TEXT,
            metrics_source TEXT,
            scrape_source TEXT,
            metrics_scraped_at TEXT,
            scraped_at TEXT,
            updated_at TEXT,
            created_at TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER
        );
        INSERT INTO vkpi_kol_video_evidence VALUES
          (1, NULL, 'legacy', '', NULL, NULL, '2026-08-01', '2026-07-01', 10, NULL, NULL, NULL),
          (2, 'image', 'legacy', '', NULL, NULL, '2026-08-01', '2026-07-01', 20, NULL, NULL, NULL);
        """
    )
    sqlite_migration = (
        migration.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        .replace("BIGINT", "INTEGER")
        .replace("TIMESTAMPTZ", "TEXT")
        .replace("e.id::text", "CAST(e.id AS TEXT)")
        .replace("NOW()", "CURRENT_TIMESTAMP")
    )
    conn.executescript(sqlite_migration)
    rows = conn.execute(
        """
        SELECT evidence_id, status, provider, source_observed_at, fetched_at, quality_flags
        FROM vkpi_content_metric_snapshots
        ORDER BY evidence_id
        """
    ).fetchall()
    assert rows == [
        (
            1,
            "legacy_current_only",
            "legacy_current_columns",
            None,
            "1970-01-01T00:00:00+00:00",
            '["legacy_current_only","not_historical","provenance_legacy_current_columns","source_observed_at_unknown"]',
        )
    ]
    assert "e.updated_at" not in migration


def test_snapshot_migration_down_matches_runner_owned_registration_contract() -> None:
    from app.db import connection as db_connection

    migrations = Path(__file__).resolve().parents[1] / "migrations"
    forward_name = "283_vkpi_content_metric_snapshots.sql"
    down_name = "283_vkpi_content_metric_snapshots_down.sql"
    down = (migrations / down_name).read_text(encoding="utf-8")

    # Forward discovery excludes down scripts; _run_postgres_migrations records
    # the forward filename only after executing it.  A manual down migration
    # must therefore delete that exact durable marker, matching older downs.
    assert forward_name in db_connection._discover_postgres_migrations()
    assert down_name not in db_connection._discover_postgres_migrations()
    assert "DROP TABLE IF EXISTS vkpi_content_metric_snapshots" in down
    assert "DELETE FROM schema_migrations" in down
    assert f"WHERE version_key = '{forward_name}'" in down
