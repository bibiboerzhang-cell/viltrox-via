"""Pure (DB-free / IO-free) helpers extracted from url_deep_crawl.

This module holds only proven-pure functions: URL/string classification
primitives, body-option readers, metadata shaping, and result formatters.
Nothing here touches get_conn, the network, crawlers, the worker queue, R2
media cache, or any global mutable state — those stay in url_deep_crawl.py.

url_deep_crawl re-exports every symbol below under its original name so all
call sites (and the module's own internal references) keep working unchanged.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.domains.kol.pool_common import _first_present, _int_or_none, _json

from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}


def _metadata_text(value: Any) -> str:
    return str(value or "").strip()


def _public_video_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "platform",
        "content_url",
        "title",
        "description",
        "view_count",
        "like_count",
        "comment_count",
        "share_count",
        "publish_date",
        "posted_at",
        "duration_seconds",
        "thumbnail_url",
        "media_kind",
        "image_urls",
        "channel_id",
        "channel_name",
        "scrape_source",
        "scrape_status",
        "scrape_error",
        "apify_run_id",
    ):
        value = metadata.get(key)
        if value in (None, ""):
            continue
        if key == "description":
            value = str(value)[:1000]
        result[key] = value
    return result


def _profile_url_for_creator(platform: str, handle: str, channel_id: str) -> str:
    if platform == "youtube":
        if channel_id:
            return f"https://www.youtube.com/channel/{channel_id}"
        if handle:
            return f"https://www.youtube.com/@{handle.lstrip('@')}"
    if platform == "instagram" and handle:
        return f"https://www.instagram.com/{handle.lstrip('@')}/"
    if platform == "tiktok" and handle:
        return f"https://www.tiktok.com/@{handle.lstrip('@')}"
    return ""


def _has_matchable_creator_identity(identity: dict[str, Any]) -> bool:
    platform = _normalise_platform(identity.get("platform"))
    if platform == "youtube":
        return bool(_metadata_text(identity.get("channel_id")).startswith("UC") or _metadata_text(identity.get("handle")))
    if platform in {"instagram", "tiktok"}:
        return bool(_metadata_text(identity.get("handle")))
    return False


def _video_execute_mode(body: dict[str, Any]) -> str:
    mode = str(body.get("mode") or "").strip()
    if mode in {"auto", "video_deep"}:
        return mode
    return "video_deep"


def _profile_should_enqueue_representative_videos(body: dict[str, Any]) -> bool:
    mode = str(body.get("mode") or "").strip()
    return mode in {"auto", "profile_with_video", "account_deep"}


def _profile_representative_video_limit(body: dict[str, Any]) -> int:
    try:
        value = int(body.get("representative_video_limit") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(3, value))


def _profile_should_materialize_history_videos(body: dict[str, Any]) -> bool:
    mode = str(body.get("mode") or "").strip()
    return mode == "account_deep" or bool(
        body.get("full_history")
        or body.get("materialize_history_videos")
        or body.get("history_video_limit")
    )


def _profile_history_video_limit(body: dict[str, Any]) -> int:
    default = _max_posts(body) if _profile_should_materialize_history_videos(body) else 0
    try:
        value = int(body.get("history_video_limit") or default or 0)
    except (TypeError, ValueError):
        value = default
    return max(1, min(120, int(value or 1)))


def _profile_exclude_video_urls(body: dict[str, Any]) -> list[str]:
    values = body.get("exclude_video_urls")
    if values is None:
        values = body.get("exclude_video_url")
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value or "").strip()]


def _profile_video_is_newer_than_cutoff(metadata: dict[str, Any], cutoff: str) -> bool:
    cutoff_date = _parse_date(cutoff)
    if not cutoff_date:
        return True
    posted_at = _parse_date(
        _first_present(
            metadata.get("posted_at"),
            metadata.get("publish_date"),
            metadata.get("published_at"),
            metadata.get("publishedAt"),
        )
    )
    if not posted_at:
        return False
    return posted_at > cutoff_date


def _duration_seconds(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number >= 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        return hours * 3600 + minutes * 60 + seconds
    return None


def _profile_video_dedupe_key(platform: str, content_url: str) -> str:
    parsed = urlparse(str(content_url or ""))
    video_id = _video_id(platform, parsed.netloc, parsed.path, parsed.query)
    if video_id:
        return f"{platform}:{video_id}"
    return _canonical_url(content_url) or str(content_url or "").strip()


def _raw_profile_backfilled_at(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    backfill = payload.get("profile_basics_backfill")
    if isinstance(backfill, dict):
        value = _metadata_text(backfill.get("profile_backfilled_at"))
        if value:
            return value
    profile_backfill = payload.get("profile_backfill")
    if isinstance(profile_backfill, dict):
        value = _metadata_text(profile_backfill.get("profile_backfilled_at") or profile_backfill.get("created_at"))
        if value:
            return value
    value = _metadata_text(payload.get("profile_backfilled_at"))
    return value


def _fit_changed_ids(result: dict[str, Any]) -> list[int]:
    ids = result.get("viltrox_fit_score_changed_ids") if isinstance(result, dict) else []
    if not isinstance(ids, list):
        return []
    changed: list[int] = []
    for item in ids:
        try:
            changed.append(int(item))
        except (TypeError, ValueError):
            continue
    return changed


def _video_metadata_date(metadata: dict[str, Any]) -> str:
    if not isinstance(metadata, dict):
        return ""
    for key in ("publish_date", "posted_at", "published_at", "publishedAt", "uploadDate", "date", "timestamp"):
        parsed = _parse_date(metadata.get(key))
        if parsed:
            return parsed
    return ""


def _compact_profile_write_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    return {
        "ok": result.get("ok"),
        "operation": result.get("operation"),
        "kol_pool_id": result.get("kol_pool_id"),
        "fields_written": result.get("fields_written"),
        "ignored_fields": result.get("ignored_fields"),
        "missing_columns": result.get("missing_columns"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
        "method": result.get("method"),
    }


def _compact_video_evidence_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    return {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "operation": result.get("operation"),
        "kol_pool_id": result.get("kol_pool_id"),
        "evidence_id": result.get("evidence_id"),
        "source_url": result.get("source_url"),
        "fields_written": result.get("fields_written"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
        "method": result.get("method"),
    }


def _compact_enqueue_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not result:
        return {}
    job = result.get("job")
    cache = result.get("cache")
    compact: dict[str, Any] = {
        "status": result.get("status"),
        "kol_pool_id": result.get("kol_pool_id"),
        "evidence_id": result.get("evidence_id"),
        "derive_method": result.get("derive_method"),
        "provider_calls": result.get("provider_calls"),
        "write_db": result.get("write_db"),
        "viltrox_fit_score_changed_ids": result.get("viltrox_fit_score_changed_ids") or [],
    }
    if isinstance(job, dict):
        compact["job"] = {
            "id": job.get("id"),
            "job_type": job.get("job_type"),
            "status": job.get("status"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        }
    if isinstance(cache, dict):
        compact["cache"] = {
            "id": cache.get("id"),
            "status": cache.get("status"),
            "derive_method": cache.get("derive_method"),
        }
    if result.get("reason"):
        compact["reason"] = result.get("reason")
    ai_analysis = result.get("ai_analysis")
    if isinstance(ai_analysis, dict):
        compact["ai_analysis"] = {
            key: ai_analysis.get(key)
            for key in (
                "state",
                "reason",
                "gate_reason",
                "model_readiness_status",
                "provider_calls_allowed",
            )
            if key in ai_analysis
        }
    return compact


def _public_profile_data(profile_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: profile_data.get(key)
        for key in ("platform", "handle", "profile_url", "avatar_url", "followers", "bio", "posts_count", "last_video_at")
        if profile_data.get(key) not in (None, "")
    }


def _max_posts(body: dict[str, Any]) -> int:
    try:
        value = int(body.get("max_posts") or body.get("history_video_limit") or 3)
    except (TypeError, ValueError):
        value = 3
    upper = 120 if _profile_should_materialize_history_videos(body) else 12
    return max(1, min(upper, value))


def _parse_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date().isoformat()
        except Exception:
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _latest_video_date(items: list[dict[str, Any]]) -> str:
    dates: list[str] = []
    for item in items:
        author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
        candidates = [
            item.get("publish_date"),
            item.get("published_at"),
            item.get("publishedAt"),
            item.get("uploadDate"),
            item.get("date"),
            item.get("timestamp"),
            item.get("createTimeISO"),
            item.get("createTime"),
            item.get("takenAtIso"),
            author.get("createTime"),
        ]
        for candidate in candidates:
            parsed = _parse_date(candidate)
            if parsed:
                dates.append(parsed)
                break
    return max(dates) if dates else ""


def _normalize_input_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value.lstrip("/")
    return value


def _platform_from_host(host: str) -> str:
    if "youtube.com" in host or host == "youtu.be":
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return ""


def _video_id(platform: str, host: str, path: str, query: str) -> str:
    parts = [part for part in path.split("/") if part]
    lowered = [part.lower() for part in parts]
    if platform == "youtube":
        if host == "youtu.be" and parts:
            return parts[0]
        if lowered[:1] == ["watch"]:
            values = parse_qs(query).get("v") or []
            return str(values[0] or "").strip()
        if len(parts) >= 2 and lowered[0] in {"shorts", "embed", "live"}:
            return parts[1]
    if platform == "instagram":
        if len(parts) >= 2 and lowered[0] in {"p", "reel", "tv"}:
            return parts[1]
        if len(parts) >= 3 and lowered[1] in {"p", "reel", "tv"}:
            return parts[2]
    if platform == "tiktok":
        for index, part in enumerate(lowered):
            if part == "video" and index + 1 < len(parts):
                return parts[index + 1]
    return ""


def _normalise_platform(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"yt", "youtube", "youtube.com"}:
        return "youtube"
    if text in {"ig", "instagram", "instagram.com"}:
        return "instagram"
    if text in {"tt", "tiktok", "tiktok.com"}:
        return "tiktok"
    return text


def _normalise_handle(platform: str, value: Any) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    lowered = text.lower()
    if platform in {"instagram", "tiktok"}:
        return lowered
    if platform == "youtube":
        return text if text.startswith("UC") else lowered
    return lowered


def _channel_id_from_handle(platform: str, handle: str) -> str:
    if platform == "youtube" and str(handle or "").startswith("UC"):
        return str(handle)
    return ""


def _canonical_url(value: str) -> str:
    text = _normalize_input_url(str(value or "").strip())
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return urlunparse(("https", host, path, "", "", ""))


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    try:
        return json.loads(str(value))
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        return {}


def _raw_values(payload: Any, keys: set[str]) -> list[str]:
    values: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, (str, int, float)):
                values.append(str(value))
            if isinstance(value, (dict, list)):
                values.extend(_raw_values(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_raw_values(item, keys))
    return values


def _all_raw_strings(payload: Any) -> list[str]:
    values: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, (str, int, float)):
                values.append(str(value))
            elif isinstance(value, (dict, list)):
                values.extend(_all_raw_strings(value))
    elif isinstance(payload, list):
        for item in payload:
            values.extend(_all_raw_strings(item))
    return values


def _contains_identity(values: list[Any], identity: str) -> bool:
    needle = _normalise_identity(identity)
    if not needle:
        return False
    for value in values:
        candidate = _normalise_identity(value)
        if candidate == needle:
            return True
        if needle.startswith("uc") and needle in candidate:
            return True
    return False


def _normalise_identity(value: Any) -> str:
    text = str(value or "").strip().lower().strip("/")
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    if "://" in text or "." in text:
        try:
            parsed = urlparse(_normalize_input_url(text))
            path_parts = [part for part in parsed.path.split("/") if part]
            if path_parts:
                if path_parts[0].lower() == "channel" and len(path_parts) > 1:
                    return path_parts[1].lower()
                if path_parts[0].startswith("@"):
                    return path_parts[0][1:].lower()
                return path_parts[-1].lower()
        except ValueError:
            pass
    return text


# Symbols intentionally NOT moved (impure: conn / network / crawler / worker /
# R2 cache / kpi orchestration): _match_pool, _pool_rows, _video_flow_plan,
# _profile_flow_plan, _execute_*, _crawl_profile_basics, _profile_data_from_crawl,
# _profile_data_for_new_video_creator, _record_deep_crawl_run, _crawler_for,
# _profile_incremental_state, _enqueue_account_dossier_extract_followup,
# _cache_video_flow_url, _creator_identity_from_*, _classified_from_creator_identity,
# _profile_representative_video_metadata, _metadata_from_profile_video_item,
# _profile_video_id/_url/_thumbnail, _identity_profile_data, _profile_target,
# _next_action, classify_url, dry_run_url_deep_crawl, enqueue_profile_deep_crawl_job,
# run_profile_deep_crawl_for_job.
