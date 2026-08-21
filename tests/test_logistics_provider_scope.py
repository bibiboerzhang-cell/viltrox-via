from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_projects
from app.domains.logistics import seventeen_track
from app.workers import apify_jobs_worker_handlers


EMPLOYEE = {
    "id": 73,
    "user_id": 173,
    "role": "employee",
    "permissions": {"vkpi": "write"},
}
MANAGER = {
    "id": 7,
    "user_id": 107,
    "role": "manager",
    "permissions": {"vkpi": "write"},
}


def _bomb(message: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(message)

    return fail


class _Cursor:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def _payload(*, assignment_ids=(801,), permissions_staff_id=73) -> dict:
    return {
        "project_id": 91,
        "scope_key": "91",
        "target_type": "logistics",
        "staff_id": permissions_staff_id,
        "triggered_by_user_id": 173,
        seventeen_track.FENCE_KEY: {
            "version": seventeen_track.FENCE_VERSION,
            "actor_kind": "staff",
            "staff_id": permissions_staff_id,
            "user_id": 173,
            "project_id": 91,
            "assignment_ids": list(assignment_ids),
        },
    }


def test_company_writer_cannot_enqueue_global_logistics_or_touch_token_db_provider(monkeypatch) -> None:
    monkeypatch.setattr(seventeen_track, "_token", _bomb("writer must not inspect provider token"))
    monkeypatch.setattr(seventeen_track, "get_conn", _bomb("writer must not open logistics DB"))
    monkeypatch.setattr(seventeen_track, "_api", _bomb("writer must not call 17TRACK"))

    with pytest.raises(HTTPException) as caught:
        vkpi_projects.enqueue_logistics_sync(body={}, staff=EMPLOYEE)

    assert caught.value.status_code == 403
    assert caught.value.detail == "logistics_global_manager_required"


def test_project_denial_precedes_token_db_and_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        seventeen_track.scope,
        "assert_project_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(seventeen_track.scope.ScopeDenied("denied")),
    )
    monkeypatch.setattr(seventeen_track, "_token", _bomb("scope denial must precede token"))
    monkeypatch.setattr(seventeen_track, "get_conn", _bomb("scope denial must precede DB"))
    monkeypatch.setattr(seventeen_track, "_api", _bomb("scope denial must precede provider"))

    with pytest.raises(HTTPException) as caught:
        vkpi_projects.enqueue_logistics_sync(body={"project_id": 91}, staff=EMPLOYEE)

    assert caught.value.status_code == 403
    assert caught.value.detail == "logistics_project_write_forbidden"


class _EnqueueConn:
    def __init__(self):
        self.payload: dict | None = None
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        if "select id from apify_jobs" in normalized:
            return _Cursor(row=None)
        if "select id from vkpi_projects" in normalized:
            assert tuple(params) == (91,)
            return _Cursor(row={"id": 91})
        if "select id, project_id, tracking_number" in normalized:
            assert tuple(params) == (91,)
            return _Cursor(
                rows=[{
                    "id": 801,
                    "project_id": 91,
                    "tracking_number": "TRACK-801",
                    "metadata_json": {},
                    "kol_pool_id": 42,
                }],
            )
        if "insert into apify_jobs" in normalized:
            self.payload = json.loads(params[1])
            return _Cursor(row={"id": 501})
        raise AssertionError(f"unexpected enqueue query: {normalized}")

    def commit(self):
        self.commits += 1


def test_single_project_enqueue_persists_actor_project_and_assignment_fence(monkeypatch) -> None:
    conn = _EnqueueConn()
    project_checks: list[tuple[int, dict, bool]] = []
    monkeypatch.setattr(seventeen_track, "_token", lambda: "configured")
    monkeypatch.setattr(seventeen_track, "get_conn", lambda: conn)
    monkeypatch.setattr(
        seventeen_track.scope,
        "assert_project_access",
        lambda project_id, staff, *, write=False: project_checks.append((project_id, staff, write)),
    )

    result = seventeen_track.enqueue_logistics_sync_job(project_id=91, staff=EMPLOYEE)

    assert result == {"status": "queued", "job_id": 501}
    assert project_checks == [(91, EMPLOYEE, True)]
    assert conn.commits == 1
    assert conn.payload is not None
    assert conn.payload["project_id"] == 91
    assert conn.payload["staff_id"] == 73
    assert conn.payload["triggered_by_user_id"] == 173
    assert conn.payload[seventeen_track.FENCE_KEY] == {
        "version": 1,
        "actor_kind": "staff",
        "staff_id": 73,
        "user_id": 173,
        "project_id": 91,
        "assignment_ids": [801],
    }


