from __future__ import annotations

import asyncio
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_agents, vkpi_kol_pool_search, vkpi_recall  # noqa: E402
from app.domains.actions import executors  # noqa: E402
from app.domains.discovery import enroll, federation  # noqa: E402
from app.domains.intelligence import semantic_recall  # noqa: E402
from app.domains.kol import (  # noqa: E402
    onboarding_workflow,
    profile_recall,
    recall_pipeline,
    unified_search,
)


def test_federated_preview_is_provider_free_and_defers_external_sources(monkeypatch):
    calls: list[dict] = []

    monkeypatch.setattr(
        federation,
        "list_providers",
        lambda _kind="": [
            {"name": "internal_pool", "enabled": True},
            {"name": "apify_search", "enabled": True},
        ],
    )
    monkeypatch.setattr(federation, "_local_read_scope", nullcontext)

    def fake_recall(query, **kwargs):
        calls.append({"query": query, **kwargs})
        return {
            "status": "ok",
            "results": [{"id": 42, "title": "Local creator", "score": 0.8}],
        }

    monkeypatch.setattr(semantic_recall, "unified_recall", fake_recall)
    monkeypatch.setattr(
        federation,
        "_apify_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview must never invoke Apify")
        ),
    )

    staff = {"id": 7, "role": "manager"}
    result = federation.federated_search(
        "camera creator",
        limit=5,
        staff=staff,
    )

    assert calls == [
        {
            "query": "camera creator",
            "kinds": ("kol",),
            "limit": 5,
            "staff": staff,
            "provider_free": True,
        }
    ]
    assert result["results"][0]["kol_pool_id"] == 42
    assert result["sources"]["apify_search"]["status"] == "background_refresh_required"


def test_get_style_external_flag_cannot_run_custom_provider_without_durable_fence(
    monkeypatch,
):
    provider_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        federation,
        "list_providers",
        lambda _kind="": [
            {"name": "internal_pool", "enabled": True},
            {"name": "commercial_search", "enabled": True},
        ],
    )
    monkeypatch.setattr(federation, "_local_read_scope", nullcontext)
    monkeypatch.setattr(federation, "current_apify_execution_context", lambda: None)
    monkeypatch.setitem(
        federation._CUSTOM,
        "commercial_search",
        lambda query, limit: provider_calls.append((query, limit)) or [],
    )
    monkeypatch.setattr(
        semantic_recall,
        "unified_recall",
        lambda *_args, **_kwargs: {"status": "ok", "results": []},
    )

    result = federation.federated_search(
        "camera creators",
        limit=5,
        staff={"id": 7, "role": "manager"},
        include_external=True,
    )

    assert provider_calls == []
    assert result["sources"]["commercial_search"]["status"] == "background_refresh_required"


def test_unified_get_cost_gate_does_not_claim_deferred_provider_cost(monkeypatch):
    monkeypatch.setattr(
        federation,
        "federated_search",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "results": [],
            "sources": {
                "internal_pool": {"count": 0, "status": "ok"},
                "apify_search": {
                    "count": 0,
                    "status": "background_refresh_required",
                },
            },
        },
    )
    monkeypatch.setattr(
        federation,
        "list_providers",
        lambda _kind="": [
            {
                "name": "apify_search",
                "enabled": True,
                "adapter_ready": True,
            }
        ],
    )
    monkeypatch.setattr(
        unified_search,
        "_history_match",
        lambda _query: {
            "available": True,
            "prior_sessions": 0,
            "searched_before": False,
        },
    )
    monkeypatch.setattr(
        unified_search,
        "current_apify_execution_context",
        lambda: None,
    )

    result = unified_search.unified_search(
        "camera creators",
        include_external=True,
        staff={"id": 7, "role": "manager"},
    )

    assert result["cost_gate"]["external_search_requested"] is True
    assert result["cost_gate"]["external_execution_authorized"] is False
    assert result["cost_gate"]["incurs_cost"] is False


def test_federated_enroll_external_execution_is_explicit(monkeypatch):
    captured: dict[str, object] = {}

    def fake_search(query, *, limit, staff, include_external):
        captured.update(
            {
                "query": query,
                "limit": limit,
                "staff": staff,
                "include_external": include_external,
            }
        )
        return {"status": "ok", "results": [], "sources": {}}

    monkeypatch.setattr(federation, "federated_search", fake_search)
    monkeypatch.setattr(
        enroll,
        "enroll_candidates",
        lambda _items, *, staff: {
            "enrolled": 0,
            "skipped": 0,
            "enrolled_ids": [],
        },
    )

    enroll.federated_discover_and_enroll(
        "camera creators",
        limit=9,
        staff={"id": 17},
        include_external=True,
    )

    assert captured["include_external"] is True


