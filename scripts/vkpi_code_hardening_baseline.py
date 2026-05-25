#!/usr/bin/env python3
"""Read-only V-KPI hardening baseline snapshot."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("VKPI_LLM_GATEWAY_FORCE_OFFLINE", "1")


TABLES = (
    "vkpi_memory_entities",
    "vkpi_memory_facts",
    "vkpi_memory_links",
    "vkpi_kol_recommendation_runs",
    "vkpi_kol_recommendations",
    "vkpi_recommendation_feedback",
    "vkpi_recommendation_outcomes",
    "vkpi_alerts",
    "vkpi_ai_cost_ledger",
)


def _count_table(conn: Any, table_name: str) -> dict[str, Any]:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
        return {"table": table_name, "count": int(row["n"] if row else 0), "ok": True}
    except Exception as exc:
        return {"table": table_name, "ok": False, "error": str(exc)[:240]}


def main() -> None:
    from app.db.connection import close_db_runtime, get_conn, get_db_actor_stats, probe_postgres_connectivity
    from app.domains import memory

    conn = get_conn()
    payload = {
        "db_connectivity": probe_postgres_connectivity(),
        "db_actor_stats": get_db_actor_stats(),
        "memory_readiness": memory.readiness(),
        "table_counts": [_count_table(conn, table_name) for table_name in TABLES],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
