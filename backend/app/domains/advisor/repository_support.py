"""Scoped persistence for Marketing Advisor conversations and personal memory.

Every query includes both ``organization_id`` and ``staff_id``.  Callers never
pass a free-form organization id; they pass a fail-closed :class:`AdvisorScope`.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.advisor.scope import AdvisorScope


_SCHEMA_TABLE = "vkpi_advisor_threads"
_CLAIM_TABLE = "vkpi_advisor_turn_claims"
ALLOWED_CONTEXT_TYPES = frozenset({"kol", "product", "project", "event", "dealer"})
ALLOWED_MEMORY_KINDS = frozenset(
    {"preference", "semantic", "episodic", "business_goal", "constraint"}
)
ALLOWED_SENSITIVITY = frozenset({"normal", "sensitive", "restricted"})
ALLOWED_ACTION_TYPES = frozenset(
    {"send_message", "external_contact", "write_business_data", "incur_cost", "business_change"}
)


class AdvisorRepositoryError(RuntimeError):
    code = "advisor_repository_error"


class AdvisorSchemaUnavailable(AdvisorRepositoryError):
    code = "advisor_schema_unavailable"


class AdvisorNotFound(AdvisorRepositoryError):
    code = "advisor_not_found"


class AdvisorConflict(AdvisorRepositoryError):
    code = "advisor_conflict"


class AdvisorValidationError(AdvisorRepositoryError):
    code = "advisor_validation_error"


def _new_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_loads(value: Any, *, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return fallback
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _positive_limit(value: Any, *, default: int = 50, maximum: int = 200) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _retention_days(value: Any) -> int:
    """Return a bounded owner retention window even for corrupted legacy rows."""

    try:
        parsed = int(value or 180)
    except (TypeError, ValueError):
        parsed = 180
    return max(1, min(parsed, 3650))


def _retention_cutoff(retention_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=_retention_days(retention_days))


def _retention_policy(retention_days: int, cutoff: datetime) -> dict[str, Any]:
    """Describe the enforced read window without implying physical deletion."""

    return {
        "mode": "read_window",
        "retention_days": _retention_days(retention_days),
        "cutoff_at": cutoff.isoformat().replace("+00:00", "Z"),
        "candidate_clock": "created_at",
        "fact_clock": "updated_at",
        "expired_rows_returned": False,
        "physical_delete_performed": False,
    }


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return _text(value, 300)
    if isinstance(value, dict):
        return {
            _text(key, 80): _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
            if _text(key, 80)
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_json(item, depth=depth + 1) for item in list(value)[:24]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value, 300)


def sanitize_provenance(value: Any) -> dict[str, str]:
    source = value if isinstance(value, dict) else {}
    content_hash = _text(source.get("content_hash"), 64).lower()
    if content_hash and (
        len(content_hash) != 64 or any(char not in "0123456789abcdef" for char in content_hash)
    ):
        raise AdvisorValidationError("provenance content_hash must be 64 lowercase hex characters")
    return {
        "source_ref": _text(source.get("source_ref"), 500),
        "source_passport_id": _text(source.get("source_passport_id"), 160),
        "observed_at": _text(source.get("observed_at"), 64),
        "content_hash": content_hash,
    }


def sanitize_context_refs(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AdvisorValidationError("context_refs must be a list")
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value[:12]:
        if not isinstance(raw, dict):
            raise AdvisorValidationError("each context reference must be an object")
        entity_type = _text(raw.get("entity_type"), 40).lower()
        entity_id = _text(raw.get("entity_id"), 160)
        if entity_type not in ALLOWED_CONTEXT_TYPES:
            raise AdvisorValidationError(f"unsupported context entity_type: {entity_type or 'missing'}")
        if not entity_id:
            raise AdvisorValidationError("context entity_id is required")
        key = (entity_type, entity_id)
        if key in seen:
            continue
        seen.add(key)
        snapshot_raw = raw.get("snapshot") if isinstance(raw.get("snapshot"), dict) else {}
        refs.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "snapshot": {
                    "label": _text(snapshot_raw.get("label"), 240),
                    "platform": _text(snapshot_raw.get("platform"), 40),
                    "observed_at": _text(snapshot_raw.get("observed_at"), 64),
                },
                "provenance": sanitize_provenance(raw.get("provenance")),
            }
        )
    return refs


def sanitize_action_drafts(value: Any) -> list[dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AdvisorValidationError("requested_actions must be a list")
    drafts: list[dict[str, Any]] = []
    for raw in value[:12]:
        if not isinstance(raw, dict):
            raise AdvisorValidationError("each requested action must be an object")
        action_type = _text(raw.get("action_type"), 40).lower()
        if action_type not in ALLOWED_ACTION_TYPES:
            raise AdvisorValidationError(f"unsupported action_type: {action_type or 'missing'}")
        try:
            estimated_cost_cents = int(raw.get("estimated_cost_cents") or 0)
        except (TypeError, ValueError) as exc:
            raise AdvisorValidationError("estimated_cost_cents must be an integer") from exc
        if estimated_cost_cents < 0 or estimated_cost_cents > 100_000_000:
            raise AdvisorValidationError("estimated_cost_cents is outside the accepted draft range")
        drafts.append(
            {
                "action_type": action_type,
                "target_type": _text(raw.get("target_type"), 40),
                "target_id": _text(raw.get("target_id"), 160),
                "estimated_cost_cents": estimated_cost_cents,
                "writes_business_data": bool(
                    raw.get("writes_business_data")
                    or action_type in {"write_business_data", "business_change"}
                ),
                "payload": _bounded_json(raw.get("payload") or {}),
                "provenance": sanitize_provenance(raw.get("provenance")),
            }
        )
    return drafts


_JSON_COLUMNS = {
    "context_refs_json",
    "provenance_json",
    "metadata_json",
    "value_json",
    "payload_json",
    "detail_json",
}


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    item = dict(row)
    for key in _JSON_COLUMNS:
        if key not in item:
            continue
        fallback: Any = [] if key == "context_refs_json" else {}
        item[key] = _json_loads(item.get(key), fallback=fallback)
    for key in ("writes_business_data",):
        if key in item:
            item[key] = bool(item.get(key))
    return item


def _ensure_schema() -> None:
    if not table_exists(_SCHEMA_TABLE):
        raise AdvisorSchemaUnavailable("migration 250_vkpi_marketing_advisor_memory.sql is not applied")


def schema_ready() -> bool:
    try:
        return bool(table_exists(_SCHEMA_TABLE))
    except Exception:
        return False


def claim_schema_ready() -> bool:
    """Return whether the durable billable-turn claim table is installed."""

    try:
        return bool(table_exists(_CLAIM_TABLE))
    except Exception:
        return False


def _scope_params(scope: AdvisorScope) -> tuple[int, int]:
    return scope.organization_id, scope.staff_id


def _claim_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _request_sha256(value: Any) -> str:
    text = _text(value, 64).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise AdvisorValidationError("request_sha256 must be 64 lowercase hex characters")
    return text


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
