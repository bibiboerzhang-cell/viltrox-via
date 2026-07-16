from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.services.via import vector_memory


def _install_mock_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []
    real_async_client = vector_memory.httpx.AsyncClient

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording_handler)

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(vector_memory.httpx, "AsyncClient", client_factory)
    return requests


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    return [[0.1, 0.2] for _ in texts]


def _seed_items(count: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "text": f"seed {index}",
            "memory_kind": "seed_doc",
            "memory_key": f"seed-{index}",
            "source_ref": f"source-{index}",
        }
        for index in range(count)
    ]


def _bundle() -> dict[str, Any]:
    return {
        "session": {"session_key": "session-test", "user_id": 7},
        "persona": {"persona_key": "persona-test"},
    }


def _qdrant_collection_response(dim: int = 2, *, status: str = "green") -> dict[str, Any]:
    return {
        "status": "ok",
        "result": {
            "status": status,
            "config": {"params": {"vectors": {"size": dim, "distance": "Cosine"}}},
        },
    }


def test_qdrant_http_failure_does_not_report_upserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._QdrantVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_qdrant_collection_response())
        return httpx.Response(503, json={"status": {"error": "write unavailable"}})

    requests = _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["upserted"] == 0
    assert result["ready"] is False
    assert "qdrant_upsert: HTTP 503" in result["error"]
    assert [request.method for request in requests] == ["GET", "PUT"]


def test_qdrant_completed_write_reports_exact_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._QdrantVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"status": {"error": "not found"}})
        if request.url.path.endswith(f"/collections/{vector_memory.QDRANT_COLLECTION}"):
            return httpx.Response(200, json={"status": "ok", "result": True})
        return httpx.Response(
            200,
            json={"status": "ok", "result": {"operation_id": 42, "status": "completed"}},
        )

    requests = _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["upserted"] == 1
    assert result["ready"] is True
    assert result["error"] == ""
    assert requests[-1].url.params["wait"] == "true"
    assert "wait" not in json.loads(requests[-1].read())
    assert [request.method for request in requests] == ["GET", "PUT", "PUT"]


def test_qdrant_protocol_failure_does_not_report_upserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._QdrantVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_qdrant_collection_response())
        return httpx.Response(
            200,
            json={"status": "ok", "result": {"operation_id": 42, "status": "failed"}},
        )

    _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["upserted"] == 0
    assert result["ready"] is False
    assert "operation status is failed" in result["error"]


def test_qdrant_search_http_failure_returns_empty_and_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._QdrantVectorBackend()
    backend._ensured_dim = 2

    _install_mock_http(
        monkeypatch,
        lambda request: httpx.Response(502, json={"status": {"error": "search unavailable"}}),
    )
    hits = asyncio.run(backend.search("persona-test", "query", 3))

    assert hits == []
    assert backend.operation_status()["ready"] is False
    assert "qdrant_search: HTTP 502" in backend.operation_status()["error"]


def test_qdrant_readiness_rejects_unhealthy_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
    backend = vector_memory._QdrantVectorBackend()
    _install_mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, json=_qdrant_collection_response(status="red")),
    )

    stats = asyncio.run(backend.runtime_stats())

    assert stats["enabled"] is True
    assert stats["ready"] is False
    assert "collection status is red" in stats["error"]


def test_weaviate_partial_http_failure_reports_zero_upserted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._WeaviateVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)
    object_writes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal object_writes
        if request.method == "GET":
            return httpx.Response(200, json={"class": vector_memory.WEAVIATE_CLASS})
        object_writes += 1
        if object_writes == 1:
            body = request.read()
            doc_id = json.loads(body)["id"]
            return httpx.Response(200, json={"class": vector_memory.WEAVIATE_CLASS, "id": doc_id})
        return httpx.Response(500, json={"error": "object write failed"})

    requests = _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items(2)))

    assert result["upserted"] == 0
    assert result["ready"] is False
    assert "weaviate_upsert: HTTP 500" in result["error"]
    assert [request.method for request in requests] == ["GET", "POST", "POST"]


def test_weaviate_confirmed_write_reports_exact_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._WeaviateVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/v1/schema":
            return httpx.Response(200, json={"class": vector_memory.WEAVIATE_CLASS})
        doc_id = json.loads(request.read())["id"]
        return httpx.Response(200, json={"class": vector_memory.WEAVIATE_CLASS, "id": doc_id})

    requests = _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["upserted"] == 1
    assert result["ready"] is True
    assert result["error"] == ""
    assert [request.method for request in requests] == ["GET", "POST", "POST"]


def test_weaviate_rejects_mismatched_object_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._WeaviateVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"class": vector_memory.WEAVIATE_CLASS})
        return httpx.Response(
            200,
            json={"class": vector_memory.WEAVIATE_CLASS, "id": "00000000-0000-0000-0000-000000000000"},
        )

    _install_mock_http(monkeypatch, handler)
    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["upserted"] == 0
    assert result["ready"] is False
    assert "object id mismatch" in result["error"]


