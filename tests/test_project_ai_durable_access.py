from __future__ import annotations

import inspect
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_projects
from app.domains.access import scope
from app.domains.projects import ai_job_access, contract_assist, retrospective_aggregate
from app.workers import apify_jobs_worker_handlers
from app.services.scheduler import jobs_tasks


class _Rows:
    def __init__(self, row: dict[str, Any] | None):
        self.row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _AccessConn:
    def __init__(self) -> None:
        self.active = True
        self.user_status = "active"
        self.project_exists = True
        self.event_exists = True

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        if "FROM staff s JOIN users u" in sql:
            return _Rows(
                {
                    "id": 11,
                    "staff_id": 11,
                    "user_id": 22,
                    "active": self.active,
                    "suspended_at": None,
                    "user_status": self.user_status,
                    "role": "employee",
                    "permissions_json": {"vkpi": "write"},
                }
            )
        if "FROM vkpi_projects" in sql:
            return _Rows({"id": 7} if self.project_exists else None)
        if "FROM vkpi_events" in sql:
            return _Rows({"id": "event-7"} if self.event_exists else None)
        raise AssertionError(sql)


class _WorkerCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_WorkerCursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))


class _WorkerConn:
    def __init__(self) -> None:
        self.worker_cursor = _WorkerCursor()

    def transaction(self):
        return nullcontext()

    def cursor(self, **_kwargs: Any) -> _WorkerCursor:
        return self.worker_cursor


def _allow_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_job_access.scope, "assert_project_access", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_job_access.scope, "assert_event_access", lambda *_a, **_k: None)


def _staff() -> dict[str, Any]:
    return {
        "id": 11,
        "staff_id": 11,
        "user_id": 22,
        "role": "employee",
        "permissions_json": {"vkpi": "write"},
    }


def _invoice_payload(path: Path, root: Path, *, event: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "target_type": "event" if event else "project",
        "target_id": "event-7" if event else 7,
        "event_id": "event-7" if event else None,
        "project_id": None if event else 7,
        "extract_key": "extract-7",
        "file_url": "/uploads/vkpi_evidence/invoice.pdf",
        "file_name": "invoice.pdf",
        "derive_method": contract_assist.INVOICE_EXTRACT_DERIVE_METHOD,
        "triggered_by_user_id": 22,
        "staff_id": 11,
    }
    payload[ai_job_access.FILE_IDENTITY_KEY] = ai_job_access.capture_file_identity(path, root=root)
    return payload


def _contract_payload() -> dict[str, Any]:
    return {
        "target_type": "contract_polish",
        "target_id": "polish-7",
        "project_id": 7,
        "polish_key": "polish-7",
        "template_key": "",
        "fields": {"deliverables": "One video"},
        "derive_method": contract_assist.CONTRACT_POLISH_DERIVE_METHOD,
        "triggered_by_user_id": 22,
        "staff_id": 11,
    }


def _seal_user_payload(
    monkeypatch: pytest.MonkeyPatch,
    conn: _AccessConn,
    payload: dict[str, Any],
    *,
    action: str,
) -> None:
    _allow_scope(monkeypatch)
    monkeypatch.setattr(ai_job_access, "get_conn", lambda: conn)
    payload[ai_job_access.FENCE_KEY] = ai_job_access.build_job_fence(
        payload, action=action, staff=_staff()
    )


