"""Shared normalization and query helpers for the V-KPI KOL pool."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import is_postgres_runtime
from app.services.cache import cache_clear, cache_set
from app.services.system import staff as staff_service

ENRICHABLE_PLATFORMS = {"youtube", "instagram", "tiktok", "xiaohongshu", "x", "bilibili", "facebook", "reddit"}
OWNER_NAME_KEYS = ("owner_name", "owner", "responsible_owner", "responsible_name", "assignee", "登记/对接人")
OWNER_ID_KEYS = ("responsible_staff_id", "owner_staff_id", "assigned_staff_id", "source_staff_id")
KOL_POOL_READ_CACHE_TTL_SEC = 300
logger = get_logger(__name__)
KOL_POOL_LIST_COLUMNS = (
    "id",
    "pool_uid",
    "platform",
    "handle",
    "profile_url",
    "display_name",
    "avatar_url",
    "bio",
    "country",
    "language",
    "email",
    "other_contacts_json",
    "followers",
    "following",
    "posts_count",
    "avg_views",
    "avg_likes",
    "avg_comments",
    "avg_shares",
    "engagement_rate",
    "primary_topic",
    "secondary_topics_json",
    "content_style",
    "production_quality",
    "audience_estimated_json",
    "brand_collaborations_json",
    "viltrox_fit_score",
    "viltrox_fit_reason",
    "potential_concerns_json",
    "recommended_product_lines_json",
    "linked_main_kol_id",
    "sync_status",
    "source_type",
    "source_ref",
    "created_by_staff_id",
    "last_seen_at",
    "created_at",
    "updated_at",
)

COUNTRY_CODE_ALIASES = {
    "us": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "美国": "US",
    "uk": "GB",
    "gb": "GB",
    "great britain": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "英国": "GB",
    "canada": "CA",
    "加拿大": "CA",
    "germany": "DE",
    "deutschland": "DE",
    "德国": "DE",
    "france": "FR",
    "法国": "FR",
    "italy": "IT",
    "意大利": "IT",
    "spain": "ES",
    "西班牙": "ES",
    "netherlands": "NL",
    "holland": "NL",
    "荷兰": "NL",
    "belgium": "BE",
    "比利时": "BE",
    "japan": "JP",
    "日本": "JP",
    "south korea": "KR",
    "korea": "KR",
    "韩国": "KR",
    "china": "CN",
    "中国": "CN",
    "australia": "AU",
    "澳大利亚": "AU",
    "brazil": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "墨西哥": "MX",
    "india": "IN",
    "印度": "IN",
    "thailand": "TH",
    "泰国": "TH",
    "vietnam": "VN",
    "越南": "VN",
    "philippines": "PH",
    "菲律宾": "PH",
    "indonesia": "ID",
    "印度尼西亚": "ID",
}

COUNTRY_NAMES = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "BE": "Belgium",
    "JP": "Japan",
    "KR": "South Korea",
    "CN": "China",
    "AU": "Australia",
    "BR": "Brazil",
    "MX": "Mexico",
    "IN": "India",
    "TH": "Thailand",
    "VN": "Vietnam",
    "PH": "Philippines",
    "ID": "Indonesia",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kol_pool_cache_key(name: str, **params: Any) -> str:
    parts = [f"{key}:{params[key]}" for key in sorted(params)]
    return f"vkpi:kol_pool:{name}:{':'.join(parts)}"


def _clear_kol_pool_read_cache() -> None:
    try:
        cache_clear(prefix="vkpi:kol_pool:")
    except Exception as exc:
        logger.warning("vkpi kol pool cache clear failed: %s", exc)


def _kol_pool_cache_hit(payload: Any) -> Any:
    if isinstance(payload, dict):
        result = dict(payload)
        result["cache"] = {"hit": True, "ttl_sec": KOL_POOL_READ_CACHE_TTL_SEC}
        return result
    return payload


def _kol_pool_cache_store(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = {**payload, "cache": {"hit": False, "ttl_sec": KOL_POOL_READ_CACHE_TTL_SEC}}
    cache_set(key, result, ttl=KOL_POOL_READ_CACHE_TTL_SEC)
    return result


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


def _country_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = re.sub(r"\s+", " ", text.lower())
    if lowered in COUNTRY_CODE_ALIASES:
        return COUNTRY_CODE_ALIASES[lowered]
    upper = text.upper()
    if upper in COUNTRY_NAMES:
        return upper
    return upper if len(upper) <= 3 else text


def _country_name(value: Any, code: str = "") -> str:
    text = str(value or "").strip()
    normalized = str(code or _country_code(text)).upper()
    if normalized in COUNTRY_NAMES:
        return COUNTRY_NAMES[normalized]
    return text or normalized


def _country_filter_variants(value: Any) -> list[str]:
    text = str(value or "").strip()
    code = _country_code(text)
    variants = {text, code, _country_name(text, code)}
    for alias, alias_code in COUNTRY_CODE_ALIASES.items():
        if alias_code == code:
            variants.add(alias)
            variants.add(alias.upper())
    return sorted({item for item in variants if item})


def _country_distribution(conn, *, limit: int = 30) -> list[dict[str, Any]]:
    if "country" not in _table_columns(conn, "vkpi_kol_pool"):
        return []
    rows = conn.execute(
        """
        SELECT country, COUNT(*) AS n
        FROM vkpi_kol_pool
        WHERE country IS NOT NULL AND TRIM(country) != ''
        GROUP BY country
        ORDER BY n DESC, country ASC
        """
    ).fetchall()
    buckets: dict[str, dict[str, Any]] = {}
    total = 0
    for row in rows:
        raw_country = str(row["country"] or "").strip()
        count = int(row["n"] or 0)
        code = _country_code(raw_country)
        if not code or count <= 0:
            continue
        item = buckets.setdefault(
            code,
            {
                "country_code": code,
                "country_name": _country_name(raw_country, code),
                "kol_count": 0,
                "raw_values": [],
            },
        )
        item["kol_count"] += count
        if raw_country not in item["raw_values"]:
            item["raw_values"].append(raw_country)
        total += count
    distribution = sorted(
        buckets.values(),
        key=lambda item: (-int(item["kol_count"] or 0), str(item["country_code"])),
    )
    for item in distribution:
        item["share"] = round((int(item["kol_count"] or 0) / total) * 100, 2) if total else 0
    return distribution[: max(1, int(limit or 30))]


def _owner_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _is_smoke_staff(member: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(member.get(key) or "")
        for key in ("name", "user_name", "display_name", "email", "user_email", "user_handle")
    ).lower()
    return any(marker in haystack for marker in ("smoke", "viltrox-smoke.local", "vkpi-"))


def _staff_lookup_by_owner_key() -> dict[str, int]:
    lookup: dict[str, int] = {}
    try:
        members = staff_service.list_members().get("members") or []
    except Exception:
        members = []
    for member in members:
        if str(member.get("active", 1)) in {"0", "false", "False"} or _is_smoke_staff(dict(member)):
            continue
        sid = _int_or_none(member.get("id") or member.get("staff_id") or member.get("user_id"))
        if not sid:
            continue
        candidates = [
            member.get("name"),
            member.get("user_name"),
            member.get("display_name"),
            member.get("email"),
            member.get("user_email"),
            member.get("user_handle"),
        ]
        for value in candidates:
            key = _owner_key(value)
            if key:
                lookup.setdefault(key, sid)
            if value and "@" in str(value):
                lookup.setdefault(_owner_key(str(value).split("@", 1)[0]), sid)
    return lookup


def _owner_name_values(raw: dict[str, Any]) -> list[str]:
    values: list[str] = []
    owner_names = raw.get("owner_names")
    if isinstance(owner_names, list):
        values.extend(str(item).strip() for item in owner_names if str(item or "").strip())
    for key in OWNER_NAME_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item or "").strip())
            continue
        text = str(value).strip()
        if not text:
            continue
        values.extend(part.strip() for part in re.split(r"[,，;/、]+", text) if part.strip())
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _resolve_responsible_staff(raw: dict[str, Any], lookup: dict[str, int]) -> tuple[int | None, str, list[str]]:
    for key in OWNER_ID_KEYS:
        sid = _int_or_none(raw.get(key))
        if sid:
            return sid, key, _owner_name_values(raw)
    owner_names = _owner_name_values(raw)
    for owner in owner_names:
        sid = lookup.get(_owner_key(owner))
        if sid:
            return sid, f"owner_name:{owner}", owner_names
    return None, "unmatched_owner_name" if owner_names else "missing_owner", owner_names


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _normalize_sync_status(value: Any) -> str:
    raw = str(value or "synced").strip().lower()
    if raw in {"ok", "success", "done", "synced"}:
        return "synced"
    if raw in {"not_configured", "unsupported", "blocked", "failed", "error"}:
        return raw
    return raw or "synced"


def _average_from_total(total: Any, count: int, fallback: Any = None) -> int | None:
    total_i = _int_or_none(total)
    if total_i is not None and count > 0:
        return int(round(total_i / count))
    return _int_or_none(fallback)


def _profile_item(raw_data: dict[str, Any]) -> dict[str, Any]:
    profile_payload = raw_data.get("profile") if isinstance(raw_data.get("profile"), dict) else {}
    items = profile_payload.get("items") if isinstance(profile_payload, dict) else None
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    account = raw_data.get("account")
    if isinstance(account, dict):
        return account
    return profile_payload if isinstance(profile_payload, dict) else {}


def _profile_stats(profile: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key in ("statistics", "public_metrics", "metrics", "stats", "authorMeta"):
        value = profile.get(key)
        if isinstance(value, dict):
            stats.update(value)
    for key, value in profile.items():
        if not isinstance(value, (dict, list)):
            stats.setdefault(key, value)
    return stats


def _nested_dict(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = source.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _content_items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("videos", "posts", "latestPosts", "items"):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("items") or []
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            if items:
                return items
    return []


def _looks_like_content_item(item: dict[str, Any]) -> bool:
    return any(
        key in item
        for key in (
            "webVideoUrl",
            "videoMeta",
            "playCount",
            "diggCount",
            "commentCount",
            "shareCount",
            "createTime",
            "createTimeISO",
            "shortCode",
            "caption",
            "text",
            "mediaUrls",
        )
    )


def _thumb_url(profile: dict[str, Any]) -> str:
    snippet = profile.get("snippet") if isinstance(profile.get("snippet"), dict) else {}
    author = _nested_dict(profile, "authorMeta", "author", "owner", "user")
    thumbnails = profile.get("thumbnails") or snippet.get("thumbnails")
    if isinstance(thumbnails, dict):
        for key in ("high", "medium", "default"):
            candidate = thumbnails.get(key)
            if isinstance(candidate, dict) and candidate.get("url"):
                return str(candidate.get("url") or "")
    return str(
        _first_present(
            profile.get("avatar_url"),
            profile.get("profilePicUrl"),
            profile.get("profilePictureUrl"),
            profile.get("avatar"),
            profile.get("image"),
            profile.get("profile_image_url"),
            profile.get("displayUrl"),
            profile.get("thumbnailUrl"),
            author.get("avatar"),
            author.get("avatarUrl"),
            author.get("originalAvatarUrl"),
            author.get("profilePictureUrl"),
            author.get("profilePicUrl"),
            author.get("image"),
        )
        or ""
    )


def _display_name(profile: dict[str, Any], fallback: str) -> str:
    snippet = profile.get("snippet") if isinstance(profile.get("snippet"), dict) else {}
    author = _nested_dict(profile, "authorMeta", "author", "owner", "user")
    return str(
        _first_present(
            profile.get("display_name"),
            profile.get("fullName"),
            profile.get("title"),
            profile.get("name"),
            profile.get("username"),
            snippet.get("title") if isinstance(snippet, dict) else None,
            author.get("nickName"),
            author.get("name"),
            author.get("username"),
            author.get("fullName"),
            fallback,
        )
        or fallback
    )


def _bio(profile: dict[str, Any]) -> str:
    snippet = profile.get("snippet") if isinstance(profile.get("snippet"), dict) else {}
    author = _nested_dict(profile, "authorMeta", "author", "owner", "user")
    return str(
        _first_present(
            profile.get("bio"),
            profile.get("biography"),
            profile.get("description"),
            snippet.get("description") if isinstance(snippet, dict) else None,
            author.get("signature"),
            author.get("bio"),
            author.get("biography"),
            author.get("description"),
        )
        or ""
    )


def _profile_url(platform: str, profile: dict[str, Any], handle: str, existing: str = "") -> str:
    author = _nested_dict(profile, "authorMeta", "author", "owner", "user")
    direct = str(
        _first_present(
            profile.get("profile_url"),
            profile.get("profileUrl"),
            profile.get("url"),
            author.get("profileUrl"),
            author.get("url"),
            profile.get("webVideoUrl"),
            existing,
        )
        or ""
    ).strip()
    if direct:
        return direct
    handle = handle.strip().lstrip("@")
    if not handle:
        return ""
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "x":
        return f"https://x.com/{handle}"
    return existing


def _platform(value: Any) -> str:
    raw = str(value or "other").strip().lower()
    return {"ig": "instagram", "tt": "tiktok", "yt": "youtube", "twitter": "x", "小红书": "xiaohongshu"}.get(raw, raw or "other")


def _sort_clause(sort_by: str) -> str:
    sort_key = str(sort_by or "fit").strip().lower()
    if sort_key == "followers":
        return "COALESCE(followers, 0) DESC, updated_at DESC"
    if sort_key in {"avg_views", "views"}:
        return "COALESCE(avg_views, 0) DESC, updated_at DESC"
    if sort_key in {"engagement", "engagement_rate"}:
        return "COALESCE(engagement_rate, 0) DESC, updated_at DESC"
    if sort_key in {"updated", "recent"}:
        return "updated_at DESC"
    if sort_key in {"oldest", "updated_oldest"}:
        return "updated_at ASC"
    if sort_key in {"missing", "gaps"}:
        return """
            (
                CASE WHEN avatar_url IS NULL OR avatar_url='' THEN 1 ELSE 0 END +
                CASE WHEN avg_views IS NULL THEN 1 ELSE 0 END +
                CASE WHEN engagement_rate IS NULL THEN 1 ELSE 0 END +
                CASE WHEN viltrox_fit_score IS NULL THEN 1 ELSE 0 END
            ) DESC,
            updated_at DESC
        """
    return "COALESCE(viltrox_fit_score, 0) DESC, updated_at DESC"


def _pool_item_gaps(item: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if not str(item.get("avatar_url") or "").strip():
        gaps.append("avatar")
    if _int_or_none(item.get("avg_views")) is None:
        gaps.append("avg_views")
    if _float_or_none(item.get("engagement_rate")) is None:
        gaps.append("engagement_rate")
    if _float_or_none(item.get("viltrox_fit_score")) is None:
        gaps.append("viltrox_fit_score")
    return gaps


def _table_columns(conn, table_name: str) -> set[str]:
    if is_postgres_runtime():
        try:
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ?
                """,
                (table_name,),
            ).fetchall()
            return {str(row["column_name"]) for row in rows}
        except Exception:
            return set()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"] if "name" in row.keys() else row[1]) for row in rows}
    except Exception:
        return set()


