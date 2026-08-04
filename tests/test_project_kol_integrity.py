"""Project KOL edge contracts that must fail before any assignment write."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _MissingProjectResult:
    def fetchone(self):
        return None


class _MissingProjectConn:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        del params
        self.sql.append(" ".join(str(sql).split()))
        return _MissingProjectResult()


@pytest.fixture()
def missing_project(monkeypatch):
    from app.domains.projects import workflow_projects_kols as workflow

    conn = _MissingProjectConn()
    monkeypatch.setattr(workflow, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow.scope, "assert_project_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "get_conn", lambda: conn)
    return workflow, conn


def test_available_kols_rejects_missing_project_before_listing_pool(missing_project):
    workflow, conn = missing_project

    with pytest.raises(LookupError, match="project not found"):
        workflow.list_available_project_kols(404, staff={"id": 84, "role": "staff"})

    assert len(conn.sql) == 1
    assert "FROM vkpi_projects" in conn.sql[0]
    assert "FROM vkpi_kol_pool" not in conn.sql[0]


def test_add_kols_rejects_missing_project_before_assignment_write(missing_project):
    workflow, conn = missing_project

    with pytest.raises(LookupError, match="project not found"):
        workflow.add_project_kols(
            404,
            {"kol_pool_ids": [1]},
            staff={"id": 84, "role": "staff"},
        )

    assert len(conn.sql) == 1
    assert "FROM vkpi_projects" in conn.sql[0]
    assert all("vkpi_project_kol_assignments" not in sql for sql in conn.sql)


class _ProjectResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _DeletedProjectConn:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql, params=()):
        del params
        compact = " ".join(str(sql).split())
        self.sql.append(compact)
        if "FROM vkpi_projects" in compact:
            if "stage_status" in compact and "<> 'deleted'" not in compact:
                return _ProjectResult({"id": 404, "stage_status": "deleted"})
            return _ProjectResult(None)
        raise AssertionError(f"deleted project must stop before downstream SQL: {compact}")


@pytest.fixture()
def deleted_project(monkeypatch):
    from app.domains.projects import workflow_projects_kols as workflow

    conn = _DeletedProjectConn()
    monkeypatch.setattr(workflow, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow.scope, "assert_project_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow, "get_conn", lambda: conn)
    return workflow, conn


def test_available_kols_rejects_soft_deleted_project(deleted_project):
    workflow, conn = deleted_project

    with pytest.raises(LookupError, match="project not found"):
        workflow.list_available_project_kols(404, staff={"id": 84, "role": "staff"})

    assert len(conn.sql) == 1
    assert "stage_status" in conn.sql[0]


def test_add_kols_rejects_soft_deleted_project_before_assignment_write(deleted_project):
    workflow, conn = deleted_project

    with pytest.raises(LookupError, match="project not found"):
        workflow.add_project_kols(
            404,
            {"kol_pool_ids": [1]},
            staff={"id": 84, "role": "staff"},
        )

    assert len(conn.sql) == 1
    assert "stage_status" in conn.sql[0]
    assert all("vkpi_project_kol_assignments" not in sql for sql in conn.sql)


class _RowsResult:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchall(self):
        return self._rows


def test_assignment_occupancy_pg_lock_is_sorted_and_rechecked(monkeypatch):
    from app.domains.projects import workflow_projects_kols as workflow

    events: list[tuple[str, tuple[int, ...]]] = []

    class _PgContractConn(workflow.PostgresCompatConnection):
        def __init__(self):
            # SQL construction contract only; execute is fully overridden.
            pass

        def execute(self, sql, params=()):
            events.append((" ".join(str(sql).split()), tuple(params)))
            return _RowsResult([{"id": value} for value in params])

    expected = {2: {"staff_id": 81, "source": "assignment"}}

    def _occupancy(_conn, ids):
        events.append(("occupancy", tuple(ids)))
        return expected

    monkeypatch.setattr(workflow, "_pool_claim_occupancy", _occupancy)

    result = workflow._locked_pool_claim_occupancy(_PgContractConn(), {9, 2, 9})

    assert result == expected
    assert events == [
        (
            "SELECT id FROM vkpi_kol_pool WHERE id IN (?,?) ORDER BY id FOR UPDATE",
            (2, 9),
        ),
        ("occupancy", (2, 9)),
    ]
    assert "_locked_pool_claim_occupancy" in workflow.add_project_kols.__code__.co_names


def test_assignment_occupancy_fake_connection_uses_ordered_read_without_for_update(monkeypatch):
    from app.domains.projects import workflow_projects_kols as workflow

    events: list[tuple[str, tuple[int, ...]]] = []

    class _FakeConn:
        def execute(self, sql, params=()):
            events.append((" ".join(str(sql).split()), tuple(params)))
            return _RowsResult([{"id": value} for value in params])

    def _occupancy(_conn, ids):
        events.append(("occupancy", tuple(ids)))
        return {}

    monkeypatch.setattr(workflow, "_pool_claim_occupancy", _occupancy)

    assert workflow._locked_pool_claim_occupancy(_FakeConn(), [4, 1, 4]) == {}
    assert events == [
        ("SELECT id FROM vkpi_kol_pool WHERE id IN (?,?) ORDER BY id", (1, 4)),
        ("occupancy", (1, 4)),
    ]


def test_project_write_guard_uses_pg_row_lock_and_plain_fake_read():
    from app.domains.projects import workflow_projects_kols as workflow

    pg_calls: list[tuple[str, tuple[int, ...]]] = []

    class _PgContractConn(workflow.PostgresCompatConnection):
        def __init__(self):
            pass

        def execute(self, sql, params=()):
            pg_calls.append((" ".join(str(sql).split()), tuple(params)))
            return _ProjectResult({"id": 11, "stage_status": "active"})

    assert workflow._require_project_for_kol_write(_PgContractConn(), 11)["id"] == 11
    assert pg_calls == [
        ("SELECT id, stage_status FROM vkpi_projects WHERE id=? FOR UPDATE", (11,)),
    ]

    fake_calls: list[str] = []

    class _FakeConn:
        def execute(self, sql, params=()):
            del params
            fake_calls.append(" ".join(str(sql).split()))
            return _ProjectResult({"id": 11, "stage_status": "active"})

    assert workflow._require_project_for_kol_write(_FakeConn(), 11)["id"] == 11
    assert fake_calls == ["SELECT id, stage_status FROM vkpi_projects WHERE id=?"]
