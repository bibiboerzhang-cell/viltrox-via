from __future__ import annotations

import json

import pytest

from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform import llm_budget_reservations as reservations
from app.platform.llm_budget_reservations import (
    LlmBudgetBlocked,
    mark_llm_provider_started,
    mark_llm_provider_unknown,
    reserve_llm_budget,
    settle_llm_reservation,
)
from app.platform.llm_gateway_ledger import record_call
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema


def _install_fixture() -> None:
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_llm_budget_reservations (
          reservation_key TEXT PRIMARY KEY,
          provider TEXT NOT NULL,
          model_name TEXT NOT NULL,
          purpose TEXT NOT NULL DEFAULT '',
          request_hash TEXT NOT NULL,
          provider_scope TEXT NOT NULL,
          cost_scope TEXT NOT NULL DEFAULT '',
          cumulative_scopes_json TEXT NOT NULL DEFAULT '[]',
          estimated_cost_usd REAL NOT NULL,
          actual_cost_usd REAL,
          state TEXT NOT NULL DEFAULT 'reserved',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          reserved_at TEXT,
          provider_started_at TEXT,
          settled_at TEXT,
          updated_at TEXT
        );
        DELETE FROM vkpi_llm_budget_reservations;
        """
    )
    conn.commit()
    budget_guard.update_budget(
        "monthly_total",
        {"cap_usd": 1.0, "current_spend": 0.0, "hard_stop_at": 1.0},
    )
    budget_guard.update_budget(
        "provider:claude",
        {"cap_usd": 1.0, "current_spend": 0.0, "hard_stop_at": 1.0},
    )
    budget_guard.update_budget(
        "single_call",
        {"cap_usd": 1.0, "current_spend": 0.0, "hard_stop_at": 1.0},
    )


def test_reservation_is_atomic_against_open_allowance_and_never_stores_prompt() -> None:
    _install_fixture()
    secret_prompt = "private-prompt-never-store"
    first = reserve_llm_budget(
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="unit",
        prompt=secret_prompt,
        estimated_cost_usd=0.6,
        metadata={
            "surface": "marketing_advisor",
            "thread_uid": "thread-42",
            "parent_job_id": 99,
            "phase": "evaluation",
            "subphase": "provider_generation",
            "attempt_index": 1,
            "attempt_total": 2,
            "unsafe_prompt_copy": secret_prompt,
            "nested": {"secret": secret_prompt},
        },
        staff={"id": 7, "organization_id": 3},
    )

    with pytest.raises(LlmBudgetBlocked) as caught:
        reserve_llm_budget(
            provider="anthropic",
            model="claude-opus-4-7",
            purpose="unit-2",
            prompt="another",
            estimated_cost_usd=0.5,
        )

    assert caught.value.reason == "hard_stop_or_projected_cap"
    assert caught.value.scope in {"monthly_total", "provider:claude"}
    row = get_conn().execute(
        "SELECT * FROM vkpi_llm_budget_reservations WHERE reservation_key=?",
        (first.reservation_key,),
    ).fetchone()
    serialized = json.dumps(dict(row), default=str, sort_keys=True)
    assert secret_prompt not in serialized
    assert len(str(row["request_hash"])) == 64
    metadata = json.loads(str(row["metadata_json"]))
    assert metadata["request_content_recorded"] is False
    assert metadata["surface"] == "marketing_advisor"
    assert metadata["thread_uid"] == "thread-42"
    assert metadata["staff_id"] == "7"
    assert metadata["organization_id"] == "3"
    assert metadata["parent_job_id"] == 99
    assert metadata["phase"] == "evaluation"
    assert metadata["subphase"] == "provider_generation"
    assert metadata["attempt_index"] == 1
    assert metadata["attempt_total"] == 2
    assert "unsafe_prompt_copy" not in metadata
    assert "nested" not in metadata


def test_progress_metadata_normalizes_total_to_attempt_total() -> None:
    metadata = reservations._progress_metadata(
        {"phase": "provider_generation", "attempt_index": 2, "total": 3},
        staff=None,
        triggered_by=None,
    )

    assert metadata["attempt_index"] == 2
    assert metadata["attempt_total"] == 3
    assert "total" not in metadata


def test_settlement_updates_cumulative_scopes_once_and_not_single_call() -> None:
    _install_fixture()
    reservation = reserve_llm_budget(
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="unit",
        prompt="safe",
        estimated_cost_usd=0.4,
    )
    mark_llm_provider_started(reservation.reservation_key)

    first = settle_llm_reservation(reservation.reservation_key, 0.25)
    second = settle_llm_reservation(reservation.reservation_key, 0.25)

    assert first == {
        "settled": True,
        "actual_cost_usd": 0.25,
        "scopes_updated": ["monthly_total", "provider:claude"],
    }
    assert second["settled"] is False
    assert second["reason"] == "already_settled"
    budgets = {
        row["scope"]: float(row["current_spend"] or 0)
        for row in get_conn().execute(
            "SELECT scope,current_spend FROM vkpi_provider_budget_caps "
            "WHERE scope IN ('monthly_total','provider:claude','single_call')"
        ).fetchall()
    }
    assert budgets["monthly_total"] == pytest.approx(0.25)
    assert budgets["provider:claude"] == pytest.approx(0.25)
    assert budgets["single_call"] == pytest.approx(0.0)


def test_unknown_provider_outcome_keeps_reservation_open() -> None:
    _install_fixture()
    reservation = reserve_llm_budget(
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="unit",
        prompt="safe",
        estimated_cost_usd=0.4,
    )
    mark_llm_provider_started(reservation.reservation_key)

    assert mark_llm_provider_unknown(reservation.reservation_key) is True
    blocked = settle_llm_reservation(reservation.reservation_key, 0.1)
    assert blocked == {"settled": False, "reason": "provider_outcome_not_confirmed"}
    state = get_conn().execute(
        "SELECT state FROM vkpi_llm_budget_reservations WHERE reservation_key=?",
        (reservation.reservation_key,),
    ).fetchone()["state"]
    assert state == "unknown"


def test_reserved_call_writes_both_ledgers_without_double_incrementing_caps() -> None:
    _install_fixture()
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    llm_before = int(
        conn.execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"]
    )
    cost_before = int(
        conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"]
    )

    recorded = record_call(
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="reservation-ledger-unit",
        prompt="private-ledger-prompt",
        input_tokens=100,
        output_tokens=20,
        cost_micro_usd=1234,
        status="success",
        fallback_used=False,
        cost_tag="single_call",
        metadata={"reservation_key": "llmres-ledger-unit"},
        update_budget_scopes=False,
        force_cost_ledger=True,
    )

    assert recorded["call"]["prompt_hash"]
    assert "private-ledger-prompt" not in json.dumps(recorded, default=str)
    assert int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"]) == llm_before + 1
    assert int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"]) == cost_before + 1
    budgets = {
        row["scope"]: float(row["current_spend"] or 0)
        for row in conn.execute(
            "SELECT scope,current_spend FROM vkpi_provider_budget_caps "
            "WHERE scope IN ('monthly_total','provider:claude','single_call')"
        ).fetchall()
    }
    assert budgets == {
        "monthly_total": pytest.approx(0.0),
        "provider:claude": pytest.approx(0.0),
        "single_call": pytest.approx(0.0),
    }
