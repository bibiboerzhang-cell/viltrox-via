from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import (
    product_resolver,
    profile_discovery_pipeline,
    profile_discovery_queue,
    smart_query_planner,
)


class _Cursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._row) if self._row else None


class _QueueConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        self.executed.append((sql, tuple(params)))
        if "INSERT INTO apify_jobs" in sql:
            return _Cursor(
                {
                    "id": 9901,
                    "job_type": "smart_search_profile_advance",
                    "status": "queued",
                    "created_at": "2026-08-13T00:00:00Z",
                    "updated_at": "2026-08-13T00:00:00Z",
                }
            )
        raise AssertionError(f"unexpected queue SQL: {' '.join(sql.split())}")

    def commit(self) -> None:
        self.commits += 1


def _dc_550() -> dict[str, Any]:
    return {
        "sku": "DC-550",
        "model_name": "DC-550 Pro",
        "marketing_name": "DC-550 5.5-inch Camera Monitor",
        "category_main": "Monitor",
        "series": "DC",
    }


def _epic_65() -> dict[str, Any]:
    return {
        "sku": "EPIC-65-MACRO-PL",
        "model_name": "EPIC 65mm T2.8 Macro 1.33x",
        "marketing_name": "EPIC 65mm Macro Anamorphic",
        "category_main": "Lens",
        "series": "EPIC",
    }


@pytest.mark.parametrize(
    ("query", "product_sku", "inferred_product", "expected_reason"),
    [
        (
            "wedding filmmakers",
            "NOT-A-CATALOG-SKU",
            None,
            "explicit_product_sku_not_in_catalog",
        ),
        (
            "EPIC 65mm macro filmmakers",
            "DC-550",
            _epic_65(),
            "conflicting_product_constraints",
        ),
    ],
)
def test_http_enqueued_client_plan_cannot_bypass_worker_catalog_guard(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    product_sku: str,
    inferred_product: dict[str, Any] | None,
    expected_reason: str,
) -> None:
    """Treat every client plan/persona as untrusted until the worker replans."""

    queue_conn = _QueueConn()
    summary_updates: list[dict[str, Any]] = []
    monkeypatch.setattr(profile_discovery_queue, "get_conn", lambda: queue_conn)
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: {"id": 8801, "status": "planned"},
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "update_session_result_summary",
        lambda session_id, **kwargs: summary_updates.append(
            {"session_id": session_id, **kwargs}
        ),
    )
    monkeypatch.setattr(
        profile_discovery_queue.search_sessions,
        "get_session",
        lambda session_id: {"id": session_id, "status": "running", "items": []},
    )

    forged_plan = {
        "status": "ready",
        "search_query": "forged bypass query",
        "product_focus": ["forged-focus"],
        "target_persona": "forged target persona",
        "resolved_product": {"sku": product_sku},
        "provider_calls_performed": False,
    }
    http_result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search_profile_advance_job(
            {
                "input": query,
                "product_sku": product_sku,
                "llm_query_plan": forged_plan,
                "product_focus": ["forged-top-level-focus"],
                "target_persona": "forged top-level persona",
                "include_new_discovery": True,
            },
            staff={"id": 42},
        )
    )

    assert http_result["status"] == "queued"
    assert http_result["provider_calls"] is False
    assert queue_conn.commits == 1
    assert len(queue_conn.executed) == 1
    worker_payload = json.loads(queue_conn.executed[0][1][0])
    assert worker_payload["query_text"] == query
    assert worker_payload["product_sku"] == product_sku
    assert worker_payload.get("_worker_planned") is not True

    calls = {"planner": 0, "recall": 0, "discovery": 0, "provider": 0}
    monkeypatch.setattr(
        product_resolver,
        "resolve_product",
        lambda _query: dict(inferred_product) if inferred_product else None,
    )
    monkeypatch.setattr(
        product_resolver,
        "resolve_product_sku",
        lambda sku: _dc_550() if sku == "DC-550" else None,
    )
    monkeypatch.setattr(product_resolver, "unresolved_product_request", lambda _query: None)

    def server_planner(
        planner_query: str,
        *,
        body: dict[str, Any] | None = None,
        staff: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls["planner"] += 1
        assert planner_query == query
        assert body is worker_payload
        return smart_query_planner._plan_text_query_impl(
            planner_query,
            body=body,
            staff=staff,
        )

    def forbidden_recall(**_kwargs: Any) -> dict[str, Any]:
        calls["recall"] += 1
        raise AssertionError("catalog guard must stop before recall")

    async def forbidden_discovery(**_kwargs: Any) -> dict[str, Any]:
        calls["discovery"] += 1
        raise AssertionError("catalog guard must stop before discovery")

    def forbidden_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["provider"] += 1
        raise AssertionError("catalog guard must stop before an LLM/provider call")

    monkeypatch.setattr(smart_query_planner, "plan_text_query", server_planner)
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        forbidden_recall,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "discover_new_creators",
        forbidden_discovery,
    )
    monkeypatch.setattr(smart_query_planner.llm_gateway, "invoke", forbidden_provider)

    worker_result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=8801,
            payload=worker_payload,
        )
    )

    assert calls == {"planner": 1, "recall": 0, "discovery": 0, "provider": 0}
    assert worker_result["status"] == "needs_clarification"
    assert worker_result["query_plan_source"] == "product_catalog_guard"
    assert worker_result["llm_query_plan"]["reason"] == expected_reason
    assert worker_result["llm_query_plan"]["provider_calls_performed"] is False
    assert worker_result["recall"]["method"] == "product_catalog_guard"
    assert worker_result["new_discovery"] is None
    assert worker_result["advance"]["status"] == "not_started"
    assert summary_updates[-1]["summary_patch"]["llm_query_plan"]["reason"] == expected_reason
