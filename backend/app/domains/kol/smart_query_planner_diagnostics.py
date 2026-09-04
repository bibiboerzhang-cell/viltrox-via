"""Provider and JSON-contract diagnostics for the smart-query planner."""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.coerce import _text


def _as_int(value: Any, default: int, *, min_value: int = 0, max_value: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def extract_json(text: str, *, logger: Any) -> dict[str, Any]:
    raw = _text(text)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def planner_not_attempted_diagnostics() -> dict[str, Any]:
    return {
        "provider_calls_performed": False,
        "provider_response_succeeded": False,
        "provider_attempts": 0,
        "provider_response_status": "not_attempted",
        "planner_parse_status": "not_attempted",
        "planner_parse_failed": False,
    }


def planner_response_diagnostics(
    response: dict[str, Any],
    raw_plan: dict[str, Any],
) -> dict[str, Any]:
    """Separate provider execution from the planner's JSON contract outcome."""

    errors = response.get("errors")
    error_rows = errors if isinstance(errors, list) else []
    error_statuses = [
        _text(item.get("status")).lower()
        for item in error_rows
        if isinstance(item, dict) and _text(item.get("status"))
    ]
    provider_attempts = _as_int(
        response.get("provider_attempts"),
        0,
        min_value=0,
        max_value=50,
    )
    response_status = _text(response.get("status")).lower()
    gateway_cache_hit = response.get("cache_hit") is True
    legacy_success_without_execution_fields = bool(
        response_status == "success"
        and "provider_attempts" not in response
        and "provider_calls_performed" not in response
        and not gateway_cache_hit
    )
    provider_calls_performed = bool(
        not gateway_cache_hit
        and (
            provider_attempts
            or response.get("provider_calls_performed") is True
            or legacy_success_without_execution_fields
        )
    )
    provider_response_succeeded = bool(
        not gateway_cache_hit
        and (
            response_status == "success"
            or any(
                status in {"parse_failure", "validation_failure", "empty_response"}
                for status in error_statuses
            )
        )
    )
    contract_failed = bool(
        not raw_plan
        and (
            response_status == "success"
            or any(
                status in {"parse_failure", "validation_failure", "empty_response"}
                for status in error_statuses
            )
        )
    )
    if raw_plan:
        parse_status = "cached_valid" if gateway_cache_hit else "success"
    elif contract_failed:
        parse_status = "planner_parse_failed"
    elif provider_calls_performed:
        parse_status = "provider_failed"
    else:
        parse_status = "not_attempted"
    return {
        "provider_calls_performed": provider_calls_performed,
        "provider_response_succeeded": provider_response_succeeded,
        "provider_attempts": provider_attempts,
        "provider_response_status": (
            "gateway_cache_hit" if gateway_cache_hit else response_status or "unknown"
        ),
        "planner_parse_status": parse_status,
        "planner_parse_failed": contract_failed,
        "gateway_cache_hit": gateway_cache_hit,
        "gateway_cache_key": _text(response.get("cache_key")),
        "gateway_cache_origin_call_uid": _text(
            response.get("cache_origin_call_uid")
        ),
    }
