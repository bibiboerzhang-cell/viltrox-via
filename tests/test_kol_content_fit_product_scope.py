from __future__ import annotations

from typing import Any

import pytest

from app.domains.kol import (
    content_fit_analysis,
    content_fit_batch,
    content_fit_enqueue,
    product_resolver,
)
from app.domains.tasks.apify_idempotency import active_job_idempotency_key


class _Cursor:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _Conn:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.calls.append((sql, tuple(params)))
        row = self.rows.pop(0) if self.rows else None
        return _Cursor(row)

    def commit(self) -> None:
        self.commits += 1


def test_content_fit_cache_namespace_is_stable_and_product_scoped() -> None:
    generic = content_fit_analysis.content_fit_derive_method()
    evo = content_fit_analysis.content_fit_derive_method(" af-35-evo ")

    assert generic == content_fit_analysis.DERIVE_METHOD
    assert evo == content_fit_analysis.content_fit_derive_method("AF-35-EVO")
    assert len({
        generic,
        evo,
        content_fit_analysis.content_fit_derive_method("AF-35-PRO"),
        content_fit_analysis.content_fit_derive_method("EPIC-35"),
    }) == 4


def test_read_and_write_cache_use_exact_product_namespace() -> None:
    conn = _Conn([
        {
            "result": '{"fit_verdict":"fit"}',
            "model": "model-a",
            "cost": 0.01,
            "updated_at": "2026-07-16T00:00:00Z",
        },
        None,
    ])
    sku = "AF-35MM-F18-PRO-FE"
    derive_method = content_fit_analysis.content_fit_derive_method(sku)

    cached = content_fit_analysis._read_cache(conn, 42, sku)
    content_fit_analysis._write_cache(
        conn,
        42,
        {"fit_verdict": "fit"},
        model="model-a",
        cost_usd=0.01,
        triggered_by_user_id=7,
        product_sku=sku,
    )

    assert cached is not None
    assert cached["product_sku"] == sku
    assert cached["derive_method"] == derive_method
    assert conn.calls[0][1][-1] == derive_method
    assert derive_method in conn.calls[1][1]


def test_on_demand_jobs_for_two_products_have_distinct_idempotency_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import connection

    conn = _Conn([None, None])
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    canonical_videos = content_fit_analysis._VideoAnalyses([{"evidence_id": 9}])
    canonical_videos.cache_gate = {"status": "canonical", "revalidation_required": False}
    monkeypatch.setattr(content_fit_analysis, "_video_analyses", lambda *_a, **_k: canonical_videos)
    monkeypatch.setattr(
        content_fit_analysis,
        "get_content_fit",
        lambda kid, product_sku=None: {
            "state": "missing",
            "kol_pool_id": kid,
            "product_sku": product_sku,
        },
    )
    monkeypatch.setattr(
        content_fit_enqueue,
        "_content_fit_ai_readiness",
        lambda: {
            "allowed": True,
            "gate_reason": "ready",
            "model_readiness_status": "production_ready",
            "ai_analysis": {"state": "ready"},
        },
    )

    def _enqueue(_conn: Any, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        captured.append(kwargs)
        return {"id": 100 + len(captured), "status": "queued"}, True

    monkeypatch.setattr(content_fit_enqueue, "enqueue_active_apify_job", _enqueue)

    first = content_fit_enqueue.enqueue_content_fit_on_demand(
        42,
        "af-35-evo",
        force=False,
        staff={"user_id": 7},
    )
    second = content_fit_enqueue.enqueue_content_fit_on_demand(
        42,
        "AF-35-PRO",
        force=False,
        staff={"user_id": 7},
    )

    assert first["status"] == second["status"] == "queued"
    assert captured[0]["payload"]["product_sku"] == "AF-35-EVO"
    assert captured[1]["payload"]["product_sku"] == "AF-35-PRO"
    assert captured[0]["payload"]["derive_method"] != captured[1]["payload"]["derive_method"]
    assert captured[0]["idempotency_key"] != captured[1]["idempotency_key"]
    assert captured[0]["idempotency_key"] == active_job_idempotency_key(
        content_fit_enqueue.CONTENT_FIT_JOB_TYPE,
        42,
        "AF-35-EVO",
    )


def test_batch_consumer_writes_product_scoped_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn()
    writes: list[dict[str, Any]] = []
    monkeypatch.setattr(content_fit_batch, "get_conn", lambda: conn)
    canonical_videos = content_fit_analysis._VideoAnalyses([{"evidence_id": 9}])
    canonical_videos.cache_gate = {"status": "canonical", "revalidation_required": False}
    monkeypatch.setattr(content_fit_analysis, "_video_analyses", lambda *_a, **_k: canonical_videos)
    monkeypatch.setattr(
        content_fit_analysis,
        "_write_cache",
        lambda _conn, kid, result, **kwargs: writes.append(
            {"kid": kid, "result": result, **kwargs}
        ),
    )
    meta = {
        "kol_pool_id": 42,
        "product_sku": "AF-35-PRO",
        "product": {"sku": "AF-35-PRO"},
        "video_count": 1,
        "video_evidence_ids": [9],
    }

    result = content_fit_batch.consume(
        {"42-acde": '{"fit_verdict":"fit","confidence":0.9}'},
        {"42-acde": meta},
    )

    assert result == {"written": 1, "failed": 0, "total": 1}
    assert writes[0]["kid"] == 42
    assert writes[0]["product_sku"] == "AF-35-PRO"


@pytest.mark.parametrize(
    ("query", "expected_sku"),
    [
        ("35mm Pro", "AF-35-F18-FE"),
        ("75mm Pro", "AF-75-F12-FE"),
    ],
)
def test_pro_series_resolves_from_catalog_series_field(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    expected_sku: str,
) -> None:
    products = [
        {
            "sku": "AF-35-F18-FE",
            "model_name": "AF 35mm F1.8 FE",
            "marketing_name": "35mm F1.8",
            "series": "Pro",
            "category_main": "Lens",
            "mount": "FE-mount",
        },
        {
            "sku": "AF-75-F12-FE",
            "model_name": "AF 75mm F1.2 FE",
            "marketing_name": "75mm F1.2",
            "series": "Pro",
            "category_main": "Lens",
            "mount": "FE-mount",
        },
        {
            "sku": "EPIC-35-T20-PL",
            "model_name": "EPIC 35mm T2.0",
            "marketing_name": "EPIC 35mm",
            "series": "EPIC",
            "category_main": "Lens",
            "mount": "PL-mount",
        },
    ]
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": products},
    )

    resolved = product_resolver.resolve_product(query)

    assert resolved is not None
    assert resolved["sku"] == expected_sku
    assert resolved["series"] == "Pro"


def test_generic_pro_photographer_does_not_resolve_as_pro_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {
            "products": [
                {
                    "sku": "AF-35-F18-FE",
                    "model_name": "AF 35mm F1.8 FE",
                    "series": "Pro",
                    "category_main": "Lens",
                }
            ]
        },
    )

    assert product_resolver.resolve_product("find a pro photographer") is None
