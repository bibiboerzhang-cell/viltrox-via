from __future__ import annotations

import json

import pytest

from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform import llm_budget_reservations as reservations
from app.platform import llm_gateway
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
        model="claude-opus-5",
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
            model="claude-opus-5",
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
        model="claude-opus-5",
        purpose="unit",
        prompt="safe",
        estimated_cost_usd=0.4,
    )
    mark_llm_provider_started(reservation.reservation_key)

    first = settle_llm_reservation(reservation.reservation_key, 0.25)
    second = settle_llm_reservation(reservation.reservation_key, 0.25)

    assert first["settled"] is True
    assert first["actual_cost_usd"] == 0.25
    assert first["actual_cost_micro_usd"] == 250_000
    assert first["scopes_updated"] == ["monthly_total", "provider:claude"]
    assert first["scope_deltas_micro_usd"] == {
        "monthly_total": 250_000,
        "provider:claude": 250_000,
    }
    assert first["readback_verified"] is True
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
        model="claude-opus-5",
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
        model="claude-opus-5",
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
    assert recorded["cost_ledger"]["ledger_id"] > 0
    assert recorded["cost_ledger"]["cost_micro_usd"] == 1234
    assert recorded["cost_ledger"]["persisted_cost_usd"] == "0.001234"
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


def test_update_budget_preserves_subcent_cap_and_spend() -> None:
    _install_fixture()
    budget_guard.update_budget(
        "provider:micro-budget-unit",
        {"cap_usd": "0.000050", "current_spend": "0.000033"},
    )
    row = get_conn().execute(
        "SELECT cap_usd,current_spend FROM vkpi_provider_budget_caps WHERE scope=?",
        ("provider:micro-budget-unit",),
    ).fetchone()
    assert round(float(row["cap_usd"]) * 1_000_000) == 50
    assert round(float(row["current_spend"]) * 1_000_000) == 33


@pytest.mark.parametrize("actual_micro", [1, 49, 50, 51, 553])
def test_settlement_preserves_micro_usd_and_reads_every_scope_back(
    actual_micro: int,
) -> None:
    _install_fixture()
    reservation = reserve_llm_budget(
        provider="anthropic",
        model="claude-opus-5",
        purpose="micro-unit",
        prompt="safe",
        estimated_cost_usd=0.000600,
    )
    mark_llm_provider_started(reservation.reservation_key)

    settled = settle_llm_reservation(
        reservation.reservation_key,
        actual_micro / 1_000_000,
    )

    assert settled["settled"] is True
    assert settled["actual_cost_micro_usd"] == actual_micro
    assert settled["readback_verified"] is True
    assert settled["scope_deltas_micro_usd"] == {
        "monthly_total": actual_micro,
        "provider:claude": actual_micro,
    }
    row = get_conn().execute(
        "SELECT actual_cost_usd FROM vkpi_llm_budget_reservations "
        "WHERE reservation_key=?",
        (reservation.reservation_key,),
    ).fetchone()
    assert round(float(row["actual_cost_usd"]) * 1_000_000) == actual_micro


@pytest.mark.parametrize("invalid", [-0.000001, float("nan"), 0.0000001])
def test_settlement_rejects_invalid_or_submicro_cost(invalid: float) -> None:
    _install_fixture()
    reservation = reserve_llm_budget(
        provider="anthropic",
        model="claude-opus-5",
        purpose="invalid-cost-unit",
        prompt="safe",
        estimated_cost_usd=0.001,
    )
    mark_llm_provider_started(reservation.reservation_key)

    with pytest.raises(ValueError):
        settle_llm_reservation(reservation.reservation_key, invalid)

    state = get_conn().execute(
        "SELECT state FROM vkpi_llm_budget_reservations WHERE reservation_key=?",
        (reservation.reservation_key,),
    ).fetchone()["state"]
    assert state == "provider_started"


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        {"recorded": True, "ledger_id": 0, "cost_micro_usd": 33},
        {"recorded": True, "ledger_id": 1, "cost_micro_usd": 34},
    ],
)
def test_forced_cost_mirror_requires_confirmed_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
    receipt: dict[str, object] | None,
) -> None:
    _install_fixture()
    ensure_vkpi_product_industry_schema()

    class Mirror:
        @staticmethod
        def record_cost(**_kwargs):
            return receipt

    monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: Mirror())
    with pytest.raises(RuntimeError, match="forced_ai_cost_ledger_write_failed"):
        record_call(
            provider="anthropic",
            model="claude-opus-5",
            purpose="forced-mirror-receipt-unit",
            cost_micro_usd=33,
            status="success",
            cost_tag="single_call",
            update_budget_scopes=False,
            force_cost_ledger=True,
        )


def test_forced_cost_mirror_propagates_sanitized_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fixture()
    ensure_vkpi_product_industry_schema()

    class ForeignKeyViolation(RuntimeError):
        pass

    class Mirror:
        @staticmethod
        def record_cost(**_kwargs):
            raise ForeignKeyViolation(
                'insert violates foreign key constraint "vkpi_ai_cost_ledger_staff_id_fkey"\n'
                "DETAIL:  Key (staff_id)=(1) is not present in table staff. "
                "api_key=sk-live-SECRET dsn=postgresql://u:pw@db/x"
            )

    monkeypatch.setattr(llm_gateway, "_budget_guard", lambda: Mirror())
    with pytest.raises(RuntimeError, match="^forced_ai_cost_ledger_write_failed: ") as caught:
        record_call(
            provider="anthropic",
            model="claude-opus-5",
            purpose="forced-mirror-failure-unit",
            cost_micro_usd=33,
            status="success",
            cost_tag="single_call",
            update_budget_scopes=False,
            force_cost_ledger=True,
        )
    message = str(caught.value)
    # C1 台账透明:根因类名 + 首行 + DETAIL 行可见;密钥/URL userinfo 打码。
    assert "ForeignKeyViolation:" in message and "staff_id" in message and "(staff_id)=(1)" in message
    assert "sk-live-SECRET" not in message and "u:pw@" not in message
    assert isinstance(caught.value.__cause__, ForeignKeyViolation)
    # 调用行已落且 metadata 带 cost_ledger_error(排障不必再去翻子进程 stderr)。
    row = get_conn().execute(
        "SELECT metadata_json FROM vkpi_llm_calls WHERE purpose=? ORDER BY id DESC LIMIT 1",
        ("forced-mirror-failure-unit",),
    ).fetchone()
    assert row is not None and "cost_ledger_error" in str(row["metadata_json"])
    assert "ForeignKeyViolation" in str(row["metadata_json"]) and "sk-live-SECRET" not in str(row["metadata_json"])


def test_forced_cost_mirror_requires_scope_before_call_row() -> None:
    _install_fixture()
    ensure_vkpi_product_industry_schema()
    before = int(
        get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"]
    )

    with pytest.raises(RuntimeError, match="forced_ai_cost_ledger_scope_missing"):
        record_call(
            provider="anthropic",
            model="claude-opus-5",
            purpose="forced-mirror-scope-unit",
            cost_micro_usd=33,
            status="success",
            force_cost_ledger=True,
        )

    after = int(
        get_conn().execute("SELECT COUNT(*) AS n FROM vkpi_llm_calls").fetchone()["n"]
    )
    assert after == before
