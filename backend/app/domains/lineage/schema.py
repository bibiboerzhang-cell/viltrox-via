"""Local SQLite guard for the V-KPI metric lineage tables.

Postgres uses migrations/024_vkpi_metric_lineage.sql. Local demo runs can start
from an older SQLite database, so lineage endpoints call this guard before
reading or writing the lineage tables. Mirrors the pattern in schema.py.
"""
from __future__ import annotations

from app.db.connection import get_conn, is_postgres_runtime

_SCHEMA_READY = False


def ensure_vkpi_lineage_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_metric_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_uid TEXT NOT NULL UNIQUE,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'all',
            scope_id INTEGER,
            trigger_source TEXT NOT NULL DEFAULT 'dashboard',
            generated_by_staff_id INTEGER,
            generated_at TEXT NOT NULL,
            definition_version TEXT NOT NULL DEFAULT 'v1',
            status TEXT NOT NULL DEFAULT 'ready',
            error_message TEXT DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_period
            ON vkpi_metric_runs(period_start, period_end);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_scope
            ON vkpi_metric_runs(scope_type, scope_id, generated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_runs_trigger
            ON vkpi_metric_runs(trigger_source, generated_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_metric_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            metric_key TEXT NOT NULL,
            value_numeric REAL,
            value_text TEXT DEFAULT '',
            currency TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            calculation_json TEXT NOT NULL DEFAULT '{}',
            source_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_values_run
            ON vkpi_metric_values(run_id, metric_key);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_values_metric_time
            ON vkpi_metric_values(metric_key, created_at DESC);

        CREATE TABLE IF NOT EXISTS vkpi_metric_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_value_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            contribution_amount REAL DEFAULT 0,
            contribution_percent REAL DEFAULT 0,
            evidence_type TEXT NOT NULL DEFAULT '',
            evidence_ref TEXT NOT NULL DEFAULT '',
            project_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            occurred_at TEXT,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_value
            ON vkpi_metric_sources(metric_value_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_business
            ON vkpi_metric_sources(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_project
            ON vkpi_metric_sources(project_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_kol
            ON vkpi_metric_sources(kol_id);
        CREATE INDEX IF NOT EXISTS idx_vkpi_metric_sources_staff
            ON vkpi_metric_sources(staff_id);
        """
    )
    conn.commit()
    _SCHEMA_READY = True
