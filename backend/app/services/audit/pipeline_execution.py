"""Behavior-preserving bounded phase helpers for the full audit pipeline."""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from app.services.audit.pipeline_contract import AnalysisOutcome, AuditDependencies
from app.services.audit.pipeline_contract import CollectedSource, DetectionOutcome, ScoringOutcome
from app.services.audit.pipeline_result import build_result


def _empty_scrape() -> Dict[str, Any]:
    return {
        "scraped_ok": False,
        "title": "",
        "caption": "",
        "scraped_text": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {
            "views": False,
            "likes": False,
            "comments": False,
            "shares": False,
            "favorites": False,
        },
        "visible_comments": [],
        "og_image": "",
        "error": "No URL",
    }


async def _collect_source(job: Any, platform: str, deps: AuditDependencies) -> CollectedSource:
    scraped = _empty_scrape()
    if job.url and deps.valid_url(job.url):
        try:
            scraped = await deps.scrape_url(job.url)
            deps.logger.info(
                "pipeline scrape ok | submission_id=%s | scraped_ok=%s | views=%s",
                job.submission_id,
                scraped.get("scraped_ok"),
                scraped.get("metrics", {}).get("views", 0),
            )
        except Exception as exc:
            deps.logger.warning(
                "pipeline scrape error | submission_id=%s | error=%s",
                job.submission_id,
                exc,
            )
            scraped["error"] = str(exc)

    title = job.title or scraped.get("title", "")
    caption = job.caption or scraped.get("caption", "")
    raw_text = job.scraped_text or scraped.get("scraped_text", "")
    seed_metrics = job.metrics or {}
    metrics = {
        "views": seed_metrics.get("views", 0) or scraped["metrics"]["views"],
        "likes": seed_metrics.get("likes", 0) or scraped["metrics"]["likes"],
        "comments": seed_metrics.get("comments", 0) or scraped["metrics"]["comments"],
        "shares": seed_metrics.get("shares", 0) or scraped["metrics"]["shares"],
        "favorites": seed_metrics.get("favorites", 0) or scraped["metrics"]["favorites"],
    }
    metrics_available = dict(scraped.get("metrics_available", {}))
    for key in metrics:
        if metrics[key] > 0:
            metrics_available[key] = True
    return CollectedSource(
        scraped=scraped,
        title=title,
        caption=caption,
        raw_text=raw_text,
        metrics=metrics,
        metrics_available=metrics_available,
    )


async def _safe_provider(deps: AuditDependencies, provider: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await deps.guarded_provider_call(provider, lambda: fn(*args, **kwargs))
    except Exception as exc:
        return {"error": str(exc), "analyzed": False, "provider": provider}


async def _safe_provider_thread(
    deps: AuditDependencies, provider: str, fn: Any, *args: Any, **kwargs: Any
) -> Any:
    try:
        return await deps.guarded_provider_call(
            provider,
            lambda: asyncio.to_thread(fn, *args, **kwargs),
        )
    except Exception as exc:
        return {"error": str(exc), "analyzed": False, "provider": provider}


def _prefilter_task(
    deps: AuditDependencies, title: str, caption: str, platform: str
) -> asyncio.Task[Any] | None:
    if not (title or caption):
        return None
    return asyncio.create_task(
        _safe_provider_thread(
            deps,
            "openai",
            deps.gpt_prefilter_caption,
            title,
            caption,
            platform,
        )
    )


async def _uploaded_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    deps: AuditDependencies,
    uploaded: Dict[str, Any],
    uploaded_analysis_path: str,
    prefilter_task: asyncio.Task[Any] | None,
) -> tuple[Any, Any, Any]:
    deps.logger.info("pipeline uploaded video triple-path | submission_id=%s", job.submission_id)
    vision_task = asyncio.create_task(
        _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_video_with_claude,
            uploaded_analysis_path,
            uploaded.get("filename", ""),
            creator_handle=job.handle or "",
        )
    )
    text_task = asyncio.create_task(
        _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_text_content,
            source.title,
            source.caption,
            job.url or "",
            platform,
            source.raw_text,
            source.scraped.get("og_image", ""),
        )
    )
    if prefilter_task is not None:
        prefilter, vision, text = await asyncio.gather(prefilter_task, vision_task, text_task)
    else:
        vision, text = await asyncio.gather(vision_task, text_task)
        prefilter = None
    return vision or text or {}, prefilter, text


