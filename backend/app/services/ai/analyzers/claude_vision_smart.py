"""Low-complexity orchestration for smart URL content analysis."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


VIDEO_PLATFORMS = (
    "Instagram",
    "TikTok",
    "Douyin",
    "Facebook",
    "Bilibili",
    "Xiaohongshu",
    "Reddit",
    "Unknown",
)


@dataclass(frozen=True)
class SmartAnalysisDependencies:
    anthropic_available: bool
    gemini_available: bool
    openai_available: bool
    ytdlp_available: bool
    initial_smart_result: Callable[[], dict[str, Any]]
    get_creator_profile: Callable[[str], dict[str, Any]]
    gpt_prefilter_caption: Callable[[str, str, str], dict[str, Any]]
    analyze_youtube_with_gemini: Callable[
        [str, str, str], Awaitable[dict[str, Any]]
    ]
    fetch_all_images_from_post: Callable[[str, str], list[Any]]
    analyze_images_batch: Callable[[list[Any], str, str, str], dict[str, Any]]
    merge_analysis: Callable[[dict[str, Any], dict[str, Any]], Any]
    temporary_directory: Callable[[], Any]
    download_direct_video_url: Callable[[str, str], dict[str, Any]]
    download_video_ytdlp: Callable[[str, str], dict[str, Any]]
    analyze_local_video_with_gemini_file_api: Callable[..., Awaitable[bool]]
    analyze_video_with_claude: Callable[..., dict[str, Any]]
    unlink: Callable[[str], None]
    parse_gear_from_caption: Callable[[str], Any]
    analyze_text_content: Callable[..., dict[str, Any]]
    compute_weighted_scores: Callable[[dict[str, Any], str], dict[str, Any]]
    logger: Any


def _creator_profile_hint(
    creator_handle: str,
    *,
    dependencies: SmartAnalysisDependencies,
) -> str:
    profile = (
        dependencies.get_creator_profile(creator_handle)
        if creator_handle
        else {}
    )
    profile_hint = ""
    if profile.get("cameras"):
        profile_hint = (
            f"\nCREATOR HISTORY: Known to use {', '.join(profile['cameras'][:2])}. "
        )
    if profile.get("viltrox_lenses"):
        profile_hint += (
            f"Known Viltrox lenses: {', '.join(profile['viltrox_lenses'][:3])}."
        )
    return profile_hint


def _apply_gpt_prefilter(
    result: dict[str, Any],
    *,
    title: str,
    caption: str,
    platform: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    if not dependencies.openai_available:
        return
    dependencies.logger.info("smart analysis | GPT pre-filter caption analysis")
    gpt_result = dependencies.gpt_prefilter_caption(title, caption, platform)
    if gpt_result.get("camera_body"):
        result["camera_body"] = gpt_result["camera_body"]
    if gpt_result.get("viltrox_lens") and not result.get("viltrox_lens"):
        result["viltrox_lens"] = gpt_result["viltrox_lens"]
        result["analyzed"] = True
        result["brand_elements"].append(
            f"GPT caption: {result['viltrox_lens']}"
        )
    if gpt_result.get("other_lens") and not result.get("other_lens"):
        result["other_lens"] = gpt_result["other_lens"]
    if gpt_result.get("content_genre") and not result.get("content_genre"):
        result["content_genre"] = gpt_result["content_genre"]
    result["layers_used"].append("gpt_prefilter")
    dependencies.logger.info(
        "smart analysis | GPT hint | viltrox=%s | confidence=%s",
        gpt_result.get("viltrox_lens"),
        gpt_result.get("confidence"),
    )


async def _apply_youtube_gemini(
    result: dict[str, Any],
    *,
    url: str,
    title: str,
    platform: str,
    creator_handle: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    if not (
        platform == "YouTube"
        and dependencies.gemini_available
        and url
    ):
        return
    dependencies.logger.info(
        "smart analysis | Gemini layer 0 — YouTube direct read"
    )
    gemini_result = await dependencies.analyze_youtube_with_gemini(
        url,
        title,
        creator_handle,
    )
    if not gemini_result.get("analyzed"):
        return
    result["layers_used"].append("gemini_youtube")
    dependencies.merge_analysis(result, gemini_result)
    result["analyzed"] = True
    result["method"] = "gemini_youtube"
    if gemini_result.get("timestamps"):
        result["timestamps"] = gemini_result["timestamps"]
    if result.get("viltrox_lens") and result.get("camera_body"):
        dependencies.logger.info(
            "smart analysis | Gemini got full gear — skipping yt-dlp, running Claude scoring"
        )
    else:
        dependencies.logger.info(
            "smart analysis | Gemini partial — continuing to Claude for gear confirmation"
        )


def _apply_image_layer(
    result: dict[str, Any],
    *,
    url: str,
    title: str,
    og_image: str,
    platform: str,
    profile_hint: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    dependencies.logger.info(
        "smart analysis | layer 1 image fetch | platform=%s",
        platform,
    )
    all_images = dependencies.fetch_all_images_from_post(url, og_image)
    dependencies.logger.info(
        "smart analysis | got %s images",
        len(all_images),
    )
    if not all_images:
        return
    result["layers_used"].append(f"images({len(all_images)})")
    image_analysis = dependencies.analyze_images_batch(
        all_images,
        title,
        platform,
        profile_hint,
    )
    if image_analysis:
        dependencies.merge_analysis(result, image_analysis)
        result["analyzed"] = True
        result["method"] = f"image_vision_{len(all_images)}imgs"


def _should_download_video(
    result: dict[str, Any],
    *,
    platform: str,
    dependencies: SmartAnalysisDependencies,
) -> bool:
    has_video_platform = platform in VIDEO_PLATFORMS
    gemini_youtube_complete = bool(
        platform == "YouTube"
        and result.get("viltrox_lens")
        and result.get("camera_body")
        and result.get("quality_scores")
    )
    return bool(
        dependencies.ytdlp_available
        and not gemini_youtube_complete
        and (
            has_video_platform
            or not result.get("viltrox_lens")
            or not result.get("camera_body")
            or not result.get("quality_scores")
            or result.get("confidence") in ("low", "none", None)
        )
    )


def _download_video(
    result: dict[str, Any],
    *,
    url: str,
    direct_video_url: str,
    tmpdir: str,
    platform: str,
    dependencies: SmartAnalysisDependencies,
) -> dict[str, Any]:
    download = (
        dependencies.download_direct_video_url(direct_video_url, tmpdir)
        if direct_video_url
        else {
            "success": False,
            "path": None,
            "duration": 0,
            "error": "direct video url missing",
        }
    )
    if download.get("success"):
        result["video_source"] = "direct_url"
        dependencies.logger.info(
            "smart analysis | layer 2 direct video url | platform=%s",
            platform,
        )
        return download
    if direct_video_url:
        dependencies.logger.warning(
            "smart analysis | direct video failed: %s",
            download.get("error"),
        )
        result["layers_used"].append("direct_video_failed")
    download = dependencies.download_video_ytdlp(url, tmpdir)
    if download.get("success"):
        result["video_source"] = "ytdlp"
    return download


async def _analyze_download(
    result: dict[str, Any],
    download: dict[str, Any],
    *,
    url: str,
    title: str,
    platform: str,
    creator_handle: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    if not (download["success"] and download["path"]):
        dependencies.logger.warning(
            "smart analysis | yt-dlp failed: %s",
            download.get("error"),
        )
        result["layers_used"].append("ytdlp_failed")
        return
    result["layers_used"].append(f"video({download['duration']:.0f}s)")
    video_path = download["path"]
    gemini_ok = False
    if dependencies.gemini_available:
        gemini_ok = await dependencies.analyze_local_video_with_gemini_file_api(
            video_path=video_path,
            url=url,
            title=title,
            platform=platform,
            creator_handle=creator_handle,
            duration_seconds=download.get("duration"),
            result=result,
        )
    if not gemini_ok:
        dependencies.logger.info(
            "smart analysis | layer 2 Claude frame fallback"
        )
        video_analysis = dependencies.analyze_video_with_claude(
            video_path,
            title or url,
            creator_handle=creator_handle,
        )
        if video_analysis and video_analysis.get("analyzed"):
            dependencies.merge_analysis(result, video_analysis)
            result["analyzed"] = True
            result["method"] = f"ytdlp_claude_{platform}"
    try:
        dependencies.unlink(video_path)
    except OSError as exc:
        dependencies.logger.debug(
            "temporary video cleanup failed: %s",
            exc,
        )


async def _apply_video_layer(
    result: dict[str, Any],
    *,
    url: str,
    title: str,
    platform: str,
    creator_handle: str,
    direct_video_url: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    if not _should_download_video(
        result,
        platform=platform,
        dependencies=dependencies,
    ):
        return
    dependencies.logger.info(
        "smart analysis | layer 2 yt-dlp | platform=%s",
        platform,
    )
    with dependencies.temporary_directory() as tmpdir:
        download = _download_video(
            result,
            url=url,
            direct_video_url=direct_video_url,
            tmpdir=tmpdir,
            platform=platform,
            dependencies=dependencies,
        )
        await _analyze_download(
            result,
            download,
            url=url,
            title=title,
            platform=platform,
            creator_handle=creator_handle,
            dependencies=dependencies,
        )


def _apply_caption_gear(
    result: dict[str, Any],
    caption_gear: Any,
    *,
    dependencies: SmartAnalysisDependencies,
) -> dict[str, Any]:
    if not isinstance(caption_gear, dict):
        caption_gear = {}
    if (
        isinstance(caption_gear, dict)
        and caption_gear.get("camera_body")
        and isinstance(result, dict)
        and not result.get("camera_body")
    ):
        result["camera_body"] = caption_gear["camera_body"]
        result["camera_brand"] = caption_gear["camera_brand"]
        result["layers_used"].append("caption_parser")
        dependencies.logger.info(
            "caption parse | camera=%s",
            result["camera_body"],
        )
    if caption_gear.get("viltrox_lens") and not result.get("viltrox_lens"):
        result["viltrox_lens"] = caption_gear["viltrox_lens"]
        result["analyzed"] = True
        if caption_gear["viltrox_lens"] not in result.get("brand_elements", []):
            result.setdefault("brand_elements", []).append(
                f"Caption: {caption_gear['viltrox_lens']}"
            )
        dependencies.logger.info(
            "caption parse | viltrox_lens=%s",
            result["viltrox_lens"],
        )
    if caption_gear.get("other_lens") and not result.get("other_lens"):
        result["other_lens"] = caption_gear["other_lens"]
    if caption_gear.get("gear_combo") and not result.get("gear_combo"):
        result["gear_combo"] = caption_gear["gear_combo"]
    return caption_gear


def _merge_text_fields(
    result: dict[str, Any],
    text_result: dict[str, Any],
) -> None:
    for field_name in [
        "camera_body",
        "camera_brand",
        "viltrox_lens",
        "other_lens",
        "flash",
        "adapter",
        "gear_combo",
    ]:
        if not result.get(field_name) and text_result.get(field_name):
            result[field_name] = text_result[field_name]
    for field_name in [
        "content_genre",
        "content_topic",
        "content_summary",
        "production_quality",
        "audience_fit",
        "content_types",
        "notes",
    ]:
        if not result.get(field_name) and text_result.get(field_name):
            result[field_name] = text_result[field_name]


def _merge_text_quality(
    result: dict[str, Any],
    text_result: dict[str, Any],
    *,
    dependencies: SmartAnalysisDependencies,
) -> None:
    for field_name in [
        "quality_scores",
        "quality_overall",
        "quality_summary",
        "reference_value",
        "reference_reasons",
        "improvements",
        "marketing_potential",
        "marketing_notes",
    ]:
        if not result.get(field_name) and text_result.get(field_name):
            result[field_name] = text_result[field_name]
        elif field_name == "improvements" and not result.get(field_name):
            result[field_name] = text_result.get(field_name, [])
    text_scores = text_result.get("quality_scores", {})
    result_scores = result.get("quality_scores", {})
    if text_scores and len(text_scores) > len(result_scores):
        result["quality_scores"] = text_scores
    if result.get("quality_scores"):
        weighted = dependencies.compute_weighted_scores(
            result["quality_scores"],
            result.get("content_genre", ""),
        )
        result["tech_score"] = weighted["tech_score"]
        result["marketing_score"] = weighted["marketing_score"]
        result["quality_overall"] = (
            weighted.get(
                "quality_overall",
                weighted.get("weighted_overall", 0),
            )
            or result.get("quality_overall", 0)
        )


def _merge_text_lists(
    result: dict[str, Any],
    text_result: dict[str, Any],
) -> None:
    for field_name in [
        "competitor_brands",
        "competitor_products",
        "brand_elements",
    ]:
        source_items = text_result.get(field_name, [])
        if isinstance(source_items, list):
            existing = result.get(field_name, [])
            for item in source_items:
                if item and item not in existing:
                    existing.append(item)
            result[field_name] = existing


def _apply_text_result(
    result: dict[str, Any],
    text_result: dict[str, Any],
    *,
    dependencies: SmartAnalysisDependencies,
) -> None:
    result["layers_used"].append("text_claude")
    _merge_text_fields(result, text_result)
    _merge_text_quality(
        result,
        text_result,
        dependencies=dependencies,
    )
    _merge_text_lists(result, text_result)


def _apply_text_layer(
    result: dict[str, Any],
    *,
    url: str,
    title: str,
    caption: str,
    scraped_text: str,
    platform: str,
    dependencies: SmartAnalysisDependencies,
) -> None:
    needs_gear = not result.get("camera_body") or not result.get("viltrox_lens")
    needs_content = not result.get("content_summary") or not result.get("content_genre")
    needs_quality = True
    if not (needs_gear or needs_content or needs_quality):
        return
    dependencies.logger.info(
        "smart analysis | layer 3 text parse | gear=%s | content=%s | quality=%s",
        needs_gear,
        needs_content,
        needs_quality,
    )
    all_caption_text = " ".join(
        filter(None, [title, caption, scraped_text])
    )
    caption_gear = _apply_caption_gear(
        result,
        dependencies.parse_gear_from_caption(all_caption_text),
        dependencies=dependencies,
    )
    if not (
        needs_content
        or needs_quality
        or (needs_gear and not caption_gear.get("viltrox_lens"))
    ):
        return
    text_result = dependencies.analyze_text_content(
        title,
        caption,
        url,
        platform,
        scraped_text,
        og_image="",
    )
    if text_result:
        _apply_text_result(
            result,
            text_result,
            dependencies=dependencies,
        )


def _log_completion(
    result: dict[str, Any],
    *,
    dependencies: SmartAnalysisDependencies,
) -> None:
    dependencies.logger.info(
        "smart analysis done | layers=%s | viltrox=%s | camera=%s | qs=%s | tech=%s | mkt=%s",
        result["layers_used"],
        result.get("viltrox_lens"),
        result.get("camera_body"),
        (
            f"yes({len(result.get('quality_scores', {}))}dims)"
            if result.get("quality_scores")
            else "MISSING"
        ),
        result.get("tech_score", 0),
        result.get("marketing_score", 0),
    )


async def analyze_url_content_smart_impl(
    url: str,
    title: str,
    caption: str,
    scraped_text: str,
    og_image: str,
    platform: str,
    creator_handle: str,
    direct_video_url: str,
    *,
    dependencies: SmartAnalysisDependencies,
) -> dict[str, Any]:
    result = dependencies.initial_smart_result()
    if not dependencies.anthropic_available and not dependencies.gemini_available:
        return result
    profile_hint = _creator_profile_hint(
        creator_handle,
        dependencies=dependencies,
    )
    _apply_gpt_prefilter(
        result,
        title=title,
        caption=caption,
        platform=platform,
        dependencies=dependencies,
    )
    await _apply_youtube_gemini(
        result,
        url=url,
        title=title,
        platform=platform,
        creator_handle=creator_handle,
        dependencies=dependencies,
    )
    _apply_image_layer(
        result,
        url=url,
        title=title,
        og_image=og_image,
        platform=platform,
        profile_hint=profile_hint,
        dependencies=dependencies,
    )
    await _apply_video_layer(
        result,
        url=url,
        title=title,
        platform=platform,
        creator_handle=creator_handle,
        direct_video_url=direct_video_url,
        dependencies=dependencies,
    )
    _apply_text_layer(
        result,
        url=url,
        title=title,
        caption=caption,
        scraped_text=scraped_text,
        platform=platform,
        dependencies=dependencies,
    )
    _log_completion(result, dependencies=dependencies)
    return result


__all__ = ["SmartAnalysisDependencies", "analyze_url_content_smart_impl"]