def test_onboarding_enqueue_uses_durable_provider_job():
    class Queue:
        backend_name = "redis"

        def __init__(self):
            self.calls: list[dict] = []

        async def enqueue(self, job_type, payload, **kwargs):
            self.calls.append({"job_type": job_type, "payload": payload, **kwargs})
            return "onboarding-task"

    queue = Queue()
    result = asyncio.run(
        onboarding_workflow.enqueue_kol_onboarding(
            queue,
            "camera creators",
            limit=7,
            staff={"id": 17},
        )
    )

    assert result["status"] == "queued"
    assert result["business_outcome"] == "pending"
    assert queue.calls == [
        {
            "job_type": "kol_onboarding",
            "payload": {
                "query": "camera creators",
                "limit": 7,
                "staff": {"id": 17},
            },
            "lock_key": "kol_onboarding:camera creators",
            "timeout_seconds": 3600,
        }
    ]


def test_discovery_query_limit_fails_before_queue_or_provider(monkeypatch):
    class Queue:
        backend_name = "redis"

        async def enqueue(self, *_args, **_kwargs):
            raise AssertionError("oversized query must fail before enqueue")

    monkeypatch.setattr(
        federation,
        "list_providers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("oversized query must fail before provider registry")
        ),
    )
    oversized = "x" * (federation.MAX_DISCOVERY_QUERY_LENGTH + 1)

    with pytest.raises(ValueError, match="at most"):
        asyncio.run(
            onboarding_workflow.enqueue_kol_onboarding(
                Queue(),
                oversized,
                staff={"id": 17},
            )
        )
    with pytest.raises(ValueError, match="at most"):
        federation.federated_search(
            oversized,
            include_external=True,
            staff={"id": 17, "role": "manager"},
        )


def test_approved_discovery_action_reports_queue_not_fake_enrolment(monkeypatch):
    from app.services.jobs import queue as queue_module

    class Queue:
        backend_name = "redis"

        def __init__(self):
            self.closed = False

        async def enqueue(self, *_args, **_kwargs):
            return "onboarding-task"

        async def close(self):
            self.closed = True

    queue = Queue()
    monkeypatch.setattr(queue_module, "build_job_queue", lambda: queue)
    monkeypatch.setattr(
        enroll,
        "federated_discover_and_enroll",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("request path must not execute external discovery inline")
        ),
    )

    result = executors._exec_discovery_enroll(
        {
            "entity_id": "camera creators",
            "payload_json": {"limit": 7},
        },
        {"id": 17, "role": "manager"},
    )

    assert result["outcome"] == "success"
    assert result["detail"]["execution_status"] == "queued"
    assert result["detail"]["business_outcome"] == "pending"
    assert "found" not in result["detail"]
    assert "enrolled" not in result["detail"]
    assert queue.closed is True


def test_cross_signal_recall_fails_before_search_for_non_manager(monkeypatch):
    monkeypatch.setattr(
        semantic_recall,
        "_recall_kind",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("permission check must run before DB search")
        ),
    )

    with pytest.raises(PermissionError, match="manager access"):
        semantic_recall.unified_recall(
            "camera project",
            kinds=("project",),
            staff={"id": 18, "role": "employee"},
            provider_free=True,
        )


def test_kol_only_recall_remains_available_to_scoped_staff(monkeypatch):
    monkeypatch.setattr(
        semantic_recall,
        "_vector_recall_kol",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        semantic_recall,
        "_recall_kind",
        lambda *_args, **_kwargs: [],
    )

    result = semantic_recall.unified_recall(
        "camera creator",
        kinds=("kol",),
        staff={"id": 18, "role": "employee"},
        provider_free=True,
    )

    assert result["status"] == "ok"


def test_semantic_provider_free_recall_never_enters_embedding_or_llm_path(monkeypatch):
    seen: list[dict] = []

    def fake_profile_recall(**kwargs):
        seen.append(kwargs)
        assert kwargs["provider_free"] is True
        return {
            "items": [
                {
                    "kol_pool_id": 9,
                    "display_name": "Pool creator",
                    "platform": "youtube",
                }
            ]
        }

    monkeypatch.setattr(profile_recall, "recall_kol_profiles", fake_profile_recall)
    result = semantic_recall.unified_recall(
        "camera creator",
        kinds=("kol",),
        limit=3,
        provider_free=True,
    )

    assert len(seen) == 1
    assert result["recall_method"] == "provider_free_pool_text+lexical"
    assert result["results"][0]["recall"] == "provider_free_pool_text"


