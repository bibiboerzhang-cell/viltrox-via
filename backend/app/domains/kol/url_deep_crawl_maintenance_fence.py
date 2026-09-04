"""Maintenance target and batch fences for KOL profile deep crawls.

The queue module keeps compatibility wrappers for its established private
entrypoints.  Pure identity projection and validation live here so maintenance
policy stays isolated from enqueue/runner orchestration.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.kol.url_deep_crawl_helpers import (
    RAW_CHANNEL_KEYS,
    _canonical_url,
    _load_json,
    _normalise_handle,
    _raw_values,
)

logger = get_logger("viltrox.domains.kol.url_deep_crawl")

MAINTENANCE_REFRESH_TASK_KEY = "kol_profile_incremental_refresh"
MAINTENANCE_TARGET_FENCE_KIND = "kol_search_inventory_daily"
MAINTENANCE_REFRESH_TIMEZONE = "America/New_York"


def _maintenance_refresh_batch_block_reason(
    payload: dict[str, Any] | None,
    *,
    as_of: datetime | None = None,
) -> str:
    """Reject stale or malformed maintenance batches before provider I/O.

    The daily ledger limits newly queued work, but a stopped worker can leave a
    backlog behind.  Binding execution to the same New York calendar day keeps
    that backlog from spending tomorrow's budget.  This fence is independent
    of the operator force-enable switch.
    """

    raw_batch_date = (payload or {}).get("maintenance_batch_date")
    batch_text = str(raw_batch_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", batch_text):
        return "maintenance_refresh_batch_invalid"
    try:
        batch_date = date.fromisoformat(batch_text)
    except ValueError:
        return "maintenance_refresh_batch_invalid"

    current = as_of or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = current.astimezone(ZoneInfo(MAINTENANCE_REFRESH_TIMEZONE)).date()
    if batch_date < local_date:
        return "maintenance_refresh_batch_expired"
    if batch_date > local_date:
        return "maintenance_refresh_batch_future"
    return ""


def _maintenance_refresh_execution_block_reason(
    payload: dict[str, Any] | None = None,
    *,
    get_connection: Callable[[], Any] = get_conn,
    batch_block_reason: Callable[..., str] = _maintenance_refresh_batch_block_reason,
) -> str:
    """Revalidate the paid maintenance gate immediately before provider I/O."""

    from app.core.release_validation import release_validation_active

    if release_validation_active():
        return "maintenance_refresh_release_validation_fenced"
    batch_reason = batch_block_reason(payload)
    if batch_reason:
        return batch_reason
    try:
        if os.environ.get("OPS_SCHEDULER_FORCE_ENABLE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return ""
        row = get_connection().execute(
            "SELECT enabled FROM scheduler_tasks WHERE task_key=?",
            (MAINTENANCE_REFRESH_TASK_KEY,),
        ).fetchone()
        if not row:
            return "maintenance_refresh_task_disabled"
        enabled = dict(row).get("enabled")
        if isinstance(enabled, bool):
            return "" if enabled else "maintenance_refresh_task_disabled"
        enabled_text = str(enabled or "").strip().lower()
        return (
            ""
            if enabled_text in {"1", "true", "t", "yes", "on"}
            else "maintenance_refresh_task_disabled"
        )
    except Exception:
        logger.warning(
            "maintenance refresh execution gate unavailable",
            exc_info=True,
        )
        return "maintenance_refresh_gate_unavailable"


def _profile_target_row(
    conn: Any,
    kol_pool_id: int,
    *,
    for_update: bool = False,
    postgres_runtime: bool | None = None,
    include_raw_platform_data: bool = False,
) -> dict[str, Any]:
    use_postgres = (
        is_postgres_runtime()
        if for_update and postgres_runtime is None
        else bool(postgres_runtime)
    )
    lock_clause = " FOR UPDATE" if for_update and use_postgres else ""
    raw_column = ", raw_platform_data" if include_raw_platform_data else ""
    row = conn.execute(
        f"""
        SELECT id, duplicate_of_id, platform, handle, profile_url{raw_column}
        FROM vkpi_kol_pool
        WHERE id=?
        LIMIT 1
        {lock_clause}
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else {}


