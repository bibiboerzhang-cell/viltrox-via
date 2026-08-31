"""Generation helpers for the competitor radar orchestration entrypoint."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping


def brand_ascii_key(value: Any) -> str:
    """Strip model-added CJK annotations while retaining the ASCII brand key."""
    raw = str(value or "").lower()
    raw = re.sub(r"[\(（][^\)）]*[\)）]", " ", raw)
    return " ".join(re.findall(r"[a-z0-9][a-z0-9&'-]*", raw)).strip()


def fetch_final_url(url: str, timeout_seconds: float) -> str:
    import httpx

    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=timeout_seconds
    ) as response:
        return str(response.url)


def ensure_schema(ops: Mapping[str, Any]) -> None:
    """Keep PostgreSQL migration-owned and bootstrap only SQLite fixtures."""
    if ops["is_postgres_runtime"]():
        return
    conn = ops["get_conn"]()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_competitor_radar (
            snapshot_date DATE PRIMARY KEY,
            content_json  TEXT NOT NULL,
            model         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def item_has_grounding(
    item: dict[str, Any],
    grounding_sources: list[dict[str, Any]],
    ops: Mapping[str, Any],
) -> bool:
    item_url = str(item.get("source_url") or "")
    brand = str(item.get("brand") or "").strip().lower()
    brand_key = ops["_brand_ascii_key"](brand)
    for source in grounding_sources:
        if not isinstance(source, dict):
            continue
        source_url = ops["_text"](source.get("source_url"), source.get("url"))
        source_blob = " ".join(
            str(source.get(key) or "").lower()
            for key in ("title", "source_url", "url")
        )
        if (
            (item_url and item_url == source_url)
            or (brand and brand in source_blob)
            or (brand_key and brand_key in source_blob)
        ):
            return True
    return False


def grounded_items(
    items: list[Any],
    grounding_sources: list[dict[str, Any]],
    ops: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    grounded = [
        item
        for item in items
        if isinstance(item, dict) and item_has_grounding(item, grounding_sources, ops)
    ]
    count = sum(1 for item in items if isinstance(item, dict)) - len(grounded)
    return grounded, count


def normalized_items(
    items: list[dict[str, Any]],
    grounding_sources: list[dict[str, Any]],
    *,
    generated_at: str,
    ops: Mapping[str, Any],
) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    for item in items[:6]:
        brand = str(item.get("brand") or "")[:40]
        item_sources = (
            list(item.get("sources"))
            if isinstance(item.get("sources"), list)
            else []
        )
        item_url = ops["_text"](item.get("source_url"), item.get("url"))
        brand_key = ops["_brand_ascii_key"](brand)
        for source in grounding_sources:
            source_url = ops["_text"](
                source.get("source_url"), source.get("url")
            )
            source_blob = " ".join(
                str(source.get(key) or "").lower()
                for key in ("title", "source_url", "url")
            )
            if (
                (item_url and source_url == item_url)
                or (brand and brand.lower() in source_blob)
                or (brand_key and brand_key in source_blob)
            ):
                item_sources.append(source)
        clean.append(
            ops["normalize_signal_item"](
                {
                    "signal_type": ops["_text"](
                        item.get("signal_type"), "competitor"
                    )[:40],
                    "brand": brand,
                    "title": str(item.get("title") or "")[:160],
                    "summary": str(item.get("summary") or "")[:240],
                    "impact": str(item.get("impact") or "")[:240],
                    "content_origin": item.get("content_origin"),
                    "source_platform": item.get("source_platform"),
                    "source_url": item_url,
                    "published_at": item.get("published_at"),
                    "observed_at": generated_at,
                    "account_handle": item.get("account_handle"),
                    "author_handle": item.get("author_handle"),
                    "channel_handle": item.get("channel_handle"),
                },
                sources=item_sources,
                observed_at=generated_at,
            )
        )
    return clean


def persist_ready_payload(
    payload: dict[str, Any],
    *,
    model_used: Any,
    ops: Mapping[str, Any],
) -> None:
    ops["_ensure_schema"]()
    conn = ops["get_conn"]()
    conn.execute(
        """
        INSERT INTO vkpi_competitor_radar (snapshot_date, content_json, model)
        VALUES (CURRENT_DATE, ?, ?)
        ON CONFLICT (snapshot_date) DO UPDATE
          SET content_json = excluded.content_json, model = excluded.model, created_at = now()
        """,
        (json.dumps(payload, ensure_ascii=False), str(model_used or "")),
    )
    conn.commit()
