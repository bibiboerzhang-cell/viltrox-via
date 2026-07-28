"""Persistence-only helpers for the V-KPI provider budget guard."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


_USD_QUANTUM = Decimal("0.000001")
_MICRO_USD_PER_USD = Decimal("1000000")


def clean_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def cost_decimal(value: Any) -> Decimal:
    """Return one non-negative database-representable micro-USD amount."""

    try:
        parsed = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("cost_usd_invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("cost_usd_invalid")
    try:
        return parsed.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("cost_usd_invalid") from exc


def micro_usd(value: Decimal) -> int:
    return int((value * _MICRO_USD_PER_USD).to_integral_exact())


def money_db_param(value: Decimal, *, postgres: bool) -> Decimal | str:
    """psycopg accepts Decimal; sqlite3 requires a text representation."""

    return value if postgres else format(value, "f")


def ensure_sqlite_budget_schema(conn: Any) -> None:
    """Install the SQLite-only ledger and budget-cap compatibility schema."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_ai_cost_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cron_task TEXT,
          ai_provider TEXT,
          model_name TEXT,
          cost_usd REAL,
          tokens_in INTEGER,
          tokens_out INTEGER,
          kol_pool_id INTEGER,
          staff_id INTEGER,
          task_item_id INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_time
          ON vkpi_ai_cost_ledger (occurred_at);

        CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_cron_time
          ON vkpi_ai_cost_ledger (cron_task, occurred_at);

        CREATE TABLE IF NOT EXISTS vkpi_provider_budget_caps (
          scope TEXT PRIMARY KEY,
          cap_usd REAL,
          current_spend REAL DEFAULT 0,
          warning_at REAL DEFAULT 0.80,
          hard_stop_at REAL DEFAULT 1.00,
          reset_at TEXT,
          fallback_action TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        INSERT OR IGNORE INTO vkpi_provider_budget_caps
            (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
        VALUES
            ('single_call', 0.50, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0', '{"seeded_by":"budget_guard_sqlite","tier":"hard_stop"}'),
            ('cron:p4_evidence_summary', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"P4"}'),
            ('cron:p4_gemini_single_kol', 3.00, 0, 0.80, 1.00, NULL, 'fallback_to_preflight_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"P4","provider":"gemini"}'),
            ('cron:market_provider_smoke', 1.00, 0, 0.80, 1.00, NULL, 'fallback_to_preflight_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"market_intelligence","provider":"llm"}');
        """
    )
    conn.commit()
