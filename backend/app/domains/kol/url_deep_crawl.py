"""Dry-run URL classifier and KOL Pool matcher for URL deep crawl.

This module is intentionally read-only. It identifies the URL type and checks
whether the creator is already present in vkpi_kol_pool; it never crawls,
queues jobs, calls providers, or writes business data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.db.connection import get_conn
from app.domains.kol.pool_common import _table_columns
from app.services.verification.viltrox_official import (
    detect_platform_from_profile_url,
    extract_handle_from_profile_url,
)
from app.utils.handles import extract_handle_from_url

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}
PROFILE_GENERIC_SEGMENTS = {
    "",
    "about",
    "accounts",
    "channel",
    "direct",
    "explore",
    "feed",
    "p",
    "reel",
    "shorts",
    "stories",
    "tagged",
    "tv",
    "user",
    "watch",
}
RAW_CHANNEL_KEYS = {
    "channel_id",
    "channelid",
    "channelId",
    "youtube_channel_id",
    "youtubeChannelId",
    "channel_url",
    "channelUrl",
    "channel",
    "external_id",
    "externalId",
}
RAW_HANDLE_KEYS = {
    "handle",
    "username",
    "user_name",
    "userName",
    "author_handle",
    "authorHandle",
    "platform_user_id",
    "platformUserId",
    "screen_name",
    "screenName",
}
RAW_URL_KEYS = {
    "url",
    "profile_url",
    "profileUrl",
    "channel_url",
    "channelUrl",
    "account_url",
    "accountUrl",
    "web_url",
    "webUrl",
}


@dataclass(frozen=True)
class ClassifiedUrl:
    original_url: str
    normalized_url: str
    url_type: str
    platform: str
    handle: str
    channel_id: str
    video_id: str
    confidence: str


def dry_run_url_deep_crawl(body: dict[str, Any]) -> dict[str, Any]:
    """Classify a user URL and match it against vkpi_kol_pool without writes."""
    execute = bool(body.get("execute", False))
    if execute:
        raise ValueError("execute=true is not supported in this dry-run endpoint")

    url = str(body.get("url") or "").strip()
    if not url:
        raise ValueError("url is required")

    classified = classify_url(url)
    matches = _match_pool(classified) if classified.platform in SUPPORTED_PLATFORMS else []
    matched_id = matches[0]["kol_pool_id"] if len(matches) == 1 else None

    return {
        "method": "kol_url_deep_crawl_dry_run_v1",
        "dry_run": True,
        "execute": False,
        "writes_performed": False,
        "provider_calls_performed": False,
        "url": {
            "input": classified.original_url,
            "normalized": classified.normalized_url,
        },
        "url_type": classified.url_type,
        "platform": classified.platform or None,
        "handle": classified.handle or None,
        "channel_id": classified.channel_id or None,
        "video_id": classified.video_id or None,
        "in_pool": len(matches) == 1,
        "matched_kol_pool_id": matched_id,
        "candidates": matches,
        "next_action": _next_action(classified, matches),
        "safety": {
            "crawl_performed": False,
            "llm_calls_performed": False,
            "worker_touched": False,
            "viltrox_fit_touched": False,
            "business_tables_written": False,
        },
    }


def classify_url(raw_url: str) -> ClassifiedUrl:
    original = str(raw_url or "").strip()
    normalized = _normalize_input_url(original)
    parsed = urlparse(normalized)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")
    lowered_path = path.lower()

    platform = (detect_platform_from_profile_url(normalized) or _platform_from_host(host) or "").lower()
    if platform not in SUPPORTED_PLATFORMS:
        return ClassifiedUrl(original, normalized, "unknown", "", "", "", "", "unsupported_platform")

    video_id = _video_id(platform, host, path, parsed.query)
    if video_id:
        handle_hint = _normalise_handle(platform, extract_handle_from_url(normalized))
        channel_id = _channel_id_from_handle(platform, handle_hint)
        return ClassifiedUrl(
            original,
            normalized,
            "video",
            platform,
            "" if channel_id else handle_hint,
            channel_id,
            video_id,
            "video_pattern",
        )

    profile_handle = extract_handle_from_profile_url(normalized, platform) or extract_handle_from_url(normalized)
    profile_handle = _normalise_handle(platform, profile_handle)
    channel_id = _channel_id_from_handle(platform, profile_handle)
    if channel_id:
        profile_handle = ""

    if profile_handle or channel_id:
        return ClassifiedUrl(
            original,
            normalized,
            "profile",
            platform,
            profile_handle,
            channel_id,
            "",
            "profile_pattern",
        )

    if platform == "instagram" and lowered_path.split("/", 1)[0] not in PROFILE_GENERIC_SEGMENTS:
        handle = _normalise_handle(platform, lowered_path.split("/", 1)[0])
        return ClassifiedUrl(original, normalized, "profile", platform, handle, "", "", "profile_fallback")

    return ClassifiedUrl(original, normalized, "unknown", platform, "", "", "", "no_extractable_identity")


def _match_pool(classified: ClassifiedUrl) -> list[dict[str, Any]]:
    rows = _pool_rows()
    ranked: dict[int, tuple[int, dict[str, Any]]] = {}
    canonical_input = _canonical_url(classified.normalized_url)

    for row in rows:
        row_platform = _normalise_platform(row.get("platform"))
        if classified.platform and row_platform != classified.platform:
            continue

        row_dict = dict(row)
        raw_payload = _load_json(row_dict.get("raw_platform_data"))
        source = ""
        priority = 999

        if classified.channel_id and classified.platform == "youtube":
            channel_values = _raw_values(raw_payload, RAW_CHANNEL_KEYS)
            channel_values.extend([row_dict.get("handle"), row_dict.get("profile_url")])
            if _contains_identity(channel_values, classified.channel_id):
                source = "platform_channel_id"
                priority = 1

        if not source and classified.handle:
            row_handle = _normalise_handle(row_platform, row_dict.get("handle"))
            raw_handles = [_normalise_handle(row_platform, item) for item in _raw_values(raw_payload, RAW_HANDLE_KEYS)]
            if row_handle == classified.handle or classified.handle in raw_handles:
                source = "platform_handle"
                priority = 2

        if not source and classified.url_type == "profile" and canonical_input:
            url_values = [row_dict.get("profile_url"), *_raw_values(raw_payload, RAW_URL_KEYS)]
            canonical_values = {_canonical_url(str(item or "")) for item in url_values if item}
            if canonical_input in canonical_values:
                source = "profile_url"
                priority = 3

        if not source and (classified.handle or classified.channel_id):
            needle = classified.channel_id or classified.handle
            raw_values = _all_raw_strings(raw_payload)
            if _contains_identity(raw_values, needle):
                source = "raw_platform_data"
                priority = 4

        if source:
            kol_id = int(row_dict["id"])
            candidate = {
                "kol_pool_id": kol_id,
                "platform": row_platform,
                "handle": row_dict.get("handle") or "",
                "display_name": row_dict.get("display_name") or "",
                "profile_url": row_dict.get("profile_url") or "",
                "match_source": source,
                "match_priority": priority,
            }
            current = ranked.get(kol_id)
            if current is None or priority < current[0]:
                ranked[kol_id] = (priority, candidate)

    return [
        candidate
        for _, candidate in sorted(
            ranked.values(),
            key=lambda item: (
                int(item[0]),
                str(item[1].get("platform") or ""),
                str(item[1].get("handle") or ""),
                int(item[1].get("kol_pool_id") or 0),
            ),
        )
    ][:10]


def _pool_rows() -> list[dict[str, Any]]:
    conn = get_conn()
    columns = _table_columns(conn, "vkpi_kol_pool")
    required = ["id", "platform", "handle", "display_name", "profile_url", "raw_platform_data"]
    selected = [column for column in required if column in columns]
    if "id" not in selected:
        return []
    rows = conn.execute(f"SELECT {', '.join(selected)} FROM vkpi_kol_pool").fetchall()
    return [dict(row) for row in rows]


def _next_action(classified: ClassifiedUrl, matches: list[dict[str, Any]]) -> dict[str, Any]:
    if classified.url_type == "unknown":
        return {
            "code": "unsupported_or_unresolved_url",
            "label": "无法识别 URL 类型",
            "description": "未执行抓取。请确认 URL 是 YouTube/Instagram/TikTok profile 或 video URL。",
        }
    if len(matches) > 1:
        return {
            "code": "choose_existing_candidate",
            "label": "发现多个候选",
            "description": "需要人工选择目标 KOL，dry-run 不会自动合并。",
        }
    if classified.url_type == "profile":
        if matches:
            return {
                "code": "profile_found_in_pool",
                "label": "已在库",
                "description": "下一步可打开现有档案，或在确认后执行安全基础补档。",
            }
        return {
            "code": "profile_not_in_pool",
            "label": "不在库",
            "description": "下一步可在确认后新建最小档案并执行安全基础补档。",
        }
    if classified.url_type == "video":
        if matches:
            return {
                "code": "video_creator_found_in_pool",
                "label": "视频创作者已在库",
                "description": "下一步可在确认后做单帖预览或排入 final_v1 深度分析。",
            }
        return {
            "code": "video_creator_unresolved_or_not_in_pool",
            "label": "视频已识别，创作者未确认在库",
            "description": "下一步可在确认后先做单帖预览，解析创作者后再决定是否建档。",
        }
    return {
        "code": "dry_run_only",
        "label": "仅识别",
        "description": "未执行抓取或写库。",
    }


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