async def _pending_upload_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    deps: AuditDependencies,
    uploaded: Dict[str, Any],
    prefilter_task: asyncio.Task[Any] | None,
) -> tuple[Any, Any, Any]:
    deps.logger.info(
        "pipeline upload awaiting video_factory | submission_id=%s | r2_key=%s",
        job.submission_id,
        str(uploaded.get("r2_key") or ""),
    )
    video = {
        "analyzed": False,
        "method": "video_factory_pending",
        "error": "Video factory decode asset unavailable",
        "r2_key": str(uploaded.get("r2_key") or ""),
        "storage_key": str(uploaded.get("storage_key") or ""),
        "analysis_path": "",
    }
    prefilter = await prefilter_task if prefilter_task is not None else None
    text = None
    if source.title or source.caption or source.raw_text:
        text = await _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_text_content,
            source.title,
            source.caption,
            job.url or "",
            platform,
            source.raw_text,
            source.scraped.get("og_image", ""),
        )
    return video, prefilter, text


async def _youtube_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    deps: AuditDependencies,
    prefilter_task: asyncio.Task[Any] | None,
) -> tuple[Any, Any, Any]:
    deps.logger.info("pipeline YouTube triple-model analysis | submission_id=%s", job.submission_id)
    gemini_task = asyncio.create_task(
        _safe_provider(
            deps,
            "gemini",
            deps.analyze_youtube_with_gemini,
            job.url,
            source.title,
            creator_handle=job.handle or "",
        )
    )
    text_task = asyncio.create_task(
        _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_text_content,
            source.title,
            source.caption,
            job.url or "",
            platform,
            source.raw_text,
            source.scraped.get("og_image", ""),
        )
    )
    if prefilter_task is not None:
        prefilter, gemini, text = await asyncio.gather(prefilter_task, gemini_task, text_task)
    else:
        gemini, text = await asyncio.gather(gemini_task, text_task)
        prefilter = None
    video = gemini if gemini and gemini.get("analyzed") else text
    if not video or not video.get("analyzed"):
        deps.logger.warning(
            "pipeline Gemini/text failed; falling back to smart analysis | submission_id=%s",
            job.submission_id,
        )
        video = await _safe_provider(
            deps,
            "claude",
            deps.analyze_url_content_smart,
            url=job.url,
            title=source.title,
            caption=source.caption,
            scraped_text=source.raw_text,
            og_image=source.scraped.get("og_image", ""),
            platform=platform,
            creator_handle=job.handle or "",
        ) or {}
    return video, prefilter, text


async def _url_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    deps: AuditDependencies,
    prefilter_task: asyncio.Task[Any] | None,
) -> tuple[Any, Any, Any]:
    deps.logger.info(
        "pipeline non-YouTube multi-path analysis | submission_id=%s | platform=%s",
        job.submission_id,
        platform,
    )
    smart_task = asyncio.create_task(
        _safe_provider(
            deps,
            "claude",
            deps.analyze_url_content_smart,
            url=job.url,
            title=source.title,
            caption=source.caption,
            scraped_text=source.raw_text,
            og_image=source.scraped.get("og_image", ""),
            platform=platform,
            creator_handle=job.handle or "",
        )
    )
    text_task = asyncio.create_task(
        _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_text_content,
            source.title,
            source.caption,
            job.url or "",
            platform,
            source.raw_text,
            source.scraped.get("og_image", ""),
        )
    )
    if prefilter_task is not None:
        prefilter, smart, text = await asyncio.gather(prefilter_task, smart_task, text_task)
    else:
        smart, text = await asyncio.gather(smart_task, text_task)
        prefilter = None
    video = smart if smart and smart.get("analyzed") else text
    return video, prefilter, text


async def _text_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    deps: AuditDependencies,
    prefilter_task: asyncio.Task[Any] | None,
) -> tuple[Any, Any, Any]:
    video = None
    text = None
    prefilter = None
    if source.title or source.caption or source.raw_text:
        deps.logger.info("pipeline text-only analysis | submission_id=%s", job.submission_id)
        text = await _safe_provider_thread(
            deps,
            "claude",
            deps.analyze_text_content,
            source.title,
            source.caption,
            job.url or "",
            platform,
            source.raw_text,
            source.scraped.get("og_image", ""),
        ) or {}
        if prefilter_task is not None:
            prefilter = await prefilter_task
        video = text
    return video, prefilter, text


