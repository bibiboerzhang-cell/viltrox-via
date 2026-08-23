"""KOL Pool single/batch enrichment adapters (moved from pool.py, behavior-preserving).

附:raw 字段提列解析器(优化波 B · D 车道,迁移 291)。``extract_raw_fields`` 是纯函数
(零网络、零 LLM),从已落库的 raw_platform_data 里榨 TikTok/Instagram/YouTube 的结构化资产;
``apply_raw_fields`` 只写迁移 208/291 的派生列,列未迁移静默跳过。存量由
scripts/ops/backfill_pool_raw_fields.py 幂等回填。红线:绝不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.platform.industry_crawlers import get_crawler
from app.domains.industry.snapshot_kpis import calculate_kpis
from app.domains.kol.pool_common import (
    ENRICHABLE_PLATFORMS,
    _average_from_total,
    _bio,
    _bool_signal,
    _clear_kol_pool_read_cache,
    _commerce_flags,
    _content_items_from_payload,
    _display_name,
    _first_present,
    _float_or_none,
    _int_or_none,
    _json,
    _looks_like_content_item,
    _nested_dict,
    _normalize_sync_status,
    _platform,
    _pool_item_gaps,
    _profile_item,
    _profile_stats,
    _profile_url,
    _table_columns,
    _thumb_url,
    _utcnow,
)
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema
from app.domains.scoring import ScoringRegistry

from app.core.logging import get_logger

logger = get_logger(__name__)


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
    # C6 零新抓提列 + 291 raw 字段提列:K8 商单/认证标记(authorMeta.verified/ttSeller/
    # commerceUserInfo.commerceUser)与主题/被标记品牌从同一份 raw 顺手提列。独立 UPDATE +
    # 独立提交:列未迁移(旧布局)或解析异常一律静默跳过,绝不影响主富化。
    # 红线:只写派生标记列,不触 viltrox_fit_score / rule_v0。
    try:
        apply_raw_fields(conn, int(kol_pool_id), raw_data, platform=platform)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("回滚失败(best-effort)", exc_info=True)
        logger.warning("raw fields extract skipped kol=%s", kol_pool_id, exc_info=True)
    # primary_topic 前向填充(2026-07-19 挂账刀②):同一份 raw 规则分类,只填空不覆盖
    # (含未来人工填写);存量由 backfill_primary_topic.py 清偿。独立 UPDATE + 独立提交,
    # 异常静默跳过。红线:纯数据列,零触 viltrox_fit_score / rule_v0 公式。
    try:
        from app.domains.kol.eleven_dimensions import derive_primary_topic

        topic, secondary = derive_primary_topic(
            {
                "display_name": display_name,
                "handle": item.get("handle"),
                "bio": bio,
                "raw_platform_data": raw_data,
            }
        )
        if topic:
            conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET primary_topic=?, secondary_topics_json=?
                WHERE id=? AND (primary_topic IS NULL OR TRIM(primary_topic) = '')
                """,
                (topic, _json(secondary), int(kol_pool_id)),
            )
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            logger.debug("回滚失败(best-effort)", exc_info=True)
        logger.warning("primary_topic derive skipped kol=%s", kol_pool_id, exc_info=True)
    # 第二道闸(2026-07-12 两粉号案):followers 已回填真值 → 立即重过触达门槛,
    # 命中给 raw_platform_data 打 low_reach 标(推荐/发现/召回三出口据此不展示;行保留)。
    # best-effort 绝不阻断富化;判据复用 discovery_filters 单一真源;零触 viltrox_fit_score。
    try:
        from app.domains.kol.reach_floor_regate import reapply_reach_floor

        reapply_reach_floor(int(kol_pool_id), conn=conn)
    except Exception:
        logger.warning("reach floor regate skipped kol=%s", kol_pool_id, exc_info=True)
    _clear_kol_pool_read_cache()
    updated = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone()
    return {
        "item": dict(updated) if updated else {},
        "sync_status": sync_status,
        "provider_status": profile_payload.get("provider_status") or sync_status,
        "posts_sampled": len(videos_items),
        "score_breakdown": scoring.breakdown,
    }


