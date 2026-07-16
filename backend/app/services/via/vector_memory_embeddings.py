"""Embedding helpers for Via vector memory."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def embed_openai_sync(
    texts: list[str],
    *,
    openai_available: bool,
    openai_client: Any,
    model: str,
    max_batch: int,
) -> list[list[float]]:
    if not openai_available or not openai_client:
        return []
    batch = list(texts)[:max_batch]
    response = openai_client.embeddings.create(model=model, input=batch)
    return [[float(value) for value in item.embedding] for item in (response.data or [])]


def embed_bge_sync(
    texts: list[str],
    *,
    encoder: Any | None,
    sentence_transformer: Any,
    model: str,
) -> tuple[list[list[float]], Any | None]:
    if sentence_transformer is None:
        return [], encoder
    if encoder is None:
        encoder = sentence_transformer(model)
    vectors = encoder.encode(texts, normalize_embeddings=True)
    return [[float(value) for value in row] for row in vectors], encoder


async def embed_texts(
    texts: list[str],
    *,
    backend: str,
    embed_bge: Callable[[list[str]], list[list[float]]],
    embed_openai: Callable[[list[str]], list[list[float]]],
    to_thread: Callable[..., Any],
) -> list[list[float]]:
    clean = [str(text or "").strip()[:7000] for text in texts if str(text or "").strip()]
    if not clean:
        return []
    if backend == "bge":
        return await to_thread(embed_bge, clean)
    if backend == "openai":
        return await to_thread(embed_openai, clean)
    return []
