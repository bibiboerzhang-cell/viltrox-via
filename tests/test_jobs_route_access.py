from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.dependencies.auth import get_user_required  # noqa: E402
from app.main import app  # noqa: E402


class _Queue:
    async def get_status(self, task_id: str):
        return {
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


def _client(user: dict | None) -> TestClient:
    app.state.job_queue = _Queue()
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
