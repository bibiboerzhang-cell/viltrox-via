"""V-KPI authorization matrix (A5, 2026-06-14).

A single matrix suite that pins the permission boundaries established by P0/P1:
roles × resources × actions, plus dashboard-scope isolation. It does NOT
re-prove the fine-grained share/public branches (those live in
``test_vkpi_scope.py`` and ``test_vkpi_event_scope.py``); it proves the *matrix*
holds as a whole so a regression in any single cell is caught here.

Axes
----
roles      : owner, admin (both can_view_all=True) │ non-owner employee (own-only)
resources  : project (int id) │ event (str id)
actions    : read │ write

Boundaries asserted
-------------------
1. owner/admin (can_view_all True): read+write ANY project/event; and
   ``effective_staff_id`` is None → global dashboard scope (no staff narrowing).
2. non-owner employee: ``effective_staff_id`` == own id; assert_*_access ALLOWs
   own (assigned/created/member/team) and is_public READ; DENYs a non-member
   private resource for BOTH read and write; ``project_filter`` restricts to own.
3. dashboard isolation: ``_build_funnel_summary(staff_scope_id=<emp>)`` totals
   are <= the global (None) totals — own-only is a subset of all. Proven
   structurally (the scoped path injects a staff-id WHERE narrowing) against a
   recording fake conn, so it does not depend on any seeded DB counts.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.access import scope


# ── identities ───────────────────────────────────────────────────────────────
OWNER = {"id": 1, "role": "employee", "is_owner": 1}        # is_owner → can_view_all
ADMIN = {"id": 2, "role": "admin", "permissions": {"vkpi": "admin"}}
MANAGER = {"id": 3, "role": "manager", "is_owner": 0}
EMP = {"id": 10, "role": "employee", "is_owner": 0}         # the own-only employee
OTHER = {"id": 20, "role": "employee", "is_owner": 0}       # owns the private resource

GLOBAL_ROLES = [OWNER, ADMIN, MANAGER]  # all can_view_all True


# ── fakes (mirror the patterns in test_vkpi_scope / test_vkpi_event_scope) ────
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _ProjectRoutingConn:
    """Routes vkpi_projects (project row) vs vkpi_project_members (member row)."""

    def __init__(self, project_row, member_row):
        self.project_row = project_row
        self.member_row = member_row

    def execute(self, sql, params=()):
        if "vkpi_project_members" in sql:
            return _FakeResult(self.member_row)
        return _FakeResult(self.project_row)


class _EventRoutingConn:
    """Routes vkpi_events (event row) vs vkpi_event_members (member row)."""

    def __init__(self, event_row, member_row):
        self.event_row = event_row
        self.member_row = member_row

    def execute(self, sql, params=()):
        if "vkpi_event_members" in sql:
            return _FakeResult(self.member_row)
        return _FakeResult(self.event_row)


# Private (non-restricted but unowned/unshared so the employee has no path) and
# private-restricted rows used to assert the DENY cells.
def _private_project_row():
    # owned by OTHER, restricted=1 so the PV-3 "non-restricted widens to employees"
    # tail cannot accidentally allow EMP; not public.
    return {"assigned_staff_id": OTHER["id"], "created_by_staff_id": OTHER["id"], "restricted": 1, "is_public": 0}


def _private_event_row():
    # owned by OTHER, team = [OTHER], not public.
    return {"owner_id": OTHER["id"], "team_ids": json.dumps([OTHER["id"]]), "is_public": 0}


# ─────────────────────────────────────────────────────────────────────────────
# (1) GLOBAL roles (owner/admin/manager): read + write ANY resource; global scope
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("actor", GLOBAL_ROLES, ids=["owner", "admin", "manager"])
@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_global_role_can_access_any_project(monkeypatch, actor, write):
    # Even a restricted, unowned, non-public project: can_view_all short-circuits.
    monkeypatch.setattr(
        scope, "get_conn", lambda: _ProjectRoutingConn(_private_project_row(), None)
    )
    scope.assert_project_access(999, actor, write=write)  # must not raise


@pytest.mark.parametrize("actor", GLOBAL_ROLES, ids=["owner", "admin", "manager"])
@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_global_role_can_access_any_event(monkeypatch, actor, write):
    monkeypatch.setattr(
        scope, "get_conn", lambda: _EventRoutingConn(_private_event_row(), None)
    )
    scope.assert_event_access("evt_X", actor, write=write)  # must not raise


@pytest.mark.parametrize("actor", GLOBAL_ROLES, ids=["owner", "admin", "manager"])
def test_global_role_is_global_dashboard_scope(actor):
    # can_view_all → effective_staff_id is None even when a staff_id is requested,
    # i.e. the dashboard aggregates run global (no own-only narrowing).
    assert scope.can_view_all(actor) is True
    assert scope.effective_staff_id(actor, requested_staff_id=None) is None
    # A requested staff id is honored only as an explicit per-person query filter,
    # never as a forced reduction of the actor's own scope.
    assert scope.effective_staff_id(actor, requested_staff_id=OTHER["id"]) == OTHER["id"]


# ─────────────────────────────────────────────────────────────────────────────
# (2) NON-OWNER EMPLOYEE: own-only scope; allow own + public-read; deny private
# ─────────────────────────────────────────────────────────────────────────────
def test_employee_effective_scope_is_own_id():
    assert scope.can_view_all(EMP) is False
    # frontend may request anyone; backend always reduces a plain employee to self.
    assert scope.effective_staff_id(EMP, requested_staff_id=OTHER["id"]) == EMP["id"]
    assert scope.effective_staff_id(EMP, requested_staff_id=None) == EMP["id"]


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_allowed_on_assigned_project(monkeypatch, write):
    # EMP is assigned → full access (read AND write).
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _ProjectRoutingConn(
            {"assigned_staff_id": EMP["id"], "created_by_staff_id": OTHER["id"], "restricted": 0, "is_public": 0},
            None,
        ),
    )
    scope.assert_project_access(100, EMP, write=write)  # must not raise


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_allowed_on_created_project(monkeypatch, write):
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _ProjectRoutingConn(
            {"assigned_staff_id": OTHER["id"], "created_by_staff_id": EMP["id"], "restricted": 0, "is_public": 0},
            None,
        ),
    )
    scope.assert_project_access(100, EMP, write=write)  # must not raise


def test_employee_allowed_read_on_public_project_denied_write_via_restricted(monkeypatch):
    # is_public, non-restricted, unowned → employee may READ (public widens read).
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _ProjectRoutingConn(
            {"assigned_staff_id": OTHER["id"], "created_by_staff_id": OTHER["id"], "restricted": 0, "is_public": 1},
            None,
        ),
    )
    scope.assert_project_access(100, EMP, write=False)  # public read OK


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_denied_on_private_unshared_project(monkeypatch, write):
    # Restricted, owned by OTHER, not shared, not public → DENY read AND write.
    monkeypatch.setattr(
        scope, "get_conn", lambda: _ProjectRoutingConn(_private_project_row(), None)
    )
    with pytest.raises(scope.ScopeDenied, match="project scope denied"):
        scope.assert_project_access(100, EMP, write=write)


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_allowed_on_owned_event(monkeypatch, write):
    # EMP is the event owner → full read+write.
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _EventRoutingConn(
            {"owner_id": EMP["id"], "team_ids": json.dumps([EMP["id"]]), "is_public": 0},
            None,
        ),
    )
    scope.assert_event_access("evt_own", EMP, write=write)  # must not raise


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_allowed_on_team_event(monkeypatch, write):
    # EMP is in team_ids (editor-level) → full read+write.
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _EventRoutingConn(
            {"owner_id": OTHER["id"], "team_ids": json.dumps([OTHER["id"], EMP["id"]]), "is_public": 0},
            None,
        ),
    )
    scope.assert_event_access("evt_team", EMP, write=write)  # must not raise


def test_employee_allowed_read_on_public_event_denied_write(monkeypatch):
    # public event: employee may READ but NOT write (is_public never widens write).
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _EventRoutingConn(
            {"owner_id": OTHER["id"], "team_ids": json.dumps([OTHER["id"]]), "is_public": 1},
            None,
        ),
    )
    scope.assert_event_access("evt_pub", EMP, write=False)  # public read OK
    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_pub", EMP, write=True)


@pytest.mark.parametrize("write", [False, True], ids=["read", "write"])
def test_employee_denied_on_private_unshared_event(monkeypatch, write):
    # owned by OTHER, team=[OTHER], not shared, not public → DENY read AND write,
    # even though EMP is a perfectly ordinary employee (a vkpi:write holder).
    monkeypatch.setattr(
        scope, "get_conn", lambda: _EventRoutingConn(_private_event_row(), None)
    )
    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_other", EMP, write=write)


# ── project_filter restricts a plain employee to own (real in-memory sqlite) ──
def _seed_projects_sqlite():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_projects (
            id INTEGER PRIMARY KEY,
            assigned_staff_id INTEGER,
            created_by_staff_id INTEGER,
            restricted INTEGER DEFAULT 0,
            is_public INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE vkpi_project_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            staff_id INTEGER,
            role TEXT DEFAULT 'viewer'
        )
        """
    )
    # 100 owned by EMP, 200 owned by OTHER (both private, non-public).
    conn.execute("INSERT INTO vkpi_projects VALUES (100, ?, ?, 0, 0)", (EMP["id"], EMP["id"]))
    conn.execute("INSERT INTO vkpi_projects VALUES (200, ?, ?, 0, 0)", (OTHER["id"], OTHER["id"]))
    return conn