def _validated_profile_identity(row: dict[str, Any], submitted_url: str) -> dict[str, str]:
    """Bind a paid profile crawl to the pool row's stored public identity.

    The submitted URL may use an equivalent platform spelling, but its stable
    native-id/handle identity must match ``profile_url`` already stored for the
    selected KOL.  A caller cannot substitute another account on the same
    platform, and a malformed/unsupported stored locator cannot be promoted
    into a provider call merely because it lives in the database.
    """

    from app.domains.kol.profile_online_identity import stable_creator_identity
    from app.domains.kol.profile_recall_qualification import canonical_creator_aliases
    from app.domains.kol.video_tracking import VideoTrackingError
    from app.services.verification.viltrox_official import (
        detect_platform_from_profile_url,
        extract_handle_from_profile_url,
    )

    platform = str(row.get("platform") or "").strip().lower()
    stored_url = str(row.get("profile_url") or "").strip()
    canonical_stored = _canonical_url(stored_url)
    canonical_submitted = _canonical_url(str(submitted_url or "").strip())
    if not canonical_stored:
        raise VideoTrackingError("kol_profile_url_missing", 409)
    if not canonical_submitted:
        raise VideoTrackingError("kol_profile_url_mismatch", 409)

    stored_platform = str(detect_platform_from_profile_url(canonical_stored) or "").lower()
    submitted_platform = str(detect_platform_from_profile_url(canonical_submitted) or "").lower()
    if platform not in {"youtube", "instagram", "tiktok"}:
        raise VideoTrackingError("kol_profile_identity_invalid", 422)
    if stored_platform != platform or submitted_platform != platform:
        raise VideoTrackingError("kol_profile_identity_mismatch", 409)

    stored_handle = extract_handle_from_profile_url(canonical_stored, platform)
    submitted_handle = extract_handle_from_profile_url(canonical_submitted, platform)

    stored_identity = stable_creator_identity(
        {"platform": platform, "handle": stored_handle, "profile_url": canonical_stored}
    )
    submitted_identity = stable_creator_identity(
        {"platform": platform, "handle": submitted_handle, "profile_url": canonical_submitted}
    )
    if not stored_identity.get("passed") or not submitted_identity.get("passed"):
        raise VideoTrackingError("kol_profile_identity_invalid", 422)

    def aliases(identity: dict[str, Any]) -> set[str]:
        native_ids = identity.get("native_ids") if isinstance(identity.get("native_ids"), dict) else {}
        return canonical_creator_aliases({**identity, **native_ids})

    shared_aliases = aliases(stored_identity).intersection(aliases(submitted_identity))
    stable_shared = sorted(
        alias for alias in shared_aliases if ":id:" in alias or ":handle:" in alias
    )
    if not stable_shared:
        raise VideoTrackingError("kol_profile_identity_mismatch", 409)
    return {
        "canonical_profile_url": canonical_stored,
        "platform": platform,
        "stable_identity_key": stable_shared[0],
    }


def _build_maintenance_target_fence(
    conn: Any,
    *,
    kol_pool_id: int,
    submitted_url: str,
) -> dict[str, Any]:
    """Bind a system refresh to one current, non-duplicate pool identity."""

    from app.domains.kol.video_tracking import VideoTrackingError

    row = _profile_target_row(
        conn,
        int(kol_pool_id),
        include_raw_platform_data=True,
    )
    if not row:
        raise VideoTrackingError("maintenance_refresh_target_not_found", 409)
    if row.get("duplicate_of_id") not in (None, "", 0, "0"):
        raise VideoTrackingError("maintenance_refresh_target_merged", 409)
    identity = _validated_maintenance_profile_identity(row, submitted_url)
    return {
        "version": 1,
        "kind": MAINTENANCE_TARGET_FENCE_KIND,
        "kol_pool_id": int(kol_pool_id),
        **identity,
    }


