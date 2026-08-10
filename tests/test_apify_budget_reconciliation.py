from __future__ import annotations

import json

import pytest

from app.db.connection import get_conn
from app.domains.costs import budget_guard
from app.platform.apify_budget_reconciliation import (
    reconcile_legacy_apify_reservation_from_ledger,
    repair_legacy_apify_double_counted_caps,
)


def _seed(run_id: str, *, cost: float = 0.125) -> tuple[str, int]:
    budget_guard.ensure_budget_schema()
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_provider_execution_claims (
          task_id TEXT PRIMARY KEY, job_type TEXT NOT NULL DEFAULT '', lease_owner TEXT NOT NULL,
          fence_token INTEGER NOT NULL, state TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
          provider_run_id TEXT, created_at TEXT, updated_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS vkpi_apify_budget_reservations (
          reservation_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, actor_id TEXT NOT NULL,
          operation TEXT NOT NULL, payload_hash TEXT NOT NULL, execution_fence_token INTEGER,
          estimate_source TEXT NOT NULL, estimated_cost_usd REAL NOT NULL, actual_cost_usd REAL,
          state TEXT NOT NULL, apify_run_id TEXT, metadata_json TEXT, reserved_at TEXT,
          provider_started_at TEXT, settled_at TEXT, updated_at TEXT,
          UNIQUE(task_id,actor_id,operation,payload_hash)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vkpi_apify_reservation_run
          ON vkpi_apify_budget_reservations(apify_run_id)
          WHERE apify_run_id IS NOT NULL AND apify_run_id<>'';
        """
    )
    conn.execute("DELETE FROM vkpi_apify_budget_reservations")
    conn.execute("DELETE FROM vkpi_provider_execution_claims")
    conn.execute(
        "DELETE FROM vkpi_ai_cost_ledger WHERE metadata_json LIKE ?",
        (f"%{run_id}%",),
    )
    key = f"reservation-{run_id}"
    task = f"task-{run_id}"
    conn.execute(
        """
        INSERT INTO vkpi_provider_execution_claims
          (task_id,job_type,lease_owner,fence_token,state,lease_expires_at,provider_run_id,
           created_at,updated_at)
        VALUES (?, 'test', 'test-owner', 1, 'completed', '2026-07-17T00:00:00Z', ?,
                '2026-07-17T00:00:00Z', '2026-07-17T00:00:00Z')
        """,
        (task, run_id),
    )
    conn.execute(
        """
        INSERT INTO vkpi_apify_budget_reservations
          (reservation_key,task_id,actor_id,operation,payload_hash,execution_fence_token,
           estimate_source,estimated_cost_usd,state,apify_run_id,metadata_json,reserved_at,
           provider_started_at,updated_at)
        VALUES (?,?,'apify/instagram-scraper','legacy_reconcile','payload',1,
                'test',0.2,'provider_started',?,?,'2026-07-17T00:00:00Z',
                '2026-07-17T00:00:01Z','2026-07-17T00:00:01Z')
        """,
        (key, task, run_id, json.dumps({"preserved": "yes"})),
    )
    conn.commit()
    budget_guard.update_budget(
        "provider:apify",
        {"cap_usd": 10, "current_spend": 0, "warning_at": 0.8, "hard_stop_at": 1},
    )
    budget_guard.update_budget(
        "monthly_total",
        {"cap_usd": 100, "current_spend": 0, "warning_at": 0.8, "hard_stop_at": 1},
    )
    recorded = budget_guard.record_cost(
        scope="provider:apify",
        ai_provider="apify",
        model_name="apify/instagram-scraper",
        cost_usd=cost,
        extra_scopes=["monthly_total"],
        update_budget_scopes=True,
        metadata={
            "actor_id": "apify/instagram-scraper",
            "operation": "legacy_reconcile",
            "apify_run_id": run_id,
            "run_status": "SUCCEEDED",
            "usage_total_usd": cost,
            "estimated": False,
            "pricing_basis": "usage_settled",
            "unified_entry": True,
            "budget_reservation_key": "",
            "budget_reservation_settlement": {},
        },
    )
    return key, int(recorded["ledger_id"])


def _caps() -> tuple[float, float]:
    return (
        float(budget_guard.get_budget_status("provider:apify")["current_spend"]),
        float(budget_guard.get_budget_status("monthly_total")["current_spend"]),
    )


def _seed_double_counted(run_id: str, *, cost: float = 0.125) -> tuple[str, int, str]:
    key, ledger_id = _seed(run_id, cost=cost)
    conn = get_conn()
    settled_at = "2026-07-18T00:00:00Z"
    conn.execute(
        "UPDATE vkpi_ai_cost_ledger SET occurred_at='2026-07-17T00:00:00Z' WHERE id=?",
        (ledger_id,),
    )
    for scope in ("provider:apify", "monthly_total"):
        conn.execute(
            "UPDATE vkpi_provider_budget_caps SET current_spend=current_spend+? WHERE scope=?",
            (cost, scope),
        )
    conn.execute(
        """
        UPDATE vkpi_apify_budget_reservations
        SET state='settled',actual_cost_usd=?,settled_at=?,metadata_json='{}'
        WHERE reservation_key=?
        """,
        (cost, settled_at, key),
    )
    conn.commit()
    return key, ledger_id, settled_at


def test_legacy_ledger_reconciliation_only_repairs_reservation_and_is_idempotent() -> None:
    run_id = "legacy-run-exact-once"
    key, ledger_id = _seed(run_id)
    conn = get_conn()
    caps_before = _caps()
    ledger_count = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"])
    ledger_before = dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone())

    first = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
    )
    row = conn.execute(
        "SELECT state,actual_cost_usd,metadata_json FROM vkpi_apify_budget_reservations WHERE reservation_key=?",
        (key,),
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert first["settled"] is True
    assert first["budget_caps_updated"] is False
    assert first["ledger_inserted"] is False
    assert row["state"] == "settled"
    assert float(row["actual_cost_usd"]) == pytest.approx(0.125)
    assert metadata["preserved"] == "yes"
    assert metadata["legacy_ledger_reconciliation"]["ledger_id"] == ledger_id
    assert metadata["legacy_ledger_reconciliation"]["claim_snapshot"]["state"] == "completed"
    assert caps_before == (0.125, 0.125)
    assert _caps() == caps_before
    assert int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"]) == ledger_count
    assert dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone()) == ledger_before

    second = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
    )
    assert second["reason"] == "already_reconciled"
    assert _caps() == caps_before
    assert int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"]) == ledger_count
    assert dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone()) == ledger_before


def test_legacy_ledger_reconciliation_fails_closed_on_duplicate_run_evidence() -> None:
    run_id = "legacy-run-duplicate"
    key, ledger_id = _seed(run_id)
    conn = get_conn()
    original = conn.execute(
        "SELECT metadata_json FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)
    ).fetchone()["metadata_json"]
    budget_guard.record_cost(
        scope="provider:apify",
        ai_provider="apify",
        model_name="apify/instagram-scraper",
        cost_usd=0.125,
        update_budget_scopes=False,
        metadata=json.loads(original),
    )
    caps_before = _caps()
    result = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
    )
    state = conn.execute(
        "SELECT state FROM vkpi_apify_budget_reservations WHERE reservation_key=?", (key,)
    ).fetchone()["state"]
    assert result == {"settled": False, "reason": "ledger_run_not_unique"}
    assert state == "provider_started"
    assert _caps() == caps_before


@pytest.mark.parametrize(
    ("metadata_patch", "expected_reason"),
    [
        ({"run_status": "RUNNING"}, "ledger_terminal_status_mismatch"),
        ({"usage_total_usd": 0.124}, "ledger_actual_cost_mismatch"),
        ({"actor_id": "apify/other-actor"}, "ledger_not_legacy_budget_accounted"),
        ({"operation": "other_operation"}, "ledger_not_legacy_budget_accounted"),
        ({"budget_reservation_key": "some-reservation"}, "ledger_not_legacy_budget_accounted"),
        ({"budget_reservation_settlement": {"settled": True}}, "ledger_not_legacy_budget_accounted"),
    ],
)
def test_legacy_ledger_reconciliation_rejects_unsettled_or_already_bound_ledger(
    metadata_patch: dict[str, object], expected_reason: str
) -> None:
    run_id = "legacy-run-" + expected_reason.replace("_", "-")
    key, ledger_id = _seed(run_id)
    conn = get_conn()
    row = conn.execute(
        "SELECT metadata_json FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    metadata.update(metadata_patch)
    conn.execute(
        "UPDATE vkpi_ai_cost_ledger SET metadata_json=? WHERE id=?",
        (json.dumps(metadata), ledger_id),
    )
    conn.commit()
    caps_before = _caps()
    result = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
    )
    assert result == {"settled": False, "reason": expected_reason}
    assert conn.execute(
        "SELECT state FROM vkpi_apify_budget_reservations WHERE reservation_key=?", (key,)
    ).fetchone()["state"] == "provider_started"
    assert _caps() == caps_before


def test_legacy_ledger_reconciliation_rejects_run_mismatch_and_nonterminal_claim() -> None:
    run_id = "legacy-run-live-claim"
    key, ledger_id = _seed(run_id)
    conn = get_conn()
    mismatch = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id="different-exact-run",
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
    )
    assert mismatch == {"settled": False, "reason": "reservation_run_id_mismatch"}

    caps_before = _caps()
    for expiry in ("2099-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "not-a-time"):
        conn.execute(
            "UPDATE vkpi_provider_execution_claims SET state='active',lease_expires_at=?",
            (expiry,),
        )
        conn.commit()
        live = reconcile_legacy_apify_reservation_from_ledger(
            key,
            expected_ledger_id=ledger_id,
            expected_run_id=run_id,
            expected_terminal_status="SUCCEEDED",
            expected_actual_cost_usd=0.125,
        )
        assert live == {"settled": False, "reason": "provider_claim_not_terminal"}
    assert conn.execute(
        "SELECT state FROM vkpi_apify_budget_reservations WHERE reservation_key=?", (key,)
    ).fetchone()["state"] == "provider_started"
    assert _caps() == caps_before


@pytest.mark.parametrize("bad_id", [True, 1.9, "1"])
def test_legacy_ledger_reconciliation_rejects_non_integer_ledger_id(bad_id: object) -> None:
    assert reconcile_legacy_apify_reservation_from_ledger(
        "reservation",
        expected_ledger_id=bad_id,  # type: ignore[arg-type]
        expected_run_id="valid-run",
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
    ) == {"settled": False, "reason": "invalid_expected_evidence"}


@pytest.mark.parametrize(
    ("assignment", "reason"),
    [
        ("metadata_json=''", "reservation_metadata_invalid"),
        ("actual_cost_usd=0.125", "reservation_state_inconsistent"),
        ("settled_at='2026-07-17T00:00:02Z'", "reservation_state_inconsistent"),
        ("provider_started_at=NULL", "reservation_state_inconsistent"),
    ],
)
def test_legacy_ledger_reconciliation_rejects_inconsistent_reservation(
    assignment: str, reason: str
) -> None:
    run_id = "legacy-reservation-" + reason.replace("_", "-") + str(len(assignment))
    key, ledger_id = _seed(run_id)
    conn = get_conn()
    conn.execute(
        f"UPDATE vkpi_apify_budget_reservations SET {assignment} WHERE reservation_key=?",
        (key,),
    )
    conn.commit()
    result = reconcile_legacy_apify_reservation_from_ledger(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
    )
    assert result == {"settled": False, "reason": reason}


def test_double_counted_cap_repair_is_atomic_audited_and_idempotent() -> None:
    run_id = "legacy-double-counted-success"
    key, ledger_id, settled_at = _seed_double_counted(run_id)
    conn = get_conn()
    ledger_count = int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"])
    ledger_before = dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone())
    all_ledgers_before = [
        dict(row) for row in conn.execute("SELECT * FROM vkpi_ai_cost_ledger ORDER BY id").fetchall()
    ]
    other_caps_before = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM vkpi_provider_budget_caps
            WHERE scope NOT IN ('provider:apify','monthly_total') ORDER BY scope
            """
        ).fetchall()
    ]
    reservation_before = dict(
        conn.execute(
            """
            SELECT state,actual_cost_usd,settled_at,provider_started_at,apify_run_id
            FROM vkpi_apify_budget_reservations WHERE reservation_key=?
            """,
            (key,),
        ).fetchone()
    )
    assert _caps() == (0.25, 0.25)

    first = repair_legacy_apify_double_counted_caps(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
        expected_settled_at=settled_at,
        expected_provider_current_spend="0.250000",
        expected_monthly_current_spend="0.250000",
    )
    assert first["repaired"] is True
    assert first["ledger_modified"] is False
    assert first["reservation_state_or_actual_modified"] is False
    assert _caps() == (0.125, 0.125)
    assert int(conn.execute("SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger").fetchone()["n"]) == ledger_count
    assert dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone()) == ledger_before
    assert [
        dict(row) for row in conn.execute("SELECT * FROM vkpi_ai_cost_ledger ORDER BY id").fetchall()
    ] == all_ledgers_before
    assert [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM vkpi_provider_budget_caps
            WHERE scope NOT IN ('provider:apify','monthly_total') ORDER BY scope
            """
        ).fetchall()
    ] == other_caps_before
    reservation_after = dict(
        conn.execute(
            """
            SELECT state,actual_cost_usd,settled_at,provider_started_at,apify_run_id
            FROM vkpi_apify_budget_reservations WHERE reservation_key=?
            """,
            (key,),
        ).fetchone()
    )
    assert reservation_after == reservation_before
    metadata = json.loads(
        conn.execute(
            "SELECT metadata_json FROM vkpi_apify_budget_reservations WHERE reservation_key=?",
            (key,),
        ).fetchone()["metadata_json"]
    )
    audit = metadata["legacy_ledger_double_budget_repair"]
    assert audit["ledger_id"] == ledger_id
    assert audit["cap_spend_before"] == {
        "monthly_total": "0.250000",
        "provider:apify": "0.250000",
    }
    assert audit["cap_spend_after"] == {
        "monthly_total": "0.125000",
        "provider:apify": "0.125000",
    }

    second = repair_legacy_apify_double_counted_caps(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd="0.125000",
        expected_settled_at=settled_at,
        expected_provider_current_spend="0.250000",
        expected_monthly_current_spend="0.250000",
    )
    assert second == {"repaired": False, "reason": "already_repaired", "ledger_id": ledger_id}
    assert _caps() == (0.125, 0.125)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("late_ledger", "ledger_not_before_reservation_settlement"),
        ("nonempty_metadata", "reservation_metadata_not_empty"),
        ("active_claim", "provider_claim_not_terminal"),
        ("duplicate_ledger", "ledger_run_not_unique"),
    ],
)
def test_double_counted_cap_repair_rejects_ambiguous_evidence(
    mutation: str, reason: str
) -> None:
    run_id = "legacy-double-reject-" + mutation.replace("_", "-")
    key, ledger_id, settled_at = _seed_double_counted(run_id)
    conn = get_conn()
    if mutation == "late_ledger":
        conn.execute(
            "UPDATE vkpi_ai_cost_ledger SET occurred_at=? WHERE id=?",
            (settled_at, ledger_id),
        )
    elif mutation == "nonempty_metadata":
        conn.execute(
            "UPDATE vkpi_apify_budget_reservations SET metadata_json=? WHERE reservation_key=?",
            (json.dumps({"unexpected": True}), key),
        )
    elif mutation == "active_claim":
        conn.execute("UPDATE vkpi_provider_execution_claims SET state='active'")
    else:
        source = dict(conn.execute("SELECT * FROM vkpi_ai_cost_ledger WHERE id=?", (ledger_id,)).fetchone())
        conn.execute(
            """
            INSERT INTO vkpi_ai_cost_ledger
              (cron_task,ai_provider,model_name,cost_usd,tokens_in,tokens_out,
               metadata_json,occurred_at)
            VALUES (?,?,?,?,0,0,?,?)
            """,
            (
                source["cron_task"], source["ai_provider"], source["model_name"],
                source["cost_usd"], source["metadata_json"], source["occurred_at"],
            ),
        )
    conn.commit()
    caps_before = _caps()
    result = repair_legacy_apify_double_counted_caps(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
        expected_settled_at=settled_at,
        expected_provider_current_spend=0.25,
        expected_monthly_current_spend=0.25,
    )
    assert result == {"repaired": False, "reason": reason}
    assert _caps() == caps_before
    assert conn.execute(
        "SELECT metadata_json FROM vkpi_apify_budget_reservations WHERE reservation_key=?",
        (key,),
    ).fetchone()["metadata_json"] == (
        json.dumps({"unexpected": True}) if mutation == "nonempty_metadata" else "{}"
    )


def test_double_counted_cap_repair_requires_exact_locked_pre_spend() -> None:
    run_id = "legacy-double-cap-drift"
    key, ledger_id, settled_at = _seed_double_counted(run_id)
    caps_before = _caps()
    result = repair_legacy_apify_double_counted_caps(
        key,
        expected_ledger_id=ledger_id,
        expected_run_id=run_id,
        expected_terminal_status="SUCCEEDED",
        expected_actual_cost_usd=0.125,
        expected_settled_at=settled_at,
        expected_provider_current_spend=0.249999,
        expected_monthly_current_spend=0.25,
    )
    assert result == {"repaired": False, "reason": "budget_caps_current_spend_mismatch"}
    assert _caps() == caps_before
