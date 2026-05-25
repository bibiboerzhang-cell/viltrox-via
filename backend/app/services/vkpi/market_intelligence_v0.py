"""DB-bound facade for P5.69 Market intelligence v0."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.domains.market.intelligence_v0 import (
    COMPETITOR_SIGNAL_TABLE,
    MARKET_STORAGE_TABLES,
    build_market_intelligence_report,
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


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _competitor_rows(limit: int, *, run_id: int | None = None) -> list[dict[str, Any]]:
    if not _table_exists(COMPETITOR_SIGNAL_TABLE):
        return []
    safe_limit = max(1, min(500, int(limit or 100)))
    where = ""
    params: list[Any] = []
    if run_id is not None:
        where = "WHERE run_id=?"
        params.append(int(run_id))
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_competitor_signals
        {where}
        ORDER BY
          CASE severity
            WHEN 'critical' THEN 0
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            ELSE 3
          END,
          score DESC,
          created_at DESC,
          id DESC
        LIMIT ?
        """,
        (*params, safe_limit),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        data["product_hints"] = _loads(data.get("product_hints_json"), [])
        data["evidence"] = _loads(data.get("evidence_json"), {})
        items.append(data)
    return items


def _market_tables() -> dict[str, dict[str, Any]]:
    table_names = (*MARKET_STORAGE_TABLES, COMPETITOR_SIGNAL_TABLE)
    return {name: {"exists": _table_exists(name), "rows": _count(name)} for name in table_names}


def build_market_intelligence_v0(*, limit: int = 120, run_id: int | None = None) -> dict[str, Any]:
    rows = _competitor_rows(limit, run_id=run_id)
    return build_market_intelligence_report(rows, tables=_market_tables(), run_id=run_id)