def _stable_profile_native_ids(platform: Any, raw_value: Any) -> dict[str, str]:
    """Project only stable account ids from a stored/provider profile payload."""

    platform_key = str(platform or "").strip().lower()
    raw = _load_json(raw_value)
    if not isinstance(raw, dict):
        return {}
    profile_payload = raw.get("profile") if isinstance(raw.get("profile"), dict) else raw
    profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else None
    first_item = (
        profile_items[0]
        if isinstance(profile_items, list)
        and profile_items
        and isinstance(profile_items[0], dict)
        else profile_payload
    )
    if not isinstance(first_item, dict):
        return {}

    def direct_values(source: Any, keys: set[str]) -> set[str]:
        if not isinstance(source, dict):
            return set()
        return {
            str(source.get(key) or "").strip()
            for key in keys
            if str(source.get(key) or "").strip()
        }

    values_by_field: dict[str, set[str]] = {}
    if platform_key == "youtube":
        # A YouTube profile payload is a channels.list item.  Restrict the
        # recursive channel-key scan to that profile payload (never videos),
        # and accept its direct channel ``id``.
        values_by_field["channel_id"] = set()
        for raw_channel in _raw_values(profile_payload, set(RAW_CHANNEL_KEYS)):
            candidate = str(raw_channel or "").strip()
            if re.fullmatch(r"UC[A-Za-z0-9_-]{6,64}", candidate):
                values_by_field["channel_id"].add(candidate)
                continue
            try:
                parsed = urlparse(candidate)
            except ValueError:
                continue
            parts = [part for part in parsed.path.split("/") if part]
            if (
                parsed.hostname
                and parsed.hostname.lower().removeprefix("www.") == "youtube.com"
                and len(parts) >= 2
                and parts[0].lower() == "channel"
                and re.fullmatch(r"UC[A-Za-z0-9_-]{6,64}", parts[1])
            ):
                values_by_field["channel_id"].add(parts[1])
        direct_id = str(first_item.get("id") or "").strip()
        if direct_id:
            values_by_field["channel_id"].add(direct_id)
    elif platform_key == "instagram":
        # Instagram's profile scraper returns an account object as items[0].
        # Do not recurse into latestPosts/owners, whose ids are content actors.
        values_by_field["account_id"] = direct_values(
            first_item,
            {
                "id",
                "account_id",
                "accountId",
                "platform_user_id",
                "platformUserId",
                "user_id",
                "userId",
                "pk",
                "pk_id",
            },
        )
    elif platform_key == "tiktok":
        # TikTok profile results are video rows: items[0].id is the video id,
        # not the account id.  Only explicitly author-scoped fields are safe.
        author = (
            first_item.get("authorMeta")
            if isinstance(first_item.get("authorMeta"), dict)
            else first_item.get("author")
            if isinstance(first_item.get("author"), dict)
            else {}
        )
        values_by_field["account_id"] = direct_values(
            author,
            {
                "id",
                "uid",
                "user_id",
                "userId",
                "account_id",
                "accountId",
                "platform_user_id",
                "platformUserId",
            },
        ) | direct_values(
            first_item,
            {
                "author_id",
                "authorId",
                "authorUid",
                "author_uid",
                "uid",
                "user_id",
                "userId",
                "account_id",
                "accountId",
                "platform_user_id",
                "platformUserId",
            },
        )
        values_by_field["sec_uid"] = direct_values(
            author,
            {"sec_uid", "secUid"},
        ) | direct_values(
            first_item,
            {"author_sec_uid", "authorSecUid", "sec_uid", "secUid"},
        )
    else:
        return {}

    output: dict[str, str] = {}
    for field, values in values_by_field.items():
        if platform_key == "youtube":
            values = {
                value
                for value in values
                if re.fullmatch(r"UC[A-Za-z0-9_-]{6,64}", value)
            }
        else:
            values = {
                value
                for value in values
                if 3 <= len(value) <= 160
                and all(char.isalnum() or char in "._-" for char in value)
            }
        if len(values) > 1:
            raise ValueError(f"conflicting {platform_key} {field}")
        if values:
            output[field] = next(iter(values))
    return output


