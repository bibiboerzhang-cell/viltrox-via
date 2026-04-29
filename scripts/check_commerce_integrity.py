#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_env import apply_runtime_env  # noqa: E402
from stdout_utils import out_json  # noqa: E402


COMMERCE_PRINT_TARGETS = [
    "backend/app/services/commerce",
    "backend/app/api/routers",
    "tests/test_commerce_order_flow.py",
    "tests/test_creator_business_flow.py",
]

SHOPIFY_GAP_SQL = """
SELECT
  to_char(date_trunc('hour', e.occurred_at), 'YYYY-MM-DD HH24:00:00') AS hour,
  COUNT(*) FILTER (WHERE e.event_type = 'orders_paid') AS events,
  COUNT(DISTINCT o.id) AS orders_created,
  COUNT(*) FILTER (WHERE e.event_type = 'orders_paid') - COUNT(DISTINCT o.id) AS gap
FROM platform_ingest_events e
LEFT JOIN orders o
  ON o.external_order_id = e.external_id
  OR o.external_order_id = CONCAT('#', e.external_id)
WHERE e.source_platform = 'shopify'
  AND e.occurred_at > NOW() - INTERVAL '7 days'
GROUP BY 1
ORDER BY 1 DESC
"""


def _run_rg_no_prints() -> list[str]:
    command = ["rg", "-n", r"print\("]
    command.extend(COMMERCE_PRINT_TARGETS)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "rg print scan failed")
    return lines


def _rows_to_dicts(rows: list[tuple], columns: list[str]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for row in rows:
        payload.append({column: row[index] for index, column in enumerate(columns)})
    return payload


def main() -> int:
    apply_runtime_env()
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is not configured")

    print_hits = _run_rg_no_prints()
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SHOPIFY_GAP_SQL)
            gap_rows = cur.fetchall()
            cur.execute(
                """
                SELECT COUNT(*)
                FROM orders
                WHERE status IN ('paid', 'fulfilled')
                  AND COALESCE(subtotal_cents, 0) = 0
                """
            )
            zero_amount_count = int(cur.fetchone()[0] or 0)

    gap_payload = _rows_to_dicts(gap_rows, ["hour", "events", "orders_created", "gap"])
    summary = {
        "commerce_print_hits": print_hits,
        "commerce_print_ok": not print_hits,
        "shopify_hourly_gap_rows": gap_payload,
        "shopify_hourly_gap_ok": all(int(row["gap"] or 0) == 0 for row in gap_payload),
        "paid_order_zero_amount_count": zero_amount_count,
        "paid_order_zero_amount_ok": zero_amount_count == 0,
    }
    out_json(summary, ensure_ascii=False, indent=2)
    return 0 if summary["commerce_print_ok"] and summary["shopify_hourly_gap_ok"] and summary["paid_order_zero_amount_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
