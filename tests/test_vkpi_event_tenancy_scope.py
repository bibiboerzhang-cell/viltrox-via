"""Offline Event tenancy contract for migration 244 and legacy rollout.

All DB objects here are recording fakes.  The suite never opens PostgreSQL,
Redis, workers, or the repository SQLite database.
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_events
from app.domains.access import scope
from app.domains.events import event_members, service


class _Result:
    def __init__(self, row: Any = None, rows: list[Any] | None = None):
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(
        self,
        *,
        schema_scoped: bool = True,
        event_org: int = 7,
        event_exists: bool = True,
        event_public: bool = False,
        memberships: dict[int, list[int]] | None = None,
    ):
        self.schema_scoped = schema_scoped
        self.event_org = event_org
        self.event_exists = event_exists
        self.event_public = event_public
        self.memberships = memberships or {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params=()):
        normalized = " ".join(str(sql).split())
        args = tuple(params)
        self.calls.append((normalized, args))
        if normalized == "PRAGMA table_info(vkpi_events)":
            cols = [{"name": "id"}]
            if self.schema_scoped:
                cols.append({"name": "organization_id"})
            return _Result(rows=cols)
        if "FROM organization_members" in normalized:
            sid = int(args[0])
            rows = [{"organization_id": org} for org in self.memberships.get(sid, [])]
            return _Result(row=rows[0] if rows else None, rows=rows)
        if "SELECT 1 FROM staff" in normalized:
            return _Result({"present": 1})
        if normalized.startswith("SELECT") and "FROM vkpi_events" in normalized:
            if "LIMIT ?" in normalized:
                return _Result(rows=[])
            requested_org = None
            if "organization_id" in normalized and args:
                requested_org = int(args[-1])
            found = self.event_exists and (requested_org is None or requested_org == self.event_org)
            if not found:
                return _Result(None)
            return _Result(
                {
                    "id": "evt_scope",
                    "organization_id": self.event_org,
                    "owner_id": 20,
                    "team_ids": [20],
                    "is_public": self.event_public,
                    "budget_json": {},
                    "related_project_ids": [],
                    "invited_kols_json": [],
                }
            )
        if normalized.startswith("SELECT") and "FROM vkpi_event_members" in normalized:
            return _Result(rows=[])
        return _Result(None)

    def commit(self):
        self.commits += 1


class _BrokenProbeConn(_Conn):
    def execute(self, sql: str, params=()):
        if "PRAGMA table_info(vkpi_events)" in str(sql):
            raise RuntimeError("metadata unavailable")
        return super().execute(sql, params)


class _BrokenMembershipConn(_Conn):
    def execute(self, sql: str, params=()):
        if "FROM organization_members" in str(sql):
            raise RuntimeError("membership unavailable")
        return super().execute(sql, params)


class _MissingStaffConn(_Conn):
    def execute(self, sql: str, params=()):
        if "SELECT 1 FROM staff" in str(sql):
            normalized = " ".join(str(sql).split())
            self.calls.append((normalized, tuple(params)))
            return _Result(None)
        return super().execute(sql, params)


class _MemberUpsertConn(_Conn):
    def execute(self, sql: str, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("INSERT INTO vkpi_event_members"):
            self.calls.append((normalized, tuple(params)))
            return _Result(
                {
                    "id": 77,
                    "event_id": params[0],
                    "staff_id": params[1],
                    "role": params[2],
                    "added_by_staff_id": params[3],
                    "created_at": "2026-07-13T00:00:00Z",
                }
            )
        return super().execute(sql, params)


def _staff(*, organization_id: int = 7, staff_id: int = 10, role: str = "employee"):
    return {"id": staff_id, "organization_id": organization_id, "role": role, "is_owner": 0}


def _sql_calls(conn: _Conn, prefix: str) -> list[tuple[str, tuple[Any, ...]]]:
    return [(sql, params) for sql, params in conn.calls if sql.startswith(prefix)]


@pytest.mark.parametrize("fn_name", ["list_events", "list_upcoming_events"])
def test_manager_lists_are_outer_scoped_before_visibility(monkeypatch, fn_name):
    conn = _Conn(event_org=7)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    getattr(service, fn_name)(_staff(role="manager"))

    event_select = next(sql for sql, _ in conn.calls if sql.startswith("SELECT * FROM vkpi_events"))
    assert "WHERE organization_id = ?" in event_select
    assert next(params for sql, params in conn.calls if sql == event_select)[0] == 7


def test_employee_public_visibility_cannot_escape_outer_organization(monkeypatch):
    conn = _Conn(event_org=7)
    monkeypatch.setattr(service, "get_conn", lambda: conn)
    monkeypatch.setattr(service, "is_postgres_runtime", lambda: True)

    service.list_events(_staff())

    sql, params = next((sql, params) for sql, params in conn.calls if sql.startswith("SELECT * FROM vkpi_events"))
    assert "WHERE organization_id = ? AND (owner_id = ?" in sql
    assert "COALESCE(is_public, FALSE) = TRUE" in sql
    assert params[:4] == (7, 10, 10, 10)


def test_detail_never_reads_children_when_parent_is_cross_organization(monkeypatch):
    conn = _Conn(event_org=8)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    result = service.get_event_detail("evt_scope", _staff(organization_id=7))

    assert result["item"] is None
    assert not any("FROM vkpi_event_tasks" in sql for sql, _ in conn.calls)
    parent_sql, parent_params = next(
        (sql, params) for sql, params in conn.calls if sql.startswith("SELECT * FROM vkpi_events")
    )
    assert "organization_id = ?" in parent_sql
    assert parent_params == ("evt_scope", 7)


def test_create_explicitly_persists_current_organization(monkeypatch):
    conn = _Conn(event_org=7, memberships={10: [7]})
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    service.create_event(
        {"id": "evt_scope", "title": "Scoped", "start_date": "2026-08-01", "end_date": "2026-08-02"},
        _staff(staff_id=10),
    )

    sql, params = _sql_calls(conn, "INSERT INTO vkpi_events")[0]
    assert "(organization_id, id, title" in sql
    assert params[:3] == (7, "evt_scope", "Scoped")
    assert params[16] == 10  # owner_id follows budget_json


def test_create_rejects_owner_from_another_organization(monkeypatch):
    conn = _Conn(event_org=7, memberships={20: [8]})
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="owner_id organization mismatch"):
        service.create_event(
            {"id": "evt_scope", "owner_id": 20},
            _staff(staff_id=10),
        )
    assert not _sql_calls(conn, "INSERT INTO vkpi_events")


@pytest.mark.parametrize(
    "operation,mutation_prefix",
    [
        (lambda: service.update_event("evt_scope", {"title": "x"}, _staff()), "UPDATE vkpi_events"),
        (lambda: service.delete_event("evt_scope", _staff()), "DELETE FROM vkpi_events"),
    ],
)
def test_root_mutations_include_organization_predicate(monkeypatch, operation, mutation_prefix):
    conn = _Conn(event_org=7)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    operation()

    sql, params = _sql_calls(conn, mutation_prefix)[0]
    assert "organization_id = ?" in sql
    assert params[-1] == 7


_CHILD_OPERATIONS: list[Callable[[], dict[str, Any]]] = [
    lambda: service.add_task("evt_scope", {}, _staff()),
    lambda: service.update_task("evt_scope", "task", {}, _staff()),
    lambda: service.delete_task("evt_scope", "task", _staff()),
    lambda: service.add_expense("evt_scope", {}, _staff()),
    lambda: service.delete_expense("evt_scope", "expense", _staff()),
    lambda: service.invite_kol("evt_scope", {}, _staff()),
    lambda: service.remove_kol("evt_scope", "invite", _staff()),
    lambda: service.add_material("evt_scope", {}, _staff()),
    lambda: service.update_material("evt_scope", "material", {}, _staff()),
    lambda: service.delete_material("evt_scope", "material", _staff()),
    lambda: service.add_product("evt_scope", {}, _staff()),
    lambda: service.update_product("evt_scope", "product", {}, _staff()),
    lambda: service.delete_product("evt_scope", "product", _staff()),
]


@pytest.mark.parametrize("operation", _CHILD_OPERATIONS)
def test_every_child_mutation_stops_at_cross_organization_parent(monkeypatch, operation):
    conn = _Conn(event_org=8)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(LookupError, match="event not found"):
        operation()

    mutations = [sql for sql, _ in conn.calls if sql.startswith(("INSERT ", "UPDATE ", "DELETE "))]
    assert mutations == []
    guard_sql, guard_params = next(
        (sql, params) for sql, params in conn.calls if sql.startswith("SELECT id FROM vkpi_events")
    )
    assert "organization_id = ?" in guard_sql
    assert guard_params == ("evt_scope", 7)


def test_team_target_must_belong_to_parent_organization(monkeypatch):
    conn = _Conn(event_org=7, memberships={99: [8]})
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="team member organization mismatch"):
        service.add_member("evt_scope", 99, _staff())
    assert not _sql_calls(conn, "UPDATE vkpi_events")


def test_share_member_target_must_belong_to_parent_organization(monkeypatch):
    conn = _Conn(event_org=7, memberships={99: [8]})
    monkeypatch.setattr(event_members, "get_conn", lambda: conn)

    result = event_members.add_member("evt_scope", 99, staff=_staff())

    assert result == {"status": "error", "error": "staff organization mismatch"}
    assert not any(sql.startswith("INSERT INTO vkpi_event_members") for sql, _ in conn.calls)


@pytest.mark.parametrize("public", [False, True])
def test_manager_or_public_shortcuts_do_not_cross_organization(monkeypatch, public):
    conn = _Conn(event_org=8, event_public=public)
    monkeypatch.setattr(scope, "get_conn", lambda: conn)
    actor = _staff(role="manager") if not public else _staff(role="employee")

    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_scope", actor)


def test_same_organization_public_event_remains_readable(monkeypatch):
    conn = _Conn(event_org=7, event_public=True)
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_scope", _staff())


def test_pre244_default_org_uses_legacy_shape_but_nondefault_fails_closed(monkeypatch):
    legacy = _Conn(schema_scoped=False, event_org=1)
    monkeypatch.setattr(service, "get_conn", lambda: legacy)

    service.list_events(_staff(organization_id=1, role="manager"))
    event_sql = next(sql for sql, _ in legacy.calls if sql.startswith("SELECT * FROM vkpi_events"))
    assert "organization_id" not in event_sql

    with pytest.raises(scope.ScopeDenied, match="event organization scope unavailable"):
        service.list_events(_staff(organization_id=2, role="manager"))


def test_schema_probe_failure_denies_default_workspace_instead_of_legacy_fallback(monkeypatch):
    conn = _BrokenProbeConn(schema_scoped=True, event_org=1)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="event organization schema unavailable"):
        service.list_events(_staff(organization_id=1, role="manager"))

    assert not any(sql.startswith("SELECT * FROM vkpi_events") for sql, _ in conn.calls)


def test_implicit_workspace_membership_failure_is_not_coerced_to_org1(monkeypatch):
    conn = _BrokenMembershipConn(schema_scoped=True, event_org=1)
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="event organization context unavailable"):
        service.list_events({"id": 10, "role": "manager"})

    assert not any(sql.startswith("SELECT * FROM vkpi_events") for sql, _ in conn.calls)


def test_implicit_multiple_memberships_require_explicit_active_workspace(monkeypatch):
    conn = _Conn(schema_scoped=True, event_org=7, memberships={10: [7, 8]})
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="event organization context is ambiguous"):
        service.list_events({"id": 10, "role": "manager"})

    assert not any(sql.startswith("SELECT * FROM vkpi_events") for sql, _ in conn.calls)


def test_implicit_legacy_workspace_requires_real_staff_identity(monkeypatch):
    conn = _MissingStaffConn(schema_scoped=False, memberships={})
    monkeypatch.setattr(service, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="event organization context unavailable"):
        service.list_events({"id": 999999, "role": "manager"})

    assert not any(sql.startswith("SELECT * FROM vkpi_events") for sql, _ in conn.calls)


def test_target_membership_failure_denies_even_legacy_org1():
    conn = _BrokenMembershipConn(schema_scoped=False, event_org=1)

    assert scope.staff_belongs_to_event_organization(conn, 99, 1) is False


def test_legacy_org1_share_target_must_exist_in_staff_table():
    conn = _MissingStaffConn(schema_scoped=False, memberships={})

    assert scope.staff_belongs_to_event_organization(conn, 999999, 1) is False


def test_share_route_maps_non_integer_staff_id_to_400(monkeypatch):
    monkeypatch.setattr(event_members, "assert_can_manage_members", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as caught:
        vkpi_events.add_event_share_member(
            "evt_scope",
            {"staff_id": "not-an-integer", "role": "viewer"},
            staff=_staff(),
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == "staff_id must be a positive integer"


def test_share_member_write_is_atomic_upsert(monkeypatch):
    conn = _MemberUpsertConn(event_org=7, memberships={99: [7]})
    monkeypatch.setattr(event_members, "get_conn", lambda: conn)
    monkeypatch.setattr(event_members.share_audit, "record", lambda **_kwargs: None)

    result = event_members.add_member(
        "evt_scope",
        99,
        role="editor",
        added_by_staff_id=10,
        staff=_staff(),
    )

    assert result["status"] == "created"
    upsert_sql = next(sql for sql, _ in conn.calls if sql.startswith("INSERT INTO vkpi_event_members"))
    assert "ON CONFLICT (event_id, staff_id) DO UPDATE" in upsert_sql


def test_team_json_update_locks_parent_row_on_postgres(monkeypatch):
    conn = _Conn(event_org=7, memberships={99: [7]})
    monkeypatch.setattr(service, "get_conn", lambda: conn)
    monkeypatch.setattr(service, "is_postgres_runtime", lambda: True)

    service.add_member("evt_scope", 99, _staff())

    locked_read = next(
        sql for sql, _ in conn.calls
        if sql.startswith("SELECT team_ids FROM vkpi_events")
    )
    assert locked_read.endswith("FOR UPDATE")