def test_invoice_file_drift_blocks_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"original invoice")
    conn = _AccessConn()
    payload = _invoice_payload(path, tmp_path)
    _seal_user_payload(monkeypatch, conn, payload, action=ai_job_access.INVOICE_EXTRACT)
    path.write_bytes(b"tampered invoice")
    monkeypatch.setattr(contract_assist, "EVIDENCE_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(contract_assist, "_evidence_path_from_url", lambda _url: path)
    monkeypatch.setattr(
        contract_assist,
        "_extract_invoice_with_timeout",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must stay at zero")),
    )

    result = contract_assist.run_invoice_extract_for_job(
        payload, staff=_staff(), enforce_access_fence=True
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "invoice_file_identity_drifted"
    assert result["provider_calls_performed"] is False
    assert result["retryable"] is False


def test_event_scope_revocation_blocks_invoice_before_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"event invoice")
    conn = _AccessConn()
    payload = _invoice_payload(path, tmp_path, event=True)
    _seal_user_payload(monkeypatch, conn, payload, action=ai_job_access.INVOICE_EXTRACT)
    monkeypatch.setattr(
        ai_job_access.scope,
        "assert_event_access",
        lambda *_a, **_k: (_ for _ in ()).throw(scope.ScopeDenied("revoked")),
    )
    monkeypatch.setattr(contract_assist, "EVIDENCE_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(contract_assist, "_evidence_path_from_url", lambda _url: path)
    monkeypatch.setattr(
        contract_assist,
        "_extract_invoice_with_timeout",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must stay at zero")),
    )

    result = contract_assist.run_invoice_extract_for_job(
        payload, staff=_staff(), enforce_access_fence=True
    )

    assert result == {
        "status": "blocked",
        "reason": "event_ai_permission_revoked",
        "provider_calls_performed": False,
        "retryable": False,
    }


def test_revoked_actor_blocks_contract_polish_before_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _AccessConn()
    payload = _contract_payload()
    _seal_user_payload(monkeypatch, conn, payload, action=ai_job_access.CONTRACT_POLISH)
    conn.active = False
    monkeypatch.setattr(
        contract_assist.llm_production,
        "generate_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM must stay at zero")),
    )

    result = contract_assist.run_contract_polish_for_job(
        payload, staff=_staff(), enforce_access_fence=True
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "project_ai_actor_inactive"
    assert result["provider_calls_performed"] is False
    assert result["retryable"] is False


def test_contract_post_provider_revocation_blocks_cache_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def revalidate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ai_job_access.ProjectAiAccessError("project_ai_permission_revoked")
        return _staff()

    monkeypatch.setattr(ai_job_access, "revalidate_job_fence", revalidate)
    monkeypatch.setattr(
        contract_assist.llm_production,
        "generate_json",
        lambda *_a, **_k: {
            "status": "success",
            "provider": "openai",
            "model": contract_assist.OPENAI_MODEL,
            "json": {"deliverables": "One professionally worded video."},
        },
    )
    monkeypatch.setattr(
        contract_assist,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("cache must not be written")),
    )

    result = contract_assist.run_contract_polish_for_job(
        _contract_payload(), staff=_staff(), enforce_access_fence=True
    )

    assert calls == 2
    assert result["status"] == "blocked"
    assert result["provider_calls_performed"] is True
    assert result["retryable"] is False


def test_legacy_retrospective_job_without_fence_is_terminal_provider_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        retrospective_aggregate,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("legacy job must stop before DB reads")),
    )
    monkeypatch.setattr(
        retrospective_aggregate.llm_production,
        "generate_json",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("LLM must stay at zero")),
    )

    result = retrospective_aggregate.run_project_retrospective(
        7,
        access_payload={"project_id": 7, "target_id": "7"},
    )

    assert result == {
        "status": "blocked",
        "reason": "project_ai_fence_missing",
        "provider_calls_performed": False,
        "retryable": False,
    }


def test_scheduler_capability_is_opaque_and_server_fence_is_verifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _AccessConn()
    monkeypatch.setattr(ai_job_access, "get_conn", lambda: conn)
    payload = {
        "target_type": "project",
        "target_id": "7",
        "project_id": 7,
        "derive_method": retrospective_aggregate.DERIVE_METHOD,
        "analysis_kind": "project_llm",
        "triggered_by_user_id": None,
        "staff_id": None,
    }
    capability = ai_job_access.issue_server_project_ai_capability(
        action=ai_job_access.PROJECT_RETROSPECTIVE, project_id=7
    )
    payload[ai_job_access.FENCE_KEY] = ai_job_access.build_job_fence(
        payload,
        action=ai_job_access.PROJECT_RETROSPECTIVE,
        staff=None,
        server_capability=capability,
    )

    actor = ai_job_access.revalidate_job_fence(
        payload, action=ai_job_access.PROJECT_RETROSPECTIVE, conn=conn
    )

    assert actor["server_owned"] is True
    with pytest.raises(ai_job_access.ProjectAiAccessError, match="server_project_ai_capability_invalid"):
        ai_job_access.build_job_fence(
            payload,
            action=ai_job_access.PROJECT_RETROSPECTIVE,
            staff=None,
            server_capability={"action": ai_job_access.PROJECT_RETROSPECTIVE},  # type: ignore[arg-type]
        )


