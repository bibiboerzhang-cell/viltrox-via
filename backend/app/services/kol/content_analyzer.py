"""services/kol/content_analyzer.py — real KOL content URL analysis."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import db_write, get_conn
from app.services.ai.analyzers.claude_vision import analyze_url_content_smart
from app.services.kol.metrics import engagement_rate
from app.services.scraping.platform_router import scrape_url

logger = get_logger(__name__)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or default).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _clean_text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _public_scrape(scrape: dict[str, Any]) -> dict[str, Any]:
    public = dict(scrape or {})
    for key in ("exception", "raw_error", "traceback"):
        public.pop(key, None)
    if public.get("error"):
        public["error"] = "url_scrape_failed"
    return public


def _public_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    public = dict(analysis or {})
    for key in ("error", "errors", "exception", "traceback"):
        public.pop(key, None)
    return public


def _published_at(value: Any, fallback: Any = None) -> str | None:
    raw = value if value not in (None, "") else fallback
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return datetime.utcfromtimestamp(float(raw)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
        return datetime.utcfromtimestamp(float(text)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if re.fullmatch(r"\d{13}", text):
        return datetime.utcfromtimestamp(float(text) / 1000).strftime("%Y-%m-%dT%H:%M:%SZ")
    return text


def _normalize_handle(row: dict[str, Any]) -> str:
    raw = str(row.get("channel_url") or row.get("channel_name") or "").strip().rstrip("/")
    for marker in ("/@", "instagram.com/", "douyin.com/", "youtube.com/"):
        if marker in raw:
            raw = raw.rsplit(marker, 1)[-1]
            break
    raw = raw.split("/", 1)[0].split("?", 1)[0]
    raw = re.sub(r"\s*[-_–—]\s*(?:\\[[^\\]]+\\]|【[^】]+】)\\s*$", "", raw)
    return raw.strip().lstrip("@")


def _score_from_analysis(analysis: dict[str, Any], views: int, likes: int, comments: int, shares: int) -> int:
    quality = _int(analysis.get("quality_overall"))
    if quality > 0:
        return max(0, min(100, quality * 10 if quality <= 10 else quality))
    scores = analysis.get("quality_scores") if isinstance(analysis.get("quality_scores"), dict) else {}
    numeric = [_int(v) for v in scores.values() if isinstance(v, (int, float, str)) and _int(v) > 0]
    if numeric:
        avg = sum(numeric) / len(numeric)
        return max(0, min(100, round(avg * 10 if avg <= 10 else avg)))
    engagement = engagement_rate(likes, comments, shares, views)
    return max(0, min(100, int(engagement * 1000) + min(30, views // 1000)))


def _topics_from_analysis(analysis: dict[str, Any], scrape: dict[str, Any]) -> list[str]:
    topics: list[str] = []
    for key in ("content_genre", "content_topic", "production_quality", "marketing_potential"):
        value = str(analysis.get(key) or "").strip()
        if value and value not in topics:
            topics.append(value)
    for key in ("content_types", "products_detected", "viltrox_products_all", "competitor_brands"):
        value = analysis.get(key)
        if isinstance(value, list):
            for item in value[:6]:
                text = str(item or "").strip()
                if text and text not in topics:
                    topics.append(text)
    for tag in scrape.get("hashtags") or []:
        text = str(tag or "").strip().lstrip("#")
        if text and text not in topics:
            topics.append(text)
    return topics[:12]


def _analysis_summary(analysis: dict[str, Any], scrape: dict[str, Any]) -> str:
    parts = [
        analysis.get("content_summary"),
        analysis.get("quality_summary"),
        analysis.get("marketing_notes"),
        scrape.get("title"),
        scrape.get("caption"),
    ]
    for part in parts:
        text = _clean_text(part, 800)
        if text:
            return text
    return "已完成 URL 元数据抓取；AI 分析暂未返回可验证摘要。"


def _analysis_result_status(scrape: dict[str, Any], analysis: dict[str, Any]) -> str:
    if bool(analysis.get("analyzed")):
        return "done"
    return "partial" if bool(scrape.get("scraped_ok")) else "failed"


def _analysis_providers(result: dict[str, Any]) -> list[str]:
    providers: list[str] = []
    for layer in result.get("layers_used") or []:
        text = str(layer).lower()
        if "gpt" in text and "openai" not in providers:
            providers.append("openai")
        if "gemini" in text and "gemini" not in providers:
            providers.append("gemini")
        if ("claude" in text or "text_" in text) and "claude" not in providers:
            providers.append("claude")
    if not providers and result.get("status") == "failed":
        providers.append("scrape")
    return providers


def _result_payload(
    content_id: int,
    scrape: dict[str, Any],
    analysis: dict[str, Any],
    *,
    status: str,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fallback = fallback or {}
    metrics = scrape.get("metrics") if isinstance(scrape.get("metrics"), dict) else {}
    views = _int(metrics.get("views"), _int(fallback.get("views")))
    likes = _int(metrics.get("likes"), _int(fallback.get("likes")))
    comments = _int(metrics.get("comments"), _int(fallback.get("comments")))
    shares = _int(metrics.get("shares"), _int(fallback.get("shares")))
    score = _score_from_analysis(analysis, views, likes, comments, shares)
    topics = _topics_from_analysis(analysis, scrape)
    summary = _analysis_summary(analysis, scrape)
    owner = _clean_text(scrape.get("owner") or scrape.get("author") or scrape.get("channel_name") or "", 240)
    result = {
        "content_id": int(content_id),
        "status": status,
        "analysis_status": "ready" if bool(analysis.get("analyzed")) else "unavailable",
        "analysis_reason": "" if bool(analysis.get("analyzed")) else "provider_unavailable",
        "quality_score": score,
        "summary": summary,
        "topics": topics,
        "metrics": {"views": views, "likes": likes, "comments": comments, "shares": shares},
        "method": str(analysis.get("method") or scrape.get("scraper") or "scrape"),
        "layers_used": analysis.get("layers_used") or [],
        "analysis": _public_analysis(analysis),
        "scrape": _public_scrape(scrape),
        "suggested_kol": {
            "channel_name": owner or _clean_text(scrape.get("title") or "URL creator", 120),
            "channel_url": _clean_text(scrape.get("owner_url") or scrape.get("channel_url") or scrape.get("source_url") or scrape.get("url") or "", 1200),
            "platform": _clean_text(scrape.get("platform") or fallback.get("platform") or "", 40),
            "country": "",
            "niche": "",
            "follower_count": _int(scrape.get("followers") or scrape.get("follower_count")),
            "avg_views": views,
            "promoted_product": "",
        },
    }
    return result


def _load_content(content_id: int) -> dict[str, Any]:
    row = get_conn().execute(
        """
        SELECT co.*, ca.product_sku, ca.kol_id, k.channel_name, k.channel_url, k.niche, k.country
        FROM kol_content co
        LEFT JOIN kol_campaigns ca ON ca.id = co.campaign_id
        LEFT JOIN kols k ON k.id = ca.kol_id
        WHERE co.id = ?
        """,
        (int(content_id),),
    ).fetchone()
    if not row:
        raise ValueError("content not found")
    return dict(row)


def _mark_failed(content_id: int, error: str, scrape: dict[str, Any] | None = None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute(
        """
        UPDATE kol_content
        SET analysis_status = ?, analysis_error = ?, analysis_method = ?,
            ai_analysis_json = ?, last_metric_refresh = ?
        WHERE id = ?
        """,
        (
            "failed",
            _clean_text(error, 1000),
            str((scrape or {}).get("scraper") or "scrape"),
            _json({"scrape": scrape or {}, "error": error}),
            _now(),
            int(content_id),
        ),
    )
    conn.commit()
    return {
        "content_id": int(content_id),
        "status": "failed",
        "error": "url_scrape_failed",
        "scrape": _public_scrape(scrape or {}),
    }


def _persist_success(content: dict[str, Any], scrape: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    content_id = int(content["id"])
    result = _result_payload(content_id, scrape, analysis, status=_analysis_result_status(scrape, analysis), fallback=content)
    metrics = result["metrics"]
    views = _int(metrics.get("views"))
    likes = _int(metrics.get("likes"))
    comments = _int(metrics.get("comments"))
    shares = _int(metrics.get("shares"))
    platform = str(scrape.get("platform") or content.get("platform") or "").lower()
    title = _clean_text(scrape.get("title") or analysis.get("content_summary"), 500)
    thumbnail = _clean_text(scrape.get("og_image"), 1200)
    visible_comments = scrape.get("visible_comments") if isinstance(scrape.get("visible_comments"), list) else []
    posted_at = _published_at(scrape.get("published_at"), content.get("posted_at"))

    conn = get_conn()
    conn.execute(
        """
        UPDATE kol_content
        SET platform = ?, posted_at = ?, views = ?, likes = ?,
            comments = ?, shares = ?, engagement_rate = ?, ai_quality_score = ?,
            ai_summary = ?, ai_topics_json = ?, last_metric_refresh = ?,
            content_title = ?, thumbnail_url = ?, scraped_text = ?,
            visible_comments_json = ?, ai_analysis_json = ?, analysis_status = ?,
            analysis_error = ?, analysis_method = ?
        WHERE id = ?
        """,
        (
            platform,
            posted_at,
            views,
            likes,
            comments,
            shares,
            engagement_rate(likes, comments, shares, views),
            result["quality_score"],
            result["summary"],
            _json(result["topics"]),
            _now(),
            title,
            thumbnail,
            _clean_text(scrape.get("scraped_text") or scrape.get("caption"), 8000),
            _json(visible_comments[:50]),
            _json({"scrape": scrape, "analysis": analysis}),
            result["status"],
            "",
            result["method"],
            content_id,
        ),
    )
    conn.commit()
    return result


async def analyze_kol_url_standalone(url: str, platform_hint: str = "", creator_handle: str = "") -> dict[str, Any]:
    """Run the same URL scraper + AI analysis pipeline without requiring an existing KOL content row."""
    clean_url = str(url or "").strip()
    if not clean_url:
        raise ValueError("url is required")
    scrape = await scrape_url(clean_url)
    if platform_hint and not scrape.get("platform"):
        scrape["platform"] = platform_hint
    if not scrape.get("scraped_ok"):
        logger.warning("standalone URL scrape failed | error=%s", str(scrape.get("error") or "")[:300])
        return {
            "content_id": 0,
            "status": "failed",
            "error": "url_scrape_failed",
            "scrape": _public_scrape(scrape),
            "steps": [
                {"label": "校验链接", "status": "done"},
                {"label": "Apify / 平台抓取", "status": "failed", "detail": "url_scrape_failed"},
                {"label": "三模型分析", "status": "skipped"},
            ],
        }
    analysis = await analyze_url_content_smart(
        url=clean_url,
        title=str(scrape.get("title") or ""),
        caption=str(scrape.get("caption") or ""),
        scraped_text=str(scrape.get("scraped_text") or ""),
        og_image=str(scrape.get("og_image") or ""),
        platform=str(scrape.get("platform") or platform_hint or ""),
        creator_handle=str(creator_handle or scrape.get("owner") or ""),
        direct_video_url=str(scrape.get("video_url") or ""),
    )
    analysis_payload = analysis or {}
    result = _result_payload(0, scrape, analysis_payload, status=_analysis_result_status(scrape, analysis_payload))
    analysis_ready = bool(analysis_payload.get("analyzed"))
    result["steps"] = [
        {"label": "校验链接", "status": "done"},
        {"label": "Apify / 平台抓取", "status": "done", "detail": str(scrape.get("scraper") or scrape.get("platform") or "")},
        {
            "label": "GPT + Gemini + Claude 分析",
            "status": "done" if analysis_ready else "skipped",
            "detail": " / ".join(_analysis_providers(result)) if analysis_ready else "provider_unavailable",
        },
        {"label": "生成可入库 KOL", "status": "done"},
    ]
    result["providers"] = _analysis_providers(result)
    return result


async def analyze_kol_content_url(content_id: int) -> dict[str, Any]:
    """Scrape the content URL, run the existing smart AI pipeline, and persist it."""
    content = await db_write(lambda: _load_content(int(content_id)))
    url = str(content.get("content_url") or "").strip()
    if not url:
        raise ValueError("content_url is empty")

    scrape = await scrape_url(url)
    if not scrape.get("scraped_ok"):
        return await db_write(lambda: _mark_failed(int(content_id), str(scrape.get("error") or "scrape failed"), scrape))

    platform = str(scrape.get("platform") or content.get("platform") or "")
    title = str(scrape.get("title") or content.get("content_title") or "")
    caption = str(scrape.get("caption") or "")
    scraped_text = str(scrape.get("scraped_text") or "")
    handle = _normalize_handle(content)
    analysis = await analyze_url_content_smart(
        url=url,
        title=title,
        caption=caption,
        scraped_text=scraped_text,
        og_image=str(scrape.get("og_image") or ""),
        platform=platform,
        creator_handle=handle,
        direct_video_url=str(scrape.get("video_url") or ""),
    )
    return await db_write(lambda: _persist_success(content, scrape, analysis or {}))
