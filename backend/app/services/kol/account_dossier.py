"""services/kol/account_dossier.py — real KOL account scans and dossier reports."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import db_write, get_conn, is_postgres_runtime
from app.services.ai.retry import call_ai_with_retry
from app.services.intelligence.account_scan_service import SCANNERS, provider_ready
from app.services.kol.metrics import engagement_rate

logger = get_logger(__name__)

try:
    from app.services.ai.clients.claude_client import get_claude_client
except Exception:
    def get_claude_client():
        return None


VILTROX_TERMS = ("viltrox", "af 35", "af 55", "evo", "pro series", "lab")
COMPETITOR_TERMS = ("sigma", "tamron", "sirui", "7artisans", "ttartisan", "meike", "laowa", "sony", "nikon", "canon")
GEAR_REVIEW_TERMS = (
    "review", "lens review", "camera review", "comparison", "vs ", "tutorial", "gear", "camera",
    "lens", "sony", "canon", "nikon", "sigma", "tamron", "viltrox", "aperture", "autofocus",
)
COMMERCIAL_DP_TERMS = (
    "director of photography", "cinematographer", "dop", "dp ", "commercial", "production company",
    "produced by", "director:", "music video", "campaign", "arri", "red camera", "film set",
)
PORTRAIT_PHOTO_TERMS = ("portrait", "wedding", "model", "photographer", "photography", "photoshoot", "studio")
CREATOR_TERMS = ("creator", "filmmaker", "videographer", "travel", "street", "cinematic", "reel")
PLATFORM_ALIASES = {
    "ig": "instagram",
    "instagram": "instagram",
    "tt": "tiktok",
    "tiktok": "tiktok",
    "yt": "youtube",
    "youtube": "youtube",
    "fb": "facebook",
    "facebook": "facebook",
    "reddit": "reddit",
    "x": "x",
    "twitter": "x",
    "douyin": "douyin",
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _safe_json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _canonical_platform(value: Any) -> str:
    return PLATFORM_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def _insert_id(conn, cur, table: str) -> int:
    lastrowid = getattr(cur, "lastrowid", None)
    if lastrowid not in (None, 0):
        return int(lastrowid)
    if is_postgres_runtime():
        row = conn.execute(
            "SELECT currval(pg_get_serial_sequence(?, 'id')) AS id",
            (table,),
        ).fetchone()
        if row:
            return int(row["id"])
    raise RuntimeError(f"Could not resolve inserted id for {table}")


def _clean_json_text(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def _row(row) -> dict[str, Any]:
    return dict(row) if row else {}


def _rows(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _mentions(text: str, terms: tuple[str, ...]) -> list[str]:
    lower = str(text or "").lower()
    found: list[str] = []
    for term in terms:
        if term in lower and term not in found:
            found.append(term)
    return found


def _text_blob(context: dict[str, Any]) -> str:
    kol = context.get("kol") or {}
    snapshot = context.get("snapshot") or {}
    posts = context.get("posts") or []
    comments = context.get("comments") or []
    parts: list[str] = [
        str(kol.get("channel_name") or ""),
        str(kol.get("media_name") or ""),
        str(kol.get("niche") or ""),
        str(kol.get("primary_category") or ""),
        str(snapshot.get("bio") or ""),
        str(snapshot.get("description") or ""),
    ]
    for post in posts[:20]:
        parts.append(str(post.get("title") or ""))
        parts.append(str(post.get("caption") or ""))
        parts.append(str(post.get("description") or ""))
    for comment in comments[:20]:
        parts.append(str(comment.get("comment_text") or ""))
    return " \n ".join(parts).lower()


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def _classify_user_persona(context: dict[str, Any]) -> dict[str, str]:
    kol = context.get("kol") or {}
    snapshot = context.get("snapshot") or {}
    platform = _canonical_platform(snapshot.get("platform") or kol.get("platform"))
    handle = str(snapshot.get("handle") or kol.get("channel_name") or "").lower()
    text = _text_blob(context)
    if "viltrox" in handle or "viltrox" in str(kol.get("media_name") or "").lower():
        return {
            "user_persona": "公司/品牌官方账号",
            "persona_reason": "账号名称或主体包含 Viltrox，归为内部监控账号，不进入普通红人联系池。",
        }
    if _count_terms(text, COMMERCIAL_DP_TERMS) >= 2:
        return {
            "user_persona": "商业影像 / DP 制片型 KOL",
            "persona_reason": "内容中出现导演、DOP、制片、商业项目或品牌片语义，适合专业样片和品牌背书。",
        }
    if _count_terms(text, GEAR_REVIEW_TERMS) >= 3:
        return {
            "user_persona": "器材评测 / 教程型 KOL",
            "persona_reason": "内容集中在镜头、相机、评测、对比或教程，适合短链归因和新品测评合作。",
        }
    if _count_terms(text, PORTRAIT_PHOTO_TERMS) >= 2:
        return {
            "user_persona": "摄影师 / 人像创作型 KOL",
            "persona_reason": "内容偏摄影、人像、婚礼或工作室创作，适合样片、肖像镜头和作品案例合作。",
        }
    if _count_terms(text, CREATOR_TERMS) >= 2:
        return {
            "user_persona": "影像创作者 / 泛摄影 KOL",
            "persona_reason": "内容偏影像创作、短视频或泛摄影表达，可作为内容资产或轻合作候选。",
        }
    if platform in {"instagram", "tiktok", "x", "reddit"} and not context.get("posts"):
        return {
            "user_persona": "待同步 / 待人工判断",
            "persona_reason": "当前没有足够真实内容数据，不能只凭平台账号判定画像。",
        }
    return {
        "user_persona": "泛内容创作者 / 待人工判断",
        "persona_reason": "已抓取数据不足以确认垂类，需要补充内容或人工复核后再联系。",
    }


def _contact_context(context: dict[str, Any]) -> bool:
    kol = context.get("kol") or {}
    snapshot = context.get("snapshot") or {}
    raw = _safe_json_loads(snapshot.get("raw_json"), {})
    links = _safe_json_loads(kol.get("contact_links_json"), [])
    return bool(
        str(kol.get("contact_email") or "").strip()
        or str(snapshot.get("contact_email") or "").strip()
        or str(raw.get("contact_email") or "").strip()
        or links
        or raw.get("contact_links")
    )


def _product_fit_for_persona(persona: str, product_sku: str = "") -> str:
    if product_sku:
        return product_sku
    if "器材评测" in persona:
        return "AF 镜头、闪光灯、Amazon/Shopify 归因链接"
    if "商业影像" in persona:
        return "Cine 镜头、LUNA、Pro 系列、专业样片合作"
    if "人像" in persona:
        return "大光圈人像镜头、轻量定焦、样片合作"
    if "泛摄影" in persona:
        return "轻量镜头、入门产品、内容资产合作"
    if "官方" in persona:
        return "内部监控，不作为红人投放目标"
    return "待定，需要补抓内容后判断"


def _priority_for_persona(*, persona: str, score: int, has_contact: bool, posts_count: int, follower_count: int) -> str:
    if "官方" in persona:
        return "Internal 监控"
    relevant = any(token in persona for token in ("器材评测", "商业影像", "人像", "泛摄影"))
    if score >= 70 and relevant and has_contact:
        return "P0 立即评估联系"
    if score >= 45 and relevant and (has_contact or follower_count >= 50000):
        return "P1 可联系"
    if relevant or posts_count:
        return "P2 观察 / 补数据"
    return "P3 暂缓"


def _recommended_action_for_persona(priority: str, persona: str, has_contact: bool, posts_count: int) -> str:
    if priority.startswith("P0"):
        return "立即进入人工复核：确认报价/档期，生成 Shopify 或 Amazon 归因链接后联系。"
    if priority.startswith("P1"):
        return "可进入联系池：先确认联系方式和近期内容方向，再安排产品匹配。"
    if priority.startswith("P2"):
        return "先补抓账号内容、邮箱或外链；数据完整后再决定是否寄样。"
    if priority.startswith("Internal"):
        return "作为品牌/公司账号监控，不分配员工普通联系任务。"
    if not posts_count:
        return "当前缺少真实内容数据，暂不建议联系。"
    return "暂缓联系，后续通过产品关键词或竞品提及再观察。"


def _normalize_handle(kol: dict[str, Any]) -> str:
    url = str(kol.get("channel_url") or "").strip()
    name = str(kol.get("channel_name") or kol.get("media_name") or "").strip()
    raw = url or name
    raw = raw.strip().rstrip("/")
    if "douyin.com/" in raw:
        return raw.rsplit("douyin.com/", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    if "youtube.com/@" in raw:
        return raw.rsplit("/@", 1)[-1].split("/", 1)[0]
    if "tiktok.com/@" in raw:
        return raw.rsplit("/@", 1)[-1].split("/", 1)[0]
    if "instagram.com/" in raw:
        return raw.rsplit("instagram.com/", 1)[-1].split("/", 1)[0]
    if "facebook.com/" in raw:
        return raw.rsplit("facebook.com/", 1)[-1].split("/", 1)[0]
    if "reddit.com/user/" in raw:
        return raw.rsplit("reddit.com/user/", 1)[-1].split("/", 1)[0]
    if "x.com/" in raw:
        return raw.rsplit("x.com/", 1)[-1].split("/", 1)[0]
    if "twitter.com/" in raw:
        return raw.rsplit("twitter.com/", 1)[-1].split("/", 1)[0]
    cleaned = re.sub(r"\s*[-_–—]\s*\[[^\]]+\]\s*$", "", raw)
    cleaned = re.sub(r"\s*[-_–—]\s*【[^】]+】\s*$", "", cleaned)
    return cleaned.strip().lstrip("@")


def _account_url(platform: str, handle: str, fallback: str = "") -> str:
    if fallback:
        return fallback
    platform = _canonical_platform(platform)
    safe = handle.lstrip("@")
    if platform == "youtube":
        return f"https://www.youtube.com/@{safe}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{safe}"
    if platform == "douyin":
        return f"https://www.douyin.com/search/{safe}"
    if platform == "instagram":
        return f"https://www.instagram.com/{safe}/"
    if platform == "facebook":
        return f"https://www.facebook.com/{safe}/"
    if platform == "reddit":
        return f"https://www.reddit.com/user/{safe}/"
    if platform == "x":
        return f"https://x.com/{safe}"
    return ""


def _comment_rows(post: dict[str, Any]) -> list[dict[str, Any]]:
    raw = post.get("raw_comments")
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw[:200]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        brand = _mentions(text, VILTROX_TERMS)
        comp = _mentions(text, COMPETITOR_TERMS)
        intent = []
        lower = text.lower()
        if any(word in lower for word in ("price", "cost", "$", "buy", "purchase")):
            intent.append("purchase")
        if any(word in lower for word in ("compare", "vs", "better", "sigma", "tamron")):
            intent.append("comparison")
        if brand:
            intent.append("viltrox")
        if comp:
            intent.append("competitor")
        rows.append(
            {
                "author_handle": str(item.get("author") or "")[:120],
                "comment_text": text[:1000],
                "like_count": _int(item.get("likes")),
                "sentiment": "unknown",
                "intent_tags": sorted(set(intent)),
            }
        )
    return rows


def _post_summary(posts: list[dict[str, Any]]) -> dict[str, Any]:
    total_views = sum(_int(post.get("views")) for post in posts)
    total_likes = sum(_int(post.get("likes")) for post in posts)
    total_comments = sum(_int(post.get("comments")) for post in posts)
    total_shares = sum(_int(post.get("shares")) for post in posts)
    titles = "\n".join(str(post.get("title") or "") for post in posts)
    comment_count = sum(len(_comment_rows(post)) for post in posts)
    return {
        "content_count": len(posts),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_shares": total_shares,
        "avg_views": round(total_views / len(posts)) if posts else 0,
        "engagement_rate": engagement_rate(total_likes, total_comments, total_shares, total_views),
        "brand_mentions": _mentions(titles, VILTROX_TERMS),
        "competitor_mentions": _mentions(titles, COMPETITOR_TERMS),
        "comment_count": comment_count,
    }


def _get_kol(kol_id: int) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not row:
        raise ValueError("KOL not found")
    return dict(row)


def _persist_scan(kol: dict[str, Any], result: dict[str, Any], status: str = "done", error: str = "") -> dict[str, Any]:
    conn = get_conn()
    posts = list(result.get("posts") or [])
    summary = _post_summary(posts)
    platform = _canonical_platform(result.get("platform") or kol.get("platform") or "")
    handle = str(result.get("handle") or _normalize_handle(kol))
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    avatar_url = str(result.get("avatar_url") or profile.get("avatar_url") or kol.get("avatar_url") or "").strip()
    profile_url = str(result.get("profile_url") or profile.get("profile_url") or kol.get("profile_url") or "").strip()
    account_url = _account_url(platform, handle, profile_url or str(kol.get("channel_url") or ""))
    contact_emails = result.get("contact_emails") or profile.get("contact_emails") or []
    if not isinstance(contact_emails, list):
        contact_emails = []
    contact_email = str(
        result.get("contact_email")
        or profile.get("contact_email")
        or (contact_emails[0] if contact_emails else "")
        or kol.get("contact_email")
        or ""
    ).strip()
    contact_links = result.get("contact_links") or profile.get("contact_links") or []
    if not isinstance(contact_links, list):
        contact_links = []
    contact_raw = {
        "profile": profile,
        "contact_emails": contact_emails,
        "contact_links": contact_links,
        "scan_status": status,
        "scan_error": error,
    }
    cur = conn.execute(
        """
        INSERT INTO kol_account_snapshots
            (kol_id, platform, handle, account_url, follower_count, content_count,
             total_views, total_likes, total_comments, total_shares, avg_views,
             engagement_rate, brand_mentions_json, competitor_mentions_json,
             comment_count, scan_status, error_message, raw_json, scanned_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(kol["id"]),
            platform,
            handle,
            account_url,
            _int(result.get("follower_count") or profile.get("follower_count") or kol.get("follower_count")),
            summary["content_count"],
            summary["total_views"],
            summary["total_likes"],
            summary["total_comments"],
            summary["total_shares"],
            summary["avg_views"],
            summary["engagement_rate"],
            _json(summary["brand_mentions"]),
            _json(summary["competitor_mentions"]),
            summary["comment_count"],
            status,
            error,
            _json({k: v for k, v in result.items() if k != "posts"}),
            _now(),
        ),
    )
    snapshot_id = _insert_id(conn, cur, "kol_account_snapshots")
    for post in posts:
        title = str(post.get("title") or "")[:500]
        post_url = str(post.get("url") or "").strip()
        if not post_url:
            continue
        thumbnail_url = _first_text(
            post.get("thumbnail"),
            post.get("thumbnail_url"),
            post.get("thumbnailUrl"),
            post.get("displayUrl"),
            post.get("display_url"),
            post.get("imageUrl"),
            post.get("image_url"),
            post.get("coverUrl"),
            post.get("cover_url"),
            post.get("videoThumbnail"),
            post.get("video_thumbnail"),
        )
        brand_mentions = sorted(set(_mentions(title, VILTROX_TERMS)))
        competitor_mentions = sorted(set(_mentions(title, COMPETITOR_TERMS)))
        conn.execute(
            """
            INSERT INTO kol_posts
                (kol_id, snapshot_id, platform, post_url, title, thumbnail_url,
                 published_at, content_type, views, likes, comments, shares,
                 brand_mentions_json, competitor_mentions_json, raw_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(kol_id, post_url) DO UPDATE SET
                snapshot_id = excluded.snapshot_id,
                title = excluded.title,
                thumbnail_url = excluded.thumbnail_url,
                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,
                shares = excluded.shares,
                brand_mentions_json = excluded.brand_mentions_json,
                competitor_mentions_json = excluded.competitor_mentions_json,
                raw_json = excluded.raw_json
            """,
            (
                int(kol["id"]),
                snapshot_id,
                platform,
                post_url,
                title,
                thumbnail_url,
                _date(post.get("published")),
                str(post.get("type") or ""),
                _int(post.get("views")),
                _int(post.get("likes")),
                _int(post.get("comments")),
                _int(post.get("shares")),
                _json(brand_mentions),
                _json(competitor_mentions),
                _json(post),
                _now(),
            ),
        )
        post_row = conn.execute("SELECT id FROM kol_posts WHERE kol_id = ? AND post_url = ?", (int(kol["id"]), post_url)).fetchone()
        post_id = int(post_row["id"]) if post_row else None
        for comment in _comment_rows(post):
            conn.execute(
                """
                INSERT INTO kol_comments
                    (kol_id, post_id, platform, post_url, author_handle, comment_text,
                     like_count, sentiment, intent_tags_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(kol["id"]),
                    post_id,
                    platform,
                    post_url,
                    comment["author_handle"],
                    comment["comment_text"],
                    comment["like_count"],
                    comment["sentiment"],
                    _json(comment["intent_tags"]),
                    _now(),
                ),
            )
    next_follower_count = _int(result.get("follower_count") or profile.get("follower_count") or kol.get("follower_count"))
    next_avg_views = summary["avg_views"] if summary["content_count"] > 0 else _int(kol.get("avg_views"))
    conn.execute(
        """
        UPDATE kols
        SET follower_count = ?,
            avg_views = ?,
            contact_email = ?,
            contact_status = ?,
            avatar_url = ?,
            profile_url = ?,
            contact_links_json = ?,
            contact_raw_json = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            next_follower_count,
            next_avg_views,
            contact_email,
            "contact_found" if contact_email or contact_links else str(kol.get("contact_status") or "cold"),
            avatar_url,
            profile_url or account_url,
            _json(contact_links),
            _json(contact_raw),
            _now(),
            int(kol["id"]),
        ),
    )
    conn.commit()
    return {
        "snapshot_id": snapshot_id,
        "status": status,
        "error_message": error,
        "platform": platform,
        "handle": handle,
        "account_url": account_url,
        "avatar_url": avatar_url,
        "profile_url": profile_url or account_url,
        "contact_email": contact_email,
        "contact_links": contact_links,
        **summary,
    }


