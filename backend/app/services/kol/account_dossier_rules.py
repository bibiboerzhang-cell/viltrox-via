"""Rule helpers for KOL account dossier classification."""
from __future__ import annotations

import json
from typing import Any

VILTROX_TERMS = ("viltrox", "af 35", "af 55", "evo", "pro series", "lab")
COMPETITOR_TERMS = ("sigma", "tamron", "sirui", "7artisans", "ttartisan", "meike", "laowa", "sony", "nikon", "canon")
GEAR_REVIEW_TERMS = (
    "review", "lens review", "camera review", "comparison", "vs ", "tutorial", "gear",
    "camera", "lens", "sony", "canon", "nikon", "sigma", "tamron", "viltrox",
    "aperture", "autofocus",
)
COMMERCIAL_DP_TERMS = (
    "director of photography", "cinematographer", "dop", "dp ", "commercial",
    "production company", "produced by", "director:", "music video", "campaign",
    "arri", "red camera", "film set",
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


def safe_json_loads(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def canonical_platform(value: Any) -> str:
    return PLATFORM_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def mentions(text: str, terms: tuple[str, ...]) -> list[str]:
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
        parts.extend([str(post.get("title") or ""), str(post.get("caption") or ""), str(post.get("description") or "")])
    for comment in comments[:20]:
        parts.append(str(comment.get("comment_text") or ""))
    return " \n ".join(parts).lower()


def _count_terms(text: str, terms: tuple[str, ...]) -> int:
    return sum(1 for term in terms if term in text)


def classify_user_persona(context: dict[str, Any]) -> dict[str, str]:
    kol = context.get("kol") or {}
    snapshot = context.get("snapshot") or {}
    platform = canonical_platform(snapshot.get("platform") or kol.get("platform"))
    handle = str(snapshot.get("handle") or kol.get("channel_name") or "").lower()
    text = _text_blob(context)
    if "viltrox" in handle or "viltrox" in str(kol.get("media_name") or "").lower():
        return {"user_persona": "公司/品牌官方账号", "persona_reason": "账号名称或主体包含 Viltrox，归为内部监控账号，不进入普通红人联系池。"}
    if _count_terms(text, COMMERCIAL_DP_TERMS) >= 2:
        return {"user_persona": "商业影像 / DP 制片型 KOL", "persona_reason": "内容中出现导演、DOP、制片、商业项目或品牌片语义，适合专业样片和品牌背书。"}
    if _count_terms(text, GEAR_REVIEW_TERMS) >= 3:
        return {"user_persona": "器材评测 / 教程型 KOL", "persona_reason": "内容集中在镜头、相机、评测、对比或教程，适合短链归因和新品测评合作。"}
    if _count_terms(text, PORTRAIT_PHOTO_TERMS) >= 2:
        return {"user_persona": "摄影师 / 人像创作型 KOL", "persona_reason": "内容偏摄影、人像、婚礼或工作室创作，适合样片、肖像镜头和作品案例合作。"}
    if _count_terms(text, CREATOR_TERMS) >= 2:
        return {"user_persona": "影像创作者 / 泛摄影 KOL", "persona_reason": "内容偏影像创作、短视频或泛摄影表达，可作为内容资产或轻合作候选。"}
    if platform in {"instagram", "tiktok", "x", "reddit"} and not context.get("posts"):
        return {"user_persona": "待同步 / 待人工判断", "persona_reason": "当前没有足够真实内容数据，不能只凭平台账号判定画像。"}
    return {"user_persona": "泛内容创作者 / 待人工判断", "persona_reason": "已抓取数据不足以确认垂类，需要补充内容或人工复核后再联系。"}


def contact_context(context: dict[str, Any]) -> bool:
    kol = context.get("kol") or {}
    snapshot = context.get("snapshot") or {}
    raw = safe_json_loads(snapshot.get("raw_json"), {})
    links = safe_json_loads(kol.get("contact_links_json"), [])
    return bool(
        str(kol.get("contact_email") or "").strip()
        or str(snapshot.get("contact_email") or "").strip()
        or str(raw.get("contact_email") or "").strip()
        or links
        or raw.get("contact_links")
    )


def product_fit_for_persona(persona: str, product_sku: str = "") -> str:
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


def priority_for_persona(*, persona: str, score: int, has_contact: bool, posts_count: int, follower_count: int) -> str:
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


def recommended_action_for_persona(priority: str, persona: str, has_contact: bool, posts_count: int) -> str:
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
