from __future__ import annotations

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("hermetic_action_db")


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


_ACTOR_STAFF_ID = 990779
_ACTOR_USER = {"id": _ACTOR_STAFF_ID, "email": "reconcile@test", "role": "employee"}
_ACTOR_STAFF = {
    "id": _ACTOR_STAFF_ID,
    "staff_id": _ACTOR_STAFF_ID,
    "user_id": _ACTOR_STAFF_ID,
    "role": "employee",
    "is_owner": 0,
    "permissions": {"vkpi": "write"},
    "email": "reconcile@test",
}
_BEARER = {"Authorization": "Bearer reconcile-token"}


@pytest.fixture()
def actor_client(hermetic_action_db):
    import app.api.dependencies.perms as perms_mod
    import app.main as main_mod
    from app.api.dependencies.auth import get_user_required
    from app.main import app
    from fastapi.testclient import TestClient

    saved = {
        "main_gcu": main_mod.get_current_user,
        "main_scfu": main_mod.staff_context_for_user,
        "perms_scfu": perms_mod.staff_context_for_user,
        "overrides": dict(app.dependency_overrides),
    }
    main_mod.get_current_user = lambda request: _ACTOR_USER
    main_mod.staff_context_for_user = lambda user: _ACTOR_STAFF
    perms_mod.staff_context_for_user = lambda user: _ACTOR_STAFF
    app.dependency_overrides[get_user_required] = lambda: _ACTOR_USER
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        main_mod.get_current_user = saved["main_gcu"]
        main_mod.staff_context_for_user = saved["main_scfu"]
        perms_mod.staff_context_for_user = saved["perms_scfu"]
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved["overrides"])


def _seed_executing_action() -> int:
    from app.db.connection import get_conn

    conn = get_conn()
    dedupe = f"reconcile:test:{uuid.uuid4().hex}"
    row = conn.execute(
        """
        INSERT INTO vkpi_action_inbox
          (dedupe_key, category, title, detail, priority, entity_type, entity_id,
           suggested_endpoint, requires_approval, owner_staff_id, reason,
           payload_json, status, created_at, updated_at)
        VALUES (?, 'failed_retry', 'reconciliation probe', 'external result unknown',
                'high', 'job', 'probe', 'POST /probe', true, ?, '',
                ?, 'executing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        RETURNING id
        """,
        (dedupe, _ACTOR_STAFF_ID, "{}"),
    ).fetchone()
    conn.commit()
    return int(dict(row)["id"])


def _cleanup(action_id: int) -> None:
    from app.db.connection import get_conn

    conn = get_conn()
    conn.execute("DELETE FROM vkpi_action_execution_ledger WHERE action_id = ?", (action_id,))
    conn.execute("DELETE FROM vkpi_action_inbox WHERE id = ?", (action_id,))
    conn.commit()


def _status(action_id: int) -> str:
    from app.db.connection import get_conn

    row = get_conn().execute(
        "SELECT status FROM vkpi_action_inbox WHERE id = ?", (action_id,)
    ).fetchone()
    return str(dict(row)["status"])


def _reconciliation_ledger(action_id: int) -> list[dict]:
    from app.db.connection import get_conn

    rows = get_conn().execute(
        """
        SELECT id, actor_staff_id, outcome, endpoint, detail_json
        FROM vkpi_action_execution_ledger
        WHERE action_id = ? AND endpoint = 'manual:reconcile'
        ORDER BY id
        """,
        (action_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def test_reconcile_requires_evidence_without_changing_state(actor_client):
    action_id = _seed_executing_action()
    try:
        response = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/reconcile",
            json={
                "decision": "succeeded",
                "reason": "checked downstream",
                "evidence": [],
                "correlation_id": f"reconcile-{uuid.uuid4().hex}",
            },
            headers=_BEARER,
        )
        assert response.status_code == 422
        assert _status(action_id) == "executing"
        assert _reconciliation_ledger(action_id) == []
    finally:
        _cleanup(action_id)


def test_unknown_stays_executing_then_success_is_terminal_and_idempotent(actor_client):
    action_id = _seed_executing_action()
    unknown_correlation = f"reconcile-{uuid.uuid4().hex}"
    success_correlation = f"reconcile-{uuid.uuid4().hex}"
    evidence = [{"source": "manual", "reference": "order:VKPI-42"}]
    try:
        unknown = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/reconcile",
            json={
                "decision": "unknown",
                "reason": "provider has not returned a receipt",
                "evidence": evidence,
                "correlation_id": unknown_correlation,
            },
            headers=_BEARER,
        )
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["status"] == "executing"
        assert _status(action_id) == "executing"

        succeeded = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/reconcile",
            json={
                "decision": "succeeded",
                "reason": "provider receipt and order match",
                "evidence": evidence,
                "correlation_id": success_correlation,
            },
            headers=_BEARER,
        )
        assert succeeded.status_code == 200, succeeded.text
        succeeded_body = succeeded.json()
        assert succeeded_body["status"] == "executed"
        assert succeeded_body["idempotent"] is False
        assert _status(action_id) == "executed"

        repeated = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/reconcile",
            json={
                "decision": "succeeded",
                "reason": "same operator retry",
                "evidence": evidence,
                "correlation_id": success_correlation,
            },
            headers=_BEARER,
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["idempotent"] is True
        assert repeated.json()["ledger_id"] == succeeded_body["ledger_id"]

        rows = _reconciliation_ledger(action_id)
        assert len(rows) == 2
        detail = rows[-1]["detail_json"]
        if isinstance(detail, str):
            detail = json.loads(detail)
        assert rows[-1]["actor_staff_id"] == _ACTOR_STAFF_ID
        assert detail["kind"] == "manual_reconciliation"
        assert detail["decision"] == "succeeded"
        assert detail["reason"] == "provider receipt and order match"
        assert detail["evidence"] == evidence
        assert detail["correlation_id"] == success_correlation
        assert detail["actor"]["staff_id"] == _ACTOR_STAFF_ID

        conflict = actor_client.post(
            f"/api/admin/vkpi/actions/{action_id}/reconcile",
            json={
                "decision": "failed",
                "reason": "conflicting retry",
                "evidence": evidence,
                "correlation_id": success_correlation,
            },
            headers=_BEARER,
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "reconciliation_correlation_conflict"
        assert _status(action_id) == "executed"
    finally:
        _cleanup(action_id)


def test_concurrent_same_correlation_writes_one_audit_row():
    from app.domains.actions import inbox

    action_id = _seed_executing_action()
    correlation = f"reconcile-{uuid.uuid4().hex}"
    try:
        def reconcile_once(_: int):
            return inbox.reconcile_executing_action(
                action_id,
                dict(_ACTOR_STAFF),
                decision="failed",
                reason="verified provider rejection",
                evidence=[{"source": "manual", "reference": "provider-log:reject-7"}],
                correlation_id=correlation,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reconcile_once, range(2)))

        assert all(result["ok"] for result in results)
        assert sorted(bool(result["idempotent"]) for result in results) == [False, True]
        assert len(_reconciliation_ledger(action_id)) == 1
        assert _status(action_id) == "failed"
    finally:
        _cleanup(action_id)