# ── raw 字段提列解析器(迁移 291;零网络、零 LLM、纯函数)──

RAW_FIELDS_EXTRACTOR_VERSION = "raw_fields_v1"
TAGGED_BRANDS_MAX = 40
_YT_KEYWORD_RE = re.compile(r'"([^"]+)"|(\S+)')
_HANDLE_STRIP_RE = re.compile(r"^[@\s]+|[\s,.:;!]+$")


def _raw_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)) and raw:
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _raw_containers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """raw 里所有「带账号/帖子结构」的容器:profile.items[] + videos[] + 顶层 profile/account。
    TT 档案 raw 每条 items 都是一条视频(authorMeta 挂在视频上);IG 是单条账号 + latestPosts;
    YT 是 channels.list 单条 + videos[]。解析器对每个容器逐个兜,键名缺席即跳过。"""
    candidates: list[dict[str, Any]] = []
    profile_payload = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
    items = profile_payload.get("items") if isinstance(profile_payload, dict) else None
    if isinstance(items, list):
        candidates.extend(item for item in items if isinstance(item, dict))
    elif profile_payload:
        candidates.append(profile_payload)
    account = raw.get("account")
    if isinstance(account, dict):
        candidates.append(account)
    videos = raw.get("videos")
    if isinstance(videos, list):
        candidates.extend(video for video in videos if isinstance(video, dict))
    # TT 档案 raw 的 profile.items[] 与 videos[] 是同一批视频(enrich_item 两处都存),按 id/URL 去重,
    # 否则 mentions / categoryId 直方会双计。
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in candidates:
        key = str(container.get("id") or container.get("webVideoUrl") or container.get("url") or container.get("shortCode") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(container)
    return out


def _post_containers(container: dict[str, Any]) -> list[dict[str, Any]]:
    """账号容器里的帖子列表(IG latestPosts/latestIgtvVideos + 轮播 childPosts);TT/YT 视频本身就是帖子。"""
    posts: list[dict[str, Any]] = [container]
    for key in ("latestPosts", "latestIgtvVideos", "posts"):
        value = container.get(key)
        if isinstance(value, list):
            posts.extend(post for post in value if isinstance(post, dict))
    expanded: list[dict[str, Any]] = []
    for post in posts:
        expanded.append(post)
        children = post.get("childPosts")
        if isinstance(children, list):
            expanded.extend(child for child in children if isinstance(child, dict))
    return expanded


def _clean_handle(value: Any) -> str:
    text = _HANDLE_STRIP_RE.sub("", str(value or "").strip())
    return text.lower()[:80]


def _collect_tagged(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """IG taggedUsers / mentions + TT detailedMentions / mentions 聚合为被标记账号榜。
    真实键名(隔离库抽样):IG post.taggedUsers[{username,full_name,is_verified}] / post.mentions[str];
    TT video.detailedMentions[{id,name,nickName,profileUrl}] / video.mentions["@Nick"]。"""
    tagged: Counter = Counter()
    mentioned: Counter = Counter()
    names: dict[str, str] = {}
    verified: dict[str, bool] = {}
    for container in _raw_containers(raw):
        for post in _post_containers(container):
            for user in post.get("taggedUsers") or []:
                if not isinstance(user, dict):
                    continue
                handle = _clean_handle(user.get("username") or user.get("uniqueId") or user.get("name"))
                if not handle:
                    continue
                tagged[handle] += 1
                if user.get("full_name") or user.get("nickName"):
                    names.setdefault(handle, str(user.get("full_name") or user.get("nickName"))[:120])
                flag = _bool_signal(user.get("is_verified") if "is_verified" in user else user.get("verified"))
                if flag is not None:
                    verified[handle] = flag
            detailed = post.get("detailedMentions")
            detailed_handles: set[str] = set()
            if isinstance(detailed, list):
                for user in detailed:
                    if not isinstance(user, dict):
                        continue
                    handle = _clean_handle(user.get("name") or user.get("uniqueId") or user.get("username"))
                    if not handle:
                        continue
                    detailed_handles.add(handle)
                    mentioned[handle] += 1
                    if user.get("nickName"):
                        names.setdefault(handle, str(user.get("nickName"))[:120])
            if detailed_handles:
                continue  # detailedMentions 已覆盖同一条帖子的 mentions 字符串,避免双计
            for mention in post.get("mentions") or []:
                handle = _clean_handle(mention) if isinstance(mention, str) else ""
                if handle:
                    mentioned[handle] += 1
    handles = set(tagged) | set(mentioned)
    rows = [
        {
            "handle": handle,
            "name": names.get(handle, ""),
            "verified": verified.get(handle),
            "tagged": int(tagged.get(handle, 0)),
            "mentioned": int(mentioned.get(handle, 0)),
            "count": int(tagged.get(handle, 0) + mentioned.get(handle, 0)),
        }
        for handle in handles
    ]
    rows.sort(key=lambda row: (-row["count"], -row["tagged"], row["handle"]))
    return rows[:TAGGED_BRANDS_MAX]


def _yt_keywords(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    out: list[str] = []
    for quoted, bare in _YT_KEYWORD_RE.findall(text):
        keyword = (quoted or bare).strip().lower()
        if keyword and keyword not in out:
            out.append(keyword[:60])
    return out[:50]


def _collect_topics(raw: dict[str, Any], platform: str) -> dict[str, Any] | None:
    """YouTube topicDetails(若 raw 带)+ brandingSettings.channel.keywords + 视频 categoryId 直方;
    TT commerceUserInfo.category;IG businessCategoryName + productType 直方。全空返回 None。"""
    topic_categories: list[str] = []
    topic_ids: list[str] = []
    keywords: list[str] = []
    category_hist: Counter = Counter()
    content_types: Counter = Counter()
    commerce_category = ""
    business_category = ""
    for container in _raw_containers(raw):
        topic = container.get("topicDetails")
        if isinstance(topic, dict):
            for item in topic.get("topicCategories") or []:
                text = str(item or "").strip()
                if text and text not in topic_categories:
                    topic_categories.append(text[:200])
            for item in topic.get("topicIds") or []:
                text = str(item or "").strip()
                if text and text not in topic_ids:
                    topic_ids.append(text[:60])
        branding = container.get("brandingSettings")
        if isinstance(branding, dict) and isinstance(branding.get("channel"), dict):
            for keyword in _yt_keywords(branding["channel"].get("keywords")):
                if keyword not in keywords:
                    keywords.append(keyword)
        snippet = container.get("snippet")
        if isinstance(snippet, dict) and snippet.get("categoryId") not in (None, ""):
            category_hist[str(snippet.get("categoryId"))] += 1
        author = _nested_dict(container, "authorMeta", "author", "owner", "user")
        info = author.get("commerceUserInfo") if isinstance(author.get("commerceUserInfo"), dict) else {}
        if not commerce_category and info.get("category"):
            commerce_category = str(info.get("category"))[:120]
        if not business_category and container.get("businessCategoryName"):
            business_category = str(container.get("businessCategoryName"))[:120]
        for post in _post_containers(container):
            if post.get("productType"):
                content_types[str(post.get("productType"))[:40]] += 1
    if not any((topic_categories, topic_ids, keywords, category_hist, content_types, commerce_category, business_category)):
        return None
    if topic_categories or topic_ids:
        source = "youtube_topic_details"
    elif keywords or category_hist:
        source = "youtube_branding_keywords"
    else:
        source = f"{platform or 'unknown'}_profile_category"
    return {
        "source": source,
        "topic_categories": topic_categories[:20],
        "topic_ids": topic_ids[:20],
        "keywords": keywords[:50],
        "video_category_ids": dict(category_hist.most_common(10)),
        "content_types": dict(content_types.most_common(10)),
        "commerce_category": commerce_category or None,
        "business_category": business_category or None,
    }


def _ig_business_flag(raw: dict[str, Any]) -> bool | None:
    for container in _raw_containers(raw):
        flag = _bool_signal(container.get("isBusinessAccount"))
        if flag is not None:
            return flag
    return None


def extract_raw_fields(raw_platform_data: Any, *, platform: str = "") -> dict[str, Any]:
    """raw_platform_data -> 派生列值(纯函数)。None=raw 无该信号(调用方不写,保持 NULL)。

    返回键:is_verified / is_tt_seller / is_commerce_user(迁移 208 三列;IG isBusinessAccount
    归入 is_commerce_user)、topic_details_json、tagged_brands_json(迁移 291)。
    联系方式不在此提列:bio/signature 里的邮箱外链走 business_contact_extract +
    contact_acquisition_queue -> contact_ingest(去重/抑制/合规闸),不新造路径。
    """
    raw = _raw_dict(raw_platform_data)
    platform_key = _platform(platform or raw.get("platform") or "")
    if not raw:
        return {
            "is_verified": None, "is_tt_seller": None, "is_commerce_user": None,
            "topic_details_json": None, "tagged_brands_json": None,
        }
    flags = _commerce_flags(raw)
    if flags.get("is_commerce_user") is None and platform_key == "instagram":
        flags["is_commerce_user"] = _ig_business_flag(raw)
    tagged = _collect_tagged(raw)
    return {
        "is_verified": flags.get("is_verified"),
        "is_tt_seller": flags.get("is_tt_seller"),
        "is_commerce_user": flags.get("is_commerce_user"),
        "topic_details_json": _collect_topics(raw, platform_key),
        "tagged_brands_json": tagged or None,
    }


def apply_raw_fields(conn: Any, kol_pool_id: int, raw_platform_data: Any, *, platform: str = "") -> dict[str, Any]:
    """把 extract_raw_fields 的结果写进 vkpi_kol_pool(只写存在的列;None 不写,保 NULL 语义)。
    独立提交;列未迁移(旧布局)返回 {"written": 0}。返回统计供回填脚本汇总。"""
    fields = extract_raw_fields(raw_platform_data, platform=platform)
    pool_columns = _table_columns(conn, "vkpi_kol_pool")
    writable: dict[str, Any] = {}
    for key in ("is_verified", "is_tt_seller", "is_commerce_user"):
        if fields.get(key) is not None and key in pool_columns:
            writable[key] = bool(fields[key])
    for key in ("topic_details_json", "tagged_brands_json"):
        if fields.get(key) is not None and key in pool_columns:
            writable[key] = json.dumps(fields[key], ensure_ascii=False)
    if "raw_fields_extracted_at" in pool_columns:
        writable["raw_fields_extracted_at"] = datetime.now(timezone.utc).isoformat()
    if "raw_fields_extractor_version" in pool_columns:
        writable["raw_fields_extractor_version"] = RAW_FIELDS_EXTRACTOR_VERSION
    if not writable:
        return {"written": 0, "fields": fields}
    assignments = ", ".join(f"{key}=?" for key in writable)
    conn.execute(
        f"UPDATE vkpi_kol_pool SET {assignments} WHERE id=?",  # noqa: S608 — 列名来自固定白名单
        (*writable.values(), int(kol_pool_id)),
    )
    conn.commit()
    return {"written": len(writable), "fields": fields}


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

    # Lazy import to avoid circular dependency: pool.py imports from this module.
    from app.domains.kol.pool import list_pool

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