def test_scheduler_uses_explicit_capability_not_fake_staff() -> None:
    source = inspect.getsource(jobs_tasks._enqueue_due_retrospectives)
    assert "issue_server_project_ai_capability" in source
    assert "server_capability=capability" in source
    assert "staff=_scheduler_system_staff()" not in source


def test_event_only_invoice_result_requires_event_read_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contract_assist,
        "get_invoice_extract",
        lambda _key: {"state": "ready", "result": {"event_id": "event-7", "extracted": {"swift": "masked"}}},
    )
    seen: list[str] = []
    monkeypatch.setattr(vkpi_projects.policy, "require_event_read", lambda target, _staff: seen.append(target))
    monkeypatch.setattr(
        vkpi_projects.policy,
        "require_project_read",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("event result is not a project")),
    )

    result = vkpi_projects.project_invoice_extract("extract-7", staff=_staff())

    assert result["state"] == "ready"
    assert seen == ["event-7"]


def test_event_only_invoice_result_denial_maps_to_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contract_assist,
        "get_invoice_extract",
        lambda _key: {"state": "ready", "result": {"event_id": "event-7"}},
    )
    monkeypatch.setattr(
        vkpi_projects.policy,
        "require_event_read",
        lambda *_a, **_k: (_ for _ in ()).throw(vkpi_projects.policy.ScopeDenied("denied")),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_projects.project_invoice_extract("extract-7", staff=_staff())

    assert exc_info.value.status_code == 403


def test_ready_ai_results_without_scope_identity_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        contract_assist,
        "get_invoice_extract",
        lambda _key: {"state": "ready", "result": {"extracted": {"swift": "sensitive"}}},
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_projects.project_invoice_extract("legacy", staff=_staff())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invoice result scope unavailable"


@pytest.mark.parametrize("handler_name", ["invoice", "polish"])
def test_contract_ai_worker_block_is_terminal_and_enforces_fence(
    monkeypatch: pytest.MonkeyPatch, handler_name: str
) -> None:
    conn = _WorkerConn()
    payload = {"staff_id": 11, "triggered_by_user_id": 22}
    seen: dict[str, Any] = {}

    def blocked(_payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "blocked", "reason": "project_ai_permission_revoked"}

    monkeypatch.setattr(apify_jobs_worker_handlers, "db_connection_sync_scope", nullcontext)
    if handler_name == "invoice":
        monkeypatch.setattr(contract_assist, "run_invoice_extract_for_job", blocked)
        apify_jobs_worker_handlers._process_contract_invoice_extract(conn, {"id": 91}, payload)
    else:
        monkeypatch.setattr(contract_assist, "run_contract_polish_for_job", blocked)
        apify_jobs_worker_handlers._process_contract_polish(conn, {"id": 92}, payload)

    assert seen["enforce_access_fence"] is True
    assert conn.worker_cursor.calls[-1][1][0] == "blocked"
    assert conn.worker_cursor.calls[-1][1][1] == "project_ai_permission_revoked"


def test_retrospective_worker_block_is_terminal_and_passes_access_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _WorkerConn()
    payload = {"target_id": "7", "project_id": 7, "staff_id": 11, "triggered_by_user_id": 22}
    seen: dict[str, Any] = {}

    def blocked(_project_id: int, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"status": "blocked", "reason": "project_ai_permission_revoked"}

    monkeypatch.setattr(apify_jobs_worker_handlers, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(retrospective_aggregate, "run_project_retrospective", blocked)

    apify_jobs_worker_handlers._process_project_retrospective(conn, {"id": 93}, payload)

    assert seen["access_payload"] is payload
    assert conn.worker_cursor.calls[-1][1][0] == "blocked"
    assert conn.worker_cursor.calls[-1][1][1] == "project_ai_permission_revoked"
