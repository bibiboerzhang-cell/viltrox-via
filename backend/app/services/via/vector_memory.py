"""
services/via/vector_memory.py — Optional vector memory layer for Via
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

import httpx

from app.core.config import (
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_LOCAL_PATH,
    QDRANT_URL,
    VIA_BGE_MODEL,
    VIA_EMBEDDING_BACKEND,
    VIA_EMBEDDING_MODEL,
    VIA_VECTOR_BACKEND,
    VIA_VECTOR_TOP_K,
    WEAVIATE_API_KEY,
    WEAVIATE_CLASS,
    WEAVIATE_URL,
)
from app.core.logging import get_logger
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE, openai_client
from app.services.via.model_router import summarize_via_exchange

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qdrant_models
except Exception:  # pragma: no cover - optional local backend
    QdrantClient = None
    qdrant_models = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - optional local backend
    SentenceTransformer = None

_bge_encoder: Any | None = None
_vector_backend_singleton: Any | None = None
logger = get_logger(__name__)

# 护栏③ enforce:per-request embedding 批量硬上限。当前最大真实批次=128(seed 路径
# knowledge_seed.build_via_seed_documents -> docs[:128]),128 不截断现有调用又挡住失控 fan-out。
MAX_EMBED_BATCH = 128


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "").strip() for part in parts if str(part or "").strip())
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def via_namespace_for_bundle(bundle: dict[str, Any]) -> str:
    persona = bundle.get("persona") or {}
    session = bundle.get("session") or {}
    persona_key = str(persona.get("persona_key") or "").strip()
    if persona_key:
        return persona_key
    user_id = int(session.get("user_id") or persona.get("user_id") or 0)
    if user_id:
        return f"user:{user_id}:primary"
    session_key = str(session.get("session_key") or "").strip()
    return f"anon:{session_key or 'default'}"


def _ref_text(item: dict[str, Any]) -> str:
    payload = item.get("payload") or {}
    summary = str(payload.get("summary") or "").strip()
    if summary:
        return summary
    fact_value = payload.get("fact_value_json")
    if isinstance(fact_value, str) and fact_value.strip():
        return fact_value[:500]
    if isinstance(fact_value, (dict, list)):
        return json.dumps(fact_value, ensure_ascii=False)[:500]
    parts = [
        str(item.get("memory_kind") or "").strip(),
        str(item.get("memory_key") or "").strip(),
        str(item.get("source_ref") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)[:300]


def _ref_payload(namespace: str, session_key: str, user_id: int, item: dict[str, Any], summary: str) -> dict[str, Any]:
    payload = item.get("payload") or {}
    return {
        "namespace": namespace,
        "session_key": session_key,
        "user_id": int(user_id or 0),
        "memory_kind": str(item.get("memory_kind") or "vector_memory"),
        "memory_key": str(item.get("memory_key") or ""),
        "source_ref": str(item.get("source_ref") or ""),
        "summary": summary[:300],
        "payload_json": json.dumps(payload, ensure_ascii=False)[:3000],
    }


def _conversation_text(user_text: str, reply_text: str, summary: str, keywords: list[str], traits: dict[str, Any], current_surface: str) -> str:
    parts = [
        f"summary: {summary}",
        f"user: {str(user_text or '').strip()}",
        f"reply: {str(reply_text or '').strip()}",
        f"keywords: {', '.join(keywords)}" if keywords else "",
        f"traits: {json.dumps(traits, ensure_ascii=False)}" if traits else "",
        f"surface: {current_surface}",
    ]
    return "\n".join(part for part in parts if part).strip()[:4000]


def _dedupe_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("memory_kind") or ""), str(item.get("source_ref") or item.get("memory_key") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _seed_payload(
    namespace: str,
    session_key: str,
    user_id: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(item.get("payload") or {})
    payload.update(
        {
            "namespace": namespace,
            "session_key": session_key,
            "user_id": int(user_id or 0),
            "memory_kind": str(item.get("memory_kind") or "seed_doc"),
            "memory_key": str(item.get("memory_key") or ""),
            "source_ref": str(item.get("source_ref") or ""),
            "summary": str(item.get("summary") or item.get("text") or "")[:300],
            "text_snippet": str(item.get("text") or payload.get("text_snippet") or "")[:1200],
            "payload_json": json.dumps(item.get("payload") or {}, ensure_ascii=False)[:3000],
        }
    )
    return payload


def _embed_openai_sync(texts: list[str]) -> list[list[float]]:
    if not OPENAI_AVAILABLE or not openai_client:
        return []
    batch = list(texts)[:MAX_EMBED_BATCH]
    resp = openai_client.embeddings.create(model=VIA_EMBEDDING_MODEL, input=batch)
    return [[float(value) for value in item.embedding] for item in (resp.data or [])]


def _embed_bge_sync(texts: list[str]) -> list[list[float]]:
    global _bge_encoder
    if SentenceTransformer is None:
        return []
    if _bge_encoder is None:
        _bge_encoder = SentenceTransformer(VIA_BGE_MODEL)
    vectors = _bge_encoder.encode(texts, normalize_embeddings=True)
    return [[float(value) for value in row] for row in vectors]


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    clean = [str(text or "").strip()[:7000] for text in texts if str(text or "").strip()]
    if not clean:
        return []
    if VIA_EMBEDDING_BACKEND == "bge":
        return await asyncio.to_thread(_embed_bge_sync, clean)
    if VIA_EMBEDDING_BACKEND == "openai":
        return await asyncio.to_thread(_embed_openai_sync, clean)
    return []


class _NullVectorBackend:
    backend_name = "none"

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        return 0

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        return []

    async def runtime_stats(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "enabled": False}


class _QdrantVectorBackend:
    backend_name = "qdrant"

    def __init__(self) -> None:
        self._ensured_dim = 0
        self._local_client: Any | None = None
        self._local_disabled_reason = ""

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if QDRANT_API_KEY:
            headers["api-key"] = QDRANT_API_KEY
        return headers

    @property
    def _use_local(self) -> bool:
        return (
            not self._local_disabled_reason
            and not QDRANT_URL
            and bool(QdrantClient and qdrant_models and QDRANT_LOCAL_PATH)
        )

    def _disable_local(self, reason: str) -> None:
        self._local_disabled_reason = str(reason or "local_qdrant_disabled").strip()[:240]
        self._local_client = None
        logger.warning(
            "via.vector_memory.local_qdrant_disabled | path=%s | reason=%s",
            QDRANT_LOCAL_PATH,
            self._local_disabled_reason,
        )

    def _get_local_client(self) -> Any | None:
        if not self._use_local:
            return None
        if self._local_client is None:
            try:
                self._local_client = QdrantClient(path=QDRANT_LOCAL_PATH)
            except Exception as exc:
                self._disable_local(str(exc))
                return None
        return self._local_client

    async def _ensure_collection(self, dim: int) -> None:
        if dim <= 0:
            return
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return
            if self._ensured_dim == dim:
                return
            try:
                exists = await asyncio.to_thread(client.collection_exists, QDRANT_COLLECTION)
            except Exception as exc:
                self._disable_local(str(exc))
                return
            if not exists:
                try:
                    await asyncio.to_thread(
                        client.create_collection,
                        collection_name=QDRANT_COLLECTION,
                        vectors_config=qdrant_models.VectorParams(size=dim, distance=qdrant_models.Distance.COSINE),
                    )
                except Exception as exc:
                    self._disable_local(str(exc))
                    return
            self._ensured_dim = dim
            return
        if not QDRANT_URL:
            return
        if self._ensured_dim == dim:
            return
        url = f"{QDRANT_URL.rstrip('/')}/collections/{QDRANT_COLLECTION}"
        payload = {"vectors": {"size": dim, "distance": "Cosine"}}
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.put(url, headers=self._headers, json=payload)
        self._ensured_dim = dim

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        if not docs:
            return 0
        if not self._use_local and not QDRANT_URL:
            return 0
        vectors = await _embed_texts([doc["text"] for doc in docs])
        if not vectors:
            return 0
        await self._ensure_collection(len(vectors[0]))
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return 0
            points = []
            for doc, vector in zip(docs, vectors):
                payload = dict(doc.get("payload") or {})
                payload["namespace"] = namespace
                points.append(
                    qdrant_models.PointStruct(
                        id=doc["id"],
                        vector=vector,
                        payload=payload,
                    )
                )
            try:
                await asyncio.to_thread(
                    client.upsert,
                    collection_name=QDRANT_COLLECTION,
                    points=points,
                    wait=False,
                )
            except Exception as exc:
                self._disable_local(str(exc))
                return 0
            return len(points)
        if not QDRANT_URL:
            return 0
        points = []
        for doc, vector in zip(docs, vectors):
            payload = dict(doc.get("payload") or {})
            payload["namespace"] = namespace
            points.append({"id": doc["id"], "vector": vector, "payload": payload})
        url = f"{QDRANT_URL.rstrip('/')}/collections/{QDRANT_COLLECTION}/points"
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.put(url, headers=self._headers, json={"points": points, "wait": False})
        return len(points)

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        if not self._use_local and not QDRANT_URL:
            return []
        vectors = await _embed_texts([query_text])
        if not vectors:
            return []
        await self._ensure_collection(len(vectors[0]))
        if self._use_local:
            client = self._get_local_client()
            if not client:
                return []
            try:
                hits = await asyncio.to_thread(
                    client.search,
                    collection_name=QDRANT_COLLECTION,
                    query_vector=vectors[0],
                    limit=max(1, int(limit)),
                    query_filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="namespace",
                                match=qdrant_models.MatchValue(value=namespace),
                            )
                        ]
                    ),
                    with_payload=True,
                )
            except Exception as exc:
                self._disable_local(str(exc))
                return []
            return [
                {
                    "score": float(getattr(item, "score", 0.0) or 0.0),
                    "payload": dict(getattr(item, "payload", None) or {}),
                    "id": getattr(item, "id", None),
                }
                for item in hits
            ]
        if not QDRANT_URL:
            return []
        payload = {
            "vector": vectors[0],
            "limit": max(1, int(limit)),
            "with_payload": True,
            "filter": {"must": [{"key": "namespace", "match": {"value": namespace}}]},
        }
        url = f"{QDRANT_URL.rstrip('/')}/collections/{QDRANT_COLLECTION}/points/search"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=self._headers, json=payload)
            data = resp.json() if resp.content else {}
        hits = data.get("result") or []
        return [
            {
                "score": float(item.get("score") or 0.0),
                "payload": item.get("payload") or {},
                "id": item.get("id"),
            }
            for item in hits
        ]

    async def runtime_stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "enabled": bool(QDRANT_URL or self._use_local),
            "collection": QDRANT_COLLECTION,
            "mode": "local" if self._use_local else "remote",
            "path": QDRANT_LOCAL_PATH if self._use_local else "",
            "local_disabled_reason": self._local_disabled_reason,
        }


class _WeaviateVectorBackend:
    backend_name = "weaviate"

    def __init__(self) -> None:
        self._ensured_dim = 0

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if WEAVIATE_API_KEY:
            headers["Authorization"] = f"Bearer {WEAVIATE_API_KEY}"
        return headers

    async def _ensure_class(self) -> None:
        if not WEAVIATE_URL or self._ensured_dim:
            return
        url = f"{WEAVIATE_URL.rstrip('/')}/v1/schema"
        payload = {
            "class": WEAVIATE_CLASS,
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
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, headers=self._headers, json=payload)
        self._ensured_dim = 1

    async def upsert(self, namespace: str, docs: list[dict[str, Any]]) -> int:
        if not WEAVIATE_URL or not docs:
            return 0
        vectors = await _embed_texts([doc["text"] for doc in docs])
        if not vectors:
            return 0
        await self._ensure_class()
        async with httpx.AsyncClient(timeout=15.0) as client:
            for doc, vector in zip(docs, vectors):
                payload = dict(doc.get("payload") or {})
                payload["namespace"] = namespace
                await client.post(
                    f"{WEAVIATE_URL.rstrip('/')}/v1/objects",
                    headers=self._headers,
                    json={
                        "class": WEAVIATE_CLASS,
                        "id": doc["id"],
                        "properties": payload,
                        "vector": vector,
                    },
                )
        return len(docs)

    async def search(self, namespace: str, query_text: str, limit: int) -> list[dict[str, Any]]:
        if not WEAVIATE_URL:
            return []
        vectors = await _embed_texts([query_text])
        if not vectors:
            return []
        await self._ensure_class()
        vector = json.dumps(vectors[0])
        query = (
            "{ Get { "
            f"{WEAVIATE_CLASS}("
            f'nearVector: {{ vector: {vector} }}, '
            f'where: {{ path:[\"namespace\"], operator: Equal, valueText: \"{namespace}\" }}, '
            f"limit: {max(1, int(limit))}"
            ") { namespace session_key user_id memory_kind memory_key source_ref summary payload_json _additional { distance id } } } }"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{WEAVIATE_URL.rstrip('/')}/v1/graphql",
                headers=self._headers,
                json={"query": query},
            )
            data = resp.json() if resp.content else {}
        rows = (((data.get("data") or {}).get("Get") or {}).get(WEAVIATE_CLASS) or [])
        out = []
        for row in rows:
            additional = row.get("_additional") or {}
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
        return out

    async def runtime_stats(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "enabled": bool(WEAVIATE_URL), "class": WEAVIATE_CLASS}


def build_via_vector_backend() -> Any:
    global _vector_backend_singleton
    if _vector_backend_singleton is not None:
        return _vector_backend_singleton
    if VIA_VECTOR_BACKEND == "qdrant":
        _vector_backend_singleton = _QdrantVectorBackend()
    elif VIA_VECTOR_BACKEND == "weaviate":
        _vector_backend_singleton = _WeaviateVectorBackend()
    else:
        _vector_backend_singleton = _NullVectorBackend()
    return _vector_backend_singleton


async def sync_bundle_memory_refs_to_vector(bundle: dict[str, Any]) -> dict[str, Any]:
    backend = build_via_vector_backend()
    namespace = via_namespace_for_bundle(bundle)
    session = bundle.get("session") or {}
    user_id = int(session.get("user_id") or 0)
    docs = []
    for item in _dedupe_refs(bundle.get("memory_refs") or [])[:24]:
        summary = _ref_text(item)
        if not summary:
            continue
        docs.append(
            {
                "id": _stable_id(namespace, item.get("memory_kind"), item.get("source_ref"), item.get("memory_key"), summary[:120]),
                "text": summary,
                "payload": _ref_payload(namespace, str(session.get("session_key") or ""), user_id, item, summary),
            }
        )
    count = await backend.upsert(namespace, docs)
    return {"backend": getattr(backend, "backend_name", "none"), "namespace": namespace, "upserted": count}


async def recall_via_vector_memory(bundle: dict[str, Any], query_text: str, limit: int = VIA_VECTOR_TOP_K) -> list[dict[str, Any]]:
    backend = build_via_vector_backend()
    namespace = via_namespace_for_bundle(bundle)
    hits = await backend.search(namespace, query_text, max(1, int(limit)))
    refs = []
    for hit in hits:
        payload = hit.get("payload") or {}
        summary = str(payload.get("summary") or "").strip()
        refs.append(
            {
                "memory_kind": "vector_recall",
                "source_ref": str(payload.get("source_ref") or hit.get("id") or ""),
                "memory_key": str(payload.get("memory_key") or ""),
                "weight": float(hit.get("score") or 0.0),
                "payload": {
                    "summary": summary[:240],
                    "text_snippet": str(payload.get("text_snippet") or "")[:600],
                    "source": "vector_memory",
                    "session_key": payload.get("session_key") or "",
                    "payload_json": payload.get("payload_json") or "{}",
                },
            }
        )
    return _dedupe_refs(refs)


async def store_via_vector_exchange(
    bundle: dict[str, Any],
    *,
    user_text: str,
    reply_text: str,
    signals: dict[str, Any] | None = None,
    current_surface: str = "upload",
) -> dict[str, Any]:
    backend = build_via_vector_backend()
    namespace = via_namespace_for_bundle(bundle)
    session = bundle.get("session") or {}
    persona = bundle.get("persona") or {}
    summary_block = await summarize_via_exchange(
        user_text=user_text,
        reply_text=reply_text,
        signals=signals or {},
        current_surface=current_surface,
    )
    text = _conversation_text(
        user_text,
        reply_text,
        summary_block.get("summary") or "",
        summary_block.get("keywords") or [],
        (signals or {}).get("traits") or {},
        current_surface,
    )
    source_ref = f"via-vector:{session.get('session_key')}:{_stable_id(user_text, reply_text)[:16]}"
    doc = {
        "id": _stable_id(namespace, source_ref),
        "text": text,
        "payload": {
            "namespace": namespace,
            "session_key": str(session.get("session_key") or ""),
            "user_id": int(session.get("user_id") or 0),
            "persona_key": str(persona.get("persona_key") or ""),
            "memory_kind": "conversation_signal",
            "memory_key": str(summary_block.get("summary") or "")[:120],
            "source_ref": source_ref,
            "summary": str(summary_block.get("summary") or "")[:300],
            "payload_json": json.dumps(
                {
                    "keywords": summary_block.get("keywords") or [],
                    "traits": (signals or {}).get("traits") or {},
                    "surface": current_surface,
                },
                ensure_ascii=False,
            )[:3000],
        },
    }
    upserted = await backend.upsert(namespace, [doc])
    return {
        "backend": getattr(backend, "backend_name", "none"),
        "namespace": namespace,
        "upserted": upserted,
        "summary": summary_block.get("summary") or "",
        "keywords": summary_block.get("keywords") or [],
        "provider": summary_block.get("provider") or "fallback",
        "model": summary_block.get("model") or "",
    }


async def store_via_seed_documents(bundle: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    backend = build_via_vector_backend()
    namespace = via_namespace_for_bundle(bundle)
    session = bundle.get("session") or {}
    session_key = str(session.get("session_key") or "")
    user_id = int(session.get("user_id") or 0)
    docs = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        docs.append(
            {
                "id": _stable_id(
                    namespace,
                    item.get("memory_kind"),
                    item.get("source_ref"),
                    item.get("memory_key"),
                    text[:160],
                ),
                "text": text[:5000],
                "payload": _seed_payload(namespace, session_key, user_id, item),
            }
        )
    upserted = await backend.upsert(namespace, docs)
    return {
        "backend": getattr(backend, "backend_name", "none"),
        "namespace": namespace,
        "upserted": upserted,
    }