class _WorkerConn:
    def __init__(self, *, permissions: str = "write", assignment_project_id: int = 91):
        self.permissions = permissions
        self.assignment_project_id = assignment_project_id
        self.commits = 0
        self.updates: list[tuple] = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).lower().split())
        if "from staff s join users" in normalized:
            return _Cursor(row={
                "id": 73,
                "user_id": 173,
                "role": "employee",
                "permissions_json": json.dumps({"vkpi": self.permissions}),
                "active": 1,
                "suspended_at": None,
                "user_status": "active",
            })
        if "select id from vkpi_projects" in normalized:
            return _Cursor(row={"id": 91})
        if "select id, project_id from vkpi_project_kol_assignments" in normalized:
            return _Cursor(rows=[{"id": 801, "project_id": self.assignment_project_id}])
        if "select id, project_id, tracking_number" in normalized:
            return _Cursor(rows=[{
                "id": 801,
                "project_id": 91,
                "tracking_number": "TRACK-801",
                "metadata_json": {},
                "kol_pool_id": 42,
            }])
        if "update vkpi_project_kol_assignments" in normalized:
            self.updates.append(tuple(params))
            return _Cursor(row=None)
        raise AssertionError(f"unexpected worker query: {normalized}")

    def commit(self):
        self.commits += 1


def test_worker_permission_revocation_is_provider_free_blocked(monkeypatch) -> None:
    monkeypatch.setattr(seventeen_track, "get_conn", lambda: _WorkerConn(permissions="none"))
    monkeypatch.setattr(seventeen_track, "_token", _bomb("revocation must precede provider token"))
    monkeypatch.setattr(seventeen_track, "_api", _bomb("revocation must not call 17TRACK"))
    monkeypatch.setattr(
        seventeen_track.scope,
        "assert_project_access",
        _bomb("permission revocation must precede project DB"),
    )

    result = seventeen_track.run_logistics_sync_for_job(_payload())

    assert result == {
        "status": "blocked:logistics_actor_permission_revoked",
        "reason": "logistics_actor_permission_revoked",
        "provider_calls_performed": False,
    }


def test_worker_project_revocation_is_provider_free_blocked(monkeypatch) -> None:
    monkeypatch.setattr(seventeen_track, "get_conn", lambda: _WorkerConn())
    monkeypatch.setattr(seventeen_track, "_token", _bomb("project revocation must precede token"))
    monkeypatch.setattr(seventeen_track, "_api", _bomb("project revocation must not call 17TRACK"))
    monkeypatch.setattr(
        seventeen_track.scope,
        "assert_project_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(seventeen_track.scope.ScopeDenied("revoked")),
    )

    result = seventeen_track.run_logistics_sync_for_job(_payload())

    assert result["status"] == "blocked:logistics_project_permission_revoked"
    assert result["reason"] == "logistics_project_permission_revoked"


