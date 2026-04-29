"""
services/audit/pipeline.py — 完整审计管线 (从老版 audit() 迁移)

取代 orchestrator 的 3-analyzer 并行模式，改为顺序执行完整链：
  scrape → vision/text analysis → detection → scoring → finalize

由 orchestrator worker 直接调用:
  result = await perform_full_audit(job)
"""
from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.db.connection import db_connection_scope
from app.core.logging import get_logger
from app.utils.urls import valid_url, detect_platform
from app.utils.handles import extract_handle_from_url


logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 上下文: 传给 pipeline 的所有依赖
# ──────────────────────────────────────────────
@dataclass
class AuditContext:
    """Pipeline 运行所需的外部依赖和配置"""
    compute_weighted_fn: Any = None
    update_benchmark_fn: Any = None
    get_vertical_fn: Any = None
    apply_learned_weights_fn: Any = None


# ──────────────────────────────────────────────
# 主函数
# ──────────────────────────────────────────────
async def perform_full_audit(job, ctx: AuditContext = None) -> Dict[str, Any]:
    """
    完整审计管线。接收 VideoJobInput，返回写入 DB 的完整结果。

    Phase 1: 数据采集 (scrape + download)
    Phase 2: AI 分析 (Vision / Text / Gemini)
    Phase 3: 检测 + 评分
    Phase 4: 画像更新
    """
    from app.services.scraping.platform_router import scrape_url
    from app.services.ai.analyzers.claude_vision import (
        analyze_video_with_claude, analyze_url_content_smart,
    )
    from app.services.ai.analyzers.claude_text import (
        analyze_text_content, check_content_similarity,
    )
    from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption
    from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
    from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE
    from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
    from app.services.ai.runtime_guards import guarded_provider_call
    from app.services.audit.similarity import (
        classify_product, detect_gear_mentions, detect_viltrox,
        analyze_comments_for_spam,
    )
    from app.services.scoring.risk import compute_risk
    from app.services.scoring.campaign import (
        compute_campaign_score, compute_creator_score, compute_ratios,
    )
    from app.services.scoring.creator import update_creator_profile
    from app.services.scoring.benchmark import update_genre_benchmark

    async def _classify_product_scoped(text: str) -> dict[str, Any]:
        async with db_connection_scope():
            return classify_product(text)

    submission_id = job.submission_id
    platform = job.platform or (detect_platform(job.url) if job.url else "Uploaded Video")
    handle = job.handle or ""
    uploaded = job.uploaded_video
    uploaded_analysis_path = str((uploaded or {}).get("analysis_path") or (uploaded or {}).get("path") or "").strip()

    logger.info(
        "pipeline start | submission_id=%s | platform=%s | source=%s",
        submission_id,
        platform,
        job.url[:50] if job.url else "upload",
    )

    # ════════════════════════════════════════════
    # Phase 1: 数据采集
    # ════════════════════════════════════════════
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
            logger.info(
                "pipeline scrape ok | submission_id=%s | scraped_ok=%s | views=%s",
                submission_id,
                scraped.get("scraped_ok"),
                scraped.get("metrics", {}).get("views", 0),
            )
        except Exception as e:
            logger.warning("pipeline scrape error | submission_id=%s | error=%s", submission_id, e)
            scraped["error"] = str(e)

    title   = job.title or scraped.get("title", "")
    caption = job.caption or scraped.get("caption", "")
    raw_text = job.scraped_text or scraped.get("scraped_text", "")

    # Merge metrics: 前端传入的 > 抓取的
    seed_metrics = job.metrics or {}
    metrics = {
        "views":     seed_metrics.get("views", 0) or scraped["metrics"]["views"],
        "likes":     seed_metrics.get("likes", 0) or scraped["metrics"]["likes"],
        "comments":  seed_metrics.get("comments", 0) or scraped["metrics"]["comments"],
        "shares":    seed_metrics.get("shares", 0) or scraped["metrics"]["shares"],
        "favorites": seed_metrics.get("favorites", 0) or scraped["metrics"]["favorites"],
    }
    metrics_available = dict(scraped.get("metrics_available", {}))
    for k in metrics:
        if metrics[k] > 0:
            metrics_available[k] = True

    # ════════════════════════════════════════════
    # Phase 2: AI 分析
    # ════════════════════════════════════════════
    video_analysis_result = None
    video_text = f"{title} {caption} {raw_text}"
    prefilter_result: Dict[str, Any] | None = None
    text_analysis_result: Dict[str, Any] | None = None

    is_youtube = platform == "YouTube" or (job.url and ("youtube.com" in job.url or "youtu.be" in job.url))
    has_upload = uploaded and isinstance(uploaded, dict) and uploaded_analysis_path

    async def _safe_provider(provider: str, fn, *args, **kwargs):
        try:
            return await guarded_provider_call(provider, lambda: fn(*args, **kwargs))
        except Exception as e:
            return {"error": str(e), "analyzed": False, "provider": provider}

    async def _safe_provider_thread(provider: str, fn, *args, **kwargs):
        try:
            return await guarded_provider_call(provider, lambda: asyncio.to_thread(fn, *args, **kwargs))
        except Exception as e:
            return {"error": str(e), "analyzed": False, "provider": provider}

    prefilter_task = None
    if title or caption:
        prefilter_task = asyncio.create_task(
            _safe_provider_thread("openai", gpt_prefilter_caption, title, caption, platform)
        )

    if has_upload:
        logger.info("pipeline uploaded video triple-path | submission_id=%s", submission_id)
        vision_task = asyncio.create_task(
            _safe_provider_thread(
                "claude",
                analyze_video_with_claude,
                uploaded_analysis_path,
                uploaded.get("filename", ""),
                creator_handle=handle,
            )
        )
        text_task = asyncio.create_task(
            _safe_provider_thread(
                "claude",
                analyze_text_content,
                title,
                caption,
                job.url or "",
                platform,
                raw_text,
                scraped.get("og_image", ""),
            )
        )
        if prefilter_task is not None:
            prefilter_result, video_analysis_result, text_analysis_result = await asyncio.gather(prefilter_task, vision_task, text_task)
        else:
            video_analysis_result, text_analysis_result = await asyncio.gather(vision_task, text_task)
        video_analysis_result = video_analysis_result or text_analysis_result or {}

    elif uploaded and isinstance(uploaded, dict) and uploaded.get("r2_key"):
        logger.info(
            "pipeline upload awaiting video_factory | submission_id=%s | r2_key=%s",
            submission_id,
            str(uploaded.get("r2_key") or ""),
        )
        video_analysis_result = {
            "analyzed": False,
            "method": "video_factory_pending",
            "error": "Video factory decode asset unavailable",
            "r2_key": str(uploaded.get("r2_key") or ""),
            "storage_key": str(uploaded.get("storage_key") or ""),
            "analysis_path": "",
        }
        if prefilter_task is not None:
            prefilter_result = await prefilter_task
        if title or caption or raw_text:
            text_analysis_result = await _safe_provider_thread(
                "claude",
                analyze_text_content,
                title,
                caption,
                job.url or "",
                platform,
                raw_text,
                scraped.get("og_image", ""),
            )
    elif job.url and is_youtube and GEMINI_AVAILABLE:
        logger.info("pipeline YouTube triple-model analysis | submission_id=%s", submission_id)
        gemini_task = asyncio.create_task(
            _safe_provider("gemini", analyze_youtube_with_gemini, job.url, title, creator_handle=handle)
        )
        claude_text_task = asyncio.create_task(
            _safe_provider_thread(
                "claude",
                analyze_text_content,
                title,
                caption,
                job.url or "",
                platform,
                raw_text,
                scraped.get("og_image", ""),
            )
        )
        if prefilter_task is not None:
            prefilter_result, gemini_result, text_analysis_result = await asyncio.gather(prefilter_task, gemini_task, claude_text_task)
        else:
            gemini_result, text_analysis_result = await asyncio.gather(gemini_task, claude_text_task)
        video_analysis_result = gemini_result if gemini_result and gemini_result.get("analyzed") else text_analysis_result

        if not video_analysis_result or not video_analysis_result.get("analyzed"):
            logger.warning("pipeline Gemini/text failed; falling back to smart analysis | submission_id=%s", submission_id)
            video_analysis_result = await _safe_provider(
                "claude",
                analyze_url_content_smart,
                url=job.url,
                title=title,
                caption=caption,
                scraped_text=raw_text,
                og_image=scraped.get("og_image", ""),
                platform=platform,
                creator_handle=handle,
            ) or {}

    elif job.url and (ANTHROPIC_AVAILABLE or GEMINI_AVAILABLE):
        logger.info("pipeline non-YouTube multi-path analysis | submission_id=%s | platform=%s", submission_id, platform)
        smart_task = asyncio.create_task(
            _safe_provider(
                "claude",
                analyze_url_content_smart,
                url=job.url,
                title=title,
                caption=caption,
                scraped_text=raw_text,
                og_image=scraped.get("og_image", ""),
                platform=platform,
                creator_handle=handle,
            )
        )
        text_task = asyncio.create_task(
            _safe_provider_thread(
                "claude",
                analyze_text_content,
                title,
                caption,
                job.url or "",
                platform,
                raw_text,
                scraped.get("og_image", ""),
            )
        )
        if prefilter_task is not None:
            prefilter_result, smart_result, text_analysis_result = await asyncio.gather(prefilter_task, smart_task, text_task)
        else:
            smart_result, text_analysis_result = await asyncio.gather(smart_task, text_task)
        video_analysis_result = smart_result if smart_result and smart_result.get("analyzed") else text_analysis_result

    else:
        if title or caption or raw_text:
            logger.info("pipeline text-only analysis | submission_id=%s", submission_id)
            text_analysis_result = await _safe_provider_thread(
                "claude",
                analyze_text_content,
                title,
                caption,
                job.url or "",
                platform,
                raw_text,
                scraped.get("og_image", ""),
            ) or {}
            if prefilter_task is not None:
                prefilter_result = await prefilter_task
            video_analysis_result = text_analysis_result

    vr = video_analysis_result or {}
    if prefilter_result:
        vr.setdefault("prefilter", prefilter_result)
        vr.setdefault("layers_used", [])
        if "gpt_prefilter" not in vr["layers_used"]:
            vr["layers_used"].append("gpt_prefilter")
    if text_analysis_result and text_analysis_result is not vr:
        vr.setdefault("text_layer", text_analysis_result)
        vr.setdefault("layers_used", [])
        if "text_claude" not in vr["layers_used"]:
            vr["layers_used"].append("text_claude")
    if uploaded and isinstance(vr, dict):
        vr.setdefault("storage_key", uploaded.get("storage_key", "") or "")
        vr.setdefault("analysis_path", uploaded_analysis_path)
        vr.setdefault("filename", uploaded.get("filename", "") or "")
        if has_upload:
            vr.setdefault("path", uploaded_analysis_path)
        if uploaded.get("r2_key"):
            vr.setdefault("r2_key", uploaded.get("r2_key", ""))
    elif job.url and isinstance(vr, dict):
        vr.setdefault("source_url", job.url)
        vr.setdefault("source_platform", platform)
        vr.setdefault("scraper", scraped.get("scraper", ""))
        vr.setdefault("source_video_url", scraped.get("video_url", "") or "")
        vr.setdefault("published_at", scraped.get("published_at") or "")
        vr.setdefault("hashtags", scraped.get("hashtags", []) or [])
        vr.setdefault(
            "source_capture",
            {
                "source_url": job.url,
                "video_url": scraped.get("video_url", "") or "",
                "og_image": scraped.get("og_image", "") or "",
                "owner_username": scraped.get("owner_username", "") or "",
                "channel_name": scraped.get("channel_name", "") or "",
                "scraper": scraped.get("scraper", "") or "",
            },
        )
        try:
            from app.services.media.url_archive import archive_submission_video_url

            archive_result = await archive_submission_video_url(
                submission_id=submission_id,
                source_url=job.url,
                platform=platform,
                scraped_video_url=scraped.get("video_url", "") or "",
                title=title,
            )
            vr["archive_status"] = "archived" if archive_result.get("archived") else "failed"
            if archive_result.get("archived"):
                vr.setdefault("r2_key", archive_result.get("r2_key", ""))
                vr["archive_method"] = archive_result.get("method", "")
                vr["archive_scope"] = archive_result.get("scope", "")
                vr["archive_asset_role"] = archive_result.get("asset_role", "")
            elif archive_result.get("error"):
                vr["archive_error"] = archive_result.get("error", "")[:200]
        except Exception as e:
            logger.warning("pipeline archive error | submission_id=%s | error=%s", submission_id, e)

    # 补充 video_text
    if vr.get("brand_elements"):
        video_text += " " + " ".join(vr["brand_elements"])
    if vr.get("products_detected"):
        video_text += " " + " ".join(vr["products_detected"])
    if vr.get("viltrox_products_all"):
        video_text += " " + " ".join(vr["viltrox_products_all"])

    # ════════════════════════════════════════════
    # Phase 3: 检测 + 评分
    # ════════════════════════════════════════════
    full_text = " ".join(filter(None, [title, caption, raw_text, video_text])).strip()

    # 产品匹配
    product_match = await _classify_product_scoped(full_text)
    gear_mentions = detect_gear_mentions(full_text)
    hints = job.hints or {}
    brand = detect_viltrox(full_text, hints)

    # ★ Learned correction lookup — admin manually corrected this URL before
    if job.url:
        try:
            from app.services.audit.learning import lookup_correction
            async with db_connection_scope():
                learned = lookup_correction(job.url)
            if learned:
                product_match = {
                    "series": learned.get("correct_series", ""),
                    "label": learned.get("correct_label", ""),
                    "confidence": "high",
                    "evidence": ["learned from admin correction"],
                }
                logger.info("pipeline learned correction | submission_id=%s | label=%s", submission_id, product_match["label"])
        except Exception as e:
            logger.warning("pipeline learning lookup error | submission_id=%s | error=%s", submission_id, e)

    # AI 结果覆盖品牌检测
    if vr.get("viltrox_detected"):
        conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
        forced = conf_map.get(vr.get("confidence", "low"), "suspected")
        if brand["status"] != "confirmed":
            brand["status"] = forced
            brand["confirmed"] = (forced == "confirmed")
        brand["evidence"] = list(set(brand["evidence"] + vr.get("brand_elements", [])))

    if vr.get("products_detected") and product_match.get("confidence") == "none":
        for pd in vr["products_detected"]:
            pm2 = await _classify_product_scoped(pd)
            if pm2["confidence"] != "none":
                product_match = pm2
                break

    # Cinema 相机交叉验证
    camera_brand = (vr.get("camera_brand") or "").upper()
    if camera_brand in ("ARRI", "RED", "BLACKMAGIC"):
        if product_match.get("series", "") in ("AIR", "LAB", "PRO", ""):
            all_prods = vr.get("products_detected", []) + vr.get("viltrox_products_all", [])
        for p in all_prods:
            if any(kw in p.lower() for kw in ("epic", "luna", "anamorphic", "zmove")):
                better = await _classify_product_scoped(p)
                if better.get("confidence") != "none":
                    product_match = better
                    break

    # DJI DL mount cross-validation
    # If camera is DJI Inspire 3 / Ronin 4D / Zenmuse X9 → force DL series
    camera_body = (vr.get("camera_body") or "").lower()
    is_dji_dl_body = (
        camera_brand == "DJI" or
        any(k in camera_body for k in ("inspire 3", "ronin 4d", "zenmuse x9", "x9-8k", "x9 8k"))
    )
    if is_dji_dl_body and product_match.get("series", "") not in ("DL",):
        # Look for any DL product in detected list
        all_prods = vr.get("products_detected", []) + vr.get("viltrox_products_all", [])
        for p in all_prods:
            if any(kw in p.lower() for kw in ("dl ", " dl", "dl mount", "f3.5", "raze", "90mm")):
                better = await _classify_product_scoped(p)
                if better.get("series") == "DL":
                    product_match = better
                    logger.info("pipeline DL cross-validation | camera=%s | label=%s", camera_body, better.get("label"))
                    break

    if product_match.get("confidence") in ("high", "medium"):
        brand["auto_flags"] = brand.get("auto_flags", {})
        brand["auto_flags"]["product"] = True

    # 风控
    comment_spam = analyze_comments_for_spam(scraped.get("visible_comments", []))
    risk = compute_risk(metrics, metrics_available, comment_spam)

    # 评分 (用老版 weighted 公式)
    content_types = brand.get("content_types", [])
    if vr.get("content_types"):
        for ct in vr["content_types"]:
            if ct not in content_types:
                content_types.append(ct)

    creator_score = compute_creator_score(
        metrics=metrics, metrics_available=metrics_available,
        risk_score=risk["risk_score"],
    )

    detected = brand.get("confirmed", False)
    campaign = compute_campaign_score(
        metrics=metrics, metrics_available=metrics_available,
        detected=detected, content_types=content_types,
        platform=platform, video_analysis=vr,
    )

    final_score = max(0, campaign["raw_score"] - risk["penalty"]) if detected else 0

    # Upload bonus +50
    if has_upload:
        final_score = min(400, final_score + 50)

    # Hints bonus
    hint_bonus = sum([
        15 if hints.get("logo") else 0,
        12 if hints.get("product") else 0,
        10 if hints.get("voice") else 0,
        10 if hints.get("review") else 0,
    ])
    final_score = min(400, final_score + hint_bonus)

    # Vision bonus
    if vr.get("viltrox_detected") and vr.get("brand_score_bonus", 0):
        final_score = min(400, final_score + vr["brand_score_bonus"])

    # Detection status
    if brand["status"] == "confirmed":
        detection_status = "confirmed"
        recommendation = "Eligible for brand campaign pool"
        overall_score = round((final_score / 4) * 0.7 + creator_score * 0.3)
    elif brand["status"] == "suspected":
        detection_status = "suspected"
        recommendation = "Pending manual review"
        overall_score = creator_score
    else:
        detection_status = "not_detected"
        recommendation = "No Viltrox content detected"
        final_score = 0
        overall_score = 0
        creator_score = 0

    # ════════════════════════════════════════════
    # Phase 4: 画像 + benchmark
    # ════════════════════════════════════════════
    if handle and vr.get("analyzed"):
        try:
            async with db_connection_scope():
                update_creator_profile(handle, vr, platform)
        except Exception as e:
            logger.warning("pipeline profile error | submission_id=%s | error=%s", submission_id, e)

    genre = vr.get("content_genre", "")
    tech_s = vr.get("tech_score", 0)
    mkt_s = vr.get("marketing_score", 0)
    percentiles = {"percentile_tech": 0, "percentile_mkt": 0}

    # Weighted scores (三轴质量分)
    if ctx and ctx.compute_weighted_fn and vr.get("quality_scores"):
        try:
            vertical = vr.get("vertical_category", "")
            if ctx.get_vertical_fn:
                v_key = ctx.get_vertical_fn(genre)
                if ctx.apply_learned_weights_fn:
                    ctx.apply_learned_weights_fn(v_key)
            ws = ctx.compute_weighted_fn(vr.get("quality_scores", {}), genre, vertical)
            tech_s = ws.get("tech_score", tech_s)
            mkt_s = ws.get("marketing_score", mkt_s)
            vr["tech_score"] = tech_s
            vr["marketing_score"] = mkt_s
            vr["quality_overall"] = ws.get("quality_overall", 0)
            vr["tech_status"] = ws.get("tech_status", "")
        except Exception as e:
            logger.warning("pipeline weighted scores error | submission_id=%s | error=%s", submission_id, e)

    if genre and tech_s > 0:
        try:
            async with db_connection_scope():
                percentiles = update_genre_benchmark(genre, tech_s, mkt_s)
        except Exception as e:
            logger.warning("pipeline benchmark error | submission_id=%s | error=%s", submission_id, e)

    # ════════════════════════════════════════════
    # 组装最终结果
    # ════════════════════════════════════════════
    products_str = ", ".join(vr.get("products_detected", [])[:3])

    result = {
        "submission_id": submission_id,
        "platform": platform,
        "extracted_handle": handle,
        "title": title,
        "detection_status": detection_status,
        "product_match": product_match,
        "content_types": content_types,
        "metrics": metrics,
        "metrics_available": metrics_available,
        "scores": {
            "content_score": campaign.get("content_score", 0),
            "campaign_interaction_score": campaign.get("campaign_interaction_score", 0),
            "creator_score": creator_score,
            "overall_score": overall_score,
            "risk_score": risk["risk_score"],
            "raw_score": campaign.get("raw_score", 0),
            "final_score": final_score,
        },
        "risk": risk,
        "recommendation": recommendation,
        "memo": (
            f"[Pipeline] status={detection_status} | "
            f"products={products_str or 'none'} | "
            f"camera={vr.get('camera_body') or '?'} | "
            f"final={final_score} creator={creator_score} risk={risk['risk_score']}"
        ),
        "evidence": brand.get("evidence", []),
        "scraped_ok": scraped.get("scraped_ok", False),
        "scrape_snapshot": {
            "source_url": job.url or "",
            "scraper": scraped.get("scraper", ""),
            "video_url": scraped.get("video_url", "") or "",
            "published_at": scraped.get("published_at") or "",
            "owner_username": scraped.get("owner_username", "") or "",
            "channel_name": scraped.get("channel_name", "") or "",
            "hashtags": scraped.get("hashtags", []) or [],
        },
        "video_analysis": vr,
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
        "comment_spam": comment_spam,
        "gear_mentions": gear_mentions,
    }

    logger.info(
        "pipeline done | submission_id=%s | status=%s | product=%s | final=%s | creator=%s | tech=%.1f | mkt=%.1f",
        submission_id,
        detection_status,
        product_match.get("label", "?"),
        final_score,
        creator_score,
        tech_s,
        mkt_s,
    )

    return result
logger = get_logger(__name__)