def test_weaviate_graphql_errors_return_empty_and_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    backend = vector_memory._WeaviateVectorBackend()
    backend._ensured_dim = 1
    _install_mock_http(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={"data": None, "errors": [{"message": "query rejected"}]},
        ),
    )

    hits = asyncio.run(backend.search("persona-test", "query", 3))

    assert hits == []
    assert backend.operation_status()["ready"] is False
    assert "GraphQL returned errors" in backend.operation_status()["error"]


def test_weaviate_readiness_http_failure_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
    backend = vector_memory._WeaviateVectorBackend()
    _install_mock_http(
        monkeypatch,
        lambda request: httpx.Response(503, json={"error": "not ready"}),
    )

    stats = asyncio.run(backend.runtime_stats())

    assert stats["enabled"] is True
    assert stats["ready"] is False
    assert "weaviate_readiness: HTTP 503" in stats["error"]


def test_local_qdrant_failure_keeps_fallback_and_never_uses_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePointStruct:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class FakeModels:
        class Distance:
            COSINE = "Cosine"

        class VectorParams:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs

        PointStruct = FakePointStruct

    class FakeClient:
        def collection_exists(self, collection: str) -> bool:
            return True

        def upsert(self, **kwargs: Any) -> None:
            raise RuntimeError("local store locked")

    def forbid_http(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("local fallback must not use HTTP")

    monkeypatch.setattr(vector_memory, "QDRANT_URL", "")
    monkeypatch.setattr(vector_memory, "QDRANT_LOCAL_PATH", "/tmp/hermetic-qdrant")
    monkeypatch.setattr(vector_memory, "QdrantClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(vector_memory, "qdrant_models", FakeModels)
    monkeypatch.setattr(vector_memory, "_embed_texts", _fake_embed)
    monkeypatch.setattr(vector_memory.httpx, "AsyncClient", forbid_http)
    backend = vector_memory._QdrantVectorBackend()

    count = asyncio.run(
        backend.upsert(
            "persona-test",
            [{"id": "point-1", "text": "local", "payload": {}}],
        )
    )

    assert count == 0
    assert backend.operation_status()["ready"] is False
    assert "local store locked" in backend.operation_status()["error"]


@pytest.mark.parametrize("backend_kind", ["qdrant", "weaviate"])
def test_embedding_provider_exception_returns_partial_without_http(
    monkeypatch: pytest.MonkeyPatch,
    backend_kind: str,
) -> None:
    async def broken_embed(_texts: list[str]) -> list[list[float]]:
        raise TimeoutError("embedding timeout")

    def forbid_http(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("vector HTTP must not run after embedding failure")

    monkeypatch.setattr(vector_memory, "_embed_texts", broken_embed)
    monkeypatch.setattr(vector_memory.httpx, "AsyncClient", forbid_http)
    if backend_kind == "qdrant":
        monkeypatch.setattr(vector_memory, "QDRANT_URL", "https://qdrant.invalid")
        backend = vector_memory._QdrantVectorBackend()
    else:
        monkeypatch.setattr(vector_memory, "WEAVIATE_URL", "https://weaviate.invalid")
        backend = vector_memory._WeaviateVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), _seed_items()))

    assert result["status"] == "partial"
    assert result["upserted"] == 0
    assert result["ready"] is False
    assert "embedding timeout" in result["error"]


def test_recall_backend_exception_returns_empty_instead_of_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenBackend:
        backend_name = "broken"

        async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
            raise RuntimeError("search transport failed")

    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", BrokenBackend())

    result = asyncio.run(vector_memory.recall_via_vector_memory(_bundle(), "query"))

    assert result == []


def test_recall_rejects_bad_backend_payload_without_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BadPayloadBackend:
        backend_name = "bad-payload"

        async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
            return [
                {"score": "not-a-number", "payload": {"summary": "bad score"}},
                {"score": 0.8, "payload": "not-an-object"},
            ]

    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", BadPayloadBackend())

    result = asyncio.run(vector_memory.recall_via_vector_memory(_bundle(), "query"))

    assert result == []


def test_exchange_summary_exception_uses_partial_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadyBackend:
        backend_name = "memory"

        async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
            return len(docs)

        def operation_status(self) -> dict[str, Any]:
            return {"ready": True, "error": ""}

    async def broken_summary(**kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("summary timeout")

    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", ReadyBackend())
    monkeypatch.setattr(vector_memory, "summarize_via_exchange", broken_summary)

    result = asyncio.run(
        vector_memory.store_via_vector_exchange(
            _bundle(),
            user_text="remember this",
            reply_text="noted",
            signals={"keywords": ["memory"]},
        )
    )

    assert result["status"] == "partial"
    assert result["reason"] == "summary_provider_unavailable"
    assert result["provider"] == "fallback"
    assert result["upserted"] == 1


def test_empty_seed_write_is_explicitly_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = vector_memory._NullVectorBackend()
    monkeypatch.setattr(vector_memory, "_vector_backend_singleton", backend)

    result = asyncio.run(vector_memory.store_via_seed_documents(_bundle(), []))

    assert result["status"] == "skipped"
    assert result["reason"] == "no_documents"
    assert result["upserted"] == 0