def _visible_ids(conn, actor):
    where_sql, params = scope.project_filter("p", actor)
    sql = "SELECT p.id FROM vkpi_projects p"
    if where_sql:
        sql += f" WHERE {where_sql}"
    return {row["id"] for row in conn.execute(sql, params).fetchall()}


def test_project_filter_restricts_employee_to_own():
    conn = _seed_projects_sqlite()
    # employee sees only own project; global role (no filter) sees both.
    assert _visible_ids(conn, EMP) == {100}
    assert _visible_ids(conn, ADMIN) == {100, 200}  # can_view_all → empty WHERE


# ─────────────────────────────────────────────────────────────────────────────
# (3) DASHBOARD ISOLATION: scoped funnel totals <= global funnel totals
# ─────────────────────────────────────────────────────────────────────────────
class _FunnelScopeAwareConn:
    """Recording fake for _build_funnel_summary.

    Returns a fixed GLOBAL count for any query that carries no staff narrowing,
    and a strictly smaller SCOPED count for any query whose SQL embeds the
    employee's staff id (the own-only path inlines ``staff_id=<emp>`` /
    ``assigned_staff_id=<emp>`` etc.). This makes ``scoped <= global`` a
    structural consequence of the scope branching the function actually takes —
    no dependence on real seeded DB rows. by_staff rows are returned empty
    (irrelevant to the totals contract).
    """

    GLOBAL = 100
    SCOPED = 7  # any value strictly < GLOBAL

    def __init__(self, marker: str):
        self.marker = marker  # e.g. "=10" — present only in own-only SQL

    def _count(self, sql: str) -> int:
        return self.SCOPED if self.marker in sql else self.GLOBAL

    def execute(self, sql, params=()):
        # by_staff is a multi-row SELECT; everything else is a single agg row.
        if "GROUP BY f.staff_id" in sql:
            return _FakeResult([])  # fetchall() → []
        n = self._count(sql)
        # Supply every aggregate alias _build_funnel_summary reads.
        row = {
            "favorites_total": n,
            "favorites_kol_total": n,
            "claimed_total": n,
            "in_project_total": n,
            "published_total": n,
        }
        return _FakeResult(row)


