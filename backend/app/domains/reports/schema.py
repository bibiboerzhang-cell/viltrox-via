"""SQLite guard for V-KPI reports/exports tables (matches schema.py pattern)."""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def _ensure_sqlite_column(conn: object, table: str, column: str, declaration: str) -> None:
    """Add columns to pre-existing local/test tables created before this schema guard.

    PostgreSQL receives these columns through migration 256.  SQLite's
    ``CREATE TABLE IF NOT EXISTS`` does not evolve an existing table, so the
    local runtime needs the same additive compatibility step.
    """

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {
        str(row["name"] if hasattr(row, "keys") and "name" in row.keys() else row[1])
        for row in rows
    }
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def ensure_vkpi_reports_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_report_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_uid TEXT NOT NULL UNIQUE,
            report_type TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'all',
            scope_id INTEGER,
            metric_run_id INTEGER,
            triggered_by_staff_id INTEGER,
            triggered_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            summary_text TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            truth_invalidated_at TEXT,
            truth_invalidation_reason TEXT NOT NULL DEFAULT '',
            truth_invalidation_migration INTEGER,
            truth_restorable INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_type_time
            ON vkpi_report_runs(report_type, triggered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_period
            ON vkpi_report_runs(period_start, period_end);
        CREATE INDEX IF NOT EXISTS idx_vkpi_report_runs_status
            ON vkpi_report_runs(status, triggered_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_report_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_run_id INTEGER NOT NULL,
            file_format TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL DEFAULT 0,
            download_url TEXT DEFAULT '',
            download_expires_at TEXT,
            sha256_hex TEXT DEFAULT '',
            download_count INTEGER NOT NULL DEFAULT 0,
            last_downloaded_at TEXT,
            last_downloaded_by_staff_id INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_report_files_run
            ON vkpi_report_files(report_run_id, file_format);

        CREATE TABLE IF NOT EXISTS vkpi_export_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            export_uid TEXT NOT NULL UNIQUE,
            requested_by_staff_id INTEGER,
            export_type TEXT NOT NULL,
            file_format TEXT NOT NULL,
            filters_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued',
            file_path TEXT DEFAULT '',
            download_url TEXT DEFAULT '',
            download_expires_at TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT DEFAULT '',
            triggered_at TEXT NOT NULL,
            completed_at TEXT,
            expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_export_jobs_staff
            ON vkpi_export_jobs(requested_by_staff_id, triggered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_export_jobs_status
            ON vkpi_export_jobs(status, triggered_at DESC);
        """
    )
    for column, declaration in (
        ("truth_invalidated_at", "TEXT"),
        ("truth_invalidation_reason", "TEXT NOT NULL DEFAULT ''"),
        ("truth_invalidation_migration", "INTEGER"),
        ("truth_restorable", "INTEGER NOT NULL DEFAULT 1"),
    ):
        _ensure_sqlite_column(conn, "vkpi_report_runs", column, declaration)
    conn.commit()
    _SCHEMA_READY = True
