"""
services/scraping/ytdlp.py — yt-dlp 视频下载 + 字幕获取
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scoring.verticals import apply_learned_weights
from app.services.scoring.creator import get_creator_profile
from app.services.scraping.ytdlp_media import _fetch_fresh_metrics_ytdlp, download_video_ytdlp

try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

_YTDLP_BIN = shutil.which("yt-dlp")
if not _YTDLP_BIN:
    candidate = Path(sys.executable).with_name("yt-dlp")
    if candidate.exists():
        _YTDLP_BIN = str(candidate)

YTDLP_BIN = _YTDLP_BIN or "yt-dlp"
YTDLP_AVAILABLE = _YTDLP_BIN is not None
logger = get_logger(__name__)
if not YTDLP_AVAILABLE:
    logger.warning("ytdlp_binary_missing")


def _proxy_host_port(proxy_url: str) -> str:
    parsed = urlparse(str(proxy_url or ""))
    host = parsed.hostname or ""
    if not host:
        return "configured"
    return f"{host}:{parsed.port}" if parsed.port else host


YTDLP_PROXY: str = os.getenv("YTDLP_PROXY", "")
if YTDLP_PROXY:
    logger.info("ytdlp_proxy_enabled", extra={"proxy_host": _proxy_host_port(YTDLP_PROXY)})

GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)

# ──────────────────────────────────────────────
# YouTube subtitle fetcher (yt-dlp)
# ──────────────────────────────────────────────
def _subtitle_timeout_seconds() -> int:
    try:
        value = int(os.getenv("YTDLP_SUBTITLE_TIMEOUT_SECONDS", "30") or "30")
    except ValueError:
        value = 30
    return max(1, min(120, value))


def _run_ytdlp_subtitle_cmd(cmd: list[str], *, timeout_seconds: int) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        _stdout, stderr = proc.communicate()
        logger.warning(
            "youtube_subtitles_timeout_fallback",
            extra={"timeout_seconds": timeout_seconds, "stderr_tail": stderr.decode(errors="ignore")[-300:]},
        )
        return -1, stderr.decode(errors="ignore")
    return int(proc.returncode or 0), stderr.decode(errors="ignore")


def fetch_youtube_subtitles(url: str, max_chars: int = 6000) -> str:
    """
    Download auto-generated subtitles via yt-dlp and return a clean
    timestamped transcript string for injection into AI prompts.
    Format: [00:05] text text text\\n[00:12] more text...
    Returns '' if subtitles unavailable or yt-dlp not installed.
    """
    if not YTDLP_AVAILABLE:
        return ""
    try:
        import tempfile, re as _re
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_path = os.path.join(tmpdir, "sub")
            timeout_seconds = _subtitle_timeout_seconds()
            cmd = [
                YTDLP_BIN,
                "--write-auto-sub",
                "--sub-lang", "en,zh-Hans,zh-Hant,zh",
                "--sub-format", "vtt",
                "--skip-download",
                "--no-playlist",
                "-o", sub_path,
                "--quiet",
                url,
            ]
            logger.info("youtube_subtitles_start", extra={"timeout_seconds": timeout_seconds})
            returncode, stderr_text = _run_ytdlp_subtitle_cmd(cmd, timeout_seconds=timeout_seconds)
            if returncode < 0:
                return ""
            if returncode != 0:
                logger.warning(
                    "youtube_subtitles_command_warning",
                    extra={"returncode": returncode, "stderr_tail": stderr_text[-300:]},
                )

            # Find the downloaded vtt file
            vtt_file = None
            for f in os.listdir(tmpdir):
                if f.endswith(".vtt"):
                    vtt_file = os.path.join(tmpdir, f)
                    break
            if not vtt_file:
                logger.info("youtube_subtitles_empty_fallback", extra={"reason": "no_vtt_file"})
                return ""

            raw = open(vtt_file, encoding="utf-8", errors="ignore").read()

            # Parse VTT -> list of (seconds, text)
            entries = []
            blocks = raw.split("\n\n")
            for block in blocks:
                lines = block.strip().splitlines()
                # Find timestamp line: 00:00:05.000 --> 00:00:08.000
                ts_line = next((l for l in lines if "-->" in l), None)
                if not ts_line:
                    continue
                # Parse start time
                start_str = ts_line.split("-->")[0].strip().split(" ")[0]
                parts = start_str.replace(",", ".").split(":")
                try:
                    if len(parts) == 3:
                        secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        secs = int(parts[0]) * 60 + float(parts[1])
                    else:
                        continue
                except ValueError:
                    continue
                # Collect text lines (skip cue settings, position tags)
                text_lines = []
                for l in lines:
                    if "-->" in l or l.strip().isdigit() or l.startswith("WEBVTT"):
                        continue
                    clean = _re.sub(r"<[^>]+>", "", l).strip()
                    if clean:
                        text_lines.append(clean)
                text = " ".join(text_lines).strip()
                if text:
                    entries.append((secs, text))

            if not entries:
                logger.info("youtube_subtitles_empty_fallback", extra={"reason": "no_entries"})
                return ""

            # De-duplicate consecutive identical lines (VTT often repeats)
            deduped = [entries[0]]
            for e in entries[1:]:
                if e[1] != deduped[-1][1]:
                    deduped.append(e)

            # Format as timestamped lines
            def _fmt(s):
                m = int(s) // 60
                sec = int(s) % 60
                return f"{m:02d}:{sec:02d}"

            lines_out = [f"[{_fmt(s)}] {t}" for s, t in deduped]
            result = "\n".join(lines_out)
            # Trim to max_chars from the start (most important info)
            if len(result) > max_chars:
                result = result[:max_chars] + "\n...[字幕截断]"
            logger.info("youtube_subtitles_loaded", extra={"lines": len(deduped), "chars": len(result)})
            return result
    except Exception as e:
        logger.warning("youtube_subtitles_error", extra={"error": str(e)})
        return ""


# ──────────────────────────────────────────────
# Gemini — YouTube 直读分析
# ──────────────────────────────────────────────
async def analyze_youtube_with_gemini(url: str, title: str, creator_handle: str = "") -> dict:
    """
    Gemini YouTube analysis via File API:
    1. Download first 2min with yt-dlp
    2. Upload to Gemini File API
    3. Analyze with gemini-2.5-flash / gemini-2.5-pro (frame by frame)
    4. Delete file from Gemini
    """
    result = {
        "analyzed": False, "method": "gemini_youtube",
        "content_summary": "", "content_genre": "", "content_topic": "",
        "timestamps": [], "competitor_mentions": [],
        "why_compelling": "", "hook_analysis": "",
        "target_audience": "", "production_quality": "",
        "camera_body": None, "viltrox_lens": None, "other_lens": None,
        "viltrox_detected": False, "viltrox_products_all": [],
        "marketing_potential": "", "marketing_notes": "",
        "error": None,
    }
    if not GEMINI_AVAILABLE or not _gemini_client:
        result["error"] = "Gemini not available"
        return result
    if not YTDLP_AVAILABLE:
        result["error"] = "yt-dlp not available for download"
        return result

    profile_ctx = ""
    if creator_handle:
        profile = get_creator_profile(creator_handle)
        if profile.get("viltrox_lenses"):
            profile_ctx = f"\n创作者历史使用过: {', '.join(profile['viltrox_lenses'][:3])}"

    # ── Fetch subtitles for precise timestamp anchoring ──
    subtitle_ctx = ""
    subtitle_raw = fetch_youtube_subtitles(url)
    if subtitle_raw:
        subtitle_ctx = (
            "\n\n=== 字幕时间轴（真实时间戳，优先用这个定位事件）===\n"
            + subtitle_raw
            + "\n=== 字幕结束 ===\n"
            "时间戳规则：timestamps 里的 time 字段必须来自上面字幕里的真实时间点，"
            "不允许猜测或等间隔填写。"
        )

    prompt = f"""你是 Viltrox (唯卓仕) 品牌资深内容分析师。请仔细观看这个完整视频的每一帧画面和音频。{profile_ctx}{subtitle_ctx}