async def scan_kol_account(kol_id: int, max_posts: int = 50) -> dict[str, Any]:
    kol = await db_write(lambda: _get_kol(int(kol_id)))
    platform = _canonical_platform(kol.get("platform"))
    handle = _normalize_handle(kol)
    scanner = SCANNERS.get(platform)
    if not scanner:
        return await db_write(
            lambda: _persist_scan(
                kol,
                {"platform": platform, "handle": handle, "posts": []},
                status="unsupported_platform",
                error=f"{platform} account scan is not configured",
            )
        )
    if not provider_ready():
        return await db_write(
            lambda: _persist_scan(
                kol,
                {"platform": platform, "handle": handle, "posts": []},
                status="provider_unavailable",
                error="APIFY_TOKEN is not configured",
            )
        )
    safe_limit = max(1, min(int(max_posts or 50), 500))
    result = await scanner(handle, safe_limit)
    status = str(result.get("status") or ("done" if result.get("posts") else "empty"))
    error = str(result.get("error") or "")
    return await db_write(lambda: _persist_scan(kol, result, status=status, error=error))


def _latest_snapshot(kol_id: int, snapshot_id: int | None = None) -> dict[str, Any]:
    conn = get_conn()
    if snapshot_id:
        row = conn.execute(
            "SELECT * FROM kol_account_snapshots WHERE kol_id = ? AND id = ?",
            (int(kol_id), int(snapshot_id)),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM kol_account_snapshots WHERE kol_id = ? ORDER BY scanned_at DESC LIMIT 1",
            (int(kol_id),),
        ).fetchone()
    return _row(row)


