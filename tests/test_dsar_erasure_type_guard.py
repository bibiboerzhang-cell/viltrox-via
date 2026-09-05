"""Hermetic erasure authorization contracts: fake SQL and no external clients."""
from __future__ import annotations

import pytest

from app.domains.kol import dsar_erasure


class FakeConnection:
    def __init__(self, ticket, *, allow_writes=False):
        self.ticket = ticket
        self.allow_writes = allow_writes
        self.statements = []
        self.commits = 0
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if not sql.lstrip().upper().startswith("SELECT"):
            assert self.allow_writes, "blocked request attempted a database write"
        return self

    def fetchone(self):
        return self.ticket

    def commit(self):
        assert self.allow_writes, "blocked request attempted a commit"
        self.commits += 1


def forbid_external_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("blocked request reached collection or an external deletion boundary")

    for name in ("collect_subject_footprint", "_delete_qdrant_points", "_delete_r2_objects"):
        monkeypatch.setattr(dsar_erasure, name, forbidden)


def assert_read_only_block(monkeypatch, ticket, *, target=9):
    conn = FakeConnection(ticket)
    monkeypatch.setattr(dsar_erasure, "get_conn", lambda: conn)
    forbid_external_effects(monkeypatch)
    result = dsar_erasure.erase_subject(target, dsar_request_id=4)
    assert result["status"] == "blocked"
    assert conn.commits == 0
    assert len(conn.statements) == 1
    assert conn.statements[0][1] == (4,)
    assert "status, request_type, subject_kol_pool_id" in conn.statements[0][0]
    return result


@pytest.mark.parametrize("status", ["approved", "executing"])
@pytest.mark.parametrize("request_type", [None, "", "rectification", "access", "do_not_contact", "unknown", "ERASURE", " erasure "])
def test_non_erasure_is_blocked_before_any_side_effect(monkeypatch, status, request_type):
    result = assert_read_only_block(monkeypatch, {
        "status": status, "request_type": request_type, "subject_kol_pool_id": 9,
    })
    assert result["reason"] == "dsar request is not an erasure request"


@pytest.mark.parametrize("ticket", [None, {}, {"status": "approved"},
                                     {"status": "executing", "request_type": "erasure"}])
def test_missing_authorization_fields_fail_closed(monkeypatch, ticket):
    assert_read_only_block(monkeypatch, ticket)


@pytest.mark.parametrize("status", [None, "", "pending", "rejected", "done", "unknown"])
def test_unapproved_erasure_never_collects_or_writes(monkeypatch, status):
    result = assert_read_only_block(monkeypatch, {
        "status": status, "request_type": "erasure", "subject_kol_pool_id": 9,
    })
    assert result["reason"] == "dsar request not approved"


@pytest.mark.parametrize("target,bound_subject", [
    (9, None), (9, 10), (9, "9"), (9, True), (9, 9.0),
    (None, 9), (0, 0), (-1, -1), (True, 1), ("9", 9), (9.0, 9),
])
def test_subject_must_be_explicit_positive_and_exactly_bound(monkeypatch, target, bound_subject):
    result = assert_read_only_block(monkeypatch, {
        "status": "approved", "request_type": "erasure", "subject_kol_pool_id": bound_subject,
    }, target=target)
    assert result["reason"] == "dsar request subject does not match"


@pytest.mark.parametrize("status", ["approved", "executing"])
def test_matching_erasure_preserves_approved_and_resume_flow_with_stubs(monkeypatch, status):
    from app.domains.audit import service

    conn = FakeConnection({"status": status, "request_type": "erasure", "subject_kol_pool_id": 9}, allow_writes=True)
    monkeypatch.setattr(dsar_erasure, "get_conn", lambda: conn)
    effects = []

    def collect(subject):
        effects.append(("collect", subject))
        return {"qdrant_point_ids": [], "r2_keys": []}

    monkeypatch.setattr(dsar_erasure, "collect_subject_footprint", collect)
    monkeypatch.setattr(dsar_erasure, "_delete_qdrant_points", lambda ids: effects.append(("qdrant", ids)) or {"status": "noop"})
    monkeypatch.setattr(dsar_erasure, "_delete_r2_objects", lambda ids: effects.append(("r2", ids)) or {"status": "noop"})
    monkeypatch.setattr(service, "log_sensitive_access", lambda **kwargs: effects.append(("audit", kwargs["resource_id"])))
    result = dsar_erasure.erase_subject(9, dsar_request_id=4)
    assert result["status"] == "done" and result["kol_pool_id"] == 9
    assert effects == [("collect", 9), ("qdrant", []), ("r2", []), ("audit", "9")]
    assert conn.commits == 1
    assert len(conn.statements) == 5  # One authorization SELECT, three fake DELETEs and a fake receipt UPDATE.
