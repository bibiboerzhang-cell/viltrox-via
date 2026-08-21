from __future__ import annotations

import sqlite3
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import refresh_evidence_metrics as refresh  # noqa: E402
from app.domains import content_metric_snapshots as snapshots  # noqa: E402


class _TupleCursor:
    """psycopg2-shaped tuple cursor backed by SQLite for transaction tests."""

    def __init__(self, connection: "_TupleConnection") -> None:
        self.connection = connection
        self.description: Any = None
        self.rowcount = -1
        self._rows: list[tuple[Any, ...]] = []
        self._index = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.connection.statements.append(sql)
        if "pg_advisory_xact_lock" in sql:
            self.description = (("pg_advisory_xact_lock", None, None, None, None, None, None),)
            self._rows = [(None,)]
            self.rowcount = 1
            return
        if "FROM information_schema.columns" in sql:
            columns = self.connection.raw.execute("PRAGMA table_info(vkpi_kol_video_evidence)").fetchall()
            self.description = (("column_name", None, None, None, None, None, None),)
            self._rows = [(row[1],) for row in columns]
            self.rowcount = len(self._rows)
            return
        translated = sql.replace("%s", "?")
        cursor = self.connection.raw.execute(translated, params)
        self.description = cursor.description
        self.rowcount = cursor.rowcount
        self._rows = [tuple(row) for row in cursor.fetchall()] if cursor.description else []

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class _TupleConnection:
    def __init__(self, raw: sqlite3.Connection) -> None:
        self.raw = raw
        self.statements: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _TupleCursor:
        return _TupleCursor(self)

    def commit(self) -> None:
        self.raw.commit()
        self.commits += 1

    def rollback(self) -> None:
        self.raw.rollback()
        self.rollbacks += 1


def test_commit_path_updates_only_observed_success_and_appends_all_attempts() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            metrics_scraped_at TEXT,
            metrics_source TEXT,
            updated_at TEXT
        );
        INSERT INTO vkpi_kol_video_evidence VALUES
          (1, 10, 2, 1, 0, NULL, NULL, 'old'),
          (2, 20, 3, 2, 1, NULL, NULL, 'old'),
          (3, 30, 4, 3, 2, NULL, NULL, 'old');
        """
    )
    snapshots.ensure_sqlite_schema(conn)

    result = refresh.maybe_update(
        conn,
        [
            {
                "id": 1,
                "status": "数字变化:view",
                "new_view_count": 15,
                "new_like_count": 2,
                "new_comment_count": None,
                "new_share_count": None,
                "provider_run_id": "run-success",
                "fetched_at": "2026-08-21T12:00:00+00:00",
            },
            {
                "id": 2,
                "status": "failed_no_return",
                "provider_run_id": "run-missing",
                "fetched_at": "2026-08-21T12:00:01+00:00",
            },
            {
                "id": 3,
                "status": "failed_all_metrics_missing",
                "new_view_count": None,
                "new_like_count": None,
                "new_comment_count": None,
                "new_share_count": None,
                "provider_run_id": "run-empty",
                "fetched_at": "2026-08-21T12:00:02+00:00",
            },
        ],
    )
    conn.commit()

    assert result == {"updated": 1, "snapshots_inserted": 3}
    latest = conn.execute(
        "SELECT id, view_count, like_count, comment_count, share_count FROM vkpi_kol_video_evidence ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in latest] == [
        (1, 15, 2, None, None),
        (2, 20, 3, 2, 1),
        (3, 30, 4, 3, 2),
    ]
    ledger = conn.execute(
        "SELECT evidence_id, status, error_code FROM vkpi_content_metric_snapshots ORDER BY evidence_id"
    ).fetchall()
    assert [tuple(row) for row in ledger] == [
        (1, "success", None),
        (2, "failed", "failed_no_return"),
        (3, "failed", "failed_all_metrics_missing"),
    ]


def test_commit_path_supports_native_psycopg2_tuple_cursor_shape() -> None:
    raw = sqlite3.connect(":memory:")
    raw.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            metrics_scraped_at TEXT,
            metrics_source TEXT,
            updated_at TEXT
        );
        INSERT INTO vkpi_kol_video_evidence VALUES
          (1, 10, 2, 1, 0, NULL, NULL, 'old'),
          (2, 20, 3, 2, 1, NULL, NULL, 'old');
        """
    )
    snapshots.ensure_sqlite_schema(raw)
    conn = _TupleConnection(raw)

    persisted = refresh.maybe_update(
        conn,
        [
            {
                "id": 1,
                "status": "数字变化:view",
                "new_view_count": 15,
                "new_like_count": 3,
                "new_comment_count": None,
                "new_share_count": None,
                "provider_run_id": "tuple-success",
                "fetched_at": "2026-08-21T12:00:00+00:00",
            },
            {
                "id": 2,
                "status": "failed_no_return",
                "provider_run_id": "tuple-failed",
                "fetched_at": "2026-08-21T12:00:01+00:00",
            },
        ],
    )
    conn.commit()

    assert persisted == {"updated": 1, "snapshots_inserted": 2}
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert raw.execute(
        "SELECT view_count, like_count, comment_count, share_count FROM vkpi_kol_video_evidence WHERE id=1"
    ).fetchone() == (15, 3, None, None)
    assert raw.execute(
        "SELECT status, error_code FROM vkpi_content_metric_snapshots WHERE evidence_id=2"
    ).fetchone() == ("failed", "failed_no_return")
    assert any("SAVEPOINT vkpi_content_metric_refresh" in sql for sql in conn.statements)
    assert any("pg_advisory_xact_lock" in sql for sql in conn.statements)
    assert any("%s" in sql for sql in conn.statements)