async def _run_analysis(job: Any, platform: str, source: CollectedSource, deps: AuditDependencies) -> AnalysisOutcome:
    uploaded = job.uploaded_video
    uploaded_analysis_path = str(
        (uploaded or {}).get("analysis_path") or (uploaded or {}).get("path") or ""
    ).strip()
    has_upload = bool(uploaded and isinstance(uploaded, dict) and uploaded_analysis_path)
    is_youtube = platform == "YouTube" or (
        job.url and ("youtube.com" in job.url or "youtu.be" in job.url)
    )
    prefilter_task = _prefilter_task(deps, source.title, source.caption, platform)
    if has_upload:
        video, prefilter, text = await _uploaded_analysis(
            job,
            platform,
            source,
            deps,
            uploaded,
            uploaded_analysis_path,
            prefilter_task,
        )
    elif uploaded and isinstance(uploaded, dict) and uploaded.get("r2_key"):
        video, prefilter, text = await _pending_upload_analysis(
            job,
            platform,
            source,
            deps,
            uploaded,
            prefilter_task,
        )
    elif job.url and is_youtube and deps.gemini_available:
        video, prefilter, text = await _youtube_analysis(job, platform, source, deps, prefilter_task)
    elif job.url and (deps.anthropic_available or deps.gemini_available):
        video, prefilter, text = await _url_analysis(job, platform, source, deps, prefilter_task)
    else:
        video, prefilter, text = await _text_analysis(job, platform, source, deps, prefilter_task)
    return AnalysisOutcome(
        video_analysis_result=video,
        prefilter_result=prefilter,
        text_analysis_result=text,
        has_upload=has_upload,
        uploaded_analysis_path=uploaded_analysis_path,
    )


def _add_analysis_layers(vr: Dict[str, Any], analysis: AnalysisOutcome) -> None:
    if analysis.prefilter_result:
        vr.setdefault("prefilter", analysis.prefilter_result)
        vr.setdefault("layers_used", [])
        if "gpt_prefilter" not in vr["layers_used"]:
            vr["layers_used"].append("gpt_prefilter")
    if analysis.text_analysis_result and analysis.text_analysis_result is not vr:
        vr.setdefault("text_layer", analysis.text_analysis_result)
        vr.setdefault("layers_used", [])
        if "text_claude" not in vr["layers_used"]:
            vr["layers_used"].append("text_claude")


def _add_uploaded_metadata(
    vr: Dict[str, Any],
    uploaded: Dict[str, Any],
    analysis: AnalysisOutcome,
) -> None:
    vr.setdefault("storage_key", uploaded.get("storage_key", "") or "")
    vr.setdefault("analysis_path", analysis.uploaded_analysis_path)
    vr.setdefault("filename", uploaded.get("filename", "") or "")
    if analysis.has_upload:
        vr.setdefault("path", analysis.uploaded_analysis_path)
    if uploaded.get("r2_key"):
        vr.setdefault("r2_key", uploaded.get("r2_key", ""))


def _add_url_metadata(vr: Dict[str, Any], job: Any, platform: str, source: CollectedSource) -> None:
    scraped = source.scraped
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


async def _archive_url(vr: Dict[str, Any], job: Any, platform: str, source: CollectedSource, deps: AuditDependencies) -> None:
    try:
        from app.services.media.url_archive import archive_submission_video_url

        archive_result = await archive_submission_video_url(
            submission_id=job.submission_id,
            source_url=job.url,
            platform=platform,
            scraped_video_url=source.scraped.get("video_url", "") or "",
            title=source.title,
        )
        vr["archive_status"] = "archived" if archive_result.get("archived") else "failed"
        if archive_result.get("archived"):
            vr.setdefault("r2_key", archive_result.get("r2_key", ""))
            vr["archive_method"] = archive_result.get("method", "")
            vr["archive_scope"] = archive_result.get("scope", "")
            vr["archive_asset_role"] = archive_result.get("asset_role", "")
        elif archive_result.get("error"):
            vr["archive_error"] = archive_result.get("error", "")[:200]
    except Exception as exc:
        deps.logger.warning(
            "pipeline archive error | submission_id=%s | error=%s",
            job.submission_id,
            exc,
        )


