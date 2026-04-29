"""
db/migrations_v5.py — V5 admin schema migrator

Call apply_v5_migrations() from db/migrations.py::init_db() after existing
init steps. Idempotent — safe to call on every boot.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)

SQL_CANDIDATES = (
    Path(__file__).resolve().parent / "sql" / "001_v5_admin_schema.sql",
    Path(__file__).resolve().parent.parent / "sql" / "001_v5_admin_schema.sql",
)


def apply_v5_migrations() -> None:
    """Apply v5 schema (batches 2-5) — idempotent."""
    sql_path = next((path for path in SQL_CANDIDATES if path.exists()), None)
    if not sql_path:
        logger.error("v5 migration sql not found in any known location: %s", SQL_CANDIDATES)
        return

    conn = get_conn()
    c = conn.cursor()

    # 1. Run the .sql file in compatibility mode.
    # Existing Viltrox 2.0 DBs already have older tables such as trust_events,
    # via_personas, via_policy_versions, genre_benchmarks, market_observations.
    # The bundled v5 SQL defines newer variants of those tables, so a raw
    # executescript() would abort when an index/seed references columns that do
    # not exist on the legacy table. We therefore execute statement-by-statement
    # and skip the incompatible ones while still creating all truly missing
    # tables (orders, payouts, integrations, staff, etc.).
    sql = sql_path.read_text()
    compatibility = _compatibility_flags(c)
    applied = 0
    skipped = 0
    for statement in _split_sql_statements(sql):
        if _should_skip_statement(statement, compatibility):
            skipped += 1
            continue
        try:
            c.execute(statement)
            applied += 1
        except Exception as e:
            skipped += 1
            logger.warning("v5 statement skipped: %s :: %s", e, statement[:120].replace("\n", " "))
    conn.commit()
    logger.info("v5 schema compatibility pass complete", extra={"applied": applied, "skipped": skipped})

    _ensure_postgres_v5_tables(c)

    # 2. ALTER users — SQLite doesn't support IF NOT EXISTS on ADD COLUMN,
    #    so we detect + add one at a time.
    _ensure_user_column(c, "trust_score",     "REAL DEFAULT 30.0")
    _ensure_user_column(c, "trust_status",    "TEXT DEFAULT 'normal'")
    _ensure_user_column(c, "violation_count", "INTEGER DEFAULT 0")
    _ensure_user_column(c, "blocked_at",      "TEXT")
    _ensure_user_column(c, "blocked_reason",  "TEXT")
    _ensure_user_column(c, "flagged_at",      "TEXT")
    _ensure_user_column(c, "flagged_reason",  "TEXT")
    _ensure_table_column(c, "platform_ingest_events", "ingested_into_orders_at", "TEXT")
    conn.commit()


def _ensure_postgres_v5_tables(cursor) -> None:
    """Create the v5 admin commerce tables when running on Postgres.

    The bundled v5 SQL is intentionally SQLite-first. In Postgres mode the
    compatibility runner skips SQLite-only statements such as AUTOINCREMENT,
    which used to leave admin commerce endpoints without their backing tables.
    """
    if not is_postgres_runtime():
        return

    statements = [
        """
        CREATE TABLE IF NOT EXISTS orders (
            id BIGSERIAL PRIMARY KEY,
            external_order_id TEXT UNIQUE NOT NULL,
            source_platform TEXT NOT NULL,
            customer_email TEXT,
            customer_country TEXT,
            subtotal_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            items_json TEXT,
            attribution_source TEXT,
            attribution_type TEXT,
            attribution_user_id BIGINT,
            utm_source TEXT,
            utm_medium TEXT,
            utm_campaign TEXT,
            commission_rate_bps INTEGER DEFAULT 0,
            commission_cents INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'paid',
            placed_at TEXT NOT NULL,
            webhook_event_ids_json TEXT,
            raw_payload TEXT,
            flagged_reason TEXT,
            flagged_by BIGINT,
            flagged_at TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_orders_attribution ON orders(attribution_source, placed_at)",
        "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, placed_at)",
        "CREATE INDEX IF NOT EXISTS idx_orders_utm ON orders(utm_source, utm_medium)",
        "CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(attribution_user_id, placed_at)",
        """
        CREATE TABLE IF NOT EXISTS payout_cycles (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'upcoming',
            process_date TEXT NOT NULL,
            processed_at TEXT,
            processed_by BIGINT,
            total_approved_cents INTEGER DEFAULT 0,
            total_paid_cents INTEGER DEFAULT 0,
            creator_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS payouts (
            id BIGSERIAL PRIMARY KEY,
            cycle_id TEXT NOT NULL REFERENCES payout_cycles(id),
            user_id BIGINT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            order_ids_json TEXT,
            gmv_cents INTEGER DEFAULT 0,
            order_count INTEGER DEFAULT 0,
            method TEXT,
            method_details TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            hold_reason TEXT,
            approved_at TEXT,
            approved_by BIGINT,
            paid_at TEXT,
            paid_tx_id TEXT,
            failed_at TEXT,
            failed_reason TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_payouts_cycle ON payouts(cycle_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_payouts_user ON payouts(user_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS attribution_clicks (
            id BIGSERIAL PRIMARY KEY,
            ref_code TEXT NOT NULL,
            ref_type TEXT,
            utm_source TEXT,
            utm_medium TEXT,
            utm_campaign TEXT,
            session_id TEXT,
            user_agent TEXT,
            ip_hash TEXT,
            country TEXT,
            landing_path TEXT,
            converted_to_order_id BIGINT,
            clicked_at TEXT NOT NULL DEFAULT (NOW()::TEXT)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_clicks_ref ON attribution_clicks(ref_code, clicked_at)",
        "CREATE INDEX IF NOT EXISTS idx_clicks_sess ON attribution_clicks(session_id)",
        """
        CREATE TABLE IF NOT EXISTS payout_disputes (
            id BIGSERIAL PRIMARY KEY,
            payout_id BIGINT,
            user_id BIGINT,
            reason TEXT NOT NULL,
            evidence_json TEXT,
            status TEXT DEFAULT 'open',
            resolved_by BIGINT,
            resolved_at TEXT,
            resolution_note TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
    ]
    applied = 0
    for statement in statements:
        cursor.execute(statement)
        applied += 1
    logger.info("v5 postgres admin tables ready", extra={"applied": applied})


def _ensure_user_column(cursor, col_name: str, col_def: str) -> None:
    """Add column to users table if not present."""
    cursor.execute("PRAGMA table_info(users)")
    existing = {row[1] for row in cursor.fetchall()}
    if col_name in existing:
        return
    sql = f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"
    try:
        cursor.execute(sql)
        logger.info("v5: added users.%s", col_name)
    except Exception as e:
        logger.warning("v5: could not add users.%s: %s", col_name, e)


def _ensure_table_column(cursor, table_name: str, col_name: str, col_def: str) -> None:
    """Add a missing column to a table if it exists."""
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing = {row[1] for row in cursor.fetchall()}
    if col_name in existing:
        return
    sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}"
    try:
        cursor.execute(sql)
        logger.info("v5: added %s.%s", table_name, col_name)
    except Exception as e:
        logger.warning("v5: could not add %s.%s: %s", table_name, col_name, e)


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buffer: list[str] = []
    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(raw_line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip().rstrip(";").strip()
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


def _table_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cursor.fetchall()}


def _compatibility_flags(cursor) -> dict[str, bool]:
    trust_cols = _table_columns(cursor, "trust_events")
    benchmark_cols = _table_columns(cursor, "genre_benchmarks")
    return {
        "legacy_trust_events": "event_type" in trust_cols and "occurred_at" not in trust_cols,
        "legacy_genre_benchmarks": bool(benchmark_cols) and "avg_score_target" not in benchmark_cols,
    }


def _should_skip_statement(statement: str, flags: dict[str, bool]) -> bool:
    normalized = " ".join(statement.lower().split())
    if flags.get("legacy_trust_events") and (
        "create index if not exists idx_trust_events_user on trust_events(user_id, occurred_at desc)" in normalized
        or "create index if not exists idx_trust_events_kind on trust_events(event_kind, occurred_at desc)" in normalized
    ):
        return True
    if flags.get("legacy_genre_benchmarks") and (
        "insert or ignore into genre_benchmarks (genre, avg_score_target, pass_rate_target, characteristics_json, sample_count)" in normalized
    ):
        return True
    return False
