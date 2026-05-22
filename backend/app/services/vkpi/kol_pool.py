"""Self-owned KOL pool and Apify/import adapters."""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.services.cache import cache_clear, cache_get, cache_set
from app.services.system import staff as staff_service
from app.services.vkpi.industry_crawlers import get_crawler
from app.services.vkpi.industry_snapshot_kpis import calculate_kpis
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.scoring import ScoringRegistry
from app.services.vkpi.workflow import staff_id as resolve_staff_id

ENRICHABLE_PLATFORMS = {"youtube", "instagram", "tiktok", "xiaohongshu", "x", "bilibili", "facebook", "reddit"}
OWNER_NAME_KEYS = ("owner_name", "owner", "responsible_owner", "responsible_name", "assignee", "登记/对接人")
OWNER_ID_KEYS = ("responsible_staff_id", "owner_staff_id", "assigned_staff_id", "source_staff_id")
KOL_POOL_READ_CACHE_TTL_SEC = 300
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
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _kol_pool_cache_key(name: str, **params: Any) -> str:
    parts = [f"{key}:{params[key]}" for key in sorted(params)]
    return f"vkpi:kol_pool:{name}:{':'.join(parts)}"


def _clear_kol_pool_read_cache() -> None:
    try:
        cache_clear(prefix="vkpi:kol_pool:")
    except Exception:
        pass


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
    except Exception:
        pass


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