async def _enrich_analysis(
    job: Any,
    platform: str,
    source: CollectedSource,
    analysis: AnalysisOutcome,
    deps: AuditDependencies,
) -> Dict[str, Any]:
    vr = analysis.video_analysis_result or {}
    _add_analysis_layers(vr, analysis)
    uploaded = job.uploaded_video
    if uploaded and isinstance(vr, dict):
        _add_uploaded_metadata(vr, uploaded, analysis)
    elif job.url and isinstance(vr, dict):
        _add_url_metadata(vr, job, platform, source)
        await _archive_url(vr, job, platform, source, deps)
    return vr


def _video_text(source: CollectedSource, vr: Dict[str, Any]) -> str:
    text = f"{source.title} {source.caption} {source.raw_text}"
    if vr.get("brand_elements"):
        text += " " + " ".join(vr["brand_elements"])
    if vr.get("products_detected"):
        text += " " + " ".join(vr["products_detected"])
    if vr.get("viltrox_products_all"):
        text += " " + " ".join(vr["viltrox_products_all"])
    return text


async def _classify_product_scoped(text: str, deps: AuditDependencies) -> Dict[str, Any]:
    async with deps.db_connection_scope():
        return deps.classify_product(text)


async def _apply_learned_correction(
    job: Any,
    product_match: Dict[str, Any],
    deps: AuditDependencies,
) -> Dict[str, Any]:
    if not job.url:
        return product_match
    try:
        from app.services.audit.learning import lookup_correction

        async with deps.db_connection_scope():
            learned = lookup_correction(job.url)
        if learned:
            product_match = {
                "series": learned.get("correct_series", ""),
                "label": learned.get("correct_label", ""),
                "confidence": "high",
                "evidence": ["learned from admin correction"],
            }
            deps.logger.info(
                "pipeline learned correction | submission_id=%s | label=%s",
                job.submission_id,
                product_match["label"],
            )
    except Exception as exc:
        deps.logger.warning(
            "pipeline learning lookup error | submission_id=%s | error=%s",
            job.submission_id,
            exc,
        )
    return product_match


def _apply_ai_brand_detection(vr: Dict[str, Any], brand: Dict[str, Any]) -> None:
    if not vr.get("viltrox_detected"):
        return
    conf_map = {"high": "confirmed", "medium": "confirmed", "low": "suspected"}
    forced = conf_map.get(vr.get("confidence", "low"), "suspected")
    if brand["status"] != "confirmed":
        brand["status"] = forced
        brand["confirmed"] = forced == "confirmed"
    brand["evidence"] = list(set(brand["evidence"] + vr.get("brand_elements", [])))


async def _refine_detected_product(
    vr: Dict[str, Any],
    product_match: Dict[str, Any],
    deps: AuditDependencies,
) -> Dict[str, Any]:
    if vr.get("products_detected") and product_match.get("confidence") == "none":
        for detected_product in vr["products_detected"]:
            candidate = await _classify_product_scoped(detected_product, deps)
            if candidate["confidence"] != "none":
                return candidate
    return product_match


async def _refine_cinema_product(
    vr: Dict[str, Any],
    product_match: Dict[str, Any],
    camera_brand: str,
    deps: AuditDependencies,
) -> Dict[str, Any]:
    if camera_brand not in ("ARRI", "RED", "BLACKMAGIC"):
        return product_match
    all_products: list[Any] = []
    if product_match.get("series", "") in ("AIR", "LAB", "PRO", ""):
        all_products = vr.get("products_detected", []) + vr.get("viltrox_products_all", [])
    for product in all_products:
        if any(keyword in product.lower() for keyword in ("epic", "luna", "anamorphic", "zmove")):
            candidate = await _classify_product_scoped(product, deps)
            if candidate.get("confidence") != "none":
                return candidate
    return product_match