def test_worker_assignment_drift_is_provider_free_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        seventeen_track,
        "get_conn",
        lambda: _WorkerConn(assignment_project_id=92),
    )
    monkeypatch.setattr(seventeen_track.scope, "assert_project_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(seventeen_track, "_token", _bomb("assignment drift must precede token"))
    monkeypatch.setattr(seventeen_track, "_api", _bomb("assignment drift must not call 17TRACK"))

    result = seventeen_track.run_logistics_sync_for_job(_payload())

    assert result["status"] == "blocked:logistics_assignment_scope_drifted"


def test_worker_valid_single_project_revalidates_then_calls_provider(monkeypatch) -> None:
    conn = _WorkerConn()
    project_checks: list[tuple[int, int, bool]] = []
    provider_calls: list[tuple[str, list[dict]]] = []
    monkeypatch.setattr(seventeen_track, "get_conn", lambda: conn)
    monkeypatch.setattr(seventeen_track, "_token", lambda: "configured")
    monkeypatch.setattr(
        seventeen_track.scope,
        "assert_project_access",
        lambda project_id, staff, *, write=False: project_checks.append((project_id, staff["id"], write)),
    )

    def fake_api(path, numbers):
        provider_calls.append((path, numbers))
        if path == "register":
            return {"code": 0, "data": {"accepted": numbers, "rejected": []}}
        return {
            "code": 0,
            "data": {
                "accepted": [{
                    "number": "TRACK-801",
                    "track_info": {
                        "latest_status": {"status": "InTransit"},
                        "latest_event": {
                            "time_iso": "2026-08-21T12:00:00Z",
                            "description": "Departed facility",
                            "location": "New York",
                        },
                        "tracking": {"providers": []},
                    },
                }],
            },
        }

    monkeypatch.setattr(seventeen_track, "_api", fake_api)

    result = seventeen_track.run_logistics_sync_for_job(_payload())

    assert result["status"] == "ready"
    assert result["synced"] == 1
    assert project_checks == [(91, 73, True), (91, 73, True), (91, 73, True)]
    assert [item[0] for item in provider_calls] == ["register", "gettrackinfo"]
    assert all(item[1] == [{"number": "TRACK-801"}] for item in provider_calls)
    assert len(conn.updates) == 1
    assert conn.commits == 1


def test_register_then_project_revocation_blocks_second_provider_and_all_writes(monkeypatch) -> None:
    conn = _WorkerConn()
    project_checks = 0
    provider_calls: list[str] = []
    monkeypatch.setattr(seventeen_track, "get_conn", lambda: conn)
    monkeypatch.setattr(seventeen_track, "_token", lambda: "configured")

    def project_access(*_args, **_kwargs):
        nonlocal project_checks
        project_checks += 1
        if project_checks >= 3:
            raise seventeen_track.scope.ScopeDenied("revoked after register")

    def provider(path, numbers):
        provider_calls.append(path)
        assert numbers == [{"number": "TRACK-801"}]
        if path != "register":
            raise AssertionError("gettrackinfo must not run after revocation")
        return {"code": 0, "data": {"accepted": numbers, "rejected": []}}

    monkeypatch.setattr(seventeen_track.scope, "assert_project_access", project_access)
    monkeypatch.setattr(seventeen_track, "_api", provider)

    result = seventeen_track.run_logistics_sync_for_job(_payload())

    assert result == {
        "status": "blocked:logistics_project_permission_revoked",
        "reason": "logistics_project_permission_revoked",
        "provider_calls_performed": True,
    }
    assert provider_calls == ["register"]
    assert conn.updates == []
    assert conn.commits == 0


class _PgCursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).lower().split()), tuple(params)))


class _PgConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, *_args, **_kwargs):
        return _PgCursor(self.calls)


@contextmanager
def _sync_scope():
    yield


def test_worker_handler_marks_access_revocation_terminal_blocked(monkeypatch) -> None:
    conn = _PgConn()
    monkeypatch.setattr(apify_jobs_worker_handlers, "_resolve_job_staff", lambda *_args: {})
    monkeypatch.setattr(
        apify_jobs_worker_handlers,
        "db_connection_sync_scope",
        _sync_scope,
    )
    monkeypatch.setattr(
        seventeen_track,
        "run_logistics_sync_for_job",
        lambda *_args, **_kwargs: {
            "status": "blocked:logistics_project_permission_revoked",
            "reason": "logistics_project_permission_revoked",
            "provider_calls_performed": True,
        },
    )

    apify_jobs_worker_handlers._process_logistics_track_sync(
        conn,
        {"id": 909},
        _payload(),
    )

    assert len(conn.calls) == 1
    assert conn.calls[0][1][0] == "blocked"
    assert conn.calls[0][1][1] == "blocked:logistics_project_permission_revoked"
    stored_payload = json.loads(conn.calls[0][1][2])
    assert stored_payload["logistics_sync_result"]["provider_calls_performed"] is True
