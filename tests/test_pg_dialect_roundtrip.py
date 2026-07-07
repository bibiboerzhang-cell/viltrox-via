"""Real-Postgres roundtrip tests for the sqlite->postgres compat layer.

These exercise behaviour that the sqlite-backed unit tests can never reach:
the placeholder/dialect rewriter, ``FOR UPDATE SKIP LOCKED`` row claiming, and
JSONB / INTERVAL translation — all against a live Postgres. Every test is
marked ``pg`` and is skipped (not failed) when no database is reachable.
"""
from __future__ import annotations

import secrets
from typing import Any

import pytest

pytestmark = pytest.mark.pg


# ---------------------------------------------------------------------------
# 1. Connection translation layer: ? placeholders, JSONB ->>, ON CONFLICT,
#    RETURNING — driven through the production PostgresCompatConnection.
# ---------------------------------------------------------------------------
def test_translation_layer_roundtrip(pg_compat: Any) -> None:
    cur = pg_compat.cursor()
    cur.execute(
        """
        CREATE TEMP TABLE _rt_kol (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}'
        ) ON COMMIT DROP
        """
    )

    # ? placeholder + RETURNING roundtrip.
    cur.execute(
        "INSERT INTO _rt_kol (id, handle, meta_json) VALUES (?, ?, ?) RETURNING id",
        (1, "alpha", '{"tier": "gold", "score": 42}'),
    )
    assert cur.fetchone()["id"] == 1

    # JSONB ->> extraction with a ? param, casting the stored text column.
    cur.execute(
        "SELECT (CAST(meta_json AS JSONB)) ->> ? AS tier FROM _rt_kol WHERE id = ?",
        ("tier", 1),
    )
    assert cur.fetchone()["tier"] == "gold"

    # INSERT OR IGNORE -> ON CONFLICT DO NOTHING translation: duplicate key is a no-op.
    cur.execute(
        "INSERT OR IGNORE INTO _rt_kol (id, handle, meta_json) VALUES (?, ?, ?)",
        (1, "dup", "{}"),
    )
    cur.execute("SELECT COUNT(*) AS n FROM _rt_kol")
    assert cur.fetchone()["n"] == 1

    # Explicit ON CONFLICT DO UPDATE ... RETURNING roundtrip.
    cur.execute(
        """
        INSERT INTO _rt_kol (id, handle, meta_json) VALUES (?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET handle = EXCLUDED.handle
        RETURNING handle
        """,
        (1, "alpha_v2", "{}"),
    )
    assert cur.fetchone()["handle"] == "alpha_v2"


# ---------------------------------------------------------------------------
# 2. FOR UPDATE SKIP LOCKED: a second connection must skip a row the first
#    connection already holds — the core of the job-claim fairness path.
# ---------------------------------------------------------------------------
def test_for_update_skip_locked(pg_dsn: str) -> None:
    import psycopg

    table = "_pgtest_skiplock_" + secrets.token_hex(6)
    setup = psycopg.connect(pg_dsn, autocommit=True)
    try:
        with setup.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, claimed INTEGER NOT NULL DEFAULT 0)"
            )
            cur.execute(f"INSERT INTO {table} (id) VALUES (1)")

        first = psycopg.connect(pg_dsn, connect_timeout=5)
        second = psycopg.connect(pg_dsn, connect_timeout=5)
        first.autocommit = False
        second.autocommit = False
        try:
            claim_sql = f"SELECT id FROM {table} WHERE claimed = 0 ORDER BY id FOR UPDATE SKIP LOCKED"

            first_cur = first.cursor()
            first_cur.execute(claim_sql)
            first_row = first_cur.fetchone()

            second_cur = second.cursor()
            second_cur.execute(claim_sql)
            second_row = second_cur.fetchone()

            assert first_row == (1,)
            # Second connection skips the row locked by the first (no waiting, no dupe claim).
            assert second_row is None
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()
    finally:
        with setup.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        setup.close()


# ---------------------------------------------------------------------------
# 3. JSONB / INTERVAL dialect translation roundtrip through the compat layer.
# ---------------------------------------------------------------------------
def test_jsonb_and_interval_dialect(pg_compat: Any) -> None:
    cur = pg_compat.cursor()
    cur.execute(
        "CREATE TEMP TABLE _rt_doc (id INTEGER PRIMARY KEY, doc JSONB NOT NULL) ON COMMIT DROP"
    )
    cur.execute(
        "INSERT INTO _rt_doc (id, doc) VALUES (?, CAST(? AS JSONB))",
        (1, '{"tier": "silver", "tags": ["a", "b"]}'),
    )

    # JSONB ->> text extraction and @> containment (containment returns bool,
    # which the compat layer normalizes to int 1/0).
    cur.execute(
        "SELECT doc ->> ? AS tier, (doc @> CAST(? AS JSONB)) AS has_tag FROM _rt_doc WHERE id = ?",
        ("tier", '{"tags": ["a"]}', 1),
    )
    row = cur.fetchone()
    assert row["tier"] == "silver"
    assert int(row["has_tag"]) == 1

    # datetime('now', ? || ' days') -> NOW() + interval. Result is normalized to
    # a sortable "%Y-%m-%dT%H:%M:%SZ" string, so +7 days sorts after +0 days.
    cur.execute(
        "SELECT datetime('now', ? || ' days') AS future, datetime('now', ? || ' days') AS present",
        ("7", "0"),
    )
    interval_row = cur.fetchone()
    assert isinstance(interval_row["future"], str)
    assert interval_row["future"] > interval_row["present"]

    # date('now', 'start of month') -> DATE_TRUNC('month', NOW())::date, the 1st.
    cur.execute("SELECT date('now', 'start of month') AS month_start")
    month_start = cur.fetchone()["month_start"]
    assert str(month_start).endswith("-01")