视频标题: {title}

=== 第一步：识别内容类型 ===
从以下类型选最匹配的一个：
review（评测：口头介绍产品）/ cinematic（电影感纯拍摄）/ tutorial（教学操作）
comparison（多产品对比）/ vlog（生活记录）/ unboxing（开箱）/ showcase（样片展示）/ bts（幕后）

=== 第二步：按类型标准客观评估 ===
review类：口述质量、产品展示清晰度、结论可信度是核心
cinematic类：画质、色彩、构图、镜头语言是核心，不看口述
tutorial类：步骤清晰度、可重复性是核心
comparison类：对比公平性、样本质量是核心
vlog类：真实感、器材自然使用是核心

=== 第三步：竞品识别（全面扫描）===
仔细扫描整个视频，识别所有竞品品牌和产品：
- 画面中出现的品牌Logo（机身/镜头桶身/包装/贴纸）
- 口头提及的任何品牌名或型号
- 比较测试中出现的竞品
- 字幕/标题卡片中的品牌文字
重点关注：Sigma / Tamron / Sony GM / Canon L / Nikon Z / Zeiss / Tokina / Samyang
          Godox / Profoto / Nanlite / Aputure（闪光灯/灯光）
          任何其他相机镜头或摄影器材品牌
每个竞品必须记录：品牌+精确型号+出现方式+和Viltrox的对比结果

