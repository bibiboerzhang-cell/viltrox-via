from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any
from pathlib import Path

from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE

from app.services.ai.clients.openai_client import OPENAI_AVAILABLE
from app.core.constants import VILTROX_CATALOG_PROMPT
from app.core.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.core.logging import get_logger
from app.platform import llm_production
from app.services.ai.analyzers.anthropic_response_text import text_blocks_joined
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.core import compute_weighted_scores
from app.services.scraping.ytdlp import download_video_ytdlp, YTDLP_AVAILABLE
from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption
from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
from app.services.ai.analyzers.claude_text import analyze_text_content
from app.services.ai.retry import call_ai_with_retry
from app.services.audit.similarity import parse_gear_from_caption
from app.services.media.frames import extract_video_frames_with_ts
from app.services.ai.analyzers.claude_vision_client import _build_anthropic_client
from app.services.ai.analyzers.claude_vision_context import build_improvement_context
from app.services.ai.analyzers.claude_vision_defaults import initial_smart_result, initial_video_result
from app.services.ai.analyzers.claude_vision_images import _analyze_images_batch
from app.services.ai.analyzers.claude_vision_local_gemini import (  # noqa: F401 - LOCAL_FILE_GEMINI_MODEL re-export
    LOCAL_FILE_GEMINI_MODEL,
    analyze_local_video_with_gemini_file_api,
)
from app.services.ai.analyzers.claude_vision_media import _download_direct_video_url, extract_dense_start_frames, fetch_all_images_from_post, save_best_frame
from app.services.ai.analyzers.claude_vision_merge import _merge_analysis

FRAMES_DIR = Path("uploads")
logger = get_logger(__name__)
# LOCAL_FILE_GEMINI_MODEL 现由 claude_vision_local_gemini 持有(上面 import 即 re-export,保旧引用)。


