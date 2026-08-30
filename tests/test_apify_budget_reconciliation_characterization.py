from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.db import connection as db_connection
from app.platform import apify_budget
from app.platform import apify_budget_reconciliation as reconciliation
from app.platform.apify_budget_contracts import APIFY_BUDGET_SCOPE
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION_FAMILY = (
    ROOT / "backend/app/platform/apify_budget_reconciliation.py",
    ROOT / "backend/app/platform/apify_budget_reconciliation_runtime.py",
    ROOT / "backend/app/platform/apify_budget_reconciliation_contract.py",
)


class _Result:
    def __init__(
        self,
        *,
        one: Any = None,
        all_rows: list[Any] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._one = one
        self._all = list(all_rows or [])
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> list[Any]:
        return list(self._all)


class _Connection:
    def __init__(self, responses: list[_Result]) -> None:
        self.responses = list(responses)
        self.events: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        normalized = " ".join(sql.split())
        self.events.append(("execute", normalized, params))
        if not self.responses:
            raise AssertionError(f"unexpected SQL: {normalized}")
        return self.responses.pop(0)

    def commit(self) -> None:
        self.events.append(("commit",))

    def rollback(self) -> None:
        self.events.append(("rollback",))


def _install(
    monkeypatch: pytest.MonkeyPatch,
    conn: _Connection,
    *,
    postgres: bool = True,
) -> list[str]:
    schema_calls: list[str] = []
    monkeypatch.setattr(db_connection, "get_conn", lambda: conn)
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: postgres)
    monkeypatch.setattr(
        apify_budget,
        "_ensure_reservation_schema",
        lambda: schema_calls.append("schema"),
    )
    monkeypatch.setattr(
        reconciliation,
        "_utcnow",
        lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    return schema_calls


def _reservation(run_id: str, *, settled: bool = False) -> dict[str, Any]:
    return {
        "reservation_key": f"reservation-{run_id}",
        "task_id": f"task-{run_id}",
        "actor_id": "apify/instagram-scraper",
        "operation": "legacy_reconcile",
        "state": "settled" if settled else "provider_started",
        "apify_run_id": run_id,
        "actual_cost_usd": "0.125000" if settled else None,
        "provider_started_at": "2026-07-17T00:00:01Z",
        "settled_at": "2026-07-18T00:00:00Z" if settled else None,
        "metadata_json": "{}" if settled else json.dumps({"preserved": "yes"}),
    }


def _claim(run_id: str) -> dict[str, Any]:
    return {
        "state": "completed",
        "lease_expires_at": "2026-07-17T00:00:00Z",
        "provider_run_id": run_id,
        "fence_token": 9,
    }


def _ledger(run_id: str, ledger_id: int, *, occurred_at: str | None = None) -> dict[str, Any]:
    row = {
        "id": ledger_id,
        "cron_task": APIFY_BUDGET_SCOPE,
        "ai_provider": "apify",
        "model_name": "apify/instagram-scraper",
        "cost_usd": "0.125000",
        "metadata_json": json.dumps(
            {
                "actor_id": "apify/instagram-scraper",
                "operation": "legacy_reconcile",
                "apify_run_id": run_id,
                "run_status": "SUCCEEDED",
                "usage_total_usd": "0.125000",
                "estimated": False,
                "pricing_basis": "usage_settled",
                "unified_entry": True,
                "scope": APIFY_BUDGET_SCOPE,
                "budget_reservation_key": "",
                "budget_reservation_settlement": {},
            }
        ),
    }
    if occurred_at is not None:
        row["occurred_at"] = occurred_at
    return row


def test_reconcile_success_preserves_sql_and_transaction_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "legacy-fake-success"
    ledger_id = 73
    conn = _Connection(
        [
            _Result(one=_reservation(run_id)),
            _Result(one=_claim(run_id)),
            _Result(all_rows=[_ledger(run_id, ledger_id)]),
            _Result(rowcount=1),
            _Result(one={"state": "settled", "actual_cost_usd": "0.125000"}),
        ]
    )
    schema_calls = _install(monkeypatch, conn)

    result = reconciliation.reconcile_legacy_apify_reservation_from_ledger(
        f"reservation-{run_id}",
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="succeeded",
        expected_actual_cost_usd="0.1250004",
    )

    assert result == {
        "settled": True,
        "ledger_id": ledger_id,
        "apify_run_id": run_id,
        "terminal_status": "SUCCEEDED",
        "actual_cost_usd": 0.125,
        "budget_caps_updated": False,
        "ledger_inserted": False,
    }
    assert schema_calls == ["schema"]
    statements = [event[1] for event in conn.events if event[0] == "execute"]
    assert statements[0].endswith("WHERE reservation_key=? FOR UPDATE")
    assert "vkpi_provider_execution_claims" in statements[1]
    assert statements[1].endswith("WHERE task_id=? FOR UPDATE")
    assert "vkpi_ai_cost_ledger" in statements[2]
    assert statements[2].endswith("AND metadata_json LIKE ? FOR UPDATE")
    assert statements[3].startswith("UPDATE vkpi_apify_budget_reservations")
    assert statements[4].startswith("SELECT state,actual_cost_usd")
    assert conn.events[-1] == ("commit",)
    assert not any(event[0] == "rollback" for event in conn.events)


def test_reconcile_lost_fence_rolls_back_without_readback_or_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "legacy-fake-lost-fence"
    ledger_id = 74
    conn = _Connection(
        [
            _Result(one=_reservation(run_id)),
            _Result(one=_claim(run_id)),
            _Result(all_rows=[_ledger(run_id, ledger_id)]),
            _Result(rowcount=0),
        ]
    )
    _install(monkeypatch, conn)

    with pytest.raises(
        RuntimeError,
        match="legacy reservation reconciliation lost its state fence",
    ):
        reconciliation.reconcile_legacy_apify_reservation_from_ledger(
            f"reservation-{run_id}",
            expected_ledger_id=ledger_id,
            expected_run_id=run_id,
            expected_terminal_status="SUCCEEDED",
            expected_actual_cost_usd="0.125000",
        )

    assert conn.events[-1] == ("rollback",)
    assert not any(event[0] == "commit" for event in conn.events)
    assert len([event for event in conn.events if event[0] == "execute"]) == 4


def _repair_responses(
    run_id: str,
    ledger_id: int,
    *,
    provider_spend: str = "0.250000",
) -> list[_Result]:
    return [
        _Result(one=_reservation(run_id, settled=True)),
        _Result(one=_claim(run_id)),
        _Result(
            all_rows=[
                _ledger(
                    run_id,
                    ledger_id,
                    occurred_at="2026-07-17T00:00:00Z",
                )
            ]
        ),
        _Result(
            all_rows=[
                {"scope": APIFY_BUDGET_SCOPE, "current_spend": provider_spend},
                {"scope": "monthly_total", "current_spend": "0.250000"},
            ]
        ),
    ]


def test_cap_repair_writes_audit_then_caps_then_readbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "legacy-fake-cap-success"
    ledger_id = 75
    audit_json = json.dumps(
        {reconciliation._CAP_REPAIR_AUDIT_KEY: {"version": 1}}
    )
    conn = _Connection(
        [
            *_repair_responses(run_id, ledger_id),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(
                all_rows=[
                    {"scope": APIFY_BUDGET_SCOPE, "current_spend": "0.125000"},
                    {"scope": "monthly_total", "current_spend": "0.125000"},
                ]
            ),
            _Result(
                one={
                    "state": "settled",
                    "actual_cost_usd": "0.125000",
                    "metadata_json": audit_json,
                }
            ),
        ]
    )
    _install(monkeypatch, conn)

    result = reconciliation.repair_legacy_apify_double_counted_caps(
        f"reservation-{run_id}",
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
        expected_settled_at="2026-07-18T00:00:00Z",
        expected_provider_current_spend="0.250000",
        expected_monthly_current_spend="0.250000",
    )

    assert result["repaired"] is True
    statements = [event[1] for event in conn.events if event[0] == "execute"]
    assert statements[4].startswith("UPDATE vkpi_apify_budget_reservations")
    assert statements[5].startswith("UPDATE vkpi_provider_budget_caps")
    assert statements[6].startswith("UPDATE vkpi_provider_budget_caps")
    execute_events = [event for event in conn.events if event[0] == "execute"]
    assert execute_events[5][2][1] == APIFY_BUDGET_SCOPE
    assert execute_events[6][2][1] == "monthly_total"
    assert statements[7].startswith("SELECT scope,current_spend")
    assert statements[8].startswith("SELECT state,actual_cost_usd,metadata_json")
    assert conn.events[-1] == ("commit",)


def test_cap_repair_mismatched_snapshot_is_a_no_write_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "legacy-fake-cap-dry-run"
    ledger_id = 76
    conn = _Connection(
        _repair_responses(run_id, ledger_id, provider_spend="0.249999")
    )
    _install(monkeypatch, conn)

    result = reconciliation.repair_legacy_apify_double_counted_caps(
        f"reservation-{run_id}",
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
        expected_settled_at="2026-07-18T00:00:00Z",
        expected_provider_current_spend="0.250000",
        expected_monthly_current_spend="0.250000",
    )

    assert result == {
        "repaired": False,
        "reason": "budget_caps_current_spend_mismatch",
    }
    statements = [event[1] for event in conn.events if event[0] == "execute"]
    assert not any(statement.startswith("UPDATE") for statement in statements)
    assert conn.events[-1] == ("rollback",)
    assert not any(event[0] == "commit" for event in conn.events)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"expected_actual_cost_usd": "-0.000001"},
        {"expected_actual_cost_usd": "NaN"},
        {"expected_run_id": "bad/run"},
        {"expected_terminal_status": "RUNNING"},
    ),
)
def test_invalid_reconcile_evidence_never_touches_schema_or_connection(
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, Any],
) -> None:
    conn = _Connection([])
    schema_calls = _install(monkeypatch, conn)
    arguments: dict[str, Any] = {
        "expected_ledger_id": 7,
        "expected_run_id": "valid-run",
        "expected_terminal_status": "SUCCEEDED",
        "expected_actual_cost_usd": "0.125000",
    }
    arguments.update(kwargs)

    assert reconciliation.reconcile_legacy_apify_reservation_from_ledger(
        "reservation-valid-run",
        **arguments,
    ) == {"settled": False, "reason": "invalid_expected_evidence"}
    assert schema_calls == []
    assert conn.events == []


def test_budget_reconciliation_family_stays_bounded_and_acyclic_by_direction() -> None:
    trees = {
        str(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in RECONCILIATION_FAMILY
    }
    rows = collect_complexity(trees)
    public_entries = {
        row.qualified_name: row
        for row in rows
        if row.qualified_name
        in {
            "reconcile_legacy_apify_reservation_from_ledger",
            "repair_legacy_apify_double_counted_caps",
        }
    }

    assert set(public_entries) == {
        "reconcile_legacy_apify_reservation_from_ledger",
        "repair_legacy_apify_double_counted_caps",
    }
    assert all(row.cc <= 10 for row in public_entries.values())
    assert max(row.cc for row in rows) < 50
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) < 800
        for path in RECONCILIATION_FAMILY
    )
    contract_source = RECONCILIATION_FAMILY[2].read_text(encoding="utf-8")
    runtime_source = RECONCILIATION_FAMILY[1].read_text(encoding="utf-8")
    assert "from app." not in contract_source
    assert "from app.platform.apify_budget_reconciliation import" not in runtime_source
