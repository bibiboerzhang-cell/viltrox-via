from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from app.domains.costs import budget_guard


ROOT = Path(__file__).resolve().parents[1]


def test_budget_schema_wrapper_preserves_budget_guard_monkeypatch_points(
    monkeypatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: False)
        monkeypatch.setattr(budget_guard, "get_conn", lambda: conn)

        budget_guard.ensure_budget_schema()

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        scopes = {
            row[0]
            for row in conn.execute(
                "SELECT scope FROM vkpi_provider_budget_caps"
            ).fetchall()
        }
        assert {
            "vkpi_ai_cost_ledger",
            "vkpi_provider_budget_caps",
        } <= tables
        assert {
            "single_call",
            "cron:p4_evidence_summary",
            "cron:p4_gemini_single_kol",
            "cron:market_provider_smoke",
        } <= scopes
    finally:
        conn.close()


def test_postgres_schema_wrapper_still_returns_before_get_conn(monkeypatch) -> None:
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        budget_guard,
        "get_conn",
        lambda: (_ for _ in ()).throw(
            AssertionError("Postgres schema wrapper must not open a connection")
        ),
    )

    budget_guard.ensure_budget_schema()


def test_fixed_precision_private_helpers_remain_available_on_budget_guard(
    monkeypatch,
) -> None:
    amount = budget_guard._cost_decimal("0.000033")
    assert amount == Decimal("0.000033")
    assert budget_guard._micro_usd(amount) == 33

    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: True)
    assert budget_guard._money_db_param(amount) is amount
    monkeypatch.setattr(budget_guard, "is_postgres_runtime", lambda: False)
    assert budget_guard._money_db_param(amount) == "0.000033"

    assert budget_guard._clean_row({"cost_usd": amount}) == {
        "cost_usd": 0.000033
    }
    monkeypatch.setattr(budget_guard, "_clean_value", lambda _value: "patched")
    assert budget_guard._clean_row({"cost_usd": amount}) == {
        "cost_usd": "patched"
    }


def test_budget_guard_split_files_remain_below_release_line_guard() -> None:
    paths = (
        ROOT / "backend/app/domains/costs/budget_guard.py",
        ROOT / "backend/app/domains/costs/budget_guard_persistence.py",
    )

    for path in paths:
        assert len(path.read_text(encoding="utf-8").splitlines()) < 1000, path