async def _refine_dji_product(
    vr: Dict[str, Any],
    product_match: Dict[str, Any],
    camera_brand: str,
    deps: AuditDependencies,
) -> Dict[str, Any]:
    camera_body = (vr.get("camera_body") or "").lower()
    is_dji_dl_body = camera_brand == "DJI" or any(
        keyword in camera_body
        for keyword in ("inspire 3", "ronin 4d", "zenmuse x9", "x9-8k", "x9 8k")
    )
    if not is_dji_dl_body or product_match.get("series", "") in ("DL",):
        return product_match
    all_products = vr.get("products_detected", []) + vr.get("viltrox_products_all", [])
    for product in all_products:
        if any(keyword in product.lower() for keyword in ("dl ", " dl", "dl mount", "f3.5", "raze", "90mm")):
            candidate = await _classify_product_scoped(product, deps)
            if candidate.get("series") == "DL":
                deps.logger.info(
                    "pipeline DL cross-validation | camera=%s | label=%s",
                    camera_body,
                    candidate.get("label"),
                )
                return candidate
    return product_match


async def _detect(
    job: Any,
    source: CollectedSource,
    vr: Dict[str, Any],
    deps: AuditDependencies,
) -> DetectionOutcome:
    full_text = " ".join(
        filter(None, [source.title, source.caption, source.raw_text, _video_text(source, vr)])
    ).strip()
    product_match = await _classify_product_scoped(full_text, deps)
    gear_mentions = deps.detect_gear_mentions(full_text)
    hints = job.hints or {}
    brand = deps.detect_viltrox(full_text, hints)
    product_match = await _apply_learned_correction(job, product_match, deps)
    _apply_ai_brand_detection(vr, brand)
    product_match = await _refine_detected_product(vr, product_match, deps)
    camera_brand = (vr.get("camera_brand") or "").upper()
    product_match = await _refine_cinema_product(vr, product_match, camera_brand, deps)
    product_match = await _refine_dji_product(vr, product_match, camera_brand, deps)
    if product_match.get("confidence") in ("high", "medium"):
        brand["auto_flags"] = brand.get("auto_flags", {})
        brand["auto_flags"]["product"] = True
    return DetectionOutcome(
        product_match=product_match,
        gear_mentions=gear_mentions,
        brand=brand,
        hints=hints,
    )


def _hint_bonus(hints: Dict[str, Any]) -> int:
    return sum(
        [
            15 if hints.get("logo") else 0,
            12 if hints.get("product") else 0,
            10 if hints.get("voice") else 0,
            10 if hints.get("review") else 0,
        ]
    )


def _detection_status(
    brand: Dict[str, Any],
    final_score: Any,
    creator_score: Any,
) -> tuple[str, str, Any, Any, Any]:
    if brand["status"] == "confirmed":
        overall_score = round((final_score / 4) * 0.7 + creator_score * 0.3)
        return "confirmed", "Eligible for brand campaign pool", overall_score, final_score, creator_score
    if brand["status"] == "suspected":
        return "suspected", "Pending manual review", creator_score, final_score, creator_score
    return "not_detected", "No Viltrox content detected", 0, 0, 0


def _score(
    platform: str,
    source: CollectedSource,
    analysis: AnalysisOutcome,
    vr: Dict[str, Any],
    detection: DetectionOutcome,
    deps: AuditDependencies,
) -> ScoringOutcome:
    comment_spam = deps.analyze_comments_for_spam(source.scraped.get("visible_comments", []))
    risk = deps.compute_risk(source.metrics, source.metrics_available, comment_spam)
    content_types = detection.brand.get("content_types", [])
    if vr.get("content_types"):
        for content_type in vr["content_types"]:
            if content_type not in content_types:
                content_types.append(content_type)
    creator_score = deps.compute_creator_score(
        metrics=source.metrics,
        metrics_available=source.metrics_available,
        risk_score=risk["risk_score"],
    )
    detected = detection.brand.get("confirmed", False)
    campaign = deps.compute_campaign_score(
        metrics=source.metrics,
        metrics_available=source.metrics_available,
        detected=detected,
        content_types=content_types,
        platform=platform,
        video_analysis=vr,
    )
    final_score = max(0, campaign["raw_score"] - risk["penalty"]) if detected else 0
    if analysis.has_upload:
        final_score = min(400, final_score + 50)
    final_score = min(400, final_score + _hint_bonus(detection.hints))
    if vr.get("viltrox_detected") and vr.get("brand_score_bonus", 0):
        final_score = min(400, final_score + vr["brand_score_bonus"])
    status, recommendation, overall, final_score, creator_score = _detection_status(
        detection.brand,
        final_score,
        creator_score,
    )
    return ScoringOutcome(
        content_types=content_types,
        creator_score=creator_score,
        campaign=campaign,
        final_score=final_score,
        detection_status=status,
        recommendation=recommendation,
        overall_score=overall,
        risk=risk,
        comment_spam=comment_spam,
    )