def analyze_video_with_claude(video_path: str, filename: str, creator_handle: str = "") -> dict:
    """
    Extract frames from video and use Claude Vision to detect Viltrox brand.
    Returns structured analysis result that feeds into scoring.
    """
    result = initial_video_result()

    if not video_path or not os.path.exists(video_path):
        result["error"] = "Video file not found on disk"
        return result

    # ── Step 1: Extract frames with timestamps ──
    frames_with_ts = extract_video_frames_with_ts(video_path, max_frames=6)
    frames_b64 = [f for f, _ in frames_with_ts]
    frame_times = [t for _, t in frames_with_ts]
    result["frames_checked"] = len(frames_b64)

    if not frames_b64:
        result["error"] = "No frames extracted — ffmpeg may not be installed (brew install ffmpeg)"
        result["method"] = "filename_only"
        fn_lower = filename.lower()
        if "viltrox" in fn_lower:
            result["viltrox_detected"] = True
            result["confidence"] = "low"
            result["brand_elements"] = ["Filename contains 'viltrox'"]
            result["brand_score_bonus"] = 8
        return result

    # Build frame timestamp hint for Claude
    def fmt_ts(secs: float) -> str:
        m = int(secs) // 60
        s = int(secs) % 60
        return f"{m:02d}:{s:02d}"

    frame_ts_hint = "FRAME TIMESTAMPS — 每帧对应视频中的真实时间点，请为每个有意义的帧都输出时间戳:\n"
    for i, t in enumerate(frame_times[:20]):
        frame_ts_hint += f"  Frame {i+1} -> {fmt_ts(t)}\n"

    # ── Step 2: Claude Vision analysis ──
    if not ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        result["error"] = (
            "anthropic not installed — run: pip install anthropic --break-system-packages"
            if not ANTHROPIC_AVAILABLE
            else "ANTHROPIC_API_KEY missing"
        )
        result["method"] = "unavailable"
        return result

    def run_claude_pass(frame_list, extra_hint=""):
        """Single Claude Vision pass. Returns parsed analysis dict or None."""
        try:
            _client = _build_anthropic_client()
            if _client is None:
                raise RuntimeError("ANTHROPIC_API_KEY missing")
            _content = []
            for b64 in frame_list[:16]:  # Send up to 16 frames
                _content.append({"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":b64}})
            hint = f"\n\nEXTRA HINT: {extra_hint}" if extra_hint else ""
            _content.append({"type":"text","text":(
                f"These are {len(frame_list)} frames from a video named \"{filename}\".\n"
                f"{frame_ts_hint}\n"
                "TIMESTAMP RULE: Only output timestamps when something MEANINGFUL changes — "
                "new scene, new gear shown, competitor appears, key demo moment. "
                "Skip frames that look identical to the previous one. Target 5-12 timestamps total.\n"
                "You are a senior brand intelligence analyst for Viltrox camera lens company.\n"
                "Your job: identify EVERY piece of camera gear in this video — Viltrox products AND competitors.\n"
                "Study every frame carefully. Even 1-second appearances matter.\n\n"

                "=== STEP 1: CAMERA BODY ===\n"
                "SONY (α logo, red rec button): FX3=small silver box no EVF, FX6=larger+fan grill+ND, "
                "FX9=shoulder style, FX30=APS-C small, A7 IV=33MP hybrid, A7R V=61MP, "
                "A7S III=12MP low light+EVF hump, A7C II=rangefinder compact, ZV-E1=vlog no hump\n"
                "CANON (red circle logo): R5/R5C=45MP+top LCD, R6 II=24MP no top LCD, "
                "R3=vertical grip built-in, C70=cinema box RF mount, C300III=shoulder rig\n"
                "NIKON (yellow N): Z9=vertical grip integrated, Z8=pro no grip, Z6III=24MP, Z5II=entry FF\n"
                "FUJIFILM (retro dials): X-T5=SLR retro 40MP, X-H2=modern large, X-H2S=speed, "
                "GFX=medium format larger body\n"
                "CINEMA: ARRI ALEXA 35/Mini(orange logo+cinema box), RED KOMODO(tiny red box), "
                "Blackmagic BMPCC 4K/6K(black box+5in screen)\n"
                "DJI: DJI Ronin 4D(integrated gimbal+camera body+monitor on top, X9 sensor, modular), "
                "DJI Inspire 3(aerial drone with Zenmuse X9-8K camera, full-frame, integrated to drone body, "
                "uses DL mount lenses, gray/dark drone form factor), "
                "DJI Zenmuse X9(gimbal camera unit, full-frame, used on Inspire 3 / Ronin 4D)\n\n"

                "DJI DL MOUNT INDICATORS — When you see Inspire 3 drone, Ronin 4D, or Zenmuse X9, "
                "the lens is almost certainly a DL-mount lens. Viltrox DL lenses include: "
                "AF 90mm F3.5 DL (compact telephoto for portrait/macro on aerial), "
                "and the Raze AF Lens Set (16/20/24/35/50/75/100mm DL primes for cinema work).\n\n"

                f"{VILTROX_CATALOG_PROMPT}\n\n"

                "CRITICAL DISTINCTION:\n"
                "  AIR 25mm F1.7 = mirrorless mount (E/XF), autofocus, small size\n"
                "  EPIC 25mm T2.0 = PL mount, manual focus, cinema rig, anamorphic\n"
                "  If camera is ARRI/RED/Blackmagic cinema + PL mount -> EPIC or LUNA\n\n"

                "=== STEP 3: COMPETITOR PRODUCTS — full catalog ===\n"
                "List EVERY competitor brand and product visible:\n"
                "LENSES: Sigma (Art/Sport/Contemporary), Tamron (SP/Di III), Zeiss (Otus/Milvus/Batis), "
                "Samyang/Rokinon, 7Artisans, TTArtisan, Meike, Yongnuo, Tokina, "
                "Sony G Master/G lens, Canon L/RF lens, Nikon S-Line/Z lens, "
                "Leica SL/M lens, Voigtlander, Meyer Optik, DZO (Vespid/Catta)\n"
                "FLASH/LIGHTING: Godox, Profoto, Westcott, Aputure, Nanlite, Zhiyun Molus\n"
                "ACCESSORIES: SmallRig, Tilta, SHAPE, Wooden Camera, Zacuto, Follow Fox\n"
                "MONITOR: SmallHD, Atomos Ninja/Shogun, Blackmagic Video Assist\n"
                "STABILIZER: DJI RS3/RS4/RS4 Pro, Zhiyun Crane/Weebill, Moza\n"
                "For each competitor product: note brand + model + context "
                "(comparison/side-by-side/standalone/incidental)\n\n"

                "=== STEP 4: CONTENT INTELLIGENCE ===\n"
                "- What is the video demonstrating? Portrait/street/travel/product/wedding/event/nature\n"
                "- Is this a review (talking to camera about gear)? Tutorial? Pure cinematic?\n"
                "- Is Viltrox the hero product, or just used incidentally?\n"
                "- Any negative comments about Viltrox or positive about competitors?\n\n"

                "=== STEP 5: VIDEO QUALITY ASSESSMENT ===\n"
                "Evaluate the video across these dimensions (score each 1-10):\n"
                "TECHNICAL QUALITY:\n"
                "  - Exposure: Is it well exposed? Blown highlights or crushed blacks?\n"
                "  - Focus: Is the subject sharp? Any focus hunting or missed focus?\n"
                "  - Stability: Handheld shake, gimbal wobble, or locked-off tripod?\n"
                "  - Color grade: Flat/log, basic correction, or professional grade?\n"
                "  - Audio (if applicable): Background noise, clear dialogue, music mix?\n"
                "CREATIVE QUALITY:\n"
                "  - Composition: Rule of thirds, leading lines, framing quality?\n"
                "  - Lighting: Natural/available, basic setup, or professional lighting?\n"
                "  - Editing rhythm: Cuts on beat, smooth transitions, pacing?\n"
                "  - Storytelling: Does it have a clear narrative arc or just B-roll dump?\n"
                "  - Hook/Retention: Would viewers keep watching past 3 seconds?\n"
                "VILTROX BRAND VALUE:\n"
                "  - How clearly is the Viltrox product featured?\n"
                "  - Does the video make Viltrox look good/professional?\n"
                "  - Would this video convert viewers to buy Viltrox?\n\n"

                "=== STEP 6: IMPROVEMENT SUGGESTIONS ===\n"
                + build_improvement_context(
                    creator_handle,
                    {},  # scores not available at prompt time; Claude will self-reference Step 5 scores
                    ""   # genre not yet known; Claude will use what it detected in Step 4
                ) +
                "\n\nBased on your Step 5 quality scores and Step 4 content analysis above, "
                "generate targeted improvement suggestions in Chinese. "
                "Reference specific timestamps or scenes you observed in the frames.\n\n"
                + hint +
                "\n\nRespond ONLY valid compact JSON (no markdown):\n{"
                '"viltrox_detected":true/false,'
                '"confidence":"high/medium/low/none",'
                '"logo_visible":true/false,'
                '"product_visible":true/false,'
                '"camera_gear_present":true/false,'
                '"camera_body":"exact model or null",'
                '"camera_brand":"Sony/Canon/Nikon/Fujifilm/ARRI/Blackmagic/RED/DJI/Other/null",'
                '"viltrox_lens":"primary Viltrox lens e.g. AF 85mm F1.4 Pro VCM or null",'
                '"viltrox_products_all":["ALL Viltrox items: lenses+flash+monitor+adapter+NexusFocus"],'
                '"other_lens":"primary non-Viltrox lens or null",'
                '"flash":"flash/strobe brand+model or null",'
                '"adapter":"adapter brand+model or null",'
                '"accessories":["cage","gimbal","monitor","matte box"],'
                '"gear_combo":"e.g. Sony FX3 + Viltrox 85mm F1.4 Pro + NexusFocus F1",'
                '"brand_elements":["specific evidence of Viltrox brand"],'
                '"products_detected":["all Viltrox products"],'
                '"competitor_products":[{"brand":"Sigma","model":"35mm F1.4 Art","context":"side-by-side comparison"}],'
                '"competitor_brands":["Sigma","Godox"],'
                '"brand_integration_depth":"incidental/featured/central/exclusive",'
                '"content_genre":"review/tutorial/cinematic/vlog/bts/portrait/street/event/comparison/unboxing",'
                '"content_topic":"one sentence what this video is about",'
                '"content_summary":"2-3句中文：内容主题、使用器材、画面风格与亮点",'
                '"production_quality":"amateur/semi-pro/professional/broadcast",'
                '"audience_fit":"poor/fair/good/excellent",'
                '"originality":"original/likely-repost/compilation",'
                '"negative_signals":["any criticism of Viltrox or praise of competitors"],'
                '"content_types":["cinematic","review","comparison"],'
                '"timestamps":['
                '{"time":"00:12","event":"Viltrox展台全景，多款镜头陈列","type":"viltrox"},'
                '{"time":"00:45","event":"EPIC 50mm T2特写，卡口清晰","type":"viltrox"},'
                '{"time":"01:20","event":"Sony A7RIV机身出现","type":"camera"},'
                '{"time":"02:10","event":"竞品Sigma 35mm出现","type":"competitor"},'
                '{"time":"03:30","event":"实拍演示，散景效果","type":"key_moment"}],'
                '"quality_scores":{'
                '"exposure":8,"focus":7,"stability":9,"color_grade":8,"composition":7,'
                '"lighting":6,"editing":8,"storytelling":5,"hook":7,"viltrox_branding":9},'
                '"quality_overall":1-10,'
                '"quality_summary":"2句中文总结视频质量亮点和不足",'
                '"reference_value":"high/medium/low",'
                '"reference_reasons":["中文说明为何有/没有参考价值"],'
                '"improvements":['
                '{"area":"叙事","priority":"high","timestamp":"02:30","problem":"具体描述在哪里出了什么问题","suggestion":"具体可执行的改进建议（中文，针对这个视频）","expected_improvement":"预期分数或效果提升"},'
                '{"area":"品牌露出","priority":"medium","timestamp":"05:12","problem":"Viltrox logo仅出现1次且角度模糊","suggestion":"在开箱或特写镜头时停留3秒以上展示镜头桶身文字","expected_improvement":"品牌露出分8->9"}],'
                '"marketing_potential":"high/medium/low",'
                '"marketing_notes":"中文：这个视频是否能转化观众购买Viltrox？为什么？",'
                '"needs_manual_review":false,'
                '"manual_review_reason":null,'
                '"notes":"Expert English: gear + quality + content + competitive context"'
                "}"
            )})
            resp = call_ai_with_retry(
                "claude_vision.video_frames",
                lambda: None,
                attempt_fn=lambda attempt, total: llm_production.generate_anthropic_messages(
                    client=_client,
                    messages=[{"role":"user","content":_content}],
                    model=CLAUDE_MODEL,
                    purpose="audit_vision_fallback",
                    max_output_tokens=2000,
                    cost_tag="cron:audit_vision_fallback",
                    metadata={
                        "task_binding": "audit_vision_fallback",
                        "surface": "audit_pipeline",
                        "phase": "vision_fallback",
                        "subphase": "video_frames",
                        "attempt_index": attempt,
                        "attempt_total": total,
                        "target_type": "video_file",
                        "target_id": str(filename or video_path)[:160],
                        "handle": str(creator_handle or "")[:80],
                        "target_label": str(filename or "video frames")[:160],
                    },
                ),
            )
            raw = text_blocks_joined(resp).strip()
            raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
            # Handle truncated JSON gracefully
            if not raw.endswith('}'):
                raw = raw[:raw.rfind('}')+1] if '}' in raw else raw + '}'
            parsed = json.loads(raw)
            logger.info(
                "vision pass | viltrox=%s | confidence=%s | lens=%s",
                parsed.get("viltrox_detected"),
                parsed.get("confidence"),
                parsed.get("viltrox_lens"),
            )
            return parsed
        except Exception as e:
            logger.warning("vision pass error: %s", e)
            return None

    try:    
        # ── Pass 1: First 8 frames (early b-roll heavy) ──
        logger.info("vision analysis start | frames=%s | file=%s", len(frames_b64), filename)
        analysis = run_claude_pass(frames_b64[:8])
        logger.info("vision pass 1 result: %s", analysis.get("viltrox_detected") if analysis else "FAILED")
    
        # ── Pass 2: Retry with remaining frames if not detected ──
        if analysis and not analysis.get("viltrox_detected") and len(frames_b64) > 8:
            logger.info("vision pass 1 no detection — retrying with frames 8-12")
            analysis2 = run_claude_pass(
                frames_b64[8:],
                extra_hint="The lens may only appear briefly. Look for lens barrel text like '50mm 1:1.4 VCM', '35mm F1.2', 'CLICK OFF/ON' switch, orange ring, or any camera gear close-up."
            )
            if analysis2 and analysis2.get("viltrox_detected"):
                analysis = analysis2  # Use the successful pass
    
        # ── Pass 3: Dense first-3s if still not found ──
        if analysis and not analysis.get("viltrox_detected"):
            try:
                dense_frames = extract_dense_start_frames(video_path)
                if dense_frames:
                    logger.info("vision pass 3 dense retry | frames=%s", len(dense_frames))
                    analysis3 = run_claude_pass(
                        dense_frames,
                        extra_hint="These are HIGH DENSITY frames from the FIRST 5 SECONDS. Look for lens close-ups, gear b-roll, any brand markings."
                    )
                    if analysis3 and analysis3.get("viltrox_detected"):
                        analysis = analysis3
            except Exception as e:
                logger.warning("vision pass 3 error: %s", e)
    
        if not analysis:
            raise ValueError("All Claude Vision passes failed")
    
        result["analyzed"] = True
        result["method"] = "claude_vision"
        result["viltrox_detected"]         = bool(analysis.get("viltrox_detected", False))
        result["confidence"]               = analysis.get("confidence", "none")
        result["logo_visible"]             = bool(analysis.get("logo_visible", False))
        result["product_visible"]          = bool(analysis.get("product_visible", False))
        result["camera_gear_present"]      = bool(analysis.get("camera_gear_present", False))
        result["camera_body"]              = analysis.get("camera_body")
        result["camera_brand"]             = analysis.get("camera_brand")
        result["viltrox_lens"]             = analysis.get("viltrox_lens")
        result["other_lens"]               = analysis.get("other_lens")
        result["flash"]                    = analysis.get("flash")
        result["adapter"]                  = analysis.get("adapter")
        result["accessories"]              = analysis.get("accessories", [])
        result["gear_combo"]               = analysis.get("gear_combo", "")
        result["brand_elements"]           = analysis.get("brand_elements", [])
        result["products_detected"]        = analysis.get("products_detected", [])
        result["viltrox_products_all"]     = analysis.get("viltrox_products_all", [])
        result["competitor_products"]      = analysis.get("competitor_products", [])
        result["brand_integration_depth"]  = analysis.get("brand_integration_depth", "incidental")
        result["content_genre"]            = analysis.get("content_genre", "")
        result["content_topic"]            = analysis.get("content_topic", "")
        result["content_summary"]          = analysis.get("content_summary", "")
        result["production_quality"]       = analysis.get("production_quality", "")
        result["editing_style"]            = analysis.get("editing_style", "")
        result["audience_fit"]             = analysis.get("audience_fit", "")
        result["originality"]              = analysis.get("originality", "original")
        result["competitor_brands"]        = analysis.get("competitor_brands", [])
        result["negative_signals"]         = analysis.get("negative_signals", [])
        result["shooting_style"]           = analysis.get("shooting_style")
        result["content_types"]            = analysis.get("content_types", [])
        result["needs_manual_review"]      = bool(analysis.get("needs_manual_review", False))
        result["manual_review_reason"]     = analysis.get("manual_review_reason")
        result["notes"]                    = analysis.get("notes", "")
        result["quality_scores"]           = analysis.get("quality_scores", {})
        result["quality_overall"]          = analysis.get("quality_overall", 0)
        result["quality_summary"]          = analysis.get("quality_summary", "")
        result["reference_value"]          = analysis.get("reference_value", "")
        result["reference_reasons"]        = analysis.get("reference_reasons", [])
        result["improvements"]             = analysis.get("improvements", [])
        result["marketing_potential"]      = analysis.get("marketing_potential", "")
        result["marketing_notes"]          = analysis.get("marketing_notes", "")
        # ── Compute weighted scores by genre ──
        ws = compute_weighted_scores(
            result.get("quality_scores", {}),
            result.get("content_genre", "")
        )
        result["tech_score"]      = ws["tech_score"]
        result["marketing_score"] = ws["marketing_score"]
        result["quality_overall"] = ws.get("quality_overall", ws.get("weighted_overall", 0))
        result["timestamps"]               = analysis.get("timestamps", [])
    
        if result["originality"] in ("likely-repost", "compilation", "screen-recording"):
            result["needs_manual_review"] = True
            result["manual_review_reason"] = f"Originality: {result['originality']}"
        if result["negative_signals"]:
            result["needs_manual_review"] = True
            result["manual_review_reason"] = (result["manual_review_reason"] or "") +             f" | Negative: {', '.join(result['negative_signals'][:3])}"
    
        if result["viltrox_detected"]:
            bonus = {"high": 45, "medium": 28, "low": 12}.get(result["confidence"], 0)
            if result["logo_visible"]:    bonus += 10
            if result["product_visible"]: bonus += 8
            if result["brand_integration_depth"] == "central":  bonus += 15
            if result["brand_integration_depth"] == "exclusive": bonus += 25
            result["brand_score_bonus"] = bonus

            # ── Save best frame (Viltrox visible) for thumbnail display ──
            # Pick the first frame with product detected (usually opening b-roll)
            if frames_b64:
                try:
                    result["best_frame_path"] = save_best_frame(frames_b64[0], video_path, FRAMES_DIR)
                except Exception as fe:
                    logger.warning("frame save error: %s", fe)
    
    except Exception as e:
        logger.exception("vision analysis failed: %s", e)

    return result