def _ensure_main_kol_schema() -> None:
    try:
        from app.api.routers.kol_ops_schema import ensure_kol_schema

        ensure_kol_schema()
    except Exception as exc:
        logger.warning("vkpi main kol schema guard failed: %s", exc)


def _safe_like(value: str) -> str:
    return f"%{str(value or '').strip().lower()}%"


def _candidate_score(pool_item: dict[str, Any], candidate: dict[str, Any]) -> int:
    platform = _platform(pool_item.get("platform") or "")
    handle = str(pool_item.get("handle") or "").strip().lower().lstrip("@")
    display = str(pool_item.get("display_name") or "").strip().lower()
    profile_url = str(pool_item.get("profile_url") or "").strip().lower().rstrip("/")
    channel_url = str(candidate.get("channel_url") or candidate.get("profile_url") or "").strip().lower().rstrip("/")
    channel_name = str(candidate.get("channel_name") or "").strip().lower()
    score = 0
    if platform and _platform(candidate.get("platform") or "") == platform:
        score += 25
    if profile_url and channel_url and profile_url == channel_url:
        score += 100
    if handle and (channel_name == handle or channel_name == f"@{handle}"):
        score += 70
    if handle and handle in channel_name:
        score += 45
    if display and display in channel_name:
        score += 30
    if handle and channel_url and handle in channel_url:
        score += 55
    return score