def _apply_weighted_scores(
    ctx: Any, vr: Dict[str, Any], genre: str, tech_score: Any, marketing_score: Any
) -> tuple[Any, Any]:
    if not (ctx and ctx.compute_weighted_fn and vr.get("quality_scores")):
        return tech_score, marketing_score
    vertical = vr.get("vertical_category", "")
    if ctx.get_vertical_fn:
        vertical_key = ctx.get_vertical_fn(genre)
        if ctx.apply_learned_weights_fn:
            ctx.apply_learned_weights_fn(vertical_key)
    weighted = ctx.compute_weighted_fn(vr.get("quality_scores", {}), genre, vertical)
    tech_score = weighted.get("tech_score", tech_score)
    marketing_score = weighted.get("marketing_score", marketing_score)
    vr["tech_score"] = tech_score
    vr["marketing_score"] = marketing_score
    vr["quality_overall"] = weighted.get("quality_overall", 0)
    vr["tech_status"] = weighted.get("tech_status", "")
    return tech_score, marketing_score


async def _update_profiles(
    job: Any,
    ctx: Any,
    platform: str,
    vr: Dict[str, Any],
    deps: AuditDependencies,
) -> tuple[Any, Any, Dict[str, Any], str]:
    handle = job.handle or ""
    if handle and vr.get("analyzed"):
        try:
            async with deps.db_connection_scope():
                deps.update_creator_profile(handle, vr, platform)
        except Exception as exc:
            deps.logger.warning(
                "pipeline profile error | submission_id=%s | error=%s",
                job.submission_id,
                exc,
            )
    genre = vr.get("content_genre", "")
    tech_score = vr.get("tech_score", 0)
    marketing_score = vr.get("marketing_score", 0)
    percentiles = {"percentile_tech": 0, "percentile_mkt": 0}
    try:
        tech_score, marketing_score = _apply_weighted_scores(
            ctx,
            vr,
            genre,
            tech_score,
            marketing_score,
        )
    except Exception as exc:
        deps.logger.warning(
            "pipeline weighted scores error | submission_id=%s | error=%s",
            job.submission_id,
            exc,
        )
    if genre and tech_score > 0:
        try:
            async with deps.db_connection_scope():
                percentiles = deps.update_genre_benchmark(genre, tech_score, marketing_score)
        except Exception as exc:
            deps.logger.warning(
                "pipeline benchmark error | submission_id=%s | error=%s",
                job.submission_id,
                exc,
            )
    return tech_score, marketing_score, percentiles, genre


async def execute_full_audit(job: Any, ctx: Any, deps: AuditDependencies) -> Dict[str, Any]:
    """Run the four bounded phases and return the historical result contract."""
    platform = job.platform or (deps.detect_platform(job.url) if job.url else "Uploaded Video")
    deps.logger.info(
        "pipeline start | submission_id=%s | platform=%s | source=%s",
        job.submission_id,
        platform,
        job.url[:50] if job.url else "upload",
    )
    source = await _collect_source(job, platform, deps)
    analysis = await _run_analysis(job, platform, source, deps)
    vr = await _enrich_analysis(job, platform, source, analysis, deps)
    detection = await _detect(job, source, vr, deps)
    scoring = _score(platform, source, analysis, vr, detection, deps)
    profile = await _update_profiles(job, ctx, platform, vr, deps)
    result = build_result(job, platform, source, vr, detection, scoring, profile)
    deps.logger.info(
        "pipeline done | submission_id=%s | status=%s | product=%s | final=%s | creator=%s | tech=%.1f | mkt=%.1f",
        job.submission_id,
        scoring.detection_status,
        detection.product_match.get("label", "?"),
        scoring.final_score,
        scoring.creator_score,
        profile[0],
        profile[1],
    )
    return result