def _analysis_context(kol_id: int, snapshot_id: int | None = None) -> dict[str, Any]:
    conn = get_conn()
    kol = _get_kol(kol_id)
    snapshot = _latest_snapshot(kol_id, snapshot_id)
    posts = _rows(
        conn.execute(
            """
            SELECT *
            FROM kol_posts
            WHERE kol_id = ?
            ORDER BY views DESC, created_at DESC
            LIMIT 25
            """,
            (int(kol_id),),
        ).fetchall()
    )
    comments = _rows(
        conn.execute(
            """
            SELECT *
            FROM kol_comments
            WHERE kol_id = ?
            ORDER BY like_count DESC, created_at DESC
            LIMIT 80
            """,
            (int(kol_id),),
        ).fetchall()
    )
    return {"kol": kol, "snapshot": snapshot, "posts": posts, "comments": comments}


def _metrics_report(context: dict[str, Any], product_sku: str = "") -> dict[str, Any]:
    snapshot = context.get("snapshot") or {}
    posts = context.get("posts") or []
    comments = context.get("comments") or []
    engagement = float(snapshot.get("engagement_rate") or 0)
    avg_views = _int(snapshot.get("avg_views"))
    comment_count = _int(snapshot.get("comment_count")) or len(comments)
    score = min(100, max(0, int(engagement * 1200) + min(40, avg_views // 2500) + min(20, comment_count // 10)))
    brand_mentions = _safe_json_loads(snapshot.get("brand_mentions_json"), [])
    competitor_mentions = _safe_json_loads(snapshot.get("competitor_mentions_json"), [])
    persona = _classify_user_persona(context)
    user_persona = persona["user_persona"]
    has_contact = _contact_context(context)
    follower_count = _int(snapshot.get("follower_count"))
    priority = _priority_for_persona(
        persona=user_persona,
        score=score,
        has_contact=has_contact,
        posts_count=len(posts),
        follower_count=follower_count,
    )
    product_fit_summary = _product_fit_for_persona(user_persona, product_sku)
    return {
        **persona,
        "cooperation_priority": priority,
        "product_fit_summary": product_fit_summary,
        "contact_action": _recommended_action_for_persona(priority, user_persona, has_contact, len(posts)),
        "contact_available": has_contact,
        "account_score": score,
        "audience_fit": min(100, score + 8 if product_sku else score),
        "product_fit": min(100, score + (12 if brand_mentions else 0)),
        "risk_level": "medium" if competitor_mentions and not brand_mentions else "low",
        "recommended_action": _recommended_action_for_persona(priority, user_persona, has_contact, len(posts)),
        "suggested_products": [product_sku] if product_sku else [product_fit_summary],
        "brand_history": {
            "viltrox_mentions": brand_mentions,
            "competitor_mentions": competitor_mentions,
            "top_posts": [
                {"title": post.get("title"), "url": post.get("post_url"), "views": post.get("views")}
                for post in posts[:5]
            ],
        },
        "comment_insights": {
            "comment_count": comment_count,
            "available_comment_samples": [comment.get("comment_text") for comment in comments[:5]],
        },
        "summary_zh": "基于已抓取账号数据生成的指标版报告；未调用 Claude 或 Claude 不可用。",
        "summary_en": "Metrics-based dossier generated from stored account scan data.",
        "method": "metrics_fallback",
    }


def _claude_report(context: dict[str, Any], product_sku: str = "") -> dict[str, Any]:
    fallback = _metrics_report(context, product_sku)
    client = get_claude_client()
    if client is None:
        return fallback
    compact = {
        "kol": context.get("kol"),
        "snapshot": context.get("snapshot"),
        "top_posts": (context.get("posts") or [])[:15],
        "top_comments": (context.get("comments") or [])[:30],
        "target_product": product_sku,
    }
    prompt = f"""
You are building a KOL account dossier for Viltrox marketing operations.
Return ONLY JSON with these fields:
{{
  "user_persona": "器材评测 / 教程型 KOL | 商业影像 / DP 制片型 KOL | 摄影师 / 人像创作型 KOL | 影像创作者 / 泛摄影 KOL | 泛内容创作者 / 待人工判断 | 公司/品牌官方账号 | 待同步 / 待人工判断",
  "cooperation_priority": "P0 立即评估联系 | P1 可联系 | P2 观察 / 补数据 | P3 暂缓 | Internal 监控",
  "product_fit_summary": "which Viltrox product family fits this account",
  "persona_reason": "why this account belongs to that persona",
  "contact_action": "next contact or data-completion action in Chinese",
  "account_score": 0-100,
  "audience_fit": 0-100,
  "product_fit": 0-100,
  "risk_level": "low|medium|high",
  "recommended_action": "concise Chinese action",
  "suggested_products": ["3 products or content angles"],
  "brand_history": {{"viltrox": "...", "competitors": "...", "cooperation_risk": "..."}},
  "comment_insights": {{"demand": "...", "concerns": "...", "notable_quotes": ["..."]}},
  "summary_zh": "Chinese executive summary",
  "summary_en": "English executive summary"
}}

Use only the provided account scan, post, and comment data. If comments are missing,
say comments are not available instead of inventing them.

DATA:
{json.dumps(compact, ensure_ascii=False, default=str)[:12000]}
"""
    try:
        resp = call_ai_with_retry(
            "kol_account_dossier.claude",
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1400,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        text = resp.content[0].text if resp.content else ""
        parsed = json.loads(_clean_json_text(text))
        if not isinstance(parsed, dict):
            raise ValueError("Claude returned non-object JSON")
        for key in (
            "user_persona",
            "cooperation_priority",
            "product_fit_summary",
            "persona_reason",
            "contact_action",
            "contact_available",
        ):
            if key not in parsed or parsed.get(key) in (None, ""):
                parsed[key] = fallback.get(key)
        parsed["account_score"] = max(0, min(100, _int(parsed.get("account_score"))))
        parsed["audience_fit"] = max(0, min(100, _int(parsed.get("audience_fit"))))
        parsed["product_fit"] = max(0, min(100, _int(parsed.get("product_fit"))))
        parsed["method"] = "claude_account_dossier"
        return parsed
    except Exception:
        logger.warning("kol_account_dossier.claude_failed", exc_info=True)
        fallback["method"] = "metrics_fallback_after_claude_error"
        return fallback


def _persist_report(kol_id: int, snapshot_id: int | None, report: dict[str, Any]) -> dict[str, Any]:
    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO kol_analysis_reports
            (kol_id, snapshot_id, report_type, account_score, audience_fit, product_fit,
             risk_level, recommended_action, suggested_products_json, brand_history_json,
             comment_insights_json, summary_zh, summary_en, raw_json, method, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(kol_id),
            int(snapshot_id) if snapshot_id else None,
            "account_dossier",
            _int(report.get("account_score")),
            _int(report.get("audience_fit")),
            _int(report.get("product_fit")),
            str(report.get("risk_level") or "unknown"),
            str(report.get("recommended_action") or ""),
            _json(report.get("suggested_products") or []),
            _json(report.get("brand_history") or {}),
            _json(report.get("comment_insights") or {}),
            str(report.get("summary_zh") or ""),
            str(report.get("summary_en") or ""),
            _json(report),
            str(report.get("method") or ""),
            _now(),
        ),
    )
    report_id = _insert_id(conn, cur, "kol_analysis_reports")
    conn.commit()
    return {"report_id": report_id, "kol_id": int(kol_id), "snapshot_id": snapshot_id, **report}


async def analyze_kol_account(kol_id: int, product_sku: str = "", snapshot_id: int | None = None) -> dict[str, Any]:
    context = await db_write(lambda: _analysis_context(int(kol_id), snapshot_id))
    snapshot = context.get("snapshot") or {}
    sid = int(snapshot.get("id") or 0) or None
    report = await asyncio.to_thread(_claude_report, context, product_sku)
    return await db_write(lambda: _persist_report(int(kol_id), sid, report))


def get_kol_dossier(kol_id: int) -> dict[str, Any]:
    conn = get_conn()
    snapshot = _latest_snapshot(int(kol_id))
    report = _row(
        conn.execute(
            "SELECT * FROM kol_analysis_reports WHERE kol_id = ? ORDER BY created_at DESC LIMIT 1",
            (int(kol_id),),
        ).fetchone()
    )
    return {
        "snapshot": snapshot,
        "report": report,
        "posts": list_kol_posts(int(kol_id), limit=10, offset=0)["items"],
        "comments": list_kol_comments(int(kol_id), limit=10, offset=0)["items"],
    }


def list_kol_posts(kol_id: int, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 25), 100))
    safe_offset = max(0, int(offset or 0))
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS c FROM kol_posts WHERE kol_id = ?", (int(kol_id),)).fetchone()
    rows = conn.execute(
        """
        SELECT *
        FROM kol_posts
        WHERE kol_id = ?
        ORDER BY views DESC, created_at DESC
        LIMIT ? OFFSET ?
        """,
        (int(kol_id), safe_limit, safe_offset),
    ).fetchall()
    return {
        "items": _rows(rows),
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "total": _int(total["c"] if total else 0),
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < _int(total["c"] if total else 0) else None,
            "prev_offset": max(0, safe_offset - safe_limit) if safe_offset > 0 else None,
        },
    }


def list_kol_comments(kol_id: int, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 25), 100))
    safe_offset = max(0, int(offset or 0))
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS c FROM kol_comments WHERE kol_id = ?", (int(kol_id),)).fetchone()
    rows = conn.execute(
        """
        SELECT *
        FROM kol_comments
        WHERE kol_id = ?
        ORDER BY like_count DESC, created_at DESC
        LIMIT ? OFFSET ?
        """,
        (int(kol_id), safe_limit, safe_offset),
    ).fetchall()
    count = _int(total["c"] if total else 0)
    return {
        "items": _rows(rows),
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "total": count,
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < count else None,
            "prev_offset": max(0, safe_offset - safe_limit) if safe_offset > 0 else None,
        },
    }