def _normalize_item(item: dict[str, Any], *, default_platform: str = "") -> dict[str, Any]:
    platform = _platform(item.get("platform") or default_platform or item.get("type") or "other")
    handle = str(item.get("handle") or item.get("username") or item.get("userName") or item.get("channelName") or item.get("name") or "").strip().lstrip("@").lower()
    profile_url = str(item.get("profile_url") or item.get("profileUrl") or item.get("url") or item.get("channelUrl") or "").strip()
    return {
        "platform": platform,
        "handle": handle,
        "profile_url": profile_url,
        "display_name": str(item.get("display_name") or item.get("fullName") or item.get("name") or handle),
        "avatar_url": str(item.get("avatar_url") or item.get("profilePicUrl") or item.get("avatar") or ""),
        "bio": str(item.get("bio") or item.get("biography") or item.get("description") or ""),
        "email": str(item.get("email") or item.get("publicEmail") or ""),
        "followers": _int_or_none(_first_present(item.get("followers"), item.get("followersCount"), item.get("subscriberCount"))),
        "following": _int_or_none(_first_present(item.get("following"), item.get("followsCount"))),
        "posts_count": _int_or_none(_first_present(item.get("posts_count"), item.get("postsCount"), item.get("videoCount"))),
        "avg_views": _int_or_none(_first_present(item.get("avg_views"), item.get("averageViews"))),
        "avg_likes": _int_or_none(_first_present(item.get("avg_likes"), item.get("averageLikes"))),
        "avg_comments": _int_or_none(_first_present(item.get("avg_comments"), item.get("averageComments"))),
        "engagement_rate": _float_or_none(_first_present(item.get("engagement_rate"), item.get("engagementRate"))),
        "raw": item,
    }
