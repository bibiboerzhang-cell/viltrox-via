from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any
from unittest.mock import patch

import pytest

from app.domains.kol import content_fit_analysis
from app.domains.tasks.apify_idempotency import active_job_idempotency_key
from app.workers.apify_jobs_worker_session import _enqueue_content_fit_after_final_v1


class _Cursor:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((sql, tuple(params)))

    def fetchone(self) -> Any:
        return self.rows.pop(0) if self.rows else None


class _Conn:
    def __init__(self, rows: list[Any]) -> None:
        self.cursor_obj = _Cursor(rows)

    def transaction(self) -> Any:
        return nullcontext()

    def cursor(self, **_kwargs: Any) -> _Cursor:
        return self.cursor_obj


def _run(
    rows: list[Any], *, product_sku: str = "",
) -> tuple[dict[str, Any] | None, _Cursor]:
    conn = _Conn(rows)
    # Authorization lineage has its own security contract tests; this file
    # isolates product namespace/dedupe behavior with an already-authorized child.
    with patch(
        "app.domains.kol.content_fit_job_access.authorize_content_fit_followup",
        side_effect=lambda payload, **_kwargs: payload,
    ):
        result = _enqueue_content_fit_after_final_v1(
            conn,  # type: ignore[arg-type]
            job_id=91,
            deep_result={"status": "ready", "kol_pool_id": 42},
            source_payload={"product_sku": product_sku},
        )
    return result, conn.cursor_obj


def _queued_rows(sku: str) -> list[Any]:
    del sku
    return [
        None,  # no same-scope cache
        None,  # no same-scope active job
        {"id": 701, "status": "queued"},  # INSERT RETURNING
    ]


def test_evo_and_pro_each_get_distinct_scoped_followup() -> None:
    evo, evo_cur = _run(_queued_rows("af-35-evo"), product_sku=" af-35-evo ")
    pro, pro_cur = _run(_queued_rows("AF-35-PRO"), product_sku="AF-35-PRO")

    assert evo is not None and pro is not None
    assert evo["status"] == pro["status"] == "queued"
    assert evo["product_sku"] == "AF-35-EVO"
    assert pro["product_sku"] == "AF-35-PRO"
    assert evo["derive_method"] != pro["derive_method"]

    evo_payload = json.loads(evo_cur.calls[2][1][0])
    pro_payload = json.loads(pro_cur.calls[2][1][0])
    assert evo_payload["product_sku"] == "AF-35-EVO"
    assert pro_payload["product_sku"] == "AF-35-PRO"
    assert evo_cur.calls[2][1][1] == active_job_idempotency_key(
        "kol_content_fit_analysis", "authorization-missing", 42, "AF-35-EVO"
    )
    assert pro_cur.calls[2][1][1] == active_job_idempotency_key(
        "kol_content_fit_analysis", "authorization-missing", 42, "AF-35-PRO"
    )


def test_same_sku_active_job_is_reused() -> None:
    result, cur = _run([
        None,
        {"id": 808, "status": "running"},
    ], product_sku="af-35-pro")

    assert result == {
        "status": "already_running",
        "job_id": 808,
        "kol_pool_id": 42,
        "product_sku": "AF-35-PRO",
        "derive_method": content_fit_analysis.content_fit_derive_method("AF-35-PRO"),
    }
    assert len(cur.calls) == 2
    assert "idempotency_key=%s" in cur.calls[1][0]


def test_generic_followup_keeps_historical_cache_namespace() -> None:
    result, cur = _run([
        {"exists": 1},  # historical generic cache
    ])

    assert result == {
        "status": "cache_reused",
        "kol_pool_id": 42,
        "product_sku": None,
        "derive_method": "content_fit_v1",
    }
    assert cur.calls[0][1] == ("content_fit_v1", "42")


def test_generic_cache_does_not_block_product_scoped_followup() -> None:
    result, cur = _run(_queued_rows("AF-75-PRO"), product_sku="AF-75-PRO")

    scoped = content_fit_analysis.content_fit_derive_method("AF-75-PRO")
    assert result is not None and result["status"] == "queued"
    assert scoped != "content_fit_v1"
    assert cur.calls[0][1] == (scoped, "42")
    assert "idempotency_key=%s" in cur.calls[1][0]


def test_parent_authorization_runs_before_any_cache_or_job_lookup() -> None:
    conn = _Conn([])

    def denied(_payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        assert conn.cursor_obj.calls == []
        raise PermissionError("revoked")

    with patch(
        "app.domains.kol.content_fit_job_access.authorize_content_fit_followup",
        side_effect=denied,
    ), pytest.raises(PermissionError, match="revoked"):
        _enqueue_content_fit_after_final_v1(
            conn,  # type: ignore[arg-type]
            job_id=91,
            deep_result={"status": "ready", "kol_pool_id": 42},
            source_payload={"product_sku": "AF-35-PRO"},
        )
    assert conn.cursor_obj.calls == []


def test_active_followup_scope_differs_across_session_owners() -> None:
    from app.workers.content_fit_followup_enqueue import _authorization_scope

    def payload(user_id: int, session_id: int) -> dict[str, Any]:
        return {
            "search_session_item_id": 7,
            "kol_provider_job_fence": {
                "mode": "user",
                "target_id": "42",
                "actor": {"user_id": user_id},
                "session": {"search_session_id": session_id},
            },
        }

    first = active_job_idempotency_key(
        "kol_content_fit_analysis", _authorization_scope(payload(34, 55)), 42, "AF-35-PRO"
    )
    second = active_job_idempotency_key(
        "kol_content_fit_analysis", _authorization_scope(payload(35, 56)), 42, "AF-35-PRO"
    )
    assert first != second
