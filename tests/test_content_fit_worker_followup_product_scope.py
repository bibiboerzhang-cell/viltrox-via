from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

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


def _run(rows: list[Any]) -> tuple[dict[str, Any] | None, _Cursor]:
    conn = _Conn(rows)
    result = _enqueue_content_fit_after_final_v1(
        conn,  # type: ignore[arg-type]
        job_id=91,
        deep_result={"status": "ready", "kol_pool_id": 42},
    )
    return result, conn.cursor_obj


def _queued_rows(sku: str) -> list[Any]:
    return [
        {"sku": sku},  # latest search product
        None,  # no same-scope cache
        None,  # no same-scope active job
        {"id": 701, "status": "queued"},  # INSERT RETURNING
    ]


def test_evo_and_pro_each_get_distinct_scoped_followup() -> None:
    evo, evo_cur = _run(_queued_rows(" af-35-evo "))
    pro, pro_cur = _run(_queued_rows("AF-35-PRO"))

    assert evo is not None and pro is not None
    assert evo["status"] == pro["status"] == "queued"
    assert evo["product_sku"] == "AF-35-EVO"
    assert pro["product_sku"] == "AF-35-PRO"
    assert evo["derive_method"] != pro["derive_method"]

    evo_payload = json.loads(evo_cur.calls[3][1][0])
    pro_payload = json.loads(pro_cur.calls[3][1][0])
    assert evo_payload["product_sku"] == "AF-35-EVO"
    assert pro_payload["product_sku"] == "AF-35-PRO"
    assert evo_cur.calls[3][1][1] == active_job_idempotency_key(
        "kol_content_fit_analysis", 42, "AF-35-EVO"
    )
    assert pro_cur.calls[3][1][1] == active_job_idempotency_key(
        "kol_content_fit_analysis", 42, "AF-35-PRO"
    )


def test_same_sku_active_job_is_reused() -> None:
    result, cur = _run([
        {"sku": "af-35-pro"},
        None,
        {"id": 808, "status": "running"},
    ])

    assert result == {
        "status": "already_running",
        "job_id": 808,
        "kol_pool_id": 42,
        "product_sku": "AF-35-PRO",
        "derive_method": content_fit_analysis.content_fit_derive_method("AF-35-PRO"),
    }
    assert len(cur.calls) == 3
    assert "COALESCE(payload->>'product_sku', '')=%s" in cur.calls[2][0]
    assert cur.calls[2][1] == ("42", "AF-35-PRO")


def test_generic_followup_keeps_historical_cache_namespace() -> None:
    result, cur = _run([
        None,  # no product-bearing session
        {"exists": 1},  # historical generic cache
    ])

    assert result == {
        "status": "cache_reused",
        "kol_pool_id": 42,
        "product_sku": None,
        "derive_method": "content_fit_v1",
    }
    assert cur.calls[1][1] == ("content_fit_v1", "42")


def test_generic_cache_does_not_block_product_scoped_followup() -> None:
    result, cur = _run(_queued_rows("AF-75-PRO"))

    scoped = content_fit_analysis.content_fit_derive_method("AF-75-PRO")
    assert result is not None and result["status"] == "queued"
    assert scoped != "content_fit_v1"
    assert cur.calls[1][1] == (scoped, "42")
    assert cur.calls[2][1] == ("42", "AF-75-PRO")
