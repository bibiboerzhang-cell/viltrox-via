from __future__ import annotations

import asyncio
import ast
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import audit as audit_router
from app.schemas.audit import AuditRequest, UploadedVideoInput
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
AUDIT_SUBMIT_FAMILY = (
    ROOT / "backend/app/api/routers/audit.py",
    ROOT / "backend/app/api/routers/audit_submit_runtime.py",
)


class _Logger:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def info(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log.info", message, kwargs.get("extra")))

    def debug(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log.debug", message))

    def warning(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log.warning", message, kwargs.get("extra")))

    def exception(self, message: str, **kwargs: Any) -> None:
        self.events.append(("log.exception", message, kwargs.get("extra")))


class _Queue:
    def __init__(self, events: list[Any], *, failure: Exception | None = None) -> None:
        self.events = events
        self.failure = failure

    async def enqueue(self, kind: str, job: Any, **kwargs: Any) -> str:
        self.events.append(("enqueue", kind, job, kwargs))
        if self.failure is not None:
            raise self.failure
        return "task-123"


def _request(queue: Any) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(job_queue=queue))
    )


def _original_audit_async() -> Any:
    return getattr(audit_router.audit_async, "__wrapped__", audit_router.audit_async)


def _install_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    similarity: dict[str, Any] | None = None,
    publish_check: dict[str, Any] | None = None,
    resolved_upload: dict[str, Any] | None = None,
    bound_asset: dict[str, Any] | None = None,
    queue_failure: Exception | None = None,
    emit_failure: Exception | None = None,
    record_failure: Exception | None = None,
) -> tuple[list[Any], _Queue]:
    events: list[Any] = []
    queue = _Queue(events, failure=queue_failure)

    def guard(*args: Any) -> None:
        events.append(("guard", args[2]))

    async def to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        events.append(("thread", func.__name__))
        return func(*args, **kwargs)

    async def queue_pressure(actual_queue: Any, *, job_type: str) -> dict[str, Any]:
        events.append(("pressure", actual_queue is queue, job_type))
        return {"lane": "audit", "pressure": 0.25}

    def valid_url(value: str) -> bool:
        events.append(("valid_url", value))
        return not value.startswith("invalid")

    def detect_platform(value: str) -> str:
        events.append(("detect_platform", value))
        return "YouTube"

    def extract_handle(value: str) -> str:
        events.append(("extract_handle", value))
        return "@creator"

    def check_similarity(*args: Any) -> dict[str, Any]:
        events.append(("similarity", args))
        return dict(similarity or {})

    def validate_publish(*args: Any) -> dict[str, Any]:
        events.append(("publish", args))
        return dict(publish_check or {"valid": True})

    def resolve_upload(value: Any) -> dict[str, Any] | None:
        events.append(("resolve_upload", value))
        return dict(resolved_upload) if resolved_upload is not None else None

    def create_stub(*args: Any) -> int:
        events.append(("create_stub", args))
        return 77

    def bind_asset(*args: Any) -> dict[str, Any] | None:
        events.append(("bind_asset", args))
        return dict(bound_asset) if bound_asset is not None else None

    async def db_read(operation: partial[Any]) -> Any:
        events.append(("db_read", operation.func.__name__))
        return operation()

    async def db_write(operation: partial[Any]) -> Any:
        events.append(("db_write", operation.func.__name__))
        return operation()

    def emit(**kwargs: Any) -> None:
        events.append(("emit", kwargs))
        if emit_failure is not None:
            raise emit_failure

    def record(submission_id: int, user_id: int | None) -> None:
        events.append(("record", submission_id, user_id))
        if record_failure is not None:
            raise record_failure

    def normalize(payload: dict[str, Any]) -> dict[str, Any]:
        events.append(("normalize", payload))
        return {**payload, "normalized": True}

    def job_input(**kwargs: Any) -> dict[str, Any]:
        events.append(("job", kwargs))
        return {"job": kwargs}

    monkeypatch.setattr(audit_router, "logger", _Logger(events))
    monkeypatch.setattr(audit_router.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(audit_router, "enforce_dynamic_submission_guard", guard)
    monkeypatch.setattr(audit_router, "enforce_queue_backpressure", queue_pressure)
    monkeypatch.setattr(audit_router, "valid_url", valid_url)
    monkeypatch.setattr(audit_router, "detect_platform", detect_platform)
    monkeypatch.setattr(audit_router, "extract_handle_from_url", extract_handle)
    monkeypatch.setattr(audit_router, "_check_similarity_sync", check_similarity)
    monkeypatch.setattr(audit_router, "validate_video_publish_date", validate_publish)
    monkeypatch.setattr(audit_router, "_resolve_uploaded_video_payload_sync", resolve_upload)
    monkeypatch.setattr(audit_router, "_create_submission_stub_sync", create_stub)
    monkeypatch.setattr(audit_router, "_bind_uploaded_asset_sync", bind_asset)
    monkeypatch.setattr(audit_router, "db_read", db_read)
    monkeypatch.setattr(audit_router, "db_write", db_write)
    monkeypatch.setattr(audit_router, "_emit_submission_to_party_layer", emit)
    monkeypatch.setattr(audit_router, "record_submission", record)
    monkeypatch.setattr(audit_router, "normalize_uploaded_video_payload", normalize)
    monkeypatch.setattr(audit_router, "VideoJobInput", job_input)
    return events, queue


def test_audit_async_url_success_preserves_effect_order_and_job_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, queue = _install_harness(monkeypatch)
    req = AuditRequest(
        url="https://youtube.com/watch?v=abc",
        user_handle="creator",
        linked_handles={"youtube": "@creator"},
        title="  title  ",
        caption=" caption ",
        raw_text=" raw ",
    )

    result = asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert result == {
        "status": "queued",
        "job_id": "task-123",
        "submission_id": 77,
        "platform": "YouTube",
        "extracted_handle": "@creator",
        "message": "Analysis started — poll /api/submissions/{id}/status for results",
        "queue": {"lane": "audit", "pressure": 0.25},
    }
    assert [event[0] for event in events] == [
        "thread",
        "guard",
        "valid_url",
        "pressure",
        "detect_platform",
        "extract_handle",
        "db_read",
        "similarity",
        "thread",
        "publish",
        "db_read",
        "resolve_upload",
        "log.info",
        "db_write",
        "create_stub",
        "emit",
        "record",
        "job",
        "enqueue",
    ]
    created = next(event for event in events if event[0] == "create_stub")
    assert created[1] == (
        9,
        req.url,
        "title",
        "caption",
        "raw",
        "YouTube",
        "@creator",
        "",
        "",
    )
    job = next(event[1] for event in events if event[0] == "job")
    assert job["submission_id"] == 77
    assert job["handle"] == "@creator"
    assert job["hints"] == req.hints.model_dump()
    assert job["metrics"] == req.metrics.model_dump()


@pytest.mark.parametrize(
    ("req", "status_code", "detail", "expected_events"),
    [
        (
            AuditRequest(),
            400,
            "URL or uploaded video required",
            ["thread", "guard"],
        ),
        (
            AuditRequest(url="invalid://url"),
            400,
            "Invalid URL",
            ["thread", "guard", "valid_url"],
        ),
    ],
)
def test_audit_async_validation_errors_preserve_guard_first(
    monkeypatch: pytest.MonkeyPatch,
    req: AuditRequest,
    status_code: int,
    detail: str,
    expected_events: list[str],
) -> None:
    events, queue = _install_harness(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
    assert [event[0] for event in events] == expected_events


def test_audit_async_ownership_rejection_stops_before_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, queue = _install_harness(monkeypatch)
    monkeypatch.setattr(
        audit_router,
        "extract_handle_from_url",
        lambda _url: "@outsider",
    )
    req = AuditRequest(
        url="https://youtube.com/watch?v=abc",
        linked_handles={"youtube": "@linked"},
    )

    result = asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert result["status"] == "rejected"
    assert result["rejection_code"] == "ownership_mismatch"
    assert result["extracted_handle"] == "@outsider"
    assert result["linked_handles"] == {"youtube": "@linked"}
    assert "@outsider" in result["rejection_reason"]
    assert "similarity" not in [event[0] for event in events]
    assert [event[0] for event in events[:4]] == [
        "thread",
        "guard",
        "valid_url",
        "pressure",
    ]


@pytest.mark.parametrize(
    ("similarity", "publish_check", "code"),
    [
        (
            {"hard_reject": True, "reason": "duplicate"},
            {"valid": True},
            "duplicate_or_spam",
        ),
        (
            {},
            {"valid": False, "reason": "too old", "observed_at": "2025-01-01"},
            "stale_video",
        ),
    ],
)
def test_audit_async_fast_rejections_preserve_stage_boundary(
    monkeypatch: pytest.MonkeyPatch,
    similarity: dict[str, Any],
    publish_check: dict[str, Any],
    code: str,
) -> None:
    events, queue = _install_harness(
        monkeypatch,
        similarity=similarity,
        publish_check=publish_check,
    )
    req = AuditRequest(
        url="https://youtube.com/watch?v=abc",
        linked_handles={"youtube": "@creator"},
    )

    result = asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert result["status"] == "rejected"
    assert result["rejection_code"] == code
    names = [event[0] for event in events]
    assert "create_stub" not in names
    if code == "duplicate_or_spam":
        assert "publish" not in names
    else:
        assert result["publish_date_check"] == publish_check


def test_audit_async_upload_binding_preserves_payload_and_skips_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = {
        "asset_id": 4,
        "analysis_path": "/tmp/video.mp4",
        "filename": "video.mp4",
        "storage_key": "old-key",
        "r2_key": "old-r2",
    }
    events, queue = _install_harness(
        monkeypatch,
        resolved_upload=resolved,
        bound_asset={"id": 8, "storage_key": "videos/bound.mp4"},
    )
    req = AuditRequest(
        user_handle="creator",
        title="",
        uploaded_video=UploadedVideoInput(
            asset_id=4,
            filename="video.mp4",
        ),
    )

    result = asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert result["status"] == "queued"
    assert result["platform"] == "Uploaded Video"
    assert result["extracted_handle"] == "@creator"
    names = [event[0] for event in events]
    assert "publish" not in names
    assert names.index("create_stub") < names.index("bind_asset")
    assert names.index("bind_asset") < names.index("normalize") < names.index("job")
    job = next(event[1] for event in events if event[0] == "job")
    assert job["uploaded_video"]["asset_id"] == 8
    assert job["uploaded_video"]["storage_key"] == "videos/bound.mp4"
    assert job["uploaded_video"]["r2_key"] == "videos/bound.mp4"
    assert job["uploaded_video"]["normalized"] is True


def test_audit_async_best_effort_failures_and_enqueue_failure_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, queue = _install_harness(
        monkeypatch,
        emit_failure=RuntimeError("party unavailable"),
        record_failure=RuntimeError("activity unavailable"),
        queue_failure=RuntimeError("queue unavailable"),
    )
    req = AuditRequest(
        url="https://youtube.com/watch?v=abc",
        linked_handles={"youtube": "@creator"},
    )

    result = asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    assert result["status"] == "queued"
    assert result["job_id"] == "enqueue_failed"
    names = [event[0] for event in events]
    assert "log.debug" in names
    assert "log.warning" in names
    assert "log.exception" in names
    assert names.index("emit") < names.index("record") < names.index("enqueue")


def test_audit_async_similarity_failure_propagates_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, queue = _install_harness(monkeypatch)

    async def failed_read(operation: partial[Any]) -> Any:
        events.append(("db_read_failed", operation.func.__name__))
        raise RuntimeError("similarity unavailable")

    monkeypatch.setattr(audit_router, "db_read", failed_read)
    req = AuditRequest(
        url="https://youtube.com/watch?v=abc",
        linked_handles={"youtube": "@creator"},
    )

    with pytest.raises(RuntimeError, match="similarity unavailable"):
        asyncio.run(_original_audit_async()(_request(queue), req, {"id": 9}))

    names = [event[0] for event in events]
    assert names[-1] == "db_read_failed"
    assert "create_stub" not in names
    assert "enqueue" not in names


def test_audit_submit_routes_and_refactor_bounds_remain_stable() -> None:
    routes = {
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__name__)
        for route in audit_router.router.routes
    }
    assert (
        "/api/audit",
        ("POST",),
        "audit_async",
    ) in routes
    assert (
        "/api/audit/v2",
        ("POST",),
        "audit_async",
    ) in routes

    trees = {
        str(path): ast.parse(path.read_text(encoding="utf-8"))
        for path in AUDIT_SUBMIT_FAMILY
    }
    rows = collect_complexity(trees)
    facade = next(
        row
        for row in rows
        if row.path.endswith("api/routers/audit.py")
        and row.qualified_name == "audit_async"
    )
    assert facade.cc <= 10
    assert max(row.cc for row in rows) <= 30
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) < 800
        for path in AUDIT_SUBMIT_FAMILY
    )
    leaf_source = AUDIT_SUBMIT_FAMILY[1].read_text(encoding="utf-8")
    assert "from app." not in leaf_source
    assert "import app." not in leaf_source