=== 第四步：强制惩罚项检测（优先于其他评分）===

【A. 分辨率检测】
- 视频分辨率低于1080p（480p/720p竖屏）：stability/exposure/color_grade/composition上限为6
- 480p及以下：所有技术维度上限为5，production_quality最高semi-pro
- 只有1080p及以上视频，技术分才能给7+

【B. 水视频检测（严重扣分）】
以下任意一条触发，storytelling和hook各-2分，marketing_potential自动降级：
- 视频超过60%时长是同一机位静止镜头循环
- 装饰性粒子/星星/闪光特效覆盖画面超过30%时长
- 镜像翻转文字等无意义视觉效果
- 同一B-roll素材出现3次以上

【C. 废话填充检测】
以下任意一条触发，hook和conclusion_strength各-1分：
- PPT风格幻灯片/图文卡片插入打断视频叙事流
- 前30秒无实质内容（纯logo动画/纯背景音乐/空镜）
- 视频超过3分钟但实质不同内容少于1分钟（大量重复素材填充）

=== 第五步：时间戳（严格要求）===
{('基于上方字幕的真实时间生成时间戳，每个事件必须能在字幕里找到对应句子。' if subtitle_raw else '基于你实际观看到的视频内容生成时间戳，根据画面和声音定位。')}
❌ 错误示例（绝对禁止）：
00:00 intro 开场
00:05 title 标题卡
00:10 presenter 主持人出现
00:15 title 标题卡2
00:20 explanation 讲解
00:25 analysis 分析
→ 这是等间隔垃圾时间戳，完全无用

✅ 正确示例（应该这样）：
00:12 Dustin手持 Viltrox 27mm F1.2 首次入画，镜头桶身 Logo 清晰可见
01:34 Sony GM 50mm F1.4 首次出现，开始双镜对比测试
03:47 Viltrox 在散景测试中明显胜出，Dustin 表示「这个价格无法被击败」
07:22 Sigma 35mm Art 加入三镜横评
12:05 室外对焦追焦测试，Viltrox 追焦速度对比 Sony GM
18:33 最终结论：Viltrox 性价比最高，适合预算有限摄影师
→ 每个时间戳都有实质内容，不可互换