def _stable_profile_handle(platform: Any, raw_value: Any) -> str:
    """Project an observed account handle from a platform profile payload."""

    from app.services.verification.viltrox_official import (
        detect_platform_from_profile_url,
        extract_handle_from_profile_url,
    )

    platform_key = str(platform or "").strip().lower()
    raw = _load_json(raw_value)
    if platform_key not in {"youtube", "instagram", "tiktok"} or not isinstance(raw, dict):
        return ""
    profile_payload = raw.get("profile") if isinstance(raw.get("profile"), dict) else raw
    items = profile_payload.get("items") if isinstance(profile_payload, dict) else None
    first_item = (
        items[0]
        if isinstance(items, list) and items and isinstance(items[0], dict)
        else profile_payload
    )
    if not isinstance(first_item, dict):
        return ""

    direct_values: list[Any] = []
    locator_values: list[Any] = []
    if platform_key == "youtube":
        snippet = first_item.get("snippet") if isinstance(first_item.get("snippet"), dict) else {}
        direct_values.extend(
            (
                first_item.get("handle"),
                first_item.get("channelHandle"),
                first_item.get("customUrl"),
                snippet.get("customUrl"),
            )
        )
        locator_values.extend(
            (
                first_item.get("url"),
                first_item.get("channelUrl"),
                first_item.get("profileUrl"),
            )
        )
    elif platform_key == "instagram":
        direct_values.extend(
            (
                first_item.get("username"),
                first_item.get("handle"),
                first_item.get("ownerUsername"),
            )
        )
        locator_values.extend(
            (
                first_item.get("url"),
                first_item.get("profileUrl"),
                first_item.get("inputUrl"),
            )
        )
    else:
        author = (
            first_item.get("authorMeta")
            if isinstance(first_item.get("authorMeta"), dict)
            else first_item.get("author")
            if isinstance(first_item.get("author"), dict)
            else {}
        )
        direct_values.extend(
            (
                author.get("name"),
                author.get("uniqueId"),
                author.get("username"),
                first_item.get("authorName"),
                first_item.get("authorUniqueId"),
            )
        )
        locator_values.extend(
            (
                author.get("profileUrl"),
                author.get("url"),
                first_item.get("authorUrl"),
                first_item.get("profileUrl"),
            )
        )

    candidates = {
        normalized
        for value in direct_values
        if (normalized := _normalise_handle(platform_key, value))
        and re.fullmatch(r"[A-Za-z0-9._-]{1,160}", normalized)
    }
    for locator in locator_values:
        locator_text = str(locator or "").strip()
        if not locator_text or detect_platform_from_profile_url(locator_text) != platform_key:
            continue
        normalized = _normalise_handle(
            platform_key,
            extract_handle_from_profile_url(locator_text, platform_key),
        )
        if normalized and re.fullmatch(r"[A-Za-z0-9._-]{1,160}", normalized):
            candidates.add(normalized)
    if platform_key == "youtube":
        channel_ids = {
            value
            for value in candidates
            if re.fullmatch(r"UC[A-Za-z0-9_-]{6,64}", value)
        }
        readable_handles = candidates - channel_ids
        if len(readable_handles) > 1 or (not readable_handles and len(channel_ids) > 1):
            raise ValueError(f"conflicting {platform_key} handle")
        if readable_handles:
            return next(iter(readable_handles))
        return next(iter(channel_ids)) if channel_ids else ""
    if len(candidates) > 1:
        raise ValueError(f"conflicting {platform_key} handle")
    return next(iter(candidates)) if candidates else ""


