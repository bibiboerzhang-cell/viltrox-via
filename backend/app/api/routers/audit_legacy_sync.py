"""Legacy synchronous audit execution path."""
from __future__ import annotations

from functools import partial
from typing import Any, Awaitable, Callable

from app.db.connection import db_read, db_write
from app.services.ai.analyzers.claude_vision import analyze_video_with_claude, analyze_url_content_smart
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE
from app.services.audit.similarity import (
    analyze_comments_for_spam,
    classify_product,
    detect_gear_mentions,
    detect_viltrox,
)
from app.services.scoring.benchmark import update_genre_benchmark
from app.services.scoring.campaign import compute_campaign_score, compute_creator_score, compute_ratios
from app.services.scoring.creator import update_creator_profile
from app.services.scoring.risk import compute_risk
from app.services.scraping.platform_router import scrape_url
from app.utils.handles import extract_handle_from_url
from app.utils.urls import detect_platform, valid_url

from app.api.routers.audit_helpers import (
    _auto_create_verification_sync,
    _check_similarity_sync,
    _resolve_uploaded_video_payload_sync,
)
from app.db.repositories.submissions import save_submission


async def run_audit_sync(
    request,
    req,
    current_user: dict[str, Any] | None,
    audit_async_func: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """
    旧同步模式: 当场分析, 当场返回完整结果。
    适用于:
    - 前端还没改成轮询模式
    - 需要立即看到完整结果的场景
    注意: 这个接口会阻塞很久 (10-60秒), 后续应迁移到 /api/audit/v2
    """
    if getattr(request.app.state, "job_queue", None) is not None:
        response = await audit_async_func(request, req, current_user)
        response["deprecated_sync"] = True
        response["message"] = "Synchronous audit is deprecated; request was queued instead."
        return response

    current_uid = current_user["id"] if current_user else None
    extracted_handle = ""
    scraped = {
        "scraped_ok": False,
        "title": "", "caption": "", "scraped_text": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False, "shares": False, "favorites": False},
        "visible_comments": [],
        "error": "No URL provided",
    }

    if req.url and valid_url(req.url):
        scraped = await scrape_url(req.url)

    title    = req.title.strip()   or scraped["title"]
    caption  = req.caption.strip() or scraped["caption"]
    raw_text = req.raw_text.strip() or scraped["scraped_text"]

    # ── Video analysis (Claude Vision) ──
    video_analysis_result = None
    video_text = ""
    resolved_uploaded_video = await db_read(partial(_resolve_uploaded_video_payload_sync, req.uploaded_video))

    if resolved_uploaded_video and resolved_uploaded_video.get("path"):
        video_text = f"{req.uploaded_video.filename} {title} {caption} {raw_text}"
        video_analysis_result = analyze_video_with_claude(
            str(resolved_uploaded_video.get("path") or ""),
            str(resolved_uploaded_video.get("filename") or req.uploaded_video.filename),
            creator_handle=extracted_handle or req.user_handle or ""
        ) or {}
        vr = video_analysis_result
        if vr.get("brand_elements"):
            video_text += " " + " ".join(vr["brand_elements"])
        if vr.get("products_detected"):
            video_text += " " + " ".join(vr["products_detected"])
        if vr.get("notes"):
            video_text += " " + vr["notes"]
        if vr.get("content_topic"):
            video_text += " " + vr["content_topic"]
    elif resolved_uploaded_video:
        video_text = f"{resolved_uploaded_video.get('filename') or req.uploaded_video.filename} {title} {caption} {raw_text}"
    else:
        # ── URL submission: smart multi-layer analysis ──
        video_text = f"{title} {caption} {raw_text}"
        if req.url and (ANTHROPIC_AVAILABLE or GEMINI_AVAILABLE):
            handle_for_analysis = req.user_handle or extract_handle_from_url(req.url) or ""
            _platform_early = detect_platform(req.url) if req.url else "Unknown"
            video_analysis_result = await analyze_url_content_smart(
                url=req.url,
                title=title,
                caption=caption,
                scraped_text=scraped.get("scraped_text", ""),
                og_image=scraped.get("og_image", ""),
                platform=_platform_early,
                creator_handle=handle_for_analysis,
            ) or {}
            vr = video_analysis_result
            if vr.get("brand_elements"):
                video_text += " " + " ".join(vr["brand_elements"])
            if vr.get("products_detected"):
                video_text += " " + " ".join(vr["products_detected"])
            if vr.get("viltrox_products_all"):
                video_text += " " + " ".join(vr["viltrox_products_all"])

    # ── Similarity / spam detection ──
    handle_for_sim = req.user_handle or (
        next(iter(req.linked_handles.values()), "") if req.linked_handles else ""
    )
    similarity_result = await db_read(
        partial(
            _check_similarity_sync,
            handle_for_sim,
            title or (req.uploaded_video.filename if req.uploaded_video else ""),
            detect_platform(req.url) if req.url else "Uploaded Video",
            req.url or "",
        )
    )

    if similarity_result.get("hard_reject"):
        return {
            "status": "rejected",
            "rejection_code": "duplicate_or_spam",
            "rejection_reason": similarity_result["reason"],
            "platform": detect_platform(req.url) if req.url else "Uploaded Video",
            "viltrox_detected": False,
            "detection_status": "rejected",
            "final_score": 0, "creator_score": 0,
            "overall_score": 0, "risk_score": 0,
            "recommendation": "Rejected — " + similarity_result["reason"],
            "memo": similarity_result["reason"],
        }

    vr = video_analysis_result or {}

    metrics = {
        "views":     req.metrics.views     or scraped["metrics"]["views"],
        "likes":     req.metrics.likes     or scraped["metrics"]["likes"],
        "comments":  req.metrics.comments  or scraped["metrics"]["comments"],
        "shares":    req.metrics.shares    or scraped["metrics"]["shares"],
        "favorites": req.metrics.favorites or scraped["metrics"]["favorites"],
    }

    metrics_available = dict(scraped["metrics_available"])
    if req.metrics.views:     metrics_available["views"]     = True
    if req.metrics.likes:     metrics_available["likes"]     = True
    if req.metrics.comments:  metrics_available["comments"]  = True
    if req.metrics.shares:    metrics_available["shares"]    = True
    if req.metrics.favorites: metrics_available["favorites"] = True

    full_text = " ".join([title, caption, raw_text, video_text]).strip()
    platform  = detect_platform(req.url) if req.url else "Uploaded Video"
    extracted_handle = extract_handle_from_url(req.url) if req.url else ""

    if not extracted_handle and req.user_handle:
        h = req.user_handle.strip()
        if h and not h.startswith("@") and not h.startswith("u/") and not h.startswith("http"):
            h = "@" + h
        extracted_handle = h

    if not extracted_handle and req.linked_handles:
        plat_key = platform.lower()
        linked_for_plat = req.linked_handles.get(plat_key, "")
        if linked_for_plat:
            extracted_handle = linked_for_plat

    if not extracted_handle and req.uploaded_video and req.linked_handles:
        for _, handle_val in req.linked_handles.items():
            if handle_val:
                extracted_handle = handle_val
                break

    # ── Ownership verification ──
    ownership_verified = True
    ownership_note = ""

    if req.url and not req.uploaded_video:
        def norm_handle(h: str) -> str:
            return h.lower().strip().lstrip("@").split("?")[0].rstrip("/")

        all_linked_norms = {norm_handle(v) for v in (req.linked_handles or {}).values() if v}
        OFFICIAL = {"viltrox.official","viltrox.usa","viltrox_official","viltroxofficial","唯卓仕官方"}

        if extracted_handle:
            submitted_norm = norm_handle(extracted_handle)
            if submitted_norm in OFFICIAL:
                ownership_verified = True
            elif all_linked_norms and submitted_norm not in all_linked_norms:
                return {
                    "status": "rejected",
                    "rejection_code": "ownership_mismatch",
                    "rejection_reason": (
                        f"⛔ 投稿被拒绝：检测到账号 @{submitted_norm} 未绑定到您的账户。\n\n"
                        "请勿提交他人内容。如需提交此账号的内容，请先在「账号管理」中绑定该平台账号。\n\n"
                        "This submission was rejected: account @" + submitted_norm +
                        " is not linked to your profile."
                    ),
                    "platform": platform,
                    "extracted_handle": extracted_handle,
                    "linked_handles": req.linked_handles,
                    "url": req.url,
                    "viltrox_detected": False,
                    "detection_status": "rejected",
                    "final_score": 0, "creator_score": 0,
                    "overall_score": 0, "risk_score": 0,
                    "recommendation": "Rejected — account not linked",
                    "memo": f"Hard reject: @{submitted_norm} not in linked accounts {list(all_linked_norms)}",
                }
            elif not all_linked_norms:
                ownership_verified = False
                ownership_note = "No linked accounts — please link a platform account first"
        elif req.url and not extracted_handle:
            ownership_verified = False
            ownership_note = "Could not verify account ownership from URL"

    product_match = classify_product(full_text)
    gear_mentions = detect_gear_mentions(full_text)
    brand = detect_viltrox(full_text, req.hints.model_dump())

    OFFICIAL = {"viltrox.official","viltrox.usa","viltrox_official","viltroxofficial","唯卓仕官方"}
    handle_norm = extracted_handle.lstrip("@").lower()
    if handle_norm in OFFICIAL and brand["status"] != "not_detected":
        brand["status"] = "confirmed"
        brand["confirmed"] = True
        if "Official Viltrox account" not in brand["evidence"]:
            brand["evidence"].insert(0, "Official Viltrox account")

    if video_analysis_result and video_analysis_result.get("analyzed"):
        vr = video_analysis_result
        if vr.get("viltrox_detected"):
            conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
            forced_status = conf_map.get(vr.get("confidence", "low"), "suspected")
            if brand["status"] != "confirmed":
                brand["status"] = forced_status
                brand["confirmed"] = (forced_status == "confirmed")
            brand["evidence"] = list(set(brand["evidence"] + vr.get("brand_elements", [])))
            if vr.get("logo_visible"):
                brand["auto_flags"]["logo"] = True
            if vr.get("product_visible"):
                brand["auto_flags"]["product"] = True
            for ct in vr.get("content_types", []):
                if ct not in brand["content_types"]:
                    brand["content_types"].append(ct)
        if vr.get("products_detected") and product_match["confidence"] == "none":
            for pd in vr["products_detected"]:
                pm2 = classify_product(pd)
                if pm2["confidence"] != "none":
                    product_match = pm2
                    break

    if product_match["confidence"] in {"high", "medium"}:
        brand["auto_flags"]["product"] = True

    comment_spam  = analyze_comments_for_spam(scraped["visible_comments"])
    risk          = compute_risk(metrics, metrics_available, comment_spam)
    creator_score = compute_creator_score(metrics.get("views", 0), metrics.get("likes", 0), metrics.get("comments", 0), metrics.get("shares", 0))
    content_score = 30 if brand["confirmed"] else 0
    campaign      = compute_campaign_score(
        content_score = content_score,
        views     = metrics.get("views", 0),
        likes     = metrics.get("likes", 0),
        comments  = metrics.get("comments", 0),
        shares    = metrics.get("shares", 0),
        favorites = metrics.get("favorites", 0),
    )
    final_score   = max(0, campaign["raw_score"] - risk["penalty"]) if brand["confirmed"] else 0

    has_video = bool(req.uploaded_video)
    if has_video:
        final_score = min(400, final_score + 50)

    hint_bonus = 0
    if req.hints.logo:    hint_bonus += 15
    if req.hints.product: hint_bonus += 12
    if req.hints.voice:   hint_bonus += 10
    if req.hints.review:  hint_bonus += 10
    final_score = min(400, final_score + hint_bonus)

    if video_analysis_result and video_analysis_result.get("viltrox_detected"):
        bonus = vr.get("brand_score_bonus", 0)
        final_score = min(400, final_score + bonus)

    if not ownership_verified:
        final_score   = 0
        overall_score = 0
        brand["status"] = "unverified_ownership"
        recommendation = "Rejected — submitted URL does not match linked account"
    else:
        overall_score = creator_score if not brand["confirmed"] else round((final_score / 4) * 0.7 + creator_score * 0.3)

    video_type_labels = {
        "review": "Product Review", "tutorial": "Tutorial / How-to",
        "lifestyle": "Lifestyle / Vlog", "photography": "Photography Showcase",
        "unboxing": "Unboxing", "comparison": "Lens Comparison",
        "cinematic": "Cinematic / Film", "travel": "Travel / Outdoor",
    }
    detected_types = brand.get("content_types", [])
    if video_analysis_result and video_analysis_result.get("content_types"):
        for ct in video_analysis_result["content_types"]:
            if ct not in detected_types:
                detected_types.append(ct)
    video_type_summary = " · ".join(
        video_type_labels.get(t, t.capitalize()) for t in detected_types
    ) if detected_types else "General / Unclassified"

    if brand["status"] == "confirmed":
        recommendation = "Eligible for brand campaign pool"
    elif brand["status"] == "suspected":
        recommendation = "Pending manual review"
    else:
        recommendation = "Rejected — no Viltrox content detected"
        final_score = 0
        overall_score = 0
        creator_score = 0

    if brand["status"] == "confirmed":
        memo = f"Status=confirmed. Platform={platform}. Campaign Score={final_score}, Creator Score={creator_score}, Overall Score={overall_score}. Evidence: {' / '.join(brand['evidence'])}. Type: {video_type_summary}."
        if video_analysis_result and video_analysis_result.get("analyzed"):
            memo += f" [Video] {vr.get('notes','')}"
    elif brand["status"] == "suspected":
        memo = f"Status=suspected. Manual review recommended. Creator Score={creator_score}. Evidence: {' / '.join(brand['evidence'])}. Type: {video_type_summary}."
    else:
        memo = f"Status=not_detected. Content not related to Viltrox — no points awarded. All scores zeroed. Type: {video_type_summary}."
        if video_analysis_result and video_analysis_result.get("error"):
            memo += f" Video analysis: {video_analysis_result['error']}"

    video_analysis = None
    if resolved_uploaded_video:
        if not title:
            title = str(resolved_uploaded_video.get("filename") or "")
        video_analysis = {
            "uploaded": True,
            "asset_id": int(resolved_uploaded_video.get("asset_id") or 0),
            "r2_key": str(resolved_uploaded_video.get("r2_key") or ""),
            "filename": str(resolved_uploaded_video.get("filename") or ""),
            "mime_type": str(resolved_uploaded_video.get("mime_type") or ""),
            "size_mb": float(resolved_uploaded_video.get("size_mb") or 0),
            "analyzed": video_analysis_result.get("analyzed", False) if video_analysis_result else False,
            "frames_checked": video_analysis_result.get("frames_checked", 0) if video_analysis_result else 0,
            "method": video_analysis_result.get("method", "none") if video_analysis_result else "none",
            "viltrox_in_video": video_analysis_result.get("viltrox_detected", False) if video_analysis_result else False,
            "confidence": video_analysis_result.get("confidence", "none") if video_analysis_result else "none",
            "brand_elements": video_analysis_result.get("brand_elements", []) if video_analysis_result else [],
            "products_found": video_analysis_result.get("products_detected", []) if video_analysis_result else [],
            "logo_visible": video_analysis_result.get("logo_visible", False) if video_analysis_result else False,
            "product_visible": video_analysis_result.get("product_visible", False) if video_analysis_result else False,
            "score_bonus": video_analysis_result.get("brand_score_bonus", 0) if video_analysis_result else 0,
            "vision_notes": video_analysis_result.get("notes", "") if video_analysis_result else "",
            "error": video_analysis_result.get("error") if video_analysis_result else None,
            "camera_mentions": gear_mentions["camera_mentions"],
            "lens_mentions": gear_mentions["lens_mentions"],
            "camera_body":    video_analysis_result.get("camera_body") if video_analysis_result else None,
            "camera_brand":   video_analysis_result.get("camera_brand") if video_analysis_result else None,
            "viltrox_lens":   video_analysis_result.get("viltrox_lens") if video_analysis_result else None,
            "other_lens":     video_analysis_result.get("other_lens") if video_analysis_result else None,
            "flash":          video_analysis_result.get("flash") if video_analysis_result else None,
            "adapter":        video_analysis_result.get("adapter") if video_analysis_result else None,
            "accessories":    video_analysis_result.get("accessories", []) if video_analysis_result else [],
            "gear_combo":     video_analysis_result.get("gear_combo", "") if video_analysis_result else "",
            "content_genre":  video_analysis_result.get("content_genre", "") if video_analysis_result else "",
            "content_topic":  video_analysis_result.get("content_topic", "") if video_analysis_result else "",
            "content_summary": video_analysis_result.get("content_summary", "") if video_analysis_result else "",
            "production_quality": video_analysis_result.get("production_quality", "") if video_analysis_result else "",
            "audience_fit":   video_analysis_result.get("audience_fit", "") if video_analysis_result else "",
            "content_types":  video_analysis_result.get("content_types", []) if video_analysis_result else [],
            "notes":          video_analysis_result.get("notes", "") if video_analysis_result else "",
            "quality_scores":      video_analysis_result.get("quality_scores", {}) if video_analysis_result else {},
            "quality_overall":     video_analysis_result.get("quality_overall", 0) if video_analysis_result else 0,
            "quality_summary":     video_analysis_result.get("quality_summary", "") if video_analysis_result else "",
            "reference_value":     video_analysis_result.get("reference_value", "") if video_analysis_result else "",
            "reference_reasons":   video_analysis_result.get("reference_reasons", []) if video_analysis_result else [],
            "improvements":        video_analysis_result.get("improvements", []) if video_analysis_result else [],
            "marketing_potential": video_analysis_result.get("marketing_potential", "") if video_analysis_result else "",
            "marketing_notes":     video_analysis_result.get("marketing_notes", "") if video_analysis_result else "",
            "tech_score":          video_analysis_result.get("tech_score", 0) if video_analysis_result else 0,
            "marketing_score":     video_analysis_result.get("marketing_score", 0) if video_analysis_result else 0,
            "best_frame_path":     video_analysis_result.get("best_frame_path", "") if video_analysis_result else "",
            "timestamps":          video_analysis_result.get("timestamps", []) if video_analysis_result else [],
        }
    else:
        video_analysis = {
            "uploaded": False,
            "analyzed": True,
            "method": "text_analysis",
            "og_image":       scraped.get("og_image", ""),
            "camera_body":    vr.get("camera_body"),
            "camera_brand":   vr.get("camera_brand"),
            "viltrox_lens":   vr.get("viltrox_lens"),
            "other_lens":     vr.get("other_lens"),
            "flash":          vr.get("flash"),
            "adapter":        vr.get("adapter"),
            "accessories":    vr.get("accessories", []),
            "gear_combo":     vr.get("gear_combo", ""),
            "brand_elements": vr.get("brand_elements", []),
            "products_found": vr.get("products_detected", []),
            "content_genre":  vr.get("content_genre", ""),
            "content_topic":  vr.get("content_topic", ""),
            "content_summary": vr.get("content_summary", ""),
            "production_quality": vr.get("production_quality", ""),
            "audience_fit":   vr.get("audience_fit", ""),
            "content_types":  vr.get("content_types", []),
            "notes":          vr.get("notes", ""),
            "camera_mentions": gear_mentions["camera_mentions"],
            "lens_mentions":   gear_mentions["lens_mentions"],
            "quality_scores":      vr.get("quality_scores", {}),
            "quality_overall":     vr.get("quality_overall", 0),
            "quality_summary":     vr.get("quality_summary", ""),
            "reference_value":     vr.get("reference_value", ""),
            "reference_reasons":   vr.get("reference_reasons", []),
            "improvements":        vr.get("improvements", []),
            "marketing_potential": vr.get("marketing_potential", ""),
            "marketing_notes":     vr.get("marketing_notes", ""),
            "tech_score":          vr.get("tech_score", 0),
            "marketing_score":     vr.get("marketing_score", 0),
            "timestamps":          vr.get("timestamps", []),
            "per_image_analysis":  vr.get("per_image_analysis", []),
        }

    result = {
        "status": "success",
        "url": req.url,
        "platform": platform,
        "extracted_handle": extracted_handle,
        "ownership_verified": ownership_verified,
        "ownership_note": ownership_note,
        "video_type_summary": video_type_summary,
        "title": title,
        "caption": caption,
        "scraped_text": raw_text,
        "scraped_ok": scraped["scraped_ok"],
        "scrape_error": scraped["error"],
        "metrics": metrics,
        "metrics_available": metrics_available,
        "detection_status": brand["status"],
        "viltrox_detected": brand["confirmed"],
        "evidence": brand["evidence"],
        "content_types": brand["content_types"],
        "auto_flags": brand["auto_flags"],
        "product_match": product_match,
        "gear_mentions": gear_mentions,
        "scores": {
            "content_score": campaign["content_score"],
            "campaign_interaction_score": campaign["campaign_interaction_score"],
            "creator_score": creator_score,
            "overall_score": overall_score,
            "risk_score": risk["risk_score"],
            "raw_score": campaign["raw_score"],
            "final_score": final_score,
        },
        "risk": risk,
        "ratios": compute_ratios(metrics.get("views",0), metrics.get("likes",0), metrics.get("comments",0), metrics.get("shares",0), metrics.get("favorites",0)),
        "recommendation": recommendation,
        "memo": memo,
        "visible_comments": scraped["visible_comments"],
        "comment_spam": comment_spam,
        "video_analysis": video_analysis,
        "tech_score":     vr.get("tech_score", 0),
        "marketing_score": vr.get("marketing_score", 0),
        "similarity": similarity_result,
        "needs_manual_review": (
            similarity_result.get("needs_review", False) or
            (video_analysis_result or {}).get("needs_manual_review", False)
        ),
        "manual_review_reason": " | ".join(filter(None, [
            similarity_result.get("reason", ""),
            (video_analysis_result or {}).get("manual_review_reason", ""),
        ])) or None,
    }

    # ── Update creator profile ──
    handle_for_profile = extracted_handle or req.user_handle or ""
    if handle_for_profile and video_analysis_result:
        update_creator_profile(handle_for_profile, video_analysis_result, platform)

    # ── Update genre benchmark + compute percentile ──
    genre_for_bench = vr.get("content_genre", "")
    tech_s  = vr.get("tech_score", 0)
    mkt_s   = vr.get("marketing_score", 0)
    percentiles = {"percentile_tech": 0, "percentile_mkt": 0}
    if genre_for_bench and tech_s > 0:
        percentiles = update_genre_benchmark(genre_for_bench, tech_s, mkt_s)
    result["percentile_tech"]         = percentiles["percentile_tech"]
    result["percentile_mkt"]          = percentiles["percentile_mkt"]
    result["content_genre"]           = genre_for_bench
    result["vertical_category"]       = vr.get("vertical_category", "")
    result["vertical_tech_score"]     = vr.get("vertical_tech_score", 0)
    result["vertical_mkt_score"]      = vr.get("vertical_mkt_score", 0)
    result["community_value"]         = vr.get("community_value", 0)
    result["product_showcase_score"]  = vr.get("product_showcase_score", 0)
    result["brand_exposure_score"]    = vr.get("brand_exposure_score", 0)
    result["storytelling_score"]      = vr.get("storytelling_score", 0)
    result["tech_status"]             = vr.get("tech_status", "")
    result["tech_floor"]              = vr.get("tech_floor", {})
    result["logo_detected"]           = vr.get("logo_detected", 0)
    result["product_closeup_count"]   = vr.get("product_closeup_count", 0)
    result["brand_exposure_detail"]   = vr.get("brand_exposure_detail", {})

    # ── Auto-add to verification queue ──
    handle_for_ver = extracted_handle or req.user_handle or ""
    platform_for_ver = platform if platform != "Uploaded Video" else (
        list(req.linked_handles.keys())[0] if req.linked_handles else "direct"
    )
    if handle_for_ver:
        try:
            await db_write(partial(_auto_create_verification_sync, platform_for_ver, handle_for_ver))
        except Exception:
            logger.exception(
                "audit.auto_create_verification_failed",
                extra={"platform": platform_for_ver, "handle": handle_for_ver},
            )

    save_submission(result, user_id=current_uid)
    return result
