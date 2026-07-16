"""Vector backend implementations for Via memory.

The runtime module is injected by vector_memory.py so legacy monkeypatches on
that module keep affecting backend configuration and dependencies.
"""
from __future__ import annotations

from typing import Any


class NullVectorBackend:
    backend_name = "none"

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        return 0

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        return []

    async def runtime_stats(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "enabled": False, "ready": False, "error": ""}

    def operation_status(self) -> dict[str, Any]:
        return {"ready": False, "error": ""}


class QdrantVectorBackend:
    backend_name = "qdrant"

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._ensured_dim = 0
        self._local_client: Any | None = None
        self._local_disabled_reason = ""
        self._remote_ready: bool | None = None
        self._last_error = ""

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._runtime.QDRANT_API_KEY:
            headers["api-key"] = self._runtime.QDRANT_API_KEY
        return headers

    @property
    def _use_local(self) -> bool:
        return (
            not self._local_disabled_reason
            and not self._runtime.QDRANT_URL
            and bool(self._runtime.QdrantClient and self._runtime.qdrant_models and self._runtime.QDRANT_LOCAL_PATH)
        )

    def _disable_local(self, reason: str) -> None:
        self._local_disabled_reason = str(reason or "local_qdrant_disabled").strip()[:240]
        self._local_client = None
        self._runtime.logger.warning(
            "via.vector_memory.local_qdrant_disabled | path=%s | reason=%s",
            self._runtime.QDRANT_LOCAL_PATH,
            self._local_disabled_reason,
        )

    def _remote_succeeded(self) -> None:
        self._remote_ready = True
        self._last_error = ""

    def _remote_failed(self, operation: str, exc: Exception) -> None:
        self._remote_ready = False
        self._last_error = self._runtime._operation_error(operation, exc)
        self._runtime.logger.warning("via.vector_memory.qdrant_remote_failed | error=%s", self._last_error)

    def _embedding_failed(self, exc: Exception) -> None:
        if self._runtime.QDRANT_URL:
            self._remote_failed("embedding", exc)
            return
        self._last_error = self._runtime._operation_error("embedding", exc)
        self._runtime.logger.warning("via.vector_memory.qdrant_embedding_failed | error=%s", self._last_error)

    def _embedding_succeeded(self) -> None:
        if self._last_error.startswith("embedding:"):
            self._last_error = ""

    def operation_status(self) -> dict[str, Any]:
        if self._runtime.QDRANT_URL:
            return {
                "ready": self._remote_ready is True,
                "error": self._last_error,
            }
        return {
            "ready": self._use_local and not self._last_error,
            "error": self._local_disabled_reason or self._last_error,
        }

    def _get_local_client(self) -> Any | None:
        if not self._use_local:
            return None
        if self._local_client is None:
            try:
                self._local_client = self._runtime.QdrantClient(path=self._runtime.QDRANT_LOCAL_PATH)
            except Exception as exc:
                self._disable_local(self._runtime._operation_error("qdrant_local_init", exc))
                return None
        return self._local_client

    async def _ensure_collection(self, dim: int) -> bool:
        if dim <= 0:
            return False
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return False
            if self._ensured_dim == dim:
                return True
            try:
                exists = await self._runtime.asyncio.to_thread(
                    client.collection_exists,
                    self._runtime.QDRANT_COLLECTION,
                )
            except Exception as exc:
                self._disable_local(self._runtime._operation_error("qdrant_local_readiness", exc))
                return False
            if not exists:
                try:
                    await self._runtime.asyncio.to_thread(
                        client.create_collection,
                        collection_name=self._runtime.QDRANT_COLLECTION,
                        vectors_config=self._runtime.qdrant_models.VectorParams(
                            size=dim,
                            distance=self._runtime.qdrant_models.Distance.COSINE,
                        ),
                    )
                except Exception as exc:
                    self._disable_local(self._runtime._operation_error("qdrant_local_create", exc))
                    return False
            self._ensured_dim = dim
            return True
        if not self._runtime.QDRANT_URL:
            return False
        if self._ensured_dim == dim:
            return True
        url = f"{self._runtime.QDRANT_URL.rstrip('/')}/collections/{self._runtime.QDRANT_COLLECTION}"
        payload = {"vectors": {"size": dim, "distance": "Cosine"}}
        operation = "qdrant_ensure_collection"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self._headers)
                if response.status_code == 404:
                    try:
                        response.raise_for_status()
                    except self._runtime.httpx.HTTPStatusError:
                        pass
                    response = await client.put(url, headers=self._headers, json=payload)
                    result = self._runtime._qdrant_result(response, operation)
                    if result is not True:
                        raise ValueError(f"{operation}: Qdrant did not confirm collection creation")
                else:
                    result = self._runtime._qdrant_result(response, operation)
                    self._runtime._validate_qdrant_collection(result, operation, expected_dim=dim)
        except Exception as exc:
            self._remote_failed(operation, exc)
            return False
        self._ensured_dim = dim
        self._remote_succeeded()
        return True

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        if not self._use_local and not self._runtime.QDRANT_URL:
            return 0
        try:
            vectors = await self._runtime._embed_texts([str(doc.get("text") or "") for doc in docs])
        except Exception as exc:  # noqa: BLE001 - embedding providers are an optional dependency
            self._embedding_failed(exc)
            return 0
        if len(vectors) != len(docs) or any(not vector for vector in vectors):
            self._embedding_failed(ValueError("embedding provider returned missing or empty vectors"))
            return 0
        self._embedding_succeeded()
        if not await self._ensure_collection(len(vectors[0])):
            return 0
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return 0
            points = []
            for doc, vector in zip(docs, vectors):
                payload = dict(doc.get("payload") or {})
                payload["namespace"] = namespace
                points.append(
                    self._runtime.qdrant_models.PointStruct(
                        id=doc["id"],
                        vector=vector,
                        payload=payload,
                    )
                )
            try:
                await self._runtime.asyncio.to_thread(
                    client.upsert,
                    collection_name=self._runtime.QDRANT_COLLECTION,
                    points=points,
                    wait=False,
                )
            except Exception as exc:
                self._disable_local(self._runtime._operation_error("qdrant_local_upsert", exc))
                return 0
            return len(points)
        if not self._runtime.QDRANT_URL:
            return 0
        points = []
        for doc, vector in zip(docs, vectors):
            payload = dict(doc.get("payload") or {})
            payload["namespace"] = namespace
            points.append({"id": doc["id"], "vector": vector, "payload": payload})
        url = f"{self._runtime.QDRANT_URL.rstrip('/')}/collections/{self._runtime.QDRANT_COLLECTION}/points"
        operation = "qdrant_upsert"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=15.0) as client:
                response = await client.put(
                    url,
                    headers=self._headers,
                    params={"wait": "true"},
                    json={"points": points},
                )
                result = self._runtime._qdrant_result(response, operation)
            if not isinstance(result, dict):
                raise ValueError(f"{operation}: invalid Qdrant operation result")
            operation_status = str(result.get("status") or "").strip().lower()
            if operation_status != "completed":
                raise ValueError(f"{operation}: Qdrant operation status is {operation_status or 'missing'}")
        except Exception as exc:
            self._remote_failed(operation, exc)
            return 0
        self._remote_succeeded()
        return len(points)

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        if not self._use_local and not self._runtime.QDRANT_URL:
            return []
        try:
            vectors = await self._runtime._embed_texts([query_text])
        except Exception as exc:  # noqa: BLE001
            self._embedding_failed(exc)
            return []
        if len(vectors) != 1 or not vectors[0]:
            self._embedding_failed(ValueError("embedding provider returned missing or empty query vector"))
            return []
        self._embedding_succeeded()
        if not await self._ensure_collection(len(vectors[0])):
            return []
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return []
            try:
                hits = await self._runtime.asyncio.to_thread(
                    client.search,
                    collection_name=self._runtime.QDRANT_COLLECTION,
                    query_vector=vectors[0],
                    limit=max(1, int(limit)),
                    query_filter=self._runtime.qdrant_models.Filter(
                        must=[
                            self._runtime.qdrant_models.FieldCondition(
                                key="namespace",
                                match=self._runtime.qdrant_models.MatchValue(value=namespace),
                            )
                        ]
                    ),
                    with_payload=True,
                )
            except Exception as exc:
                self._disable_local(self._runtime._operation_error("qdrant_local_search", exc))
                return []
            return [
                {
                    "score": float(getattr(item, "score", 0.0) or 0.0),
                    "payload": dict(getattr(item, "payload", None) or {}),
                    "id": getattr(item, "id", None),
                }
                for item in hits
            ]
        if not self._runtime.QDRANT_URL:
            return []
        payload = {
            "vector": vectors[0],
            "limit": max(1, int(limit)),
            "with_payload": True,
            "filter": {"must": [{"key": "namespace", "match": {"value": namespace}}]},
        }
        url = f"{self._runtime.QDRANT_URL.rstrip('/')}/collections/{self._runtime.QDRANT_COLLECTION}/points/search"
        operation = "qdrant_search"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers=self._headers, json=payload)
                hits = self._runtime._qdrant_result(response, operation)
            if not isinstance(hits, list):
                raise ValueError(f"{operation}: Qdrant result is not a list")
            out = []
            for item in hits:
                if not isinstance(item, dict):
                    raise ValueError(f"{operation}: invalid Qdrant search item")
                out.append(
                    {
                        "score": float(item.get("score") or 0.0),
                        "payload": item.get("payload") or {},
                        "id": item.get("id"),
                    }
                )
        except Exception as exc:
            self._remote_failed(operation, exc)
            return []
        self._remote_succeeded()
        return out

    async def runtime_stats(self) -> dict[str, Any]:
        if self._runtime.QDRANT_URL:
            operation = "qdrant_readiness"
            try:
                url = f"{self._runtime.QDRANT_URL.rstrip('/')}/collections/{self._runtime.QDRANT_COLLECTION}"
                async with self._runtime.httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url, headers=self._headers)
                    result = self._runtime._qdrant_result(response, operation)
                self._runtime._validate_qdrant_collection(result, operation)
            except Exception as exc:
                self._remote_failed(operation, exc)
            else:
                self._remote_succeeded()
        status = self.operation_status()
        return {
            "backend": self.backend_name,
            "enabled": bool(self._runtime.QDRANT_URL or self._use_local),
            "ready": status["ready"],
            "error": status["error"],
            "collection": self._runtime.QDRANT_COLLECTION,
            "mode": "local" if self._use_local else "remote",
            "path": self._runtime.QDRANT_LOCAL_PATH if self._use_local else "",
            "local_disabled_reason": self._local_disabled_reason,
        }