async def analyze_url_content_smart(
    url: str, title: str, caption: str,
    scraped_text: str, og_image: str, platform: str,
    creator_handle: str = "",
    direct_video_url: str = "",
) -> dict:
    """
    Smart multi-layer content analysis for URL submissions:
    Layer 1: All images (carousels, galleries) -> Vision
    Layer 2: yt-dlp video download -> frame analysis
    Layer 3: Text fallback
    Returns merged analysis result.
    """
    result = initial_smart_result()

    if not ANTHROPIC_AVAILABLE and not GEMINI_AVAILABLE:
        return result

    # ── Get creator profile for context ──
    profile = get_creator_profile(creator_handle) if creator_handle else {}
    profile_hint = ""
    if profile.get("cameras"):
        profile_hint = f"\nCREATOR HISTORY: Known to use {', '.join(profile['cameras'][:2])}. "
    if profile.get("viltrox_lenses"):
        profile_hint += f"Known Viltrox lenses: {', '.join(profile['viltrox_lenses'][:3])}."

    # ── GPT Pre-filter (runs first, extremely cheap) ──
    gpt_result = {}
    if OPENAI_AVAILABLE:
        logger.info("smart analysis | GPT pre-filter caption analysis")
        gpt_result = gpt_prefilter_caption(title, caption, platform)
        # GPT fills in gear hints but NEVER skips full analysis
        # Content summary / quality scores / improvements always need Claude/Gemini
        if gpt_result.get("camera_body"):
            result["camera_body"] = gpt_result["camera_body"]
        if gpt_result.get("viltrox_lens") and not result.get("viltrox_lens"):
            result["viltrox_lens"] = gpt_result["viltrox_lens"]
            result["analyzed"] = True
            result["brand_elements"].append(f"GPT caption: {result['viltrox_lens']}")
        if gpt_result.get("other_lens") and not result.get("other_lens"):
            result["other_lens"] = gpt_result["other_lens"]
        if gpt_result.get("content_genre") and not result.get("content_genre"):
            result["content_genre"] = gpt_result["content_genre"]
        result["layers_used"].append("gpt_prefilter")
        logger.info(
            "smart analysis | GPT hint | viltrox=%s | confidence=%s",
            gpt_result.get("viltrox_lens"),
            gpt_result.get("confidence"),
        )

    # ── Gemini Layer 0: YouTube direct read (fastest, no download) ──
    if platform == "YouTube" and GEMINI_AVAILABLE and url:
        logger.info("smart analysis | Gemini layer 0 — YouTube direct read")
        gemini_result = await analyze_youtube_with_gemini(url, title, creator_handle)
        if gemini_result.get("analyzed"):
            result["layers_used"].append("gemini_youtube")
            _merge_analysis(result, gemini_result)
            result["analyzed"] = True
            result["method"] = "gemini_youtube"
            if gemini_result.get("timestamps"):
                result["timestamps"] = gemini_result["timestamps"]
            # If Gemini got full gear + content, skip yt-dlp download only
            # but ALWAYS continue to Claude for quality scores + improvements
            if result.get("viltrox_lens") and result.get("camera_body"):
                logger.info("smart analysis | Gemini got full gear — skipping yt-dlp, running Claude scoring")
            else:
                logger.info("smart analysis | Gemini partial — continuing to Claude for gear confirmation")

    # ── Layer 1: Fetch all images from post ──
    logger.info("smart analysis | layer 1 image fetch | platform=%s", platform)
    all_images = fetch_all_images_from_post(url, og_image)
    logger.info("smart analysis | got %s images", len(all_images))

    if all_images:
        result["layers_used"].append(f"images({len(all_images)})")
        # Analyze all images with Vision
        img_analysis = _analyze_images_batch(all_images, title, platform, profile_hint)
        if img_analysis:
            _merge_analysis(result, img_analysis)
            result["analyzed"] = True
            result["method"] = f"image_vision_{len(all_images)}imgs"

    # ── Layer 2: yt-dlp video download ──
    # For non-YouTube platforms with video content (Instagram/TikTok/Facebook/etc),
    # ALWAYS download and analyze video frames. Text/image analysis is too unreliable
    # for accurate gear identification. Only skip if Layer 0 (Gemini YouTube) already
    # got a definitive read.
    has_video_platform = platform in (
        "Instagram", "TikTok", "Douyin", "Facebook", "Bilibili", "Xiaohongshu", "Reddit", "Unknown"
    )
    gemini_youtube_complete = (
        platform == "YouTube"
        and result.get("viltrox_lens")
        and result.get("camera_body")
        and result.get("quality_scores")
    )
    should_download = (
        YTDLP_AVAILABLE
        and not gemini_youtube_complete
        and (
            has_video_platform
            or not result.get("viltrox_lens")
            or not result.get("camera_body")
            or not result.get("quality_scores")
            or result.get("confidence") in ("low", "none", None)
        )
    )

    if should_download:
        logger.info("smart analysis | layer 2 yt-dlp | platform=%s", platform)
        with tempfile.TemporaryDirectory() as tmpdir:
            dl = _download_direct_video_url(direct_video_url, tmpdir) if direct_video_url else {"success": False, "path": None, "duration": 0, "error": "direct video url missing"}
            if dl.get("success"):
                result["video_source"] = "direct_url"
                logger.info("smart analysis | layer 2 direct video url | platform=%s", platform)
            else:
                if direct_video_url:
                    logger.warning("smart analysis | direct video failed: %s", dl.get("error"))
                    result["layers_used"].append("direct_video_failed")
                dl = download_video_ytdlp(url, tmpdir)
                if dl.get("success"):
                    result["video_source"] = "ytdlp"
            if dl["success"] and dl["path"]:
                result["layers_used"].append(f"video({dl['duration']:.0f}s)")
                video_path = dl["path"]

                # ── Route: Gemini File API (preferred for all platforms) ──
                # 2026-08-23 C3:本地文件梯子抽到 claude_vision_local_gemini,并经
                # llm_production 严格边界(任务绑定 local_file_video)。
                gemini_ok = False
                if GEMINI_AVAILABLE:
                    gemini_ok = await analyze_local_video_with_gemini_file_api(
                        video_path=video_path,
                        url=url,
                        title=title,
                        platform=platform,
                        creator_handle=creator_handle,
                        duration_seconds=dl.get("duration"),
                        result=result,
                    )

                # ── Fallback: Claude frame analysis if Gemini failed ──
                if not gemini_ok:
                    logger.info("smart analysis | layer 2 Claude frame fallback")
                    video_analysis = analyze_video_with_claude(
                        video_path, title or url, creator_handle=creator_handle
                    )
                    if video_analysis and video_analysis.get("analyzed"):
                        _merge_analysis(result, video_analysis)
                        result["analyzed"] = True
                        result["method"] = f"ytdlp_claude_{platform}"

                try:
                    os.unlink(video_path)
                except OSError as exc:
                    logger.debug("temporary video cleanup failed: %s", exc)
            else:
                logger.warning("smart analysis | yt-dlp failed: %s", dl.get("error"))
                result["layers_used"].append("ytdlp_failed")

    # ── Layer 3: Text analysis — always run for quality scores ──
    needs_gear = not result.get("camera_body") or not result.get("viltrox_lens")
    needs_content = not result.get("content_summary") or not result.get("content_genre")
    needs_quality = True  # Always generate quality scores on first submission

    if needs_gear or needs_content or needs_quality:
        logger.info(
            "smart analysis | layer 3 text parse | gear=%s | content=%s | quality=%s",
            needs_gear,
            needs_content,
            needs_quality,
        )

        # Fast caption parser first (no API call)
        all_caption_text = " ".join(filter(None, [title, caption, scraped_text]))
        caption_gear = parse_gear_from_caption(all_caption_text)
        if not isinstance(caption_gear, dict):
            caption_gear = {}
        if isinstance(caption_gear, dict) and caption_gear.get("camera_body") and isinstance(result, dict) and not result.get("camera_body"):
            result["camera_body"] = caption_gear["camera_body"]
            result["camera_brand"] = caption_gear["camera_brand"]
            result["layers_used"].append("caption_parser")
            logger.info("caption parse | camera=%s", result["camera_body"])
        if caption_gear.get("viltrox_lens") and not result.get("viltrox_lens"):
            result["viltrox_lens"] = caption_gear["viltrox_lens"]
            result["analyzed"] = True
            if caption_gear["viltrox_lens"] not in result.get("brand_elements", []):
                result.setdefault("brand_elements", []).append(f"Caption: {caption_gear['viltrox_lens']}")
            logger.info("caption parse | viltrox_lens=%s", result["viltrox_lens"])
        if caption_gear.get("other_lens") and not result.get("other_lens"):
            result["other_lens"] = caption_gear["other_lens"]
        if caption_gear.get("gear_combo") and not result.get("gear_combo"):
            result["gear_combo"] = caption_gear["gear_combo"]

        # Claude text analysis — always run if quality scores or improvements missing
        if needs_content or needs_quality or (needs_gear and not caption_gear.get("viltrox_lens")):
            text_result = analyze_text_content(
                title, caption, url, platform, scraped_text, og_image=""
            )
            if text_result:
                result["layers_used"].append("text_claude")
                for field in ["camera_body", "camera_brand", "viltrox_lens",
                              "other_lens", "flash", "adapter", "gear_combo"]:
                    if not result.get(field) and text_result.get(field):
                        result[field] = text_result[field]
                for field in ["content_genre", "content_topic", "content_summary",
                              "production_quality", "audience_fit", "content_types", "notes"]:
                    if not result.get(field) and text_result.get(field):
                        result[field] = text_result[field]
                # Quality fields: only fill if Gemini didn't already provide them
                for field in ["quality_scores", "quality_overall", "quality_summary",
                              "reference_value", "reference_reasons",
                              "improvements", "marketing_potential", "marketing_notes"]:
                    if not result.get(field) and text_result.get(field):
                        result[field] = text_result[field]
                    elif field == "improvements" and not result.get(field):
                        result[field] = text_result.get(field, [])
                # If text analysis gave better/more quality_scores, use them
                ts_qs = text_result.get("quality_scores", {})
                rs_qs = result.get("quality_scores", {})
                if ts_qs and len(ts_qs) > len(rs_qs):
                    result["quality_scores"] = ts_qs
                # Recompute weighted scores after all layers merged
                if result.get("quality_scores"):
                    ws = compute_weighted_scores(
                        result["quality_scores"],
                        result.get("content_genre", "")
                    )
                    result["tech_score"]      = ws["tech_score"]
                    result["marketing_score"] = ws["marketing_score"]
                    result["quality_overall"] = ws.get("quality_overall", ws.get("weighted_overall", 0)) or result.get("quality_overall", 0)
                for field in ["competitor_brands", "competitor_products", "brand_elements"]:
                    src = text_result.get(field, [])
                    if isinstance(src, list):
                        existing = result.get(field, [])
                        for item in src:
                            if item and item not in existing:
                                existing.append(item)
                        result[field] = existing

    logger.info(
        "smart analysis done | layers=%s | viltrox=%s | camera=%s | qs=%s | tech=%s | mkt=%s",
        result["layers_used"],
        result.get("viltrox_lens"),
        result.get("camera_body"),
        f"yes({len(result.get('quality_scores', {}))}dims)" if result.get("quality_scores") else "MISSING",
        result.get("tech_score", 0),
        result.get("marketing_score", 0),
    )
    return result
