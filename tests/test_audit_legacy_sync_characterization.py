"""Observable-contract tests for the retired synchronous audit boundary."""
from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers.audit_legacy_sync import run_audit_sync
from scripts.vkpi_engineering_health_collect import collect_complexity


def _request(job_queue):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(job_queue=job_queue))
    )


def test_queue_branch_preserves_async_result_and_deprecation_fields() -> None:
    request = _request(object())
    req = object()
    current_user = {"id": 17}
    calls: list[tuple[object, object, object]] = []
    response = {"status": "queued", "job_id": "audit-42"}

    async def audit_async_func(actual_request, actual_req, actual_user):
        calls.append((actual_request, actual_req, actual_user))
        return response

    result = asyncio.run(
        run_audit_sync(request, req, current_user, audit_async_func)
    )

    assert calls == [(request, req, current_user)]
    assert result is response
    assert result == {
        "status": "queued",
        "job_id": "audit-42",
        "deprecated_sync": True,
        "message": "Synchronous audit is deprecated; request was queued instead.",
    }


def test_missing_queue_fails_closed_without_calling_async_path() -> None:
    calls = 0

    async def audit_async_func(*_args):
        nonlocal calls
        calls += 1
        raise AssertionError("durable audit callback must not run without a queue")

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            run_audit_sync(_request(None), object(), None, audit_async_func)
        )

    assert calls == 0
    assert captured.value.status_code == 503
    assert captured.value.detail == "durable job queue unavailable"


def test_queue_branch_preserves_async_exception() -> None:
    expected = RuntimeError("queue rejected audit")

    async def audit_async_func(*_args):
        raise expected

    with pytest.raises(RuntimeError) as captured:
        asyncio.run(
            run_audit_sync(_request(object()), object(), None, audit_async_func)
        )

    assert captured.value is expected


def test_sync_boundary_has_no_unreachable_tail_or_provider_database_imports() -> None:
    module_path = Path(inspect.getsourcefile(run_audit_sync) or "")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_audit_sync"
    )

    assert isinstance(function.body[-1], ast.Raise)
    imported_modules = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        name.startswith(
            (
                "app.db",
                "app.services.ai",
                "app.services.scraping",
                "app.services.audit",
            )
        )
        for name in imported_modules
    )


def test_run_audit_sync_complexity_and_signature_remain_bounded() -> None:
    module_path = Path(inspect.getsourcefile(run_audit_sync) or "")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    rows = collect_complexity({str(module_path): tree})
    complexity = next(
        row for row in rows if row.qualified_name == "run_audit_sync"
    )

    assert complexity.cc <= 30
    assert list(inspect.signature(run_audit_sync).parameters) == [
        "request",
        "req",
        "current_user",
        "audit_async_func",
    ]