规则：
- 长视频（>10分钟）生成 20-30 个时间戳
- 短视频（<3分钟）生成 8-12 个时间戳
- 每个时间戳必须描述该时刻「具体发生了什么」，不能用泛泛的词如「讲解」「分析」「介绍」
- 竞品首次出现必须记录
- Viltrox 产品每次特写必须记录
- 评测者说出关键结论的时刻必须记录

只返回 JSON，不包含 Markdown:
{{
  "content_genre": "review/cinematic/tutorial/comparison/vlog/unboxing/showcase/bts",
  "content_type_cn": "该类型的中文名称",
  "content_summary": "3-4句中文总结：第一句说明视频类型+核心内容，后面说具体亮点和不足",
  "content_topic": "English: one sentence topic",
  "production_quality": "amateur/semi-pro/professional/broadcast",
  "why_compelling": "这个视频最吸引人的地方，针对该类型具体说明（中文）",
  "hook_analysis": "前30秒的钩子设计分析（中文）",
  "target_audience": "目标受众：摄影水平、器材预算、内容偏好（中文）",
  "viltrox_detected": true,
  "viltrox_products_mentioned": ["精确型号，如 Viltrox AF 85mm F1.8 MF FE"],
  "camera_body": "精确机身型号或null",
  "viltrox_lens": "精确Viltrox镜头名称或null",
  "other_lens": "竞品镜头精确型号或null",
  "competitor_products": [
    {{
      "brand": "品牌名（Sigma/Tamron/Sony/Canon/Nikon/Zeiss/Tokina/Samyang/Rokinon/Godox/Profoto/Nanlite等）",
      "model": "精确型号（如 35mm F1.4 DG DN Art）",
      "category": "lens/flash/camera/accessory",
      "context": "head-to-head/three-way/briefly-shown/mentioned-only",
      "sentiment": "viltrox_wins/viltrox_loses/neutral/not_compared",
      "screen_time_seconds": 30,
      "first_appearance": "MM:SS"
    }}
  ],
  "competitor_brands": ["出现过的所有竞品品牌列表，只要画面或口头出现都要列出"],
  "timestamps": [
    {{"time": "MM:SS", "event": "中文：具体描述这个时间点的事件，不要泛泛", "type": "viltrox/competitor/camera/key_moment/intro/conclusion"}}
  ],
  "competitor_mentions": [
    {{"brand": "品牌", "model": "型号", "time": "MM:SS", "context": "具体提及场景", "sentiment": "positive/neutral/negative"}}
  ],
  "type_specific_notes": "在该垂类社区里，这个视频的水准如何？（中文，2句）",
  "marketing_potential": "high/medium/low",
  "marketing_notes": "为何适合或不适合推广Viltrox（中文）",
  "brand_integration_depth": "incidental/featured/central/exclusive",
  "vertical_category": "wedding/food/lifestyle/review/cinematic/sports/travel/portrait/tutorial/commercial",
  "vertical_quality_notes": "和同垂类最优秀内容相比差距在哪？（中文，2句）",
  "community_value": 7,
  "brand_exposure_detail": {{
    "logo_on_lens_barrel": true,
    "logo_on_screen_overlay": false,
    "logo_in_thumbnail": false,
    "product_closeup_count": 3,
    "brand_mention_count": 2,
    "product_screen_time_pct": 40,
    "notes": "中文：品牌曝光方式描述"
  }},
  "quality_scores": {{
    "=== 评分标准（必须严格执行）===": "10分=全球TOP5%示范案例(极稀少); 9分=专业出色(仅10%); 8分=良好(25%); 7分=普通够用(30%); 6分=明显缺陷(20%); 5分及以下=严重问题。【强制校准】480p视频技术分<=5; 720p<=6; 1080p+才能7+; 水视频/过度特效自动-2分; 素人视频应在5-7之间，专业制作才能到8+",
    "exposure": 7,
    "stability": 7,
    "color_grade": 6,
    "composition": 7,
    "lighting": 6,
    "editing": 7,
    "storytelling": 6,
    "hook": 7,
    "viltrox_branding": 7,
    "logo_visibility": 6,
    "product_screen_time": 6,
    "close_up_quality": 7,
    "audience_fit": 7,
    "authenticity": 7,
    "conclusion_strength": 6
  }},
  "quality_overall": 7,
  "quality_summary": "2句中文：第一句说品牌曝光亮点，第二句说故事说服力不足在哪",
  "reference_value": "high/medium/low",
  "reference_reasons": ["中文说明"],
  "improvements": [
    {{"area": "品牌曝光", "priority": "high", "timestamp": "02:30", "problem": "具体问题", "suggestion": "具体可执行方案（中文）", "expected_improvement": "预期效果"}}
  ]
}}"""

    gemini_file = None
    tmp_path = None

    # ── Model priority list (April 2026) ──────────────────────────────────────
    # gemini-3-flash-preview: shut down; gemini-3-pro-preview: shut down 2026-03-09
    # gemini-1.5-x: shut down 2025-04-29; gemini-2.0-flash: GA until 2026-06-01
    # Safe stable order: 2.5-flash first (best price/perf, video capable),
    # then 2.5-pro (slower/pricier but more accurate), then 2.0-flash as fallback.
    GEMINI_MODELS = [
        "gemini-2.5-flash",          # stable GA — best price/perf for video
        "gemini-2.5-pro",            # stable GA — most accurate, slower
        "gemini-2.0-flash",          # stable GA — fallback (shuts down 2026-06-01)
    ]

    try:
        # Step 1: Download FULL video at 720p. Keep this below the worker
        # subprocess timeout so failures report as a download timeout, not a
        # generic Gemini child-process kill.
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = os.path.join(tmpdir, "gemini_video.mp4")
            logger.info("gemini_fileapi_download_start", extra={"url": url})

            dl_cmd = [
                YTDLP_BIN,
                "-f", "best[ext=mp4][height<=720]/18/best[height<=720]/best",
                "--merge-output-format", "mp4",
                "-o", tmp_path,
                "--no-playlist",
                "--quiet",
            ]
            if YTDLP_PROXY:
                dl_cmd += ["--proxy", YTDLP_PROXY]
            dl_cmd.append(url)
            dl_proc = await asyncio.to_thread(
                lambda: subprocess.run(
                    dl_cmd,
                    capture_output=True,
                    timeout=GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS,
                )
            )
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 1000:
                result["error"] = "yt-dlp video download failed for Gemini analysis"
                logger.warning("gemini_fileapi_download_failed", extra={"url": url})
                return result

            file_size_mb = os.path.getsize(tmp_path) / 1024 / 1024
            logger.info("gemini_fileapi_upload_start", extra={"size_mb": round(file_size_mb, 1)})

            # Step 2: Upload to Gemini File API
            # BUG FIX: capture upload errors explicitly — a failed upload returns
            # an object whose .name may be None, causing files.get() to 404.
            try:
                def _upload():
                    return _gemini_client.files.upload(
                        file=tmp_path,
                        config={"mime_type": "video/mp4"}
                    )
                gemini_file = await asyncio.to_thread(_upload)
            except Exception as upload_err:
                result["error"] = f"Gemini File API upload failed: {upload_err}"
                logger.warning("gemini_fileapi_upload_failed", extra={"error": str(upload_err)})
                return result

            # Validate upload returned a usable file object
            if not gemini_file or not getattr(gemini_file, "name", None):
                result["error"] = "Gemini upload returned empty file object"
                logger.warning("gemini_fileapi_upload_invalid_file", extra={"file": str(gemini_file)})
                return result

            logger.info(
                "gemini_fileapi_upload_complete",
                extra={"file_name": gemini_file.name, "uri": gemini_file.uri},
            )

            # Step 3: Wait for file to be ACTIVE (usually 5-60 seconds for video)
            # BUG FIX 1: files.get() itself can throw 404 — wrap in try-except.
            # BUG FIX 2: Exit immediately on FAILED state instead of burning 60s.
            for poll_attempt in range(30):   # max 90s (30 × 3s)
                try:
                    def _check(name=gemini_file.name):
                        return _gemini_client.files.get(name=name)
                    polled = await asyncio.to_thread(_check)
                    gemini_file = polled
                except Exception as poll_err:
                    # 404 here means the file disappeared (upload may have silently failed)
                    result["error"] = f"files.get() 404 during polling — upload may have failed: {poll_err}"
                    logger.warning(
                        "gemini_fileapi_poll_error",
                        extra={"attempt": poll_attempt, "error": str(poll_err)},
                    )
                    return result

                state = getattr(gemini_file.state, "name", str(gemini_file.state))
                logger.info("gemini_fileapi_poll", extra={"attempt": poll_attempt + 1, "state": state})

                if state == "ACTIVE":
                    break
                if state == "FAILED":
                    # File processing failed on Google's side — no point waiting
                    result["error"] = f"Gemini file processing FAILED (state={state}). Try re-uploading."
                    logger.warning(
                        "gemini_fileapi_processing_failed",
                        extra={"attempt": poll_attempt + 1, "state": state},
                    )
                    return result
                await asyncio.sleep(3)
            else:
                result["error"] = f"Gemini file ACTIVE timeout after 90s (final state={state})"
                logger.warning("gemini_fileapi_poll_timeout", extra={"state": state})
                return result

            logger.info("gemini_fileapi_active", extra={"uri": gemini_file.uri})

            # BUG FIX 3: Validate uri before calling generate_content.
            # A file can be ACTIVE but have a malformed uri (edge case seen in SDK v0.8+).
            if not getattr(gemini_file, "uri", None):
                result["error"] = "Gemini file ACTIVE but uri is empty — cannot call generate_content"
                logger.warning("gemini_fileapi_empty_uri", extra={"file_name": gemini_file.name})
                return result

            # Step 4: Analyze with Gemini — try stable models in priority order
            # Capture the file name string for the finally-block delete guard
            _active_file_name = gemini_file.name
            MODELS = GEMINI_MODELS
            last_err = ""
            for model_name in MODELS:
                try:
                    def _analyze(m=model_name, f=gemini_file):
                        return _gemini_client.models.generate_content(
                            model=m,
                            contents=[
                                genai_types.Part.from_uri(
                                    file_uri=f.uri,
                                    mime_type="video/mp4"
                                ),
                                prompt
                            ]
                        )
                    resp = await asyncio.to_thread(_analyze)
                    raw = resp.text.strip()
                    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw)

                    result["analyzed"]             = True
                    result["method"]               = f"gemini_fileapi_{model_name}"
                    result["content_summary"]      = parsed.get("content_summary", "")
                    result["content_genre"]        = parsed.get("content_genre", "")
                    result["content_topic"]        = parsed.get("content_topic", "")
                    result["production_quality"]   = parsed.get("production_quality", "")
                    result["why_compelling"]       = parsed.get("why_compelling", "")
                    result["hook_analysis"]        = parsed.get("hook_analysis", "")
                    result["target_audience"]      = parsed.get("target_audience", "")
                    result["timestamps"]           = parsed.get("timestamps", [])
                    result["competitor_mentions"]  = parsed.get("competitor_mentions", [])
                    result["viltrox_detected"]     = parsed.get("viltrox_detected", False)
                    result["viltrox_products_all"] = parsed.get("viltrox_products_mentioned", [])
                    result["camera_body"]          = parsed.get("camera_body")
                    result["viltrox_lens"]         = parsed.get("viltrox_lens")
                    result["other_lens"]           = parsed.get("other_lens")
                    result["marketing_potential"]  = parsed.get("marketing_potential", "")
                    result["marketing_notes"]      = parsed.get("marketing_notes", "")
                    result["brand_integration_depth"] = parsed.get("brand_integration_depth", "")
                    result["type_specific_notes"]  = parsed.get("type_specific_notes", "")
                    # ── Vertical community fields ──
                    result["vertical_category"]      = parsed.get("vertical_category", "")
                    result["vertical_quality_notes"] = parsed.get("vertical_quality_notes", "")
                    result["community_value"]         = parsed.get("community_value", 0)
                    # ── Brand exposure detail ──
                    bed = parsed.get("brand_exposure_detail", {})
                    result["logo_detected"]         = int(bool(
                        bed.get("logo_on_lens_barrel") or bed.get("logo_on_screen_overlay")
                    ))
                    result["product_closeup_count"] = bed.get("product_closeup_count", 0)
                    result["brand_mention_count"]   = bed.get("brand_mention_count", 0)
                    result["brand_exposure_detail"] = bed
                    # ── Quality scores — strip instruction key ──
                    qs = parsed.get("quality_scores", {})
                    qs = {k: v for k, v in qs.items() if isinstance(v, (int, float)) and v > 0}
                    if qs:
                        result["quality_scores"]    = qs
                        result["quality_overall"]   = parsed.get("quality_overall", 0)
                        result["quality_summary"]   = parsed.get("quality_summary", "")
                        result["reference_value"]   = parsed.get("reference_value", "")
                        result["reference_reasons"] = parsed.get("reference_reasons", [])
                        result["improvements"]      = parsed.get("improvements", [])
                    # ── Compute three-axis scores ──
                    genre    = result.get("content_genre", "")
                    vertical = result.get("vertical_category", "")
                    v_key = get_vertical(genre)
                    apply_learned_weights(v_key)
                    ws = compute_weighted_scores(result.get("quality_scores", {}), genre, vertical)
                    result["brand_exposure_score"] = ws["brand_exposure_score"]
                    result["storytelling_score"]   = ws["storytelling_score"]
                    result["tech_status"]          = ws["tech_floor"]["status"]
                    result["tech_floor"]           = ws["tech_floor"]
                    result["tech_score"]           = ws["tech_score"]
                    result["marketing_score"]      = ws["marketing_score"]
                    result["vertical_tech_score"]  = ws["tech_score"]
                    result["vertical_mkt_score"]   = ws["marketing_score"]
                    result["quality_overall"]      = ws["quality_overall"] or result.get("quality_overall", 0)
                    logger.info(
                        "gemini_fileapi_success",
                        extra={
                            "model": model_name,
                            "genre": genre,
                            "vertical": v_key,
                            "timestamps": len(result["timestamps"]),
                            "brand_score": ws["brand_exposure_score"],
                            "story_score": ws["storytelling_score"],
                            "tech_floor": ws["tech_floor"]["status"],
                            "logo_detected": bool(result.get("logo_detected")),
                            "quality_dims": len(result.get("quality_scores", {})),
                        },
                    )
                    break
                except Exception as e:
                    import traceback
                    last_err = str(e)
                    logger.warning(
                        "gemini_fileapi_model_failed",
                        extra={
                            "model": model_name,
                            "error": str(e)[:80],
                            "traceback_tail": traceback.format_exc()[-500:],
                        },
                    )
                    continue

            if not result["analyzed"]:
                result["error"] = last_err

    except Exception as e:
        result["error"] = str(e)
        logger.exception("gemini_analysis_failed")
    finally:
        # Step 5: Always delete file from Gemini File API to avoid storage charges.
        # BUG FIX: Only delete if the file was successfully registered (has a name).
        # Using the captured _active_file_name string avoids holding a reference to
        # the mutable gemini_file object that polling may have partially updated.
        _file_to_delete = getattr(gemini_file, "name", None) if gemini_file else None
        if _file_to_delete:
            try:
                def _delete(name=_file_to_delete):
                    _gemini_client.files.delete(name=name)
                await asyncio.to_thread(_delete)
                logger.info("gemini_fileapi_deleted", extra={"file_name": _file_to_delete})
            except Exception as del_err:
                # 404 here is harmless — file was already gone or never fully created
                logger.warning("gemini_fileapi_delete_skipped", extra={"error": str(del_err)})

    return result


# ──────────────────────────────────────────────
# GPT-4o mini — 快速预筛 + 批量数据处理
# ──────────────────────────────────────────────
def gpt_prefilter_caption(title: str, caption: str, platform: str) -> dict:
    """
    Use GPT-4o-mini to quickly pre-filter submissions from caption/title.
    Extremely cheap ($0.0003/1K tokens) — runs before any expensive analysis.
    Returns: viltrox_likely, gear_extracted, skip_vision
    """
    result = {
        "viltrox_likely": False,
        "camera_body": None,
        "viltrox_lens": None,
        "other_lens": None,
        "skip_vision": False,
        "content_genre": "",
        "confidence": "none",
        "error": None,
    }
    if not OPENAI_AVAILABLE or not _openai_client:
        return result
    if not title and not caption:
        return result

    try:
        text = f"Title: {title}\nCaption: {caption[:800]}\nPlatform: {platform}"
        resp = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            temperature=0,
            messages=[{
                "role": "system",
                "content": (
                    "You are a camera gear extraction AI for Viltrox brand. "
                    "Extract gear info from social media captions. "
                    "Respond ONLY with compact JSON, no markdown."
                )
            }, {
                "role": "user",
                "content": (
                    f"{text}\n\n"
                    "Return JSON:\n"
                    '{"viltrox_likely":true/false,'
                    '"camera_body":"Sony A7RIV or null",'
                    '"viltrox_lens":"Viltrox 27mm F1.2 or null",'
                    '"other_lens":"non-Viltrox lens or null",'
                    '"content_genre":"review/tutorial/cinematic/vlog/other",'
                    '"skip_vision":true/false,'
                    '"confidence":"high/medium/low/none"}'
                    "\n\nskip_vision=true only if camera AND lens are clearly stated in text."
                )
            }]
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        result.update(parsed)
        logger.info(
            "gpt_prefilter_caption_complete",
            extra={
                "viltrox_likely": parsed.get("viltrox_likely"),
                "skip_vision": parsed.get("skip_vision"),
                "confidence": parsed.get("confidence"),
            },
        )
    except Exception as e:
        result["error"] = str(e)
        logger.warning("gpt_prefilter_caption_failed", extra={"error": str(e)})
    return result


def gpt_analyze_engagement_anomaly(
    metrics: dict, platform: str, handle: str,
    history: list
) -> dict:
    """
    Use GPT-4o-mini to detect fake engagement / anomalies in bulk.
    Called during daily 12:00 recalculation — zero Claude cost.
    """
    result = {"anomaly": False, "risk_delta": 0, "reasons": [], "error": None}
    if not OPENAI_AVAILABLE or not _openai_client:
        return result
    try:
        hist_str = json.dumps(history[-5:], ensure_ascii=False) if history else "[]"
        prompt = (
            f"Platform: {platform}, Creator: {handle}\n"
            f"Current metrics: {json.dumps(metrics)}\n"
            f"Recent history (last 5): {hist_str}\n\n"
            "Detect fake engagement anomalies. Return JSON:\n"
            '{"anomaly":true/false,"risk_delta":0-50,'
            '"reasons":["中文原因列表"]}'
        )
        resp = _openai_client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            temperature=0,
            messages=[
                {"role": "system", "content": "You are a social media fraud detection AI. Respond only JSON."},
                {"role": "user", "content": prompt}
            ]
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```json\s*|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        result.update(parsed)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("gpt_anomaly_failed", extra={"error": str(e)})
    return result