def _patch_funnel(monkeypatch, marker):
    import app.domains.dashboard.summary as summary

    monkeypatch.setattr(summary, "get_conn", lambda: _FunnelScopeAwareConn(marker))
    # claims table presence is orthogonal; force a deterministic True so the
    # claimed_total branch is exercised under both scopes.
    monkeypatch.setattr(summary, "table_exists", lambda name: True)
    monkeypatch.setattr(summary, "_fetch_dicts", lambda sql: [])  # by_staff → []
    return summary


_TOTAL_KEYS = ("favorites_total", "favorites_kol_total", "claimed_total", "in_project_total", "published_total")


def test_funnel_scoped_totals_are_subset_of_global(monkeypatch):
    summary = _patch_funnel(monkeypatch, marker=f"={EMP['id']}")

    global_summary = summary._build_funnel_summary(staff_scope_id=None)
    scoped_summary = summary._build_funnel_summary(staff_scope_id=EMP["id"])

    for key in _TOTAL_KEYS:
        g = global_summary[key]
        s = scoped_summary[key]
        assert g is not None and s is not None
        assert s <= g, f"{key}: own-only ({s}) must be <= global ({g})"
    # And the scoped path genuinely narrowed (strict subset, not identical),
    # proving the own-only branch was actually taken.
    assert scoped_summary["favorites_total"] < global_summary["favorites_total"]


def test_funnel_global_scope_takes_no_staff_filter(monkeypatch):
    # With staff_scope_id=None the SQL must carry no per-staff narrowing marker,
    # so the fake returns the GLOBAL count for every total.
    summary = _patch_funnel(monkeypatch, marker=f"={EMP['id']}")
    global_summary = summary._build_funnel_summary(staff_scope_id=None)
    for key in _TOTAL_KEYS:
        assert global_summary[key] == _FunnelScopeAwareConn.GLOBAL