def test_three_stage_get_preview_skips_embedding_rerank_and_cache_write(monkeypatch):
    monkeypatch.setattr(
        recall_pipeline,
        "_embedding_recall",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET preview must not call embedding")
        ),
    )
    monkeypatch.setattr(
        recall_pipeline,
        "_rerank_stage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET preview must not call LLM rerank or cache")
        ),
    )
    monkeypatch.setattr(
        recall_pipeline,
        "_ngram_recall",
        lambda *_args, **_kwargs: [
            {
                "kol_pool_id": 7,
                "display_name": "Local creator",
                "country": "",
                "recall_score": 0.8,
            }
        ],
    )
    monkeypatch.setattr(
        recall_pipeline,
        "_coarse_rank",
        lambda _db, candidates: (candidates, {"n": len(candidates)}),
    )

    result = recall_pipeline.semantic_recall(
        "camera reviewer",
        conn=object(),
        provider_free=True,
    )

    assert result["status"] == "ready"
    assert result["stages"]["recall"]["degraded_reason"] == "provider_free_preview"
    assert result["stages"]["rerank"] == {
        "status": "skipped",
        "cost_note": "provider_free_preview_zero_cost",
    }


def test_semantic_get_route_forces_provider_free_mode(monkeypatch):
    captured: dict[str, object] = {}

    def fake_recall(query, *, limit, provider_free):
        captured.update(
            {"query": query, "limit": limit, "provider_free": provider_free}
        )
        return {"status": "empty", "items": [], "stages": {}}

    monkeypatch.setattr(recall_pipeline, "semantic_recall", fake_recall)
    result = vkpi_recall.get_semantic_recall(
        query="camera reviewer",
        limit=20,
        staff={"id": 7},
    )

    assert captured["provider_free"] is True
    assert result["provider_calls"] is False
    assert result["write_db"] is False
    assert result["execution_mode"] == "provider_free_preview"


def test_agents_recall_get_forces_provider_free_mode(monkeypatch):
    captured: dict[str, object] = {}

    def fake_unified(query, **kwargs):
        captured.update({"query": query, **kwargs})
        return {"status": "ok", "results": []}

    monkeypatch.setattr(semantic_recall, "unified_recall", fake_unified)
    vkpi_agents.unified_recall(
        q="camera project",
        limit=10,
        staff={"id": 7, "role": "manager"},
    )

    assert captured["provider_free"] is True


def test_kol_recall_get_is_provider_free_and_never_materializes_a_session(monkeypatch):
    captured: list[dict] = []

    def fake_recall(**kwargs):
        captured.append(kwargs)
        return {"items": [], "diagnostics": {}}

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        fake_recall,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_search_sessions,
        "ensure_session_for_result",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("read-only GET must not create a session")
        ),
    )

    result = vkpi_kol_pool_search.recall_kol_profiles(
        query_text="camera creator",
        product_sku="",
        candidate_limit=50,
        limit=10,
        creator_quota=7,
        reviewer_quota=3,
        ratio_policy="soft",
        mixed_policy="dominant",
        dedupe=True,
        vector_weight=0.85,
        type_weight=0.15,
        type_boost_enabled=True,
        exclude_chinese=True,
        session_id=None,
        create_session=False,
        staff={"id": 7, "role": "manager"},
    )

    assert captured[0]["provider_free"] is True
    assert result["provider_calls"] is False
    assert result["write_db"] is False
    assert result["execution_mode"] == "provider_free_preview"


def test_kol_recall_get_rejects_write_compatibility_flags_before_recall(monkeypatch):
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("write compatibility flags must fail before recall")
        ),
    )

    with pytest.raises(vkpi_kol_pool_search.HTTPException) as exc_info:
        vkpi_kol_pool_search.recall_kol_profiles(
            query_text="camera creator",
            product_sku="",
            candidate_limit=50,
            limit=10,
            creator_quota=7,
            reviewer_quota=3,
            ratio_policy="soft",
            mixed_policy="dominant",
            dedupe=True,
            vector_weight=0.85,
            type_weight=0.15,
            type_boost_enabled=True,
            exclude_chinese=True,
            session_id=None,
            create_session=True,
            staff={"id": 7, "role": "manager"},
        )
    assert exc_info.value.status_code == 400
