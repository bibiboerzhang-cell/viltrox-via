"""KPI source row evidence enrichment helpers."""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.shared import vkpi_kpi_evidence_enrichment

logger = get_logger(__name__)


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.warning("vkpi kpi evidence json parse failed: %s", exc)
        return {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _row(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    try:
        item = conn.execute(sql, params).fetchone()
        return dict(item) if item else {}
    except Exception as exc:
        logger.warning("vkpi kpi evidence query failed: %s", exc)
        return {}


def _source_ref_id(source_ref: Any, prefix: str) -> int:
    raw = str(source_ref or "")
    marker = f"{prefix}:"
    if not raw.startswith(marker):
        return 0
    return _int(raw.split(marker, 1)[1].split(":", 1)[0])


def _add_entity(entities: list[dict[str, Any]], entity_type: str, item: dict[str, Any], label_key: str = "name") -> None:
    if not item:
        return
    entities.append(
        {
            "type": entity_type,
            "id": item.get("id"),
            "label": item.get(label_key) or item.get("project_name") or item.get("channel_name") or item.get("slug") or item.get("source_ref") or item.get("recommendation_uid") or item.get("order_name") or item.get("id"),
        }
    )


def enrich_kpi_source_row(conn: Any, source_row: dict[str, Any]) -> dict[str, Any]:
    """Attach source_context to one KPI ledger row.

    The ledger remains append-only; this only resolves human-readable context for
    staff/KOL evidence drawers and does not change metric values.
    """
    row = dict(source_row)
    metadata = _parse_json(row.get("metadata_json") or row.get("metadata"))
    source_ref = row.get("source_ref")
    source_type = str(row.get("source_type") or "")
    context: dict[str, Any] = {"entities": []}
    entities: list[dict[str, Any]] = context["entities"]
    vkpi_kpi_evidence_enrichment.enrich_context(
        conn,
        row,
        metadata,
        source_ref,
        source_type,
        context,
        entities,
        globals(),
    )

    if metadata.get("formula"):
        context["formula"] = metadata.get("formula")
        context["components"] = metadata.get("components") or []
    context["entity_count"] = len(entities)
    row["metadata"] = metadata
    row["source_context"] = context
    return row
