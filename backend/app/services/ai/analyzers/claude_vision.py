"""
services/ai/analyzers/claude_vision.py — Claude Vision 视频帧 + 图片批量分析
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import asyncio
import urllib.request
from typing import Dict, List, Any
from pathlib import Path

# ── 第三方库 ──
try:
    import anthropic
except ImportError:
    pass

# ── 业务依赖 ──
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client as _gemini_client

try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE
from app.core.constants import VILTROX_CATALOG_PROMPT, USER_AGENT
from app.core.config import ANTHROPIC_API_KEY
from app.core.logging import get_logger
from app.services.scoring.creator import get_creator_profile
from app.services.scoring.core import compute_weighted_scores
from app.services.scraping.ytdlp import download_video_ytdlp, fetch_youtube_subtitles, YTDLP_AVAILABLE
from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption
from app.services.ai.analyzers.gemini_video import analyze_youtube_with_gemini
from app.services.ai.analyzers.claude_text import analyze_text_content
from app.services.ai.retry import call_ai_with_retry
from app.services.audit.similarity import parse_gear_from_caption
from app.services.media.frames import extract_video_frames_with_ts

FRAMES_DIR = Path("uploads")
logger = get_logger(__name__)


def _download_direct_video_url(video_url: str, output_dir: str) -> dict:
    """Download a platform-provided direct MP4/play URL for Gemini/Claude analysis."""
    result = {"success": False, "path": None, "duration": 0, "error": None, "platform": "direct"}
    clean_url = str(video_url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        result["error"] = "direct video url missing"
        return result
    try:
        out_path = Path(output_dir) / "direct_video.mp4"
        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
            },
        )
        max_bytes = 500 * 1024 * 1024
        read_bytes = 0
        with urllib.request.urlopen(req, timeout=60) as resp, open(out_path, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > max_bytes:
                    result["error"] = "direct video exceeds 500MB"
                    return result
                fh.write(chunk)
        if not out_path.exists() or out_path.stat().st_size <= 0:
            result["error"] = "direct video download produced empty file"
            return result
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(out_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        try:
            result["duration"] = float(json.loads(probe.stdout)["format"]["duration"])
        except Exception:
            pass
        result["success"] = True
        result["path"] = str(out_path)
        return result
    except Exception as exc:
        result["error"] = f"direct video download failed: {str(exc)[:200]}"
        return result


def _build_anthropic_client():
    if not ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        return None
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
# build_improvement_context
def build_improvement_context(creator_handle: str, current_scores: dict, content_genre: str) -> str:
    """
    Build rich context for improvement suggestions:
    - Creator's historical weak areas
    - Current video score breakdown
    - Video-type specific weights
    - Score gap analysis
    """
    # ── Video type specific priorities ──
    VIDEO_TYPE_FOCUS = {
        "review":     {"hook": "钩子（前15秒必须抓住观众）", "storytelling": "叙事结构（问题->测试->结论）", "viltrox_branding": "品牌露出"},
        "tutorial":   {"hook": "开场吸引力", "storytelling": "步骤清晰度", "editing": "剪辑节奏（不能拖沓）"},
        "cinematic":  {"composition": "构图与美学", "color_grade": "调色风格", "lighting": "打光层次"},
        "vlog":       {"hook": "前5秒留存率", "storytelling": "故事感", "editing": "剪辑流畅度"},
        "comparison": {"viltrox_branding": "品牌公平曝光", "storytelling": "对比逻辑清晰", "hook": "对比结论吸引力"},
        "unboxing":   {"viltrox_branding": "产品特写质量", "composition": "拍摄角度", "lighting": "产品打光"},
        "portrait":   {"composition": "构图与人像美感", "lighting": "人像打光", "color_grade": "肤色调色"},
        "bts":        {"storytelling": "幕后故事感", "editing": "节奏与氛围", "viltrox_branding": "器材使用展示"},
    }
    genre_key = (content_genre or "").lower().split("/")[0].strip()
    type_focus = VIDEO_TYPE_FOCUS.get(genre_key, {})

    # ── Creator history context ──
    history_ctx = ""
    if creator_handle:
        profile = get_creator_profile(creator_handle)
        weak = profile.get("weak_areas", [])
        avg  = profile.get("avg_scores", {})
        count = profile.get("submission_count", 0)
        if count >= 2 and weak:
            history_ctx = f"\n创作者历史弱项（{count}次投稿平均）: {', '.join(weak)}"
            if avg:
                low_items = {k: v for k, v in avg.items() if 0 < v < 7.5}
                if low_items:
                    history_ctx += f"\n  具体分数: " + ", ".join(f"{k}={v}" for k,v in sorted(low_items.items(), key=lambda x: x[1]))

    # ── Current video score gap analysis ──
    score_ctx = ""
    if current_scores:
        low_scores = {k: v for k, v in current_scores.items() if isinstance(v, (int, float)) and 0 < v < 8}
        if low_scores:
            sorted_low = sorted(low_scores.items(), key=lambda x: x[1])
            score_ctx = "\n本次视频评分明细（低于8分项目）: " + ", ".join(f"{k}={v}" for k,v in sorted_low)

    # ── Type-specific instruction ──
    type_ctx = ""
    if type_focus:
        type_ctx = f"\n视频类型「{genre_key}」最关键维度: " + ", ".join(f"{v}" for v in type_focus.values())

    return f"""
