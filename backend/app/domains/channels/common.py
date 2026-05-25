"""Employee platform bindings and sync state for Viltrox Marketing."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import secrets
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.cache import cache_clear, cache_get, cache_set
from app.domains.access import scope
from app.domains.media import cached_image_url, cached_video_url, cached_video_url_for_item
from app.services.vkpi.official_post_identity import canonical_post_identity
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok", "xhs", "xiaohongshu", "bilibili", "facebook", "reddit", "x", "threads", "twitch", "pinterest", "vimeo", "website", "other"}
CHANNEL_READ_CACHE_TTL_SEC = 300
CHANNEL_READ_CACHE_VERSION = "v3"
logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _channel_cache_scope(staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> str:
    if view_as_staff_id:
        return f"staff:{int(view_as_staff_id)}"
    if scope.can_view_all(staff):
        return "all"
    return f"staff:{resolve_staff_id(staff) or 0}"


def _channel_cache_key(name: str, *, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 0) -> str:
    return f"vkpi:channels:{CHANNEL_READ_CACHE_VERSION}:{name}:{_channel_cache_scope(staff, view_as_staff_id)}:limit:{int(limit or 0)}"


def _clear_channel_read_cache() -> None:
    try:
        cache_clear(prefix="vkpi:channels:")
    except Exception as exc:
        logger.warning("vkpi channel cache clear failed: %s", exc)


def _channel_cache_hit(payload: Any) -> Any:
    if isinstance(payload, dict):
        result = dict(payload)
        result["cache"] = {"hit": True, "ttl_sec": CHANNEL_READ_CACHE_TTL_SEC}
        return result
    return payload


def _channel_cache_store(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = {**payload, "cache": {"hit": False, "ttl_sec": CHANNEL_READ_CACHE_TTL_SEC}}
    cache_set(key, result, ttl=CHANNEL_READ_CACHE_TTL_SEC)
    return result


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _count(value: Any) -> int:
    return max(0, _int(value))


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.debug("vkpi channel json parse failed: %s", exc)
        return {}


def _is_official_channel_row(row: dict[str, Any]) -> bool:
    metadata = _parse_json(row.get("metadata_json"))
    return (
        _bool(metadata.get("official_account"))
        or _bool(metadata.get("official_list_20260516"))
        or _text(metadata.get("binding_source")) == "official_account_list_2026_05_16"
    )


def _cumulative_floor_status(raw_payload: dict[str, Any], *, sample_count: int = 0) -> dict[str, Any]:
    floor = raw_payload.get("cumulative_floor") if isinstance(raw_payload, dict) else {}
    if not isinstance(floor, dict):
        floor = {}
    fields = floor.get("fields") if isinstance(floor.get("fields"), dict) else {}
    protected_fields = sorted(str(key) for key, value in fields.items() if value is not None)
    if not protected_fields:
        return {
            "baseline_protected": False,
            "baseline_protected_fields": [],
            "baseline_protected_detail": {},
        }
    details: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, dict):
            details[str(key)] = {
                "provider_value": _int(value.get("provider_value")),
                "kept_value": _int(value.get("kept_value")),
            }
    if sample_count:
        label = f"本轮样本 {sample_count} 条 < 历史累计，沿用历史值"
    else:
        label = "本轮样本小于历史累计，沿用历史值"
    return {
        "baseline_protected": True,
        "baseline_protected_label": label,
        "baseline_protected_reason": _text(
            floor.get("reason"),
            "provider returned a narrower sample or missing cumulative value than the previous official-account baseline",
        ),
        "baseline_protected_fields": protected_fields,
        "baseline_protected_detail": details,
    }


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _fernet() -> Fernet:
    key = os.environ.get("VKPI_CHANNELS_ENCRYPTION_KEY") or os.environ.get("JWT_SECRET") or os.environ.get("APP_SECRET") or "vkpi-local-dev-key"
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "****"


def _platform(value: Any) -> str:
    raw = str(value or "other").strip().lower()
    if raw == "twitter":
        raw = "x"
    if raw == "red":
        raw = "reddit"
    if raw in {"xiaohongshu", "小红书"}:
        raw = "xhs"
    return raw if raw in SUPPORTED_PLATFORMS else "other"

__all__ = [name for name in globals() if not name.startswith('__')]
