"""Shared response helpers for deterministic intelligent queries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.intelligent_query.contracts import NormalizedRequest
from app.domains.intelligent_query.repository import freshness_status, parse_timestamp


def latest_observed_at(*values: Any) -> Any:
    dated = [(parse_timestamp(value), value) for value in values if value]
    dated = [(parsed, value) for parsed, value in dated if parsed is not None]
    return max(dated, key=lambda item: item[0])[1] if dated else None


def is_en(request: NormalizedRequest) -> bool:
    return request.locale == "en-US"


def localized(request: NormalizedRequest, zh: str, en: str) -> str:
    return en if is_en(request) else zh


def fact(
    key: str,
    label_zh: str,
    label_en: str,
    value: Any,
    *,
    request: NormalizedRequest,
    value_type: str = "integer",
    unit: str = "",
    basis: str | tuple[str, str],
    confidence: str = "high",
) -> dict[str, Any]:
    resolved_basis = localized(request, basis[0], basis[1]) if isinstance(basis, tuple) else basis
    out = {
        "key": key,
        "label": label_en if is_en(request) else label_zh,
        "value": value,
        "value_type": value_type,
        "basis": resolved_basis,
        "confidence": confidence,
    }
    if unit:
        out["unit"] = unit
    return out


def missing(
    request: NormalizedRequest,
    field: str,
    reason_zh: str,
    reason_en: str,
    impact_zh: str,
    impact_en: str,
) -> dict[str, str]:
    return {
        "field": field,
        "reason": localized(request, reason_zh, reason_en),
        "impact": localized(request, impact_zh, impact_en),
    }


def freshness(
    response: dict[str, Any],
    request: NormalizedRequest,
    *,
    now: datetime,
    updated_at: Any = None,
    windowed: bool = False,
) -> None:
    response["freshness"].update(
        {
            "status": freshness_status(updated_at, now=now),
            "data_updated_at": str(updated_at) if updated_at else None,
        }
    )
    if windowed:
        response["freshness"].update(
            {
                "window_start": request.window.start_iso,
                "window_end": request.window.end_iso,
            }
        )


__all__ = ["fact", "freshness", "is_en", "latest_observed_at", "localized", "missing"]