def import_items(items: list[dict[str, Any]], *, source_type: str = "manual", source_ref: str = "", platform: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    actor = resolve_staff_id(staff) or None
    conn = get_conn()
    imported = 0
    skipped = 0
    rows: list[dict[str, Any]] = []
    now = _utcnow()
    staff_lookup = _staff_lookup_by_owner_key()
    for raw in items:
        item = _normalize_item(raw, default_platform=platform)
        if not item["handle"]:
            skipped += 1
            continue
        raw_payload = dict(item["raw"] or {})
        responsible_staff_id, match_status, owner_names = _resolve_responsible_staff(raw_payload, staff_lookup)
        if owner_names:
            raw_payload["owner_names"] = owner_names
        if responsible_staff_id:
            raw_payload["responsible_staff_id"] = responsible_staff_id
        raw_payload["responsible_staff_match_status"] = match_status
        item["raw"] = raw_payload
        uid = f"pool-{secrets.token_hex(8)}"
        conn.execute(
            """
            INSERT INTO vkpi_kol_pool
+                (pool_uid, platform, handle, profile_url, display_name, avatar_url, bio, email,
+                 followers, following, posts_count, avg_views, avg_likes, avg_comments,
+                 engagement_rate, source_type, source_ref, raw_platform_data, created_by_staff_id,
+                 last_seen_at, created_at, updated_at)
+            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
+            ON CONFLICT(platform, handle) DO UPDATE SET
+                profile_url=excluded.profile_url,
+                display_name=excluded.display_name,
+                avatar_url=excluded.avatar_url,
+                bio=excluded.bio,
+                email=excluded.email,
+                followers=excluded.followers,
+                following=excluded.following,
+                posts_count=excluded.posts_count,
+                avg_views=excluded.avg_views,
+                avg_likes=excluded.avg_likes,
+                avg_comments=excluded.avg_comments,
+                engagement_rate=excluded.engagement_rate,
+                source_type=excluded.source_type,
+                source_ref=excluded.source_ref,
+                raw_platform_data=excluded.raw_platform_data,
+                last_seen_at=excluded.last_seen_at,
+                updated_at=excluded.updated_at
+            """.replace("+", ""),
            (
                uid,
                item["platform"],
                item["handle"],
                item["profile_url"],
                item["display_name"],
                item["avatar_url"],
                item["bio"],
                item["email"],
                item["followers"],
                item["following"],
                item["posts_count"],
                item["avg_views"],
                item["avg_likes"],
                item["avg_comments"],
                item["engagement_rate"],
                source_type,
                source_ref,
                _json(item["raw"]),
                actor,
                now,
                now,
                now,
            ),
        )
        imported += 1
        row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE platform=? AND handle=?", (item["platform"], item["handle"])).fetchone()
        if row:
            rows.append(dict(row))
    conn.commit()
    _clear_kol_pool_read_cache()
    return {"imported": imported, "skipped": skipped, "items": rows}


def list_pool(
    limit: int = 100,
    platform: str = "",
    query: str = "",
    country: str = "",
    data_status: str = "",
    sort_by: str = "fit",
    enrichable: bool | None = None,
) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(500, int(limit or 100)))
    cache_key = _kol_pool_cache_key(
        "list",
        limit=safe_limit,
        platform=_platform(platform) if platform else "",
        query=str(query or "").strip().lower(),
        country=_country_code(country) if country else "",
        data_status=str(data_status or "").strip().lower(),
        sort_by=str(sort_by or "fit").strip().lower(),
        enrichable="any" if enrichable is None else str(bool(enrichable)).lower(),
    )
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)
    where: list[str] = []
    params: list[Any] = []
    if platform:
        where.append("platform=?")
        params.append(_platform(platform))
    if query:
        where.append("(handle LIKE ? OR display_name LIKE ? OR bio LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])
    if country:
        variants = [variant.lower() for variant in _country_filter_variants(country)]
        if variants:
            placeholders = ",".join(["?"] * len(variants))
            where.append(f"LOWER(COALESCE(country, '')) IN ({placeholders})")
            params.extend(variants)
    status = str(data_status or "").strip().lower()
    if status == "missing":
        where.append(
            """
            (
                avatar_url IS NULL OR avatar_url='' OR
                avg_views IS NULL OR
                engagement_rate IS NULL OR
                viltrox_fit_score IS NULL
            )
            """
        )
    elif status == "complete":
        where.append(
            """
            avatar_url IS NOT NULL AND avatar_url!='' AND
            avg_views IS NOT NULL AND
            engagement_rate IS NOT NULL AND
            viltrox_fit_score IS NOT NULL
            """
        )
    if enrichable is True:
        placeholders = ",".join(["?"] * len(ENRICHABLE_PLATFORMS))
        where.append(f"platform IN ({placeholders})")
        params.extend(sorted(ENRICHABLE_PLATFORMS))
    elif enrichable is False:
        placeholders = ",".join(["?"] * len(ENRICHABLE_PLATFORMS))
        where.append(f"platform NOT IN ({placeholders})")
        params.extend(sorted(ENRICHABLE_PLATFORMS))
    clause = "WHERE " + " AND ".join(where) if where else ""
    order_clause = _sort_clause(sort_by)
    conn = get_conn()
    table_columns = _table_columns(conn, "vkpi_kol_pool")
    select_columns = [column for column in KOL_POOL_LIST_COLUMNS if column in table_columns]
    select_clause = ", ".join(select_columns) if "id" in select_columns else "*"
    rows = conn.execute(
        f"SELECT {select_clause} FROM vkpi_kol_pool {clause} ORDER BY {order_clause} LIMIT ?",
        (*params, safe_limit),
    ).fetchall()
    return _kol_pool_cache_store(cache_key, {"items": [dict(row) for row in rows]})


def main_candidates(kol_pool_id: int, *, limit: int = 5) -> dict[str, Any]:
    """Find likely kols-table matches for a pool candidate."""

    ensure_vkpi_product_industry_schema()
    _ensure_main_kol_schema()
    conn = get_conn()
    pool_row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not pool_row:
        raise LookupError("kol pool item not found")
    item = dict(pool_row)
    columns = _table_columns(conn, "kols")
    if not columns:
        return {"kol_pool_id": int(kol_pool_id), "item": item, "candidates": []}

    select_columns = [
        col
        for col in (
            "id",
            "channel_name",
            "channel_url",
            "platform",
            "follower_count",
            "avg_views",
            "contact_status",
            "avatar_url",
            "profile_url",
            "created_at",
            "updated_at",
        )
        if col in columns
    ]
    if "id" not in select_columns:
        return {"kol_pool_id": int(kol_pool_id), "item": item, "candidates": []}

    platform = _platform(item.get("platform") or "")
    handle = str(item.get("handle") or "").strip().lower().lstrip("@")
    display = str(item.get("display_name") or "").strip().lower()
    profile_url = str(item.get("profile_url") or "").strip().lower().rstrip("/")
    clauses: list[str] = []
    params: list[Any] = []
    if "platform" in columns and platform:
        clauses.append("LOWER(COALESCE(platform, ''))=?")
        params.append(platform)
    match_parts: list[str] = []
    if profile_url:
        if "channel_url" in columns:
            match_parts.append("LOWER(RTRIM(COALESCE(channel_url, ''), '/'))=?")
            params.append(profile_url)
        if "profile_url" in columns:
            match_parts.append("LOWER(RTRIM(COALESCE(profile_url, ''), '/'))=?")
            params.append(profile_url)
    if handle:
        if "channel_name" in columns:
            match_parts.append("LOWER(COALESCE(channel_name, '')) LIKE ?")
            params.append(_safe_like(handle))
        if "channel_url" in columns:
            match_parts.append("LOWER(COALESCE(channel_url, '')) LIKE ?")
            params.append(_safe_like(handle))
        if "profile_url" in columns:
            match_parts.append("LOWER(COALESCE(profile_url, '')) LIKE ?")
            params.append(_safe_like(handle))
    if display and "channel_name" in columns:
        match_parts.append("LOWER(COALESCE(channel_name, '')) LIKE ?")
        params.append(_safe_like(display))
    if match_parts:
        clauses.append("(" + " OR ".join(match_parts) + ")")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    rows = conn.execute(
        f"SELECT {', '.join(select_columns)} FROM kols {where} ORDER BY id DESC LIMIT ?",
        (*params, max(1, min(50, int(limit or 5) * 5))),
    ).fetchall()
    candidates = [dict(row) for row in rows]
    for candidate in candidates:
        candidate["match_score"] = _candidate_score(item, candidate)
        candidate["match_reason"] = _main_candidate_reason(item, candidate)
    candidates.sort(key=lambda row: int(row.get("match_score") or 0), reverse=True)
    return {
        "kol_pool_id": int(kol_pool_id),
        "item": item,
        "candidates": candidates[: max(1, min(20, int(limit or 5)))],
    }


def _main_candidate_reason(pool_item: dict[str, Any], candidate: dict[str, Any]) -> str:
    reasons: list[str] = []
    if _platform(pool_item.get("platform")) == _platform(candidate.get("platform")):
        reasons.append("same platform")
    handle = str(pool_item.get("handle") or "").strip().lower().lstrip("@")
    candidate_name = str(candidate.get("channel_name") or "").lower()
    if handle and handle in candidate_name:
        reasons.append("handle in name")
    profile_url = str(pool_item.get("profile_url") or "").strip().lower().rstrip("/")
    candidate_url = str(candidate.get("channel_url") or candidate.get("profile_url") or "").strip().lower().rstrip("/")
    if profile_url and profile_url == candidate_url:
        reasons.append("same profile url")
    return ", ".join(reasons) or "weak text match"


def promote_to_main(
    kol_pool_id: int,
    *,
    staff: dict[str, Any] | None = None,
    mode: str = "match_or_create",
) -> dict[str, Any]:
    """Link a pool row to kols, creating the main-row when no good match exists."""

    ensure_vkpi_product_industry_schema()
    _ensure_main_kol_schema()
    conn = get_conn()
    pool_row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not pool_row:
        raise LookupError("kol pool item not found")
    item = dict(pool_row)
    linked_id = _int_or_none(item.get("linked_main_kol_id"))
    if linked_id:
        main_row = _get_main_kol(linked_id)
        return {
            "linked": True,
            "mode": "already_linked",
            "kol_pool_id": int(kol_pool_id),
            "main_kol_id": linked_id,
            "item": item,
            "main_kol": main_row,
            "candidates": [main_row] if main_row else [],
        }

    candidates = main_candidates(kol_pool_id, limit=5).get("candidates") or []
    best = candidates[0] if candidates and int(candidates[0].get("match_score") or 0) >= 80 else None
    if best:
        main_kol_id = int(best["id"])
        action = "matched"
    else:
        if mode == "match_only":
            return {
                "linked": False,
                "mode": "no_match",
                "kol_pool_id": int(kol_pool_id),
                "main_kol_id": None,
                "item": item,
                "candidates": candidates,
            }
        main_kol_id = _create_main_kol_from_pool(conn, item, staff=staff)
        action = "created"

    now = _utcnow()
    conn.execute(
        "UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?",
        (main_kol_id, now, int(kol_pool_id)),
    )
    conn.commit()
    _clear_kol_pool_read_cache()
    updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return {
        "linked": True,
        "mode": action,
        "kol_pool_id": int(kol_pool_id),
        "main_kol_id": int(main_kol_id),
        "item": dict(updated) if updated else item,
        "main_kol": _get_main_kol(main_kol_id),
        "candidates": candidates,
    }


def _get_main_kol(kol_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
    return dict(row) if row else {}


def _create_main_kol_from_pool(conn, item: dict[str, Any], *, staff: dict[str, Any] | None = None) -> int:
    columns = _table_columns(conn, "kols")
    if not columns:
        raise RuntimeError("kols table is not available")
    now = _utcnow()
    actor = resolve_staff_id(staff) or None
    display_name = str(item.get("display_name") or item.get("handle") or "KOL").strip() or "KOL"
    profile_url = str(item.get("profile_url") or "")
    raw = _loads(item.get("raw_platform_data"), {}) or {}
    values: dict[str, Any] = {
        "channel_name": display_name,
        "channel_url": profile_url,
        "platform": _platform(item.get("platform") or "other"),
        "country": "",
        "niche": str(_first_present(item.get("primary_topic"), item.get("content_style"), "")),
        "project_name": "",
        "owner_name": "",
        "media_name": display_name,
        "duplicate_flag": "",
        "scale_tier": "",
        "content_type": str(item.get("content_style") or ""),
        "approval_note": "",
        "channel_tags": str(item.get("primary_topic") or ""),
        "affiliate_id": "",
        "affiliate_link": "",
        "discount_code": "",
        "amazon_link": "",
        "short_link": "",
        "primary_category": str(item.get("primary_topic") or ""),
        "promoted_product": "",
        "follower_count": int(_int_or_none(item.get("followers")) or 0),
        "avg_views": int(_int_or_none(item.get("avg_views")) or 0),
        "contact_email": str(item.get("email") or ""),
        "contact_phone": "",
        "contact_status": "candidate",
        "notes": f"Created from KOL Pool #{item.get('id')}: {item.get('platform')}/{item.get('handle')} source={item.get('source_type') or ''} ref={item.get('source_ref') or ''}",
        "assigned_staff_id": actor,
        "created_by_staff_id": actor,
        "created_at": now,
        "updated_at": now,
        "avatar_url": str(item.get("avatar_url") or ""),
        "profile_url": profile_url,
        "contact_links_json": _json([]),
        "contact_raw_json": _json({"kol_pool_id": item.get("id"), "handle": item.get("handle"), "raw_platform_data": raw}),
    }
    insert_columns = [column for column in values if column in columns]
    placeholders = ",".join(["?"] * len(insert_columns))
    conn.execute(
        f"INSERT INTO kols ({', '.join(insert_columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in insert_columns),
    )
    row = conn.execute(
        """
        SELECT id FROM kols
        WHERE platform=? AND channel_name=? AND created_at=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (values["platform"], values["channel_name"], now),
    ).fetchone()
    if not row:
        raise RuntimeError("failed to create main kol")
    return int(row["id"])


def summary() -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    cache_key = _kol_pool_cache_key("summary")
    cached = cache_get(cache_key)
    if cached is not None:
        return _kol_pool_cache_hit(cached)
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone()
    linked = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE linked_main_kol_id IS NOT NULL").fetchone()
    historical = conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool WHERE source_type=?", ("promo_plan_xlsx",)).fetchone()
    by_platform = conn.execute(
        "SELECT platform, COUNT(*) AS n FROM vkpi_kol_pool GROUP BY platform ORDER BY n DESC, platform ASC"
    ).fetchall()
    by_source = conn.execute(
        "SELECT source_type, COUNT(*) AS n FROM vkpi_kol_pool GROUP BY source_type ORDER BY n DESC, source_type ASC"
    ).fetchall()
    country_distribution = _country_distribution(conn)
    return _kol_pool_cache_store(cache_key, {
        "total": int(total["n"] if total else 0),
        "linked_main_kol_count": int(linked["n"] if linked else 0),
        "historical_collaboration_count": int(historical["n"] if historical else 0),
        "candidate_asset_count": int(total["n"] if total else 0),
        "source_scope": "partial" if historical and int(historical["n"] or 0) else "mixed",
        "by_platform": [dict(row) for row in by_platform],
        "by_source": [dict(row) for row in by_source],
        "country_distribution": country_distribution,
        "note": "KOL Pool 是资产池；source_type=promo_plan_xlsx 表示局部历史/计划名录，不等于 Daily Top100 新候选。",
    })


def get_item(kol_pool_id: int) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    row = get_conn().execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    return {"item": dict(row)}


def enrich_item(
    kol_pool_id: int,
    *,
    max_posts: int = 12,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch one KOL Pool candidate from the real platform adapter and update metrics.

    This intentionally enriches a single row per request. Bulk refresh belongs to
    a separate scheduled workflow because it can spend Apify/YouTube quota.
    """

    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    item = dict(row)
    platform = _platform(item.get("platform") or "")
    crawler = get_crawler(platform)
    if crawler is None:
        return {"item": item, "sync_status": "unsupported", "provider_status": "unsupported", "message": f"{platform} crawler not registered"}
    if not getattr(crawler, "configured", False):
        now = _utcnow()
        conn.execute(
            "UPDATE vkpi_kol_pool SET sync_status=?, updated_at=? WHERE id=?",
            ("not_configured", now, int(kol_pool_id)),
        )
        conn.commit()
        _clear_kol_pool_read_cache()
        updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
        return {
            "item": dict(updated) if updated else item,
            "sync_status": "not_configured",
            "provider_status": "not_configured",
            "message": f"{platform} API or crawler is not configured",
        }

    handle_or_url = str(item.get("profile_url") or item.get("handle") or "")
    max_posts_i = max(1, min(50, int(max_posts or 12)))
    if platform == "youtube":
        profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id="")
    else:
        profile_payload = crawler.crawl_channel_profile(handle_or_url, channel_id="", max_posts=max_posts_i)

    profile_items = profile_payload.get("items") if isinstance(profile_payload, dict) else []
    profile = profile_items[0] if isinstance(profile_items, list) and profile_items and isinstance(profile_items[0], dict) else {}
    channel_id = ""
    if platform == "youtube":
        channel_id = str(profile.get("id") or "")
    else:
        channel_id = str(
            _first_present(
                profile.get("username"),
                profile.get("handle"),
                profile.get("screen_name"),
                item.get("handle"),
            )
            or ""
        )

    videos_payload: dict[str, Any] = {}
    videos_items: list[dict[str, Any]] = []
    if platform == "youtube" and channel_id and hasattr(crawler, "crawl_channel_videos"):
        videos_payload = crawler.crawl_channel_videos(channel_id, max_results=max_posts_i)
        videos = videos_payload.get("items") if isinstance(videos_payload, dict) else []
        videos_items = [video for video in videos if isinstance(video, dict)] if isinstance(videos, list) else []
        if not videos_items and isinstance(profile_payload, dict):
            fallback_videos = profile_payload.get("videos")
            if isinstance(fallback_videos, list):
                videos_items = [video for video in fallback_videos if isinstance(video, dict)]
    elif isinstance(profile_payload, dict):
        payload_items = _content_items_from_payload(profile_payload)
        if payload_items and _looks_like_content_item(payload_items[0]):
            videos_items = payload_items
        elif profile:
            videos_items = _content_items_from_payload(profile)

    raw_data = {
        "source": f"{platform}_crawler",
        "profile": profile_payload,
        "videos": videos_items,
        "kpi_status": profile_payload.get("sync_status") or profile_payload.get("provider_status") or "synced",
        "source_ref": f"kol_pool:{kol_pool_id}",
    }
    if platform == "youtube":
        youtube_source = str(profile_payload.get("provider_source") or videos_payload.get("provider_source") or "").strip()
        raw_data["source"] = "youtube_apify" if youtube_source == "apify" else "youtube_api"
        raw_data["youtube_provider_source"] = youtube_source or "youtube_api"
        youtube_fallback_from = profile_payload.get("fallback_from") or videos_payload.get("fallback_from")
        if youtube_fallback_from:
            raw_data["youtube_fallback_from"] = youtube_fallback_from
        raw_data["youtube_kpi_status"] = raw_data["kpi_status"]

    kpis = calculate_kpis(raw_data)
    profile = _profile_item(raw_data)
    stats = _profile_stats(profile)
    followers = _int_or_none(_first_present(kpis.get("followers"), item.get("followers")))
    posts_count = _int_or_none(_first_present(kpis.get("posts"), item.get("posts_count")))
    avg_views = _int_or_none(_first_present(kpis.get("avg_views"), item.get("avg_views")))
    sample_count = len(videos_items) or int(posts_count or 0)
    avg_likes = _average_from_total(kpis.get("likes"), sample_count, item.get("avg_likes"))
    avg_comments = _average_from_total(kpis.get("comments"), sample_count, item.get("avg_comments"))
    engagement_ratio = _float_or_none(kpis.get("engagement_rate"))
    engagement_rate = (engagement_ratio * 100.0) if engagement_ratio is not None and engagement_ratio <= 1 else engagement_ratio
    display_name = _display_name(profile, str(item.get("display_name") or item.get("handle") or ""))
    avatar_url = _thumb_url(profile) or str(item.get("avatar_url") or "")
    bio = _bio(profile) or str(item.get("bio") or "")
    profile_url = _profile_url(platform, profile, str(item.get("handle") or ""), str(item.get("profile_url") or ""))
    sync_status = _normalize_sync_status(raw_data.get("kpi_status"))

    scoring = ScoringRegistry.get("rule_v0").score(
        {
            "platform": platform,
            "followers": followers,
            "posts_count": posts_count,
            "avg_views": avg_views,
            "engagement_rate": engagement_ratio,
            "primary_topic": item.get("primary_topic") or bio,
            "sync_status": sync_status,
        },
        {"product_name": "Viltrox lens", "category": "camera lens", "target_platforms": [platform]},
    )
    now = _utcnow()
    conn.execute(
        """
        UPDATE vkpi_kol_pool
        SET profile_url=?,
            display_name=?,
            avatar_url=?,
            bio=?,
            followers=?,
            following=?,
            posts_count=?,
            avg_views=?,
            avg_likes=?,
            avg_comments=?,
            engagement_rate=?,
            viltrox_fit_score=?,
            viltrox_fit_reason=?,
            sync_status=?,
            raw_platform_data=?,
            last_seen_at=?,
            updated_at=?
        WHERE id=?
        """,
        (
            profile_url,
            display_name,
            avatar_url,
            bio,
            followers,
            _int_or_none(_first_present(stats.get("following"), stats.get("followingCount"), stats.get("followsCount"), item.get("following"))),
            posts_count,
            avg_views,
            avg_likes,
            avg_comments,
            engagement_rate,
            float(scoring.score),
            "; ".join([*scoring.strengths, *scoring.concerns])[:1000],
            sync_status,
            _json(raw_data),
            now,
            now,
            int(kol_pool_id),
        ),
    )
    conn.commit()
    _clear_kol_pool_read_cache()
    updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return {
        "item": dict(updated) if updated else {},
        "sync_status": sync_status,
        "provider_status": profile_payload.get("provider_status") or sync_status,
        "posts_sampled": len(videos_items),
        "score_breakdown": scoring.breakdown,
    }


def batch_enrich_items(
    *,
    ids: list[int] | None = None,
    platform: str = "",
    query: str = "",
    data_status: str = "missing",
    limit: int = 3,
    max_posts: int = 6,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich a small bounded batch from KOL Pool.

    This is intentionally capped to keep real provider/API spend predictable.
    If ids are omitted, the batch is selected from the current filter and only
    includes rows with known crawler registrations.
    """

    ensure_vkpi_product_industry_schema()
    safe_limit = max(1, min(5, int(limit or 3)))
    max_posts_i = max(1, min(24, int(max_posts or 6)))
    conn = get_conn()

    selected_ids: list[int] = []
    if ids:
        for raw_id in ids[:safe_limit]:
            try:
                selected_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue
    else:
        selected = list_pool(
            limit=safe_limit,
            platform=platform,
            query=query,
            data_status=data_status,
            sort_by="missing",
            enrichable=True,
        )
        selected_ids = [int(row["id"]) for row in selected.get("items") or [] if row.get("id")]

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    complete = 0
    for kol_pool_id in selected_ids:
        row = conn.execute("SELECT id, platform, handle FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
        if not row:
            skipped.append({"id": kol_pool_id, "reason": "not_found"})
            continue
        row_dict = dict(row)
        platform_key = _platform(row_dict.get("platform"))
        if platform_key not in ENRICHABLE_PLATFORMS:
            skipped.append({"id": kol_pool_id, "platform": platform_key, "reason": "unsupported"})
            continue
        try:
            result = enrich_item(kol_pool_id, max_posts=max_posts_i, staff=staff)
            if result.get("item"):
                updated_item = result["item"]
                items.append(updated_item)
                gaps = _pool_item_gaps(updated_item)
                if gaps:
                    partial.append({"id": kol_pool_id, "platform": platform_key, "gaps": gaps})
                else:
                    complete += 1
            status = str(result.get("sync_status") or "")
            if status not in {"synced", "ok", "success"}:
                skipped.append({"id": kol_pool_id, "platform": platform_key, "reason": status or "not_synced", "message": result.get("message")})
        except Exception as exc:
            errors.append({"id": kol_pool_id, "platform": platform_key, "error": str(exc)[:500]})

    return {
        "requested": len(ids or selected_ids),
        "attempted": len(selected_ids),
        "enriched": len(items),
        "complete": complete,
        "partial": partial,
        "skipped": skipped,
        "errors": errors,
        "items": items,
        "limit": safe_limit,
        "max_posts": max_posts_i,
        "capped": bool(ids and len(ids) > safe_limit),
    }
