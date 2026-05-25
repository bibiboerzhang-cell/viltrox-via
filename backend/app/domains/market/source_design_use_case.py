"""DB-bound facade for P5.66 market signal source design."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.market.source_design import (
    CANONICAL_CONTRACT,
    SOURCE_REGISTRY,
    TABLE_NAMES,
    build_market_source_design_report_from_tables,
    source_readiness,
)


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _count(table_name: str) -> int:
    if not _table_exists(table_name):
        return 0
    try:
        row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def _source_readiness(source: dict[str, Any]) -> dict[str, Any]:
    return source_readiness(source)


def _source_tables() -> dict[str, dict[str, Any]]:
    return {table_name: {"exists": _table_exists(table_name), "rows": _count(table_name)} for table_name in TABLE_NAMES}


def build_market_source_design_report() -> dict[str, Any]:
    return build_market_source_design_report_from_tables(_source_tables())


__all__ = [
    "CANONICAL_CONTRACT",
    "SOURCE_REGISTRY",
    "TABLE_NAMES",
    "_source_readiness",
    "build_market_source_design_report",
    "source_readiness",
]
