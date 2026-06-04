"""Read-only KOL LLM deep-analysis result accessors.

This module only reads vkpi_kol_llm_deep_analysis_results. The llm_v6_fit
field is independent from vkpi_kol_pool.viltrox_fit_score and must not affect
KOL Pool ordering.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def _qa_payload(dimensions: dict[str, Any]) -> dict[str, Any]:
    qa = dimensions.get("qa")
    return qa if isinstance(qa, dict) else {}


def _has_qa(dimensions: dict[str, Any]) -> bool:
    qa = _qa_payload(dimensions)
    return bool(qa and (qa.get("qa_pass") is not None or qa.get("checks") or qa.get("issues") or qa.get("summary")))


def _qa_pass(dimensions: dict[str, Any]) -> bool | None:
    qa = _qa_payload(dimensions)
    value = qa.get("qa_pass")
    return value if isinstance(value, bool) else None


def _source_evidence_sort_value(item: dict[str, Any]) -> int:
    value = item.get("source_evidence_id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _item_sort_key(item: dict[str, Any]) -> tuple[bool, float, float, int]:
    return (
        bool(item.get("llm_has_qa")),
        _number(item.get("llm_v6_fit")),
        _number(item.get("confidence")),
        _source_evidence_sort_value(item),
    )


def _row_to_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    dimensions = _loads(item.get("llm_dimensions_11"), {})
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    normalized = {
        "id": item.get("id"),
        "kol_pool_id": item.get("kol_pool_id"),
        "source_url": item.get("source_url"),
        "source_evidence_id": item.get("source_evidence_id"),
        "analysis_kind": item.get("analysis_kind"),
        "llm_v6_fit": item.get("llm_v6_fit"),
        "llm_dimensions_11": dimensions,
        "method": item.get("method"),
        "provider": item.get("provider"),
        "confidence": item.get("confidence"),
        "source_cache_id": item.get("source_cache_id"),
        "status": item.get("status"),
        "created_at": item.get("created_at"),
        "llm_has_qa": _has_qa(dimensions),
        "llm_qa_pass": _qa_pass(dimensions),
    }
    return _jsonable(normalized)


def get_kol_llm_deep_analysis(kol_pool_id: int, *, limit: int = 20) -> dict[str, Any]:
    """Return read-only deep-analysis rows for one KOL Pool item."""

    safe_limit = max(1, min(int(limit or 20), 50))
    rows = get_conn().execute(
        """
        SELECT
          id,
          kol_pool_id,
          source_url,
          source_evidence_id,
          analysis_kind,
          llm_v6_fit,
          llm_dimensions_11,
          method,
          provider,
          confidence,
          source_cache_id,
          status,
          created_at
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=?
          AND status='ready'
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(kol_pool_id), safe_limit),
    ).fetchall()
    items = [_row_to_item(row) for row in rows]
    if not items:
        return {
            "status": "missing",
            "kol_pool_id": int(kol_pool_id),
            "summary": {
                "count": 0,
                "primary_source_evidence_id": None,
                "llm_v6_fit_max": None,
                "llm_has_qa_count": 0,
                "source": "vkpi_kol_llm_deep_analysis_results",
                "llm_calls": False,
                "write_db": False,
            },
            "primary_result": None,
            "items": [],
            "count": 0,
        }

    sorted_items = sorted(items, key=_item_sort_key, reverse=True)
    primary = sorted_items[0]
    scores = [_number(item.get("llm_v6_fit")) for item in items if item.get("llm_v6_fit") is not None]
    analysis_kind_counts = Counter(str(item.get("analysis_kind") or "") for item in items)
    provider_counts = Counter(str(item.get("provider") or "") for item in items)
    return {
        "status": "ready",
        "kol_pool_id": int(kol_pool_id),
        "summary": {
            "count": len(items),
            "primary_source_evidence_id": primary.get("source_evidence_id"),
            "primary_llm_v6_fit": primary.get("llm_v6_fit"),
            "llm_v6_fit_max": max(scores) if scores else None,
            "llm_v6_fit_min": min(scores) if scores else None,
            "llm_has_qa_count": sum(1 for item in items if item.get("llm_has_qa")),
            "analysis_kind_counts": dict(analysis_kind_counts),
            "provider_counts": dict(provider_counts),
            "sort_policy": "qa_first_then_llm_v6_fit_confidence_source_evidence_desc",
            "source": "vkpi_kol_llm_deep_analysis_results",
            "llm_calls": False,
            "write_db": False,
        },
        "primary_result": primary,
        "items": sorted_items,
        "count": len(items),
    }
