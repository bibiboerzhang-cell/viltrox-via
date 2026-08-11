"""Read-only status contract for unbound GTM outreach Actions."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_gtm_verdicts
from app.domains.actions import approval_evidence
from app.domains.market_brain import outreach_truth_bridge


MANAGER = {
    "id": 7,
    "role": "manager",
    "organization_id": 1,
    "organization_scope_status": "resolved",
}
APPROVED_AT = "2026-08-11T02:00:00+00:00"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_action_inbox (
          id INTEGER PRIMARY KEY, dedupe_key TEXT, category TEXT, title TEXT,
          detail TEXT, priority TEXT, entity_type TEXT, entity_id TEXT,
          suggested_endpoint TEXT, estimated_cost_cents INTEGER,
          writes_business_data INTEGER, uses_llm INTEGER, requires_approval INTEGER,
          owner_staff_id INTEGER, reason TEXT, payload_json TEXT, touches_v6_fit INTEGER,
          expected_gain TEXT, risk_level TEXT, evidence_refs_json TEXT,
          verification_plan_json TEXT, affected_tables_json TEXT,
          approval_reason TEXT, status TEXT, approved_by_staff_id INTEGER,
          approved_at TEXT, approval_snapshot_sha256 TEXT
        );
        CREATE TABLE vkpi_action_outreach_truth_bridges (
          organization_id INTEGER NOT NULL, action_inbox_id INTEGER NOT NULL
        );
        CREATE TABLE vkpi_event_ledger (
          organization_id INTEGER, event_type TEXT, entity_type TEXT, entity_id TEXT,
          actor_id TEXT, source TEXT, payload_json TEXT, provenance_json TEXT
        );
        """
    )
    return conn


def _seed_action(
    conn: sqlite3.Connection,
    *,
    status: str = "approved",
    valid_approval: bool = True,
) -> None:
    row: dict[str, Any] = {
        "id": 41,
        "dedupe_key": "gtm:41",
        "category": "gtm_bet",
        "title": "outreach",
        "detail": "contact creator",
        "priority": "normal",
        "entity_type": "kol",
        "entity_id": "17",
        "suggested_endpoint": "/gtm",
        "estimated_cost_cents": 0,
        "writes_business_data": 1,
        "uses_llm": 0,
        "requires_approval": 1,
        "owner_staff_id": 7,
        "reason": "registered bet",
        "payload_json": "{}",
        "touches_v6_fit": 0,
        "expected_gain": "reply",
        "risk_level": "low",
        "evidence_refs_json": "[]",
        "verification_plan_json": "[]",
        "affected_tables_json": "[]",
        "approval_reason": "approved for test",
        "status": status,
        "approved_by_staff_id": 7 if valid_approval else None,
        "approved_at": APPROVED_AT if valid_approval else None,
    }
    snapshot_hash = approval_evidence._hash(row, row["approval_reason"])
    row["approval_snapshot_sha256"] = snapshot_hash if valid_approval else ""
    columns = approval_evidence.APPROVAL_CONTRACT_COLUMNS.split(",")
    conn.execute(
        f"INSERT INTO vkpi_action_inbox ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        tuple(row.get(column) for column in columns),
    )
    if valid_approval:
        conn.execute(
            """
            INSERT INTO vkpi_event_ledger VALUES (
              1,'action_approved','action','41','7',?, '{}',?
            )
            """,
            (
                approval_evidence._EVENT_SOURCE,
                json.dumps({"approval_snapshot_sha256": snapshot_hash}),
            ),
        )
    conn.commit()


def _status(conn: sqlite3.Connection) -> dict[str, Any]:
    return outreach_truth_bridge.get_outreach_binding_status(
        41, staff=MANAGER, _connection=conn,
    )


@pytest.mark.parametrize(
    ("status", "valid_approval", "bindable", "reason"),
    [
        ("approved", True, True, "eligible"),
        ("suggested", False, False, "outreach_action_not_approved_gtm_bet"),
        ("approved", False, False, "outreach_action_approval_proof_invalid"),
    ],
)
def test_existing_unbound_action_is_read_only_200_with_server_eligibility(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    valid_approval: bool,
    bindable: bool,
    reason: str,
) -> None:
    conn = _db()
    _seed_action(conn, status=status, valid_approval=valid_approval)
    monkeypatch.setattr(outreach_truth_bridge, "table_exists", lambda _name: True)
    monkeypatch.setattr(outreach_truth_bridge, "is_postgres_runtime", lambda: False)
    changes_before = conn.total_changes

    assert _status(conn) == {
        "ok": True,
        "status": "unbound",
        "bound": False,
        "bindable": bindable,
        "eligibility_reason": reason,
        "action_inbox_id": 41,
        "binding": None,
        "reply_verification": None,
    }
    assert conn.total_changes == changes_before


def test_truly_missing_action_stays_404_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    monkeypatch.setattr(outreach_truth_bridge, "table_exists", lambda _name: True)
    assert _status(conn) == {"ok": False, "reason": "outreach_action_not_found"}


@pytest.mark.parametrize(
    ("status", "valid_approval", "reason"),
    [
        ("suggested", False, "outreach_action_not_approved_gtm_bet"),
        ("approved", False, "outreach_action_approval_proof_invalid"),
    ],
)
def test_create_binding_remains_fail_closed_for_non_bindable_action(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    valid_approval: bool,
    reason: str,
) -> None:
    conn = _db()
    _seed_action(conn, status=status, valid_approval=valid_approval)
    monkeypatch.setattr(outreach_truth_bridge, "table_exists", lambda _name: True)
    monkeypatch.setattr(outreach_truth_bridge, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(outreach_truth_bridge, "_race_result", lambda *_args, **_kwargs: None)

    result = outreach_truth_bridge.create_outreach_binding(
        41,
        correlation_id="outreach-bind-0041",
        staff=MANAGER,
        _connection=conn,
    )
    assert result == {"ok": False, "reason": reason}


def test_route_returns_unbound_but_preserves_missing_and_scope_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unbound = {
        "ok": True,
        "status": "unbound",
        "bound": False,
        "bindable": False,
        "eligibility_reason": "outreach_action_not_approved_gtm_bet",
        "action_inbox_id": 41,
        "binding": None,
        "reply_verification": None,
    }
    monkeypatch.setattr(
        outreach_truth_bridge, "get_outreach_binding_status",
        lambda *_args, **_kwargs: unbound,
    )
    assert vkpi_gtm_verdicts.get_action_outreach_binding_status(
        41, staff=MANAGER,
    ) == unbound

    monkeypatch.setattr(
        outreach_truth_bridge, "get_outreach_binding_status",
        lambda *_args, **_kwargs: {"ok": False, "reason": "outreach_action_not_found"},
    )
    with pytest.raises(HTTPException) as missing:
        vkpi_gtm_verdicts.get_action_outreach_binding_status(404, staff=MANAGER)
    assert (missing.value.status_code, missing.value.detail) == (
        404, "outreach_action_not_found",
    )
    with pytest.raises(HTTPException) as denied:
        vkpi_gtm_verdicts.get_action_outreach_binding_status(
            41, staff={**MANAGER, "organization_id": 2},
        )
    assert denied.value.status_code == 403