class WeaviateVectorBackend:
    backend_name = "weaviate"

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime
        self._ensured_dim = 0
        self._remote_ready: bool | None = None
        self._last_error = ""

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._runtime.WEAVIATE_API_KEY:
            headers["Authorization"] = f"Bearer {self._runtime.WEAVIATE_API_KEY}"
        return headers

    def _remote_succeeded(self) -> None:
        self._remote_ready = True
        self._last_error = ""

    def _remote_failed(self, operation: str, exc: Exception) -> None:
        self._remote_ready = False
        self._last_error = self._runtime._operation_error(operation, exc)
        self._runtime.logger.warning("via.vector_memory.weaviate_remote_failed | error=%s", self._last_error)

    def _embedding_failed(self, exc: Exception) -> None:
        self._remote_failed("embedding", exc)

    def _embedding_succeeded(self) -> None:
        if self._last_error.startswith("embedding:"):
            self._last_error = ""

    def operation_status(self) -> dict[str, Any]:
        return {
            "ready": bool(self._runtime.WEAVIATE_URL) and self._remote_ready is True,
            "error": self._last_error,
        }

    async def _ensure_class(self) -> bool:
        if not self._runtime.WEAVIATE_URL or self._ensured_dim:
            return bool(self._runtime.WEAVIATE_URL)
        base_url = f"{self._runtime.WEAVIATE_URL.rstrip('/')}/v1/schema"
        payload = {
            "class": self._runtime.WEAVIATE_CLASS,
            "vectorizer": "none",
            "properties": [
                {"name": "namespace", "dataType": ["text"]},
                {"name": "session_key", "dataType": ["text"]},
                {"name": "user_id", "dataType": ["int"]},
                {"name": "memory_kind", "dataType": ["text"]},
                {"name": "memory_key", "dataType": ["text"]},
                {"name": "source_ref", "dataType": ["text"]},
                {"name": "summary", "dataType": ["text"]},
                {"name": "payload_json", "dataType": ["text"]},
            ],
        }
        operation = "weaviate_ensure_class"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{base_url}/{self._runtime.WEAVIATE_CLASS}", headers=self._headers)
                if response.status_code == 404:
                    try:
                        response.raise_for_status()
                    except self._runtime.httpx.HTTPStatusError:
                        pass
                    response = await client.post(base_url, headers=self._headers, json=payload)
                data = self._runtime._response_json_object(response, operation)
                self._runtime._validate_weaviate_class(data, operation)
        except Exception as exc:
            self._remote_failed(operation, exc)
            return False
        self._ensured_dim = 1
        self._remote_succeeded()
        return True

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        if not self._runtime.WEAVIATE_URL or not docs:
            return 0
        try:
            vectors = await self._runtime._embed_texts([str(doc.get("text") or "") for doc in docs])
        except Exception as exc:  # noqa: BLE001
            self._embedding_failed(exc)
            return 0
        if len(vectors) != len(docs) or any(not vector for vector in vectors):
            self._embedding_failed(ValueError("embedding provider returned missing or empty vectors"))
            return 0
        self._embedding_succeeded()
        if not await self._ensure_class():
            return 0
        pairs = list(zip(docs, vectors))
        operation = "weaviate_upsert"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=15.0) as client:
                for doc, vector in pairs:
                    payload = dict(doc.get("payload") or {})
                    payload["namespace"] = namespace
                    response = await client.post(
                        f"{self._runtime.WEAVIATE_URL.rstrip('/')}/v1/objects",
                        headers=self._headers,
                        json={
                            "class": self._runtime.WEAVIATE_CLASS,
                            "id": doc["id"],
                            "properties": payload,
                            "vector": vector,
                        },
                    )
                    data = self._runtime._response_json_object(response, operation)
                    self._runtime._validate_weaviate_class(data, operation)
                    if str(data.get("id") or "").lower() != str(doc["id"]).lower():
                        raise ValueError(f"{operation}: Weaviate object id mismatch")
        except Exception as exc:
            self._remote_failed(operation, exc)
            return 0
        self._remote_succeeded()
        return len(pairs)

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        if not self._runtime.WEAVIATE_URL:
            return []
        try:
            vectors = await self._runtime._embed_texts([query_text])
        except Exception as exc:  # noqa: BLE001
            self._embedding_failed(exc)
            return []
        if len(vectors) != 1 or not vectors[0]:
            self._embedding_failed(ValueError("embedding provider returned missing or empty query vector"))
            return []
        self._embedding_succeeded()
        if not await self._ensure_class():
            return []
        vector = self._runtime.json.dumps(vectors[0])
        namespace_literal = self._runtime.json.dumps(namespace)
        query = (
            "{ Get { "
            f"{self._runtime.WEAVIATE_CLASS}("
            f'nearVector: {{ vector: {vector} }}, '
            f'where: {{ path:[\"namespace\"], operator: Equal, valueText: {namespace_literal} }}, '
            f"limit: {max(1, int(limit))}"
            ") { namespace session_key user_id memory_kind memory_key source_ref "
            "summary payload_json _additional { distance id } } } }"
        )
        operation = "weaviate_search"
        try:
            async with self._runtime.httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._runtime.WEAVIATE_URL.rstrip('/')}/v1/graphql",
                    headers=self._headers,
                    json={"query": query},
                )
                data = self._runtime._response_json_object(response, operation)
            if data.get("errors"):
                raise ValueError(f"{operation}: Weaviate GraphQL returned errors")
            graphql_data = data.get("data")
            if not isinstance(graphql_data, dict):
                raise ValueError(f"{operation}: Weaviate GraphQL data missing")
            get_data = graphql_data.get("Get")
            if not isinstance(get_data, dict):
                raise ValueError(f"{operation}: Weaviate GraphQL Get result missing")
            rows = get_data.get(self._runtime.WEAVIATE_CLASS)
            if not isinstance(rows, list):
                raise ValueError(f"{operation}: Weaviate GraphQL class result is not a list")
            out = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"{operation}: invalid Weaviate search item")
                additional = row.get("_additional") or {}
                if not isinstance(additional, dict):
                    raise ValueError(f"{operation}: invalid Weaviate additional result")
                payload = {
                    "namespace": row.get("namespace") or "",
                    "session_key": row.get("session_key") or "",
                    "user_id": int(row.get("user_id") or 0),
                    "memory_kind": row.get("memory_kind") or "",
                    "memory_key": row.get("memory_key") or "",
                    "source_ref": row.get("source_ref") or "",
                    "summary": row.get("summary") or "",
                    "payload_json": row.get("payload_json") or "{}",
                }
                out.append(
                    {
                        "score": max(0.0, 1.0 - float(additional.get("distance") or 1.0)),
                        "payload": payload,
                        "id": additional.get("id"),
                    }
                )
        except Exception as exc:
            self._remote_failed(operation, exc)
            return []
        self._remote_succeeded()
        return out

    async def runtime_stats(self) -> dict[str, Any]:
        if self._runtime.WEAVIATE_URL:
            operation = "weaviate_readiness"
            try:
                url = f"{self._runtime.WEAVIATE_URL.rstrip('/')}/v1/schema/{self._runtime.WEAVIATE_CLASS}"
                async with self._runtime.httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url, headers=self._headers)
                    data = self._runtime._response_json_object(response, operation)
                self._runtime._validate_weaviate_class(data, operation)
            except Exception as exc:
                self._remote_failed(operation, exc)
            else:
                self._remote_succeeded()
        status = self.operation_status()
        return {
            "backend": self.backend_name,
            "enabled": bool(self._runtime.WEAVIATE_URL),
            "ready": status["ready"],
            "error": status["error"],
            "class": self._runtime.WEAVIATE_CLASS,
        }