def _validated_maintenance_profile_identity(
    row: dict[str, Any],
    submitted_url: str,
) -> dict[str, Any]:
    """Require row handle, stored URL and any native id to describe one account."""

    from app.domains.kol.video_tracking import VideoTrackingError
    from app.services.verification.viltrox_official import extract_handle_from_profile_url

    identity = _validated_profile_identity(row, submitted_url)
    platform = identity["platform"]
    row_handle = _normalise_handle(platform, row.get("handle"))
    url_handle = _normalise_handle(
        platform,
        extract_handle_from_profile_url(identity["canonical_profile_url"], platform),
    )
    try:
        native_ids = _stable_profile_native_ids(
            platform,
            row.get("raw_platform_data"),
        )
    except ValueError as exc:
        raise VideoTrackingError("maintenance_refresh_target_identity_invalid", 409) from exc
    if not row_handle or not url_handle:
        raise VideoTrackingError("maintenance_refresh_target_identity_invalid", 409)
    if platform == "youtube" and url_handle.startswith("UC"):
        # Legacy rows commonly retain a readable @handle while their durable
        # locator is /channel/UC....  The native id, not textual handle equality,
        # is the safe bridge between those two equivalent identities.
        raw_channel_id = str(native_ids.get("channel_id") or "")
        if raw_channel_id and raw_channel_id != url_handle:
            raise VideoTrackingError("maintenance_refresh_target_identity_invalid", 409)
        native_ids["channel_id"] = url_handle
    elif row_handle != url_handle:
        raise VideoTrackingError("maintenance_refresh_target_identity_invalid", 409)
    return {
        **identity,
        "stable_handle": row_handle,
        "stable_native_ids": native_ids,
    }


def _revalidate_maintenance_target_fence(
    payload: dict[str, Any],
    *,
    conn: Any | None = None,
    lock_target: bool = False,
    get_connection: Callable[[], Any] = get_conn,
    postgres_runtime: bool | None = None,
) -> dict[str, Any] | None:
    """Recheck the scheduler-owned target without applying My-KOL ownership."""

    if payload.get("maintenance_refresh") is not True:
        return None

    from app.domains.kol.video_tracking import VideoTrackingError

    fence = payload.get("maintenance_target_fence")
    if not isinstance(fence, dict):
        raise VideoTrackingError("maintenance_refresh_target_fence_invalid", 403)
    try:
        version = int(fence.get("version") or 0)
        kol_pool_id = int(payload.get("kol_pool_id") or 0)
        fenced_kol_pool_id = int(fence.get("kol_pool_id") or 0)
    except (TypeError, ValueError):
        raise VideoTrackingError("maintenance_refresh_target_fence_invalid", 403) from None
    if (
        version != 1
        or str(fence.get("kind") or "") != MAINTENANCE_TARGET_FENCE_KIND
        or kol_pool_id <= 0
        or kol_pool_id != fenced_kol_pool_id
    ):
        raise VideoTrackingError("maintenance_refresh_target_fence_invalid", 403)

    db = conn or get_connection()
    row = _profile_target_row(
        db,
        kol_pool_id,
        for_update=lock_target,
        postgres_runtime=postgres_runtime,
        include_raw_platform_data=True,
    )
    if not row:
        raise VideoTrackingError("maintenance_refresh_target_not_found", 409)
    if row.get("duplicate_of_id") not in (None, "", 0, "0"):
        raise VideoTrackingError("maintenance_refresh_target_merged", 409)
    try:
        current_identity = _validated_maintenance_profile_identity(
            row,
            str(payload.get("url") or ""),
        )
    except VideoTrackingError as exc:
        raise VideoTrackingError("maintenance_refresh_target_drifted", 409) from exc
    if (
        any(
            current_identity[field] != str(fence.get(field) or "")
            for field in (
                "canonical_profile_url",
                "platform",
                "stable_identity_key",
                "stable_handle",
            )
        )
        or current_identity["stable_native_ids"]
        != (
            dict(fence.get("stable_native_ids"))
            if isinstance(fence.get("stable_native_ids"), dict)
            else {}
        )
    ):
        raise VideoTrackingError("maintenance_refresh_target_drifted", 409)

    # Provider input is always the still-current canonical locator represented
    # by the durable fence, never a mutable copy left in the queued payload.
    payload["url"] = current_identity["canonical_profile_url"]
    return {"kol_pool_id": kol_pool_id, **current_identity}
