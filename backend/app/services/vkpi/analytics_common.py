"""Shared helpers for V-KPI product analytics services."""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.db.connection import is_postgres_runtime
from app.services.vkpi.workflow import staff_id as resolve_staff_id


DEFAULT_PLATFORMS = ["youtube", "instagram", "tiktok", "xiaohongshu", "facebook", "reddit", "x"]
CHINA_TZ = ZoneInfo("Asia/Shanghai")
logger = get_logger(__name__)
OFFICIAL_ACCOUNT_KEYWORDS = ("viltrox", "唯卓仕")
PLATFORM_EQUIVALENTS = {
    "youtube": ["youtube", "yt"],
    "yt": ["youtube", "yt"],
    "instagram": ["instagram", "ig"],
    "ig": ["instagram", "ig"],
    "tiktok": ["tiktok", "tt"],
    "tt": ["tiktok", "tt"],
    "xiaohongshu": ["xiaohongshu", "xhs"],
    "xhs": ["xiaohongshu", "xhs"],
    "facebook": ["facebook", "fb"],
    "fb": ["facebook", "fb"],
    "x": ["x", "twitter"],
    "twitter": ["x", "twitter"],
    "bilibili": ["bilibili", "bili"],
    "bili": ["bilibili", "bili"],
}
BUYER_INTENT_TERMS = {
    "review": "评测意图",
    "vs": "对比选购",
    "comparison": "对比选购",
    "compare": "对比选购",
    "should you buy": "购买决策",
    "best lens": "购买决策",
    "sample": "样片参考",
    "autofocus": "性能关注",
    "low light": "弱光场景",
    "portrait": "人像场景",
    "wedding": "婚礼/商业拍摄",
    "filmmaker": "视频创作者",
    "cinematic": "视频创作者",
    "photography": "摄影用户",
    "videography": "视频用户",
    "camera lens": "镜头购买意图",
    "镜头": "镜头购买意图",
    "评测": "评测意图",
    "对比": "对比选购",
    "样片": "样片参考",
    "人像": "人像场景",
    "视频": "视频用户",
}
COMPETITOR_TERMS = (
    "sigma",
    "tamron",
    "sony gm",
    "sony g master",
    "canon rf",
    "nikon z",
    "fujifilm xf",
    "fuji x",
    "samyang",
    "rokinon",
    "sirui",
    "laowa",
    "ttartisan",
    "7artisans",
    "meike",
    "zeiss",
    "voigtlander",
)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _run_uid(run_type: str) -> str:
    return f"ana-{run_type}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _db_bool(value: bool) -> bool | int:
    """Postgres has real booleans; SQLite uses 0/1 in the local fallback schema."""
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _china_today() -> str:
    return datetime.now(CHINA_TZ).date().isoformat()


def _platform_variants(platform: str) -> list[str]:
    clean = str(platform or "").strip().lower()
    return PLATFORM_EQUIVALENTS.get(clean, [clean] if clean else [])


def _is_official_account(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("handle", "channel_name", "profile_url")
    ).lower()
    return any(term in haystack for term in OFFICIAL_ACCOUNT_KEYWORDS)


def _text_for_relevance(row: dict[str, Any]) -> str:
    metadata = _loads_json(row.get("metadata_json"), {}) or {}
    video = metadata.get("video") if isinstance(metadata, dict) else {}
    bits = [
        row.get("source_product_sku"),
        row.get("source_video_title"),
        row.get("channel_name"),
        row.get("handle"),
        row.get("source_video_url"),
    ]
    if isinstance(video, dict):
        bits.extend([video.get("title"), video.get("description"), video.get("caption")])
    return " ".join(str(bit or "") for bit in bits).lower()


def _content_intelligence(row: dict[str, Any]) -> dict[str, Any]:
    text = _text_for_relevance(row)
    matched_intents = [label for term, label in BUYER_INTENT_TERMS.items() if term in text]
    matched_competitors = [term for term in COMPETITOR_TERMS if term in text]
    mentions_viltrox = "viltrox" in text or "唯卓仕" in text
    product = str(row.get("source_product_sku") or "").strip()

    buyer_profile = "镜头购买决策人 / 摄影视频用户"
    viewer_profile = "关注镜头评测、样片、对比和拍摄场景的潜在买家"
    if any(label in matched_intents for label in ("视频创作者", "视频用户")):
        buyer_profile = "视频创作者 / 摄影器材升级用户"
        viewer_profile = "关注自动对焦、弱光、视频画质和实拍工作流的人群"
    elif any(label in matched_intents for label in ("人像场景", "婚礼/商业拍摄")):
        buyer_profile = "人像 / 婚礼 / 商业摄影用户"
        viewer_profile = "关注焦外、肤色、弱光和镜头性价比的人群"

    reasons: list[str] = []
    if mentions_viltrox:
        reasons.append("内容直接提到 Viltrox / 唯卓仕")
    if product:
        reasons.append(f"匹配监控产品 {product}")
    if matched_competitors:
        reasons.append("提到同级竞品：" + "、".join(matched_competitors[:3]))
    if matched_intents:
        reasons.append("存在购买/选型意图：" + "、".join(dict.fromkeys(matched_intents[:4])))
    if not reasons:
        reasons.append("与镜头、拍摄或相机用户场景相关")

    score_bonus = 0
    if mentions_viltrox:
        score_bonus += 18
    if matched_competitors:
        score_bonus += 12
    score_bonus += min(20, len(set(matched_intents)) * 5)

    return {
        "score_bonus": score_bonus,
        "relevance_reason": "；".join(reasons),
        "buyer_profile": buyer_profile,
        "viewer_profile": viewer_profile,
        "content_angle": " / ".join(dict.fromkeys(matched_intents[:3])) or "产品相关内容",
        "matched_competitors": matched_competitors[:5],
        "matched_intents": list(dict.fromkeys(matched_intents)),
        "mentions_viltrox": mentions_viltrox,
    }


def _provider_status_from_error(exc: Exception) -> str:
    message = str(exc).lower()
    if isinstance(exc, (ImportError, ModuleNotFoundError, NotImplementedError)):
        return "not_configured"
    if "not found" in message or "404" in message:
        return "provider_not_found"
    if "timeout" in message or "timed out" in message:
        return "provider_timeout"
    return "provider_error"


def _provider_error_payload(exc: Exception, *, platform: str, query: str) -> dict[str, Any]:
    status = _provider_status_from_error(exc)
    return {
        "query": query,
        "platform": platform,
        "overview": {"total_videos": 0, "total_views": 0, "total_likes": 0, "total_comments": 0},
        "comparison": {},
        "categories": {},
        "error": "平台抓取暂未配置或本次抓取失败，未生成假数据。",
        "metadata": {
            "provider_status": status,
            "provider_error": str(exc)[:500],
        },
    }
