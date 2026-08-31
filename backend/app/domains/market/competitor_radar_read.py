"""Read-result assembly helpers for the competitor-radar facade."""
from __future__ import annotations

from typing import Any


def invalid_radar_result(
    row: dict[str, Any],
    content: dict[str, Any],
    validation_errors: list[Any],
    ops: dict[str, Any],
) -> dict[str, Any]:
    generated_at = ops["_text"](
        content.get("generated_at"), row.get("created_at"), row.get("snapshot_date")
    )
    freshness = ops["_freshness_payload"](generated_at)
    metadata = {
        "status": "invalid",
        "result_status": "invalid",
        "contract_status": "invalid",
        "contract_version": ops["_RESULT_CONTRACT_VERSION"],
        "is_ready": False,
        "snapshot_date": str(row.get("snapshot_date") or ""),
        "generated_at": generated_at,
        "items": [],
        "sources": [],
        "evidence": [],
        "validation_errors": validation_errors,
        "provenance": (
            content.get("provenance")
            if isinstance(content.get("provenance"), dict)
            else {}
        ),
        **freshness,
    }
    return {
        "available": False,
        "reason": "invalid_result_contract",
        "model": row.get("model"),
        **metadata,
        "content": metadata,
    }


def enrich_radar_items(
    items: list[Any],
    market_sources: list[dict[str, Any]],
    grounding_sources: list[dict[str, Any]],
    generated_at: str,
    ops: dict[str, Any],
) -> list[dict[str, Any]]:
    text = ops["_text"]
    enriched: list[dict[str, Any]] = []
    for raw_item in items:
        item = raw_item if isinstance(raw_item, dict) else {}
        brand = str(item.get("brand") or "").strip().lower()
        item_sources = list(item.get("sources")) if isinstance(item.get("sources"), list) else []
        urls = {
            text(source.get("source_url"), source.get("url"))
            for source in item_sources
            if isinstance(source, dict) and source.get("url")
        }
        for source in market_sources:
            source_blob = " ".join(
                str(source.get(key) or "").lower()
                for key in ("brand", "title", "url")
            )
            if not brand or brand not in source_blob:
                continue
            source_url = text(source.get("source_url"), source.get("url"))
            if source_url and source_url not in urls:
                item_sources.append(source)
                urls.add(source_url)
            if len(item_sources) >= 3:
                break
        has_direct_source = any(
            isinstance(source, dict)
            and text(source.get("relation_type")).lower() in ops["_DIRECT_RELATIONS"]
            and text(source.get("source_url"), source.get("url"))
            for source in item_sources
        )
        if not has_direct_source and brand:
            for source in grounding_sources:
                if not isinstance(source, dict):
                    continue
                if brand not in str(source.get("title") or "").lower():
                    continue
                item_sources.append(source)
                break
        enriched.append(
            ops["normalize_signal_item"](
                item, sources=item_sources, observed_at=generated_at,
            )
        )
    origin_rank = {
        ops["_ORIGIN_EXTERNAL"]: 0,
        ops["_ORIGIN_UNKNOWN"]: 1,
        ops["_ORIGIN_OWNED"]: 2,
    }
    enriched.sort(
        key=lambda item: origin_rank.get(str(item.get("content_origin") or ""), 1)
    )
    return enriched


def ready_radar_result(
    row: dict[str, Any],
    content: dict[str, Any],
    *,
    contract_status: str,
    validation_errors: list[Any],
    items: list[Any],
    grounding_sources: list[dict[str, Any]],
    market_sources: list[dict[str, Any]],
    generated_at: str,
    ops: dict[str, Any],
) -> dict[str, Any]:
    enriched_items = enrich_radar_items(
        items, market_sources, grounding_sources, generated_at, ops,
    )
    freshness = ops["_freshness_payload"](
        content.get("generated_at"), row.get("created_at"), row.get("snapshot_date")
    )
    result_status = ops["_result_status"](
        contract_status,
        str(freshness.get("freshness_status") or "unknown"),
        grounded=bool(grounding_sources),
    )
    enriched = {
        **content,
        **freshness,
        "status": result_status,
        "result_status": result_status,
        "contract_status": contract_status,
        "contract_version": ops["_RESULT_CONTRACT_VERSION"],
        "is_ready": result_status == "ready",
        "snapshot_date": str(row.get("snapshot_date") or ""),
        "generated_at": generated_at,
        "items": enriched_items,
        "sources": grounding_sources,
        "evidence": grounding_sources,
        "validation_errors": validation_errors,
        "provenance": (
            content.get("provenance")
            if isinstance(content.get("provenance"), dict)
            else {}
        ),
    }
    return {
        "available": bool(enriched_items),
        "status": result_status,
        "result_status": result_status,
        "contract_status": contract_status,
        "contract_version": ops["_RESULT_CONTRACT_VERSION"],
        "is_ready": result_status == "ready",
        "model": row.get("model"),
        "snapshot_date": enriched["snapshot_date"],
        "generated_at": generated_at,
        **freshness,
        "content": enriched,
    }


def radar_contract_status(contract: dict[str, Any], sources: dict[str, Any]) -> str:
    statuses = {
        str(contract.get("status") or "invalid"),
        str(sources.get("status") or "degraded"),
    }
    if "invalid" in statuses:
        return "invalid"
    if "degraded" in statuses:
        return "degraded"
    return "ready"


__all__ = [
    "invalid_radar_result",
    "radar_contract_status",
    "ready_radar_result",
]
