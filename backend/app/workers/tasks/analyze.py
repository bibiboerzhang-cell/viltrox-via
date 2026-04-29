"""
workers/tasks/analyze.py — 真分流 worker 主处理函数

当 /api/audit/v2 入队后, orchestrator 的 worker 最终会调这里。
这里包含从 audit.py 剪出来的所有重活逻辑。

用法:
  在 orchestrator._run_single 末尾或 db_writer 中集成:
    from app.workers.tasks.analyze import process_full_audit
    await process_full_audit(job)
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime
from typing import Dict, Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.db.repositories.submissions import (
    mark_submission_running,
    finalize_submission,
    mark_submission_failed,
)
from app.utils.urls import valid_url, detect_platform
from app.utils.handles import extract_handle_from_url
from app.services.scraping.platform_router import scrape_url
from app.services.ai.analyzers.claude_vision import analyze_video_with_claude, analyze_url_content_smart
from app.services.ai.analyzers.claude_text import check_content_similarity
from app.services.audit.similarity import (
    classify_product, detect_gear_mentions, detect_viltrox, analyze_comments_for_spam
)
from app.services.scoring.risk import compute_risk
from app.services.scoring.campaign import compute_creator_score, compute_campaign_score, compute_ratios
from app.services.scoring.creator import update_creator_profile
from app.services.scoring.benchmark import update_genre_benchmark
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE

logger = get_logger(__name__)


async def process_full_audit(job) -> Dict[str, Any]:
    """
    接收 VideoJobInput, 执行完整审计流程, 写回 submission 结果。
    
    job 字段:
      - submission_id: int
      - url: str
      - title: str
      - handle: str
      - platform: str
      - caption: str
      - scraped_text: str
      - og_image: str
    """
    submission_id = job.submission_id

    # ── Step 1: 标记 running ──
    try:
        mark_submission_running(submission_id)
    except Exception as e:
        logger.exception(
            "worker.mark_submission_running_failed",
            extra={"submission_id": submission_id},
        )

    try:
        # ── Step 2: 抓取 ──
        scraped = {
            "scraped_ok": False,
            "title": "", "caption": "", "scraped_text": "",
            "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
            "metrics_available": {"views": False, "likes": False, "comments": False, "shares": False, "favorites": False},
            "visible_comments": [],
            "og_image": "",
            "error": "No URL",
        }

        if job.url and valid_url(job.url):
            try:
                scraped = await scrape_url(job.url)
            except Exception as e:
                logger.exception(
                    "worker.scrape_failed",
                    extra={"submission_id": submission_id, "url": job.url},
                )
                scraped["error"] = str(e)

        title    = job.title or scraped.get("title", "")
        caption  = job.caption or scraped.get("caption", "")
        raw_text = job.scraped_text or scraped.get("scraped_text", "")
        platform = job.platform or (detect_platform(job.url) if job.url else "Unknown")
        handle   = job.handle or (extract_handle_from_url(job.url) if job.url else "")

        # ── Step 3: AI 分析 ──
        video_analysis_result = None

        if job.url and (ANTHROPIC_AVAILABLE or GEMINI_AVAILABLE):
            try:
                video_analysis_result = await analyze_url_content_smart(
                    url=job.url,
                    title=title,
                    caption=caption,
                    scraped_text=raw_text,
                    og_image=scraped.get("og_image", "") or job.og_image or "",
                    platform=platform,
                    creator_handle=handle,
                ) or {}
            except Exception as e:
                logger.exception(
                    "worker.analyze_url_failed",
                    extra={"submission_id": submission_id, "url": job.url},
                )
                video_analysis_result = {"error": str(e), "analyzed": False}

        vr = video_analysis_result or {}

        # ── Step 4: 评分 ──
        metrics = scraped.get("metrics", {})
        metrics_available = scraped.get("metrics_available", {})

        full_text = " ".join(filter(None, [title, caption, raw_text])).strip()
        if vr.get("brand_elements"):
            full_text += " " + " ".join(vr["brand_elements"])
        if vr.get("products_detected"):
            full_text += " " + " ".join(vr["products_detected"])

        product_match = classify_product(full_text)
        gear_mentions = detect_gear_mentions(full_text)
        brand = detect_viltrox(full_text, {})

        # Override with AI results
        if vr.get("viltrox_detected"):
            conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
            forced_status = conf_map.get(vr.get("confidence", "low"), "suspected")
            if brand["status"] != "confirmed":
                brand["status"] = forced_status
                brand["confirmed"] = (forced_status == "confirmed")
            brand["evidence"] = list(set(brand["evidence"] + vr.get("brand_elements", [])))

        if vr.get("products_detected") and product_match["confidence"] == "none":
            for pd in vr["products_detected"]:
                pm2 = classify_product(pd)
                if pm2["confidence"] != "none":
                    product_match = pm2
                    break

        comment_spam = analyze_comments_for_spam(scraped.get("visible_comments", []))
        risk = compute_risk(metrics, metrics_available, comment_spam)

        creator_score = compute_creator_score(
            metrics.get("views", 0), metrics.get("likes", 0),
            metrics.get("comments", 0), metrics.get("shares", 0),
        )

        content_score = 30 if brand["confirmed"] else 0
        campaign = compute_campaign_score(
            content_score=content_score,
            views=metrics.get("views", 0),
            likes=metrics.get("likes", 0),
            comments=metrics.get("comments", 0),
            shares=metrics.get("shares", 0),
            favorites=metrics.get("favorites", 0),
        )

        final_score = max(0, campaign["raw_score"] - risk["penalty"]) if brand["confirmed"] else 0

        if vr.get("viltrox_detected"):
            bonus = vr.get("brand_score_bonus", 0)
            final_score = min(400, final_score + bonus)

        overall_score = creator_score if not brand["confirmed"] else round((final_score / 4) * 0.7 + creator_score * 0.3)

        if brand["status"] == "confirmed":
            recommendation = "Eligible for brand campaign pool"
        elif brand["status"] == "suspected":
            recommendation = "Pending manual review"
        else:
            recommendation = "Rejected — no Viltrox content detected"
            final_score = 0
            overall_score = 0
            creator_score = 0

        # ── Step 5: 画像 + benchmark ──
        if handle and video_analysis_result:
            try:
                update_creator_profile(handle, video_analysis_result, platform)
            except Exception as e:
                logger.exception(
                    "worker.update_creator_profile_failed",
                    extra={"submission_id": submission_id, "handle": handle},
                )

        genre = vr.get("content_genre", "")
        tech_s = vr.get("tech_score", 0)
        mkt_s = vr.get("marketing_score", 0)
        percentiles = {"percentile_tech": 0, "percentile_mkt": 0}
        if genre and tech_s > 0:
            try:
                percentiles = update_genre_benchmark(genre, tech_s, mkt_s)
            except Exception as e:
                logger.exception(
                    "worker.update_genre_benchmark_failed",
                    extra={"submission_id": submission_id, "genre": genre},
                )

        # ── Step 6: 组装结果 ──
        video_analysis = {
            "uploaded": False,
            "analyzed": vr.get("analyzed", False),
            "method": vr.get("method", "text_analysis"),
            "og_image": scraped.get("og_image", ""),
            "camera_body": vr.get("camera_body"),
            "camera_brand": vr.get("camera_brand"),
            "viltrox_lens": vr.get("viltrox_lens"),
            "other_lens": vr.get("other_lens"),
            "flash": vr.get("flash"),
            "adapter": vr.get("adapter"),
            "accessories": vr.get("accessories", []),
            "gear_combo": vr.get("gear_combo", ""),
            "brand_elements": vr.get("brand_elements", []),
            "products_found": vr.get("products_detected", []),
            "content_genre": genre,
            "content_topic": vr.get("content_topic", ""),
            "content_summary": vr.get("content_summary", ""),
            "content_types": vr.get("content_types", []),
            "notes": vr.get("notes", ""),
            "quality_scores": vr.get("quality_scores", {}),
            "quality_overall": vr.get("quality_overall", 0),
            "tech_score": tech_s,
            "marketing_score": mkt_s,
            "timestamps": vr.get("timestamps", []),
        }

        memo = f"[Worker] Status={brand['status']}. Platform={platform}. Final={final_score}."

        result_data = {
            "platform": platform,
            "extracted_handle": handle,
            "title": title,
            "detection_status": brand["status"],
            "product_match": product_match,
            "content_types": brand.get("content_types", []),
            "metrics": metrics,
            "scores": {
                "content_score": campaign["content_score"],
                "campaign_interaction_score": campaign["campaign_interaction_score"],
                "creator_score": creator_score,
                "overall_score": overall_score,
                "risk_score": risk["risk_score"],
                "raw_score": campaign["raw_score"],
                "final_score": final_score,
            },
            "recommendation": recommendation,
            "memo": memo,
            "evidence": brand["evidence"],
            "scraped_ok": scraped.get("scraped_ok", False),
            "video_analysis": video_analysis,
            "tech_score": tech_s,
            "marketing_score": mkt_s,
            "content_genre": genre,
            "percentile_tech": percentiles.get("percentile_tech", 0),
            "percentile_mkt": percentiles.get("percentile_mkt", 0),
            "vertical_category": vr.get("vertical_category", ""),
            "vertical_tech_score": vr.get("vertical_tech_score", 0),
            "vertical_mkt_score": vr.get("vertical_mkt_score", 0),
            "community_value": vr.get("community_value", 0),
            "product_showcase_score": vr.get("product_showcase_score", 0),
            "brand_exposure_score": vr.get("brand_exposure_score", 0),
            "storytelling_score": vr.get("storytelling_score", 0),
            "tech_status": vr.get("tech_status", ""),
            "logo_detected": vr.get("logo_detected", 0),
            "product_closeup_count": vr.get("product_closeup_count", 0),
        }

        # ── Step 7: 写回 DB ──
        finalize_submission(submission_id, result_data)
        logger.info(
            "worker.audit_submission_done",
            extra={
                "submission_id": submission_id,
                "status": brand["status"],
                "final_score": final_score,
            },
        )
        return result_data

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()[-500:]}"
        logger.exception(
            "worker.audit_submission_failed",
            extra={"submission_id": submission_id},
        )
        mark_submission_failed(submission_id, error_msg)
        raise