=== 改进建议上下文 ===
视频类型: {content_genre or '未知'}{type_ctx}{score_ctx}{history_ctx}

改进建议要求（严格执行）:
1. 只针对评分低于8分的维度给建议，不要重复说好的地方
2. 每条建议必须引用具体时间点（如「02:30处」）或具体画面描述
3. 说清楚「问题是什么」再给「解决方案」，不是泛泛的建议
4. 根据视频类型决定优先级：{genre_key}类视频最重要的是{list(type_focus.values())[0] if type_focus else '整体质量'}
5. 改进建议必须可执行，避免「增加品牌露出」「加强叙事」这种空话
6. 预期效果要量化（如「叙事分可从6->8」）
7. 控制在4-6条建议，宁少勿滥

改进建议格式（JSON，全中文）:
{{"area": "叙事", "priority": "high", "timestamp": "02:30", "problem": "直接跳入产品特写，缺少使用场景引入", "suggestion": "在开头30秒加入手动镜头失焦的痛点场景，用挫败感引入NexusFocus的解决方案", "expected_improvement": "叙事分6->8，前30秒留存率预计+15%"}}
"""


# analyze_video_with_claude
def analyze_video_with_claude(video_path: str, filename: str, creator_handle: str = "") -> dict:
    """
    Extract frames from video and use Claude Vision to detect Viltrox brand.
    Returns structured analysis result that feeds into scoring.
    """
    result = {
        "analyzed": False,
        "frames_checked": 0,
        "viltrox_detected": False,
        "confidence": "none",
        "logo_visible": False,
        "product_visible": False,
        "brand_elements": [],
        "products_detected": [],
        "content_types": [],
        "camera_gear_present": False,
        "notes": "",
        "brand_score_bonus": 0,
        "method": "none",
        "error": None,
    }

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
                lambda: _client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=2000,
                    messages=[{"role":"user","content":_content}],
                ),
            )
            raw = resp.content[0].text.strip()
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
                dense_frames = []
                with tempfile.TemporaryDirectory() as td:
                    subprocess.run(
                        ["ffmpeg", "-i", video_path, "-t", "5",
                         "-vf", "fps=4,scale=1280:-1",
                         "-frames:v", "8", "-q:v", "2",
                         os.path.join(td, "dense_%03d.jpg")],
                        capture_output=True, timeout=30
                    )
                    for fn in sorted(os.listdir(td)):
                        if fn.endswith(".jpg"):
                            with open(os.path.join(td, fn), "rb") as f:
                                dense_frames.append(base64.b64encode(f.read()).decode())
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
                    best_b64 = frames_b64[0]  # First frame = most likely product b-roll
                    best_frame_path = FRAMES_DIR / f"best_{os.path.basename(video_path)}.jpg"
                    with open(best_frame_path, "wb") as bf:
                        bf.write(base64.b64decode(best_b64))
                    result["best_frame_path"] = str(best_frame_path)
                except Exception as fe:
                    logger.warning("frame save error: %s", fe)
    
    except Exception as e:
        logger.exception("vision analysis failed: %s", e)

    return result
# ──────────────────────────────────────────────


# _analyze_images_batch
def _analyze_images_batch(images_b64: list, title: str, platform: str, profile_hint: str = "") -> dict:
    """Analyze a batch of images (carousel/gallery) with Claude Vision.
    Each image gets individual composition + gear analysis."""
    if not images_b64 or not ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        return {}
    try:
        client = _build_anthropic_client()
        if client is None:
            return {}
        content = []
        for i, b64 in enumerate(images_b64[:10]):
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": b64
            }})
            content.append({"type": "text", "text": f"[图片 {i+1}/{len(images_b64)}]"})

        # Build per-image analysis request
        per_image_schema = ""
        for i in range(min(len(images_b64), 10)):
            per_image_schema += (
                f'{{"image_index":{i+1},'
                f'"composition":"构图描述（中文，e.g.三分构图/中心对称/对角线）",'
                f'"lighting":"布光描述（中文，e.g.自然光/单点闪光/影棚三点光）",'
                f'"camera_body":"机身型号或null",'
                f'"lens":"镜头型号或null",'
                f'"viltrox_visible":true/false,'
                f'"viltrox_product":"Viltrox产品名或null",'
                f'"key_elements":"画面主要元素（中文）",'
                f'"quality_note":"画质和美学亮点（中文）"}},'
            )

        content.append({"type": "text", "text": (
            f"这是来自 {platform} 的 {len(images_b64)} 张图片，标题: \"{title}\"。\n"
            + profile_hint + "\n\n"
            "你是 Viltrox 品牌情报分析师。请逐张分析每张图片的构图和器材。\n\n"
            "重点识别:\n"
            "- VILTROX文字/唯卓仕/橙色圆环/VCM/APO/LAB/Pro/EVO/AIR/EPIC/LUNA/RAZE\n"
            "- 相机机身logo和外形\n"
            "- 竞品镜头品牌 (Sigma/Tamron/Zeiss/Sony GM/Canon L等)\n"
            "- 闪光灯/灯光 (Godox/Profoto/Aputure)\n"
            "- 配件 (SmallRig/Tilta/DJI云台/Atomos监视器)\n\n"
            "返回 JSON (只返回JSON，不要markdown):\n{"
            '"viltrox_detected":true/false,'
            '"confidence":"high/medium/low/none",'
            '"logo_visible":true/false,'
            '"product_visible":true/false,'
            '"camera_gear_present":true/false,'
            '"camera_body":"型号或null",'
            '"camera_brand":"Sony/Canon/Nikon/Fujifilm/ARRI/Blackmagic/RED/DJI/Other/null",'
            '"viltrox_lens":"如 AF 85mm F1.4 Pro VCM 或 null",'
            '"viltrox_products_all":["所有可见Viltrox产品"],'
            '"other_lens":"品牌+型号或null",'
            '"flash":"品牌+型号或null",'
            '"adapter":"品牌+型号或null",'
            '"accessories":["列表"],'
            '"gear_combo":"相机+镜头组合",'
            '"brand_elements":["具体Viltrox证据"],'
            '"products_detected":["Viltrox产品"],'
            '"competitor_products":[{"brand":"Sigma","model":"35mm Art","context":"对比"}],'
            '"competitor_brands":["列表"],'
            '"brand_integration_depth":"incidental/featured/central/exclusive",'
            '"content_genre":"review/tutorial/cinematic/vlog/bts/portrait/street/unboxing/comparison",'
            '"content_topic":"内容主题（英文）",'
            '"content_summary":"2-3句中文内容简介",'
            '"production_quality":"amateur/semi-pro/professional/broadcast",'
            '"audience_fit":"poor/fair/good/excellent",'
            '"content_types":["列表"],'
            '"negative_signals":[],'
            f'"per_image_analysis":[{per_image_schema[:-1]}],'
            '"quality_scores":{"exposure":7,"focus":7,"stability":8,"color_grade":7,"composition":7,"lighting":6,"editing":7,"storytelling":5,"hook":6,"viltrox_branding":8},'
            '"quality_overall":7,'
            '"quality_summary":"2句中文总结图片质量亮点和不足",'
            '"reference_value":"high/medium/low",'
            '"reference_reasons":["中文说明"],'
            '"improvements":[{"area":"构图","priority":"medium","timestamp":"图片1","problem":"具体问题","suggestion":"具体建议（中文）","expected_improvement":"预期效果"}],'
            '"marketing_potential":"high/medium/low",'
            '"marketing_notes":"中文：是否能转化观众购买Viltrox",'
            '"needs_manual_review":false,'
            '"manual_review_reason":null,'
            '"notes":"English: all gear + content description"'
            "}"
        )})

        resp = call_ai_with_retry(
            "claude_vision.image_batch",
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": content}],
            ),
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        if not raw.endswith('}'): raw = raw[:raw.rfind('}')+1] if '}' in raw else raw+'}'
        parsed = json.loads(raw)
        logger.info(
            "image vision | viltrox=%s | confidence=%s | lens=%s",
            parsed.get("viltrox_detected"),
            parsed.get("confidence"),
            parsed.get("viltrox_lens"),
        )
        return parsed
    except Exception as e:
        logger.warning("image vision error: %s", e)
        return {}


# _merge_analysis
def _merge_analysis(target: dict, source: dict):
    """Merge source analysis into target, preferring higher confidence values."""
    # Confidence ranking — Vision results override GPT text hints
    conf_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
    source_conf = conf_rank.get(source.get("confidence", "none"), 0)
    target_conf = conf_rank.get(target.get("confidence", "none"), 0)

    gear_fields = ["camera_body", "camera_brand", "viltrox_lens", "other_lens",
                   "flash", "adapter", "gear_combo"]
    for f in gear_fields:
        src_val = source.get(f)
        tgt_val = target.get(f)
        # Always prefer Vision result (higher confidence) over GPT hint
        if src_val and (not tgt_val or source_conf > target_conf):
            target[f] = src_val

    simple_fields = [
        "brand_integration_depth", "content_genre", "content_topic", "content_summary",
        "production_quality", "audience_fit", "originality",
        "confidence", "logo_visible", "product_visible",
        "needs_manual_review", "manual_review_reason", "notes",
        "quality_overall", "quality_summary",
        "reference_value", "marketing_potential", "marketing_notes",
    ]
    for f in simple_fields:
        if source.get(f) and not target.get(f):
            target[f] = source[f]

    # Dict fields (quality_scores)
    if source.get("quality_scores") and not target.get("quality_scores"):
        target["quality_scores"] = source["quality_scores"]

    # Per-image analysis (images only)
    if source.get("per_image_analysis") and not target.get("per_image_analysis"):
        target["per_image_analysis"] = source["per_image_analysis"]

    # Merge lists (deduplicate)
    list_fields = [
        "accessories", "brand_elements", "products_detected",
        "viltrox_products_all", "competitor_brands",
        "competitor_products", "content_types", "negative_signals",
        "reference_reasons", "improvements", "timestamps",
    ]
    for f in list_fields:
        src_list = source.get(f, [])
        if isinstance(src_list, str):
            try:
                src_list = json.loads(src_list)
            except Exception:
                src_list = []
        if not isinstance(src_list, list): src_list = []
        existing = target.get(f, [])
        if not isinstance(existing, list): existing = []
        for item in src_list:
            if item and item not in existing:
                existing.append(item)
        target[f] = existing

    # viltrox_detected: OR logic
    if source.get("viltrox_detected"):
        target["viltrox_detected"] = True



# analyze_url_content_smart
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
    result = {
        "analyzed": False, "method": "none",
        "camera_body": None, "camera_brand": None,
        "viltrox_lens": None, "other_lens": None,
        "flash": None, "adapter": None,
        "accessories": [], "gear_combo": "",
        "brand_elements": [], "products_detected": [],
        "viltrox_products_all": [], "competitor_products": [],
        "competitor_brands": [], "content_genre": "",
        "content_topic": "", "content_summary": "",
        "production_quality": "", "audience_fit": "",
        "content_types": [], "notes": "",
        "layers_used": [], "error": None,
        # Quality fields
        "quality_scores": {}, "quality_overall": 0,
        "quality_summary": "", "reference_value": "",
        "reference_reasons": [], "improvements": [],
        "marketing_potential": "", "marketing_notes": "",
        "timestamps": [], "video_source": "",
    }

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
                gemini_ok = False
                if GEMINI_AVAILABLE:
                    try:
                        logger.info("smart analysis | layer 2 Gemini File API | platform=%s", platform)
                        def _upload_local():
                            return _gemini_client.files.upload(
                                file=video_path,
                                config={"mime_type": "video/mp4"}
                            )
                        gfile = await asyncio.to_thread(_upload_local)

                        # Wait for ACTIVE
                        import time
                        for _ in range(20):
                            def _chk(f=gfile):
                                return _gemini_client.files.get(name=f.name)
                            gfile = await asyncio.to_thread(_chk)
                            if gfile.state.name == "ACTIVE":
                                break
                            await asyncio.sleep(3)

                        if gfile.state.name == "ACTIVE":
                            # Reuse the same Gemini prompt
                            subtitle_raw = fetch_youtube_subtitles(url) if "youtube" in url.lower() else ""
                            subtitle_ctx = (
                                "\n\n=== 字幕时间轴 ===\n" + subtitle_raw + "\n=== 字幕结束 ===\n"
                                "timestamps 必须来自字幕真实时间，禁止等间隔填写。"
                                if subtitle_raw else ""
                            )
                            profile_ctx = ""
                            if creator_handle:
                                prof = get_creator_profile(creator_handle)
                                if prof.get("viltrox_lenses"):
                                    profile_ctx = f"\n创作者历史使用过: {', '.join(prof['viltrox_lenses'][:3])}"

                            # Build prompt inline (same structure as YouTube prompt)
                            local_prompt = f"""你是 Viltrox 品牌内容分析师。仔细观看这个完整视频。{profile_ctx}{subtitle_ctx}
