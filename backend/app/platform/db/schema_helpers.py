"""Portable helpers shared by local SQLite schema guards."""
from __future__ import annotations

from app.db.connection import is_postgres_runtime


def ensure_sqlite_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Add missing columns while retaining the repository's SQL portability."""
    if is_postgres_runtime():
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        ).fetchall()
        existing = {str(row["column_name"]) for row in rows}
    else:
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