def test_main_commit_runs_with_native_tuple_connection(monkeypatch, tmp_path: Path) -> None:
    raw = sqlite3.connect(":memory:")
    raw.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            metrics_scraped_at TEXT,
            metrics_source TEXT,
            updated_at TEXT
        );
        INSERT INTO vkpi_kol_video_evidence VALUES
          (1, 10, 2, 1, 0, NULL, NULL, 'old');
        """
    )
    snapshots.ensure_sqlite_schema(raw)
    conn = _TupleConnection(raw)
    report_path = tmp_path / "report.md"
    report_path.write_text("fixture report", encoding="utf-8")
    emitted: list[str] = []

    monkeypatch.setattr(
        refresh,
        "parse_args",
        lambda: Namespace(commit=True, dry_run=False, platform="all", batch_size=50),
    )
    monkeypatch.setattr(refresh.psycopg2, "connect", lambda _url: conn)
    monkeypatch.setattr(refresh, "ApifyClient", lambda _token: object())
    monkeypatch.setattr(
        refresh,
        "load_rows",
        lambda _conn, _platform: [
            {
                "id": 1,
                "platform": "youtube",
                "content_url": "https://www.youtube.com/watch?v=abcdefghijk",
                "view_count": 10,
                "like_count": 2,
                "comment_count": 1,
            }
        ],
    )
    monkeypatch.setattr(refresh, "global_metrics", lambda _conn: (1, 10))
    monkeypatch.setattr(
        refresh,
        "call_actor",
        lambda _client, _platform, _urls: (
            [
                {
                    "key": "youtube:abcdefghijk",
                    "returned_url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "view_count": 15,
                    "like_count": 3,
                    "comment_count": 1,
                    "share_count": None,
                }
            ],
            {"run_id": "main-commit-run", "platform": "youtube"},
        ),
    )
    monkeypatch.setattr(
        refresh,
        "write_outputs",
        lambda *_args: (tmp_path / "metrics.csv", report_path),
    )
    monkeypatch.setattr(refresh, "out", lambda value: emitted.append(str(value)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://fixture/unused")

    refresh.main()

    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert raw.execute(
        "SELECT view_count, like_count FROM vkpi_kol_video_evidence WHERE id=1"
    ).fetchone() == (15, 3)
    assert raw.execute(
        "SELECT status, views FROM vkpi_content_metric_snapshots WHERE evidence_id=1"
    ).fetchone() == ("success", 15)
    assert "UPDATED_ROWS=1" in emitted
    assert "SNAPSHOTS_INSERTED=1" in emitted