平台: {platform} | 标题: {title or url}

第一步识别内容类型: review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts
第二步按类型标准评估，禁止生成 00:00/00:05 等间隔时间戳。

评分标准（严格）：9-10分=TOP 5-10%；8分=良好(25%)；7分=普通(30%)；6分=有缺陷(20%)；5分以下=严重问题。

只返回JSON，不含Markdown:
{{
  "content_genre": "review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts",
  "content_type_cn": "类型中文名",
  "content_summary": "3句中文：类型+内容+亮点",
  "production_quality": "amateur/semi-pro/professional/broadcast",
  "vertical_category": "wedding/food/lifestyle/review/cinematic/sports/travel/portrait/tutorial/commercial",
  "viltrox_detected": true,
  "viltrox_products_mentioned": ["精确型号"],
  "camera_body": "型号或null",
  "viltrox_lens": "型号或null",
  "other_lens": "型号或null",
  "timestamps": [
    {{"time": "MM:SS", "event": "中文具体事件", "type": "viltrox/competitor/camera/key_moment/intro/conclusion"}}
  ],
  "brand_exposure_detail": {{
    "logo_on_lens_barrel": false,
    "logo_on_screen_overlay": false,
    "logo_in_thumbnail": false,
    "product_closeup_count": 0,
    "brand_mention_count": 0,
    "product_screen_time_pct": 0,
    "notes": "中文说明"
  }},
  "quality_scores": {{
    "exposure": 7, "stability": 7, "color_grade": 6, "composition": 7,
    "lighting": 6, "editing": 7, "storytelling": 6, "hook": 7,
    "viltrox_branding": 7, "logo_visibility": 6, "product_screen_time": 6,
    "close_up_quality": 7, "audience_fit": 7, "authenticity": 7, "conclusion_strength": 6
  }},
  "quality_overall": 7,
  "quality_summary": "2句中文：品牌曝光亮点+故事说服力不足",
  "marketing_potential": "high/medium/low",
  "marketing_notes": "转化分析（中文）",
  "reference_value": "high/medium/low",
  "improvements": [
    {{"area": "品牌曝光", "priority": "high", "timestamp": "00:05", "problem": "具体问题", "suggestion": "具体方案（中文）", "expected_improvement": "预期效果"}}
  ]
}}"""

                            MODELS = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"]
                            for model_name in MODELS:
                                try:
                                    def _analyze(m=model_name, f=gfile):
                                        return _gemini_client.models.generate_content(
                                            model=m,
                                            contents=[
                                                genai_types.Part.from_uri(
                                                    file_uri=f.uri,
                                                    mime_type="video/mp4"
                                                ),
                                                local_prompt
                                            ]
                                        )
                                    resp = await asyncio.to_thread(_analyze)
                                    raw = resp.text.strip()
                                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                                    parsed = json.loads(raw)

                                    # Merge into result (same fields as YouTube Gemini)
                                    for field in ["content_genre","content_type_cn","content_summary",
                                                  "production_quality","vertical_category","marketing_potential",
                                                  "marketing_notes","reference_value"]:
                                        if parsed.get(field):
                                            result[field] = parsed[field]
                                    if parsed.get("viltrox_products_mentioned"):
                                        result["viltrox_products_all"] = parsed["viltrox_products_mentioned"]
                                    for f in ["camera_body","viltrox_lens","other_lens"]:
                                        if parsed.get(f) and not result.get(f):
                                            result[f] = parsed[f]
                                    if parsed.get("timestamps"):
                                        result["timestamps"] = parsed["timestamps"]
                                    bed = parsed.get("brand_exposure_detail", {})
                                    result["logo_detected"]         = int(bool(bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")))
                                    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
                                    result["brand_mention_count"]   = bed.get("brand_mention_count", 0)
                                    result["brand_exposure_detail"] = bed
                                    qs = {k: v for k, v in parsed.get("quality_scores", {}).items()
                                          if isinstance(v, (int, float)) and v > 0}
                                    if qs:
                                        result["quality_scores"]   = qs
                                        result["quality_overall"]  = parsed.get("quality_overall", 0)
                                        result["quality_summary"]  = parsed.get("quality_summary", "")
                                        result["improvements"]     = parsed.get("improvements", [])
                                    # Compute three-axis
                                    ws = compute_weighted_scores(
                                        result.get("quality_scores", {}),
                                        result.get("content_genre", ""),
                                        result.get("vertical_category", "")
                                    )
                                    result["brand_exposure_score"] = ws["brand_exposure_score"]
                                    result["storytelling_score"]   = ws["storytelling_score"]
                                    result["tech_status"]          = ws["tech_floor"]["status"]
                                    result["tech_floor"]           = ws["tech_floor"]
                                    result["tech_score"]           = ws["tech_score"]
                                    result["marketing_score"]      = ws["marketing_score"]
                                    result["analyzed"]             = True
                                    result["method"]               = f"gemini_fileapi_{platform}_{model_name}"
                                    result["layers_used"].append(f"gemini_{model_name}")
                                    gemini_ok = True
                                    logger.info(
                                        "smart analysis | layer 2 Gemini ok | model=%s | brand=%s | story=%s | tech_floor=%s | qs=%s",
                                        model_name,
                                        ws["brand_exposure_score"],
                                        ws["storytelling_score"],
                                        ws["tech_floor"]["status"],
                                        f"yes({len(qs)}dims)" if qs else "no",
                                    )
                                    break
                                except Exception as e:
                                    logger.warning("smart analysis | layer 2 Gemini failed | model=%s | error=%s", model_name, str(e)[:60])
                                    continue

                        # Cleanup Gemini file
                        try:
                            await asyncio.to_thread(lambda f=gfile: _gemini_client.files.delete(name=f.name))
                        except Exception:
                            pass

                    except Exception as e:
                        logger.warning("smart analysis | layer 2 Gemini upload error: %s", e)

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
                except Exception:
                    pass
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


# fetch_all_images_from_post
def fetch_all_images_from_post(url: str, og_image: str = "") -> list[str]:
    """
    Fetch ALL images from a multi-image post (Instagram carousel, Reddit gallery, etc.)
    Returns list of base64-encoded image strings.
    """
    images_b64 = []

    # Try yt-dlp to get all images (it supports image galleries too)
    if YTDLP_AVAILABLE:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                cmd = [
                    "yt-dlp",
                    "--no-playlist",
                    "-f", "jpg/png/webp/best",
                    "--write-thumbnail",
                    "--skip-video-download",
                    "-o", os.path.join(tmpdir, "img_%(autonumber)s.%(ext)s"),
                    "--no-warnings", "--quiet",
                    url
                ]
                cookie_file = Path("cookies.txt")
                if cookie_file.exists():
                    cmd += ["--cookies", str(cookie_file)]

                subprocess.run(cmd, capture_output=True, timeout=30)

                # Collect all images
                for fname in sorted(os.listdir(tmpdir)):
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        fpath = os.path.join(tmpdir, fname)
                        if os.path.getsize(fpath) > 5000:  # skip tiny files
                            with open(fpath, "rb") as f:
                                images_b64.append(base64.b64encode(f.read()).decode())
                            if len(images_b64) >= 10:  # max 10 images
                                break
        except Exception as e:
            logger.warning("yt-dlp images fetch error: %s", e)

    # Fallback: use og_image
    if not images_b64 and og_image:
        try:
            import urllib.request
            req = urllib.request.Request(og_image, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            if len(data) > 5000:
                images_b64.append(base64.b64encode(data).decode())
        except Exception:
            pass

    return images_b64
