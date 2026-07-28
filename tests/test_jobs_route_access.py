from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.auth import get_user_required  # noqa: E402
from app.api.routers import jobs as jobs_router  # noqa: E402
from app.main import app  # noqa: E402


class _Queue:
    def __init__(self, **overrides):
        self.overrides = overrides

    async def get_status(self, task_id: str):
        snapshot = {
            "task_id": task_id,
            "job_type": "video_analysis",
            "status": "done",
            "user_id": 7,
            "triggered_by_staff_id": 11,
            "payload_json": '{"secret":"must-not-leak"}',
            "result_json": '{"ok":true}',
            "result_path": "/private/result.json",
            "error_message": "private provider trace",
        }
        snapshot.update(self.overrides)
        return snapshot


@pytest.fixture(autouse=True)
def _restore_app_state():
    missing = object()
    previous_queue = getattr(app.state, "job_queue", missing)
    previous_overrides = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)
    if previous_queue is missing:
        app.state._state.pop("job_queue", None)
    else:
        app.state.job_queue = previous_queue


def _client(user: dict | None, *, queue: _Queue | None = None) -> TestClient:
    app.state.job_queue = queue or _Queue()
    if user is not None:
        app.dependency_overrides[get_user_required] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_job_status_requires_login() -> None:
    app.dependency_overrides.pop(get_user_required, None)
    response = _client(None).get("/api/jobs/t-1")
    assert response.status_code == 401


def test_job_status_hides_other_users_jobs() -> None:
    response = _client({"id": 8, "staff_id": 12, "role": "employee"}).get("/api/jobs/t-1")
    assert response.status_code == 404


def test_job_owner_gets_only_safe_status_fields() -> None:
    response = _client({"id": 7, "staff_id": 11, "role": "employee"}).get("/api/jobs/t-1")
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == {"ok": True}
    assert "payload_json" not in body
    assert "result_path" not in body
    assert "error_message" not in body
    assert "user_id" not in body


def test_job_status_decodes_double_encoded_result_object() -> None:
    encoded = json.dumps(json.dumps({"ok": True, "items": [1, 2]}))
    response = _client(
        {"id": 7, "staff_id": 11, "role": "employee"},
        queue=_Queue(result_json=encoded, result_path=""),
    ).get("/api/jobs/t-1")

    assert response.status_code == 200
    assert response.json()["result"] == {"ok": True, "items": [1, 2]}


def test_job_status_loads_double_encoded_internal_result_pointer(monkeypatch) -> None:
    encoded = json.dumps(
        json.dumps({"status": "done", "result_path": "/safe/job-results/t-1.json"})
    )
    loaded_paths = []
    monkeypatch.setattr(
        jobs_router,
        "load_job_result",
        lambda path: loaded_paths.append(path) or {"videos": [{"id": "v-1"}]},
    )

    response = _client(
        {"id": 7, "staff_id": 11, "role": "employee"},
        queue=_Queue(result_json=encoded, result_path=""),
    ).get("/api/jobs/t-1")

    assert response.status_code == 200
    assert response.json()["result"] == {"videos": [{"id": "v-1"}]}
    assert loaded_paths == ["/safe/job-results/t-1.json"]
    assert "result_path" not in response.json()


def test_job_status_does_not_leak_rejected_result_pointer(monkeypatch) -> None:
    encoded = json.dumps({"status": "done", "result_path": "/etc/arbitrary.json"})

    def reject_path(_path):
        raise ValueError("result path outside configured job results directory")

    monkeypatch.setattr(jobs_router, "load_job_result", reject_path)
    response = _client(
        {"id": 7, "staff_id": 11, "role": "employee"},
        queue=_Queue(result_json=encoded, result_path=""),
    ).get("/api/jobs/t-1")

    assert response.status_code == 200
    body = response.json()
    assert "result" not in body
    assert "result_path" not in body
    assert "/etc/arbitrary.json" not in response.text
    assert "outside configured" not in response.text


def test_job_status_preserves_normal_result_object_with_result_path_field(monkeypatch) -> None:
    normal_result = {
        "status": "done",
        "result_path": "customer-visible/report.json",
        "summary": {"count": 3},
    }

    def unexpected_load(_path):
        raise AssertionError("normal result objects must not be treated as pointers")

    monkeypatch.setattr(jobs_router, "load_job_result", unexpected_load)
    response = _client(
        {"id": 7, "staff_id": 11, "role": "employee"},
        queue=_Queue(result_json=normal_result, result_path=""),
    ).get("/api/jobs/t-1")

    assert response.status_code == 200
    assert response.json()["result"] == normal_result
