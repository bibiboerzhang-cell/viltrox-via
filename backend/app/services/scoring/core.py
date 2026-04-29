"""
services/scoring/core.py — 三轴评分框架 + A/B/C 打分规则强制执行
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.constants import (
    BRAND_EXPOSURE_DIMS, STORYTELLING_DIMS, TECH_FLOOR_DIMS,
    TECH_FLOOR_PASS, TECH_FLOOR_WARNING,
    VERTICAL_WEIGHTS, GENRE_TO_VERTICAL,
)

logger = get_logger(__name__)


def compute_tech_status(quality_scores: dict) -> dict:
    vals = [quality_scores.get(d, 0) for d in TECH_FLOOR_DIMS if quality_scores.get(d, 0) > 0]
    if not vals:
        return {"status": "unknown", "avg": 0, "weak_dims": []}
    avg = sum(vals) / len(vals)
    weak = [d for d in TECH_FLOOR_DIMS if 0 < quality_scores.get(d, 0) < 6]
    if avg >= TECH_FLOOR_PASS:
        status = "pass"
    elif avg >= TECH_FLOOR_WARNING:
        status = "warning"
    else:
        status = "fail"
    return {"status": status, "avg": round(avg, 1), "weak_dims": weak}


# ── A/B/C 打分规则代码级强制执行 ─────────────────────────
# 不依赖 AI prompt，后端硬执行，分数稳定可预期

TECH_DIMS = [
    "exposure", "stability", "color_grade", "composition",
    "lighting", "editing", "focus", "close_up_quality"
]


def probe_video_resolution(video_path: str) -> dict:
    """用 ffprobe 获取视频分辨率"""
    import os, subprocess, json, shutil
    if not video_path or not os.path.exists(video_path) or not shutil.which("ffprobe"):
        return {}
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", video_path],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams", [])
        if not streams:
            return {}
        s = streams[0]
        return {"width": int(s.get("width", 0)), "height": int(s.get("height", 0))}
    except Exception:
        logger.warning(
            "scoring.probe_video_resolution_failed",
            extra={"video_path": video_path},
            exc_info=True,
        )
        return {}


def apply_score_guardrails(
    quality_scores: dict,
    video_resolution: dict | None = None,
    water_video_flags: dict | None = None,
    filler_flags: dict | None = None,
) -> dict:
    """
    A/B/C 三条打分规则硬执行，不依赖 AI prompt：

    A. 分辨率上限
       - 480p 及以下：所有技术维度 <= 5
       - 720p：技术维度 <= 6
       - 只有 1080p+ 才能给 7+

    B. 水视频检测（任意触发 storytelling/hook -2）
       - 同一机位静止镜头超过 60%
       - 装饰性粒子/星星特效超过 30% 时长
       - 同一 B-roll 素材出现 3 次以上

    C. 废话填充检测（任意触发 hook/conclusion_strength -1）
       - PPT 幻灯片插入打断叙事
       - 前 30 秒无实质内容
       - 视频 >3 分钟但实质内容 <1 分钟
    """
    qs = dict(quality_scores or {})
    adjustments: list[str] = []

    # ── A: 分辨率硬上限 ──
    shorter_side = 0
    if video_resolution:
        w = int(video_resolution.get("width", 0) or 0)
        h = int(video_resolution.get("height", 0) or 0)
        if w and h:
            shorter_side = min(w, h)

    tech_cap = None
    if shorter_side and shorter_side <= 480:
        tech_cap = 5
        adjustments.append(f"resolution_cap:480p({shorter_side}px) -> tech dims max 5")
    elif shorter_side and shorter_side <= 720:
        tech_cap = 6
        adjustments.append(f"resolution_cap:720p({shorter_side}px) -> tech dims max 6")

    if tech_cap is not None:
        for dim in TECH_DIMS:
            if isinstance(qs.get(dim), (int, float)) and qs[dim] > tech_cap:
                qs[dim] = tech_cap

    # ── B: 水视频检测 ──
    water_triggered = any(bool(v) for v in (water_video_flags or {}).values())
    if water_triggered:
        for dim in ("storytelling", "hook"):
            if isinstance(qs.get(dim), (int, float)):
                qs[dim] = max(1, qs[dim] - 2)
        adjustments.append("water_video_penalty: storytelling/hook -2")

    # ── C: 废话填充检测 ──
    filler_triggered = any(bool(v) for v in (filler_flags or {}).values())
    if filler_triggered:
        for dim in ("hook", "conclusion_strength"):
            if isinstance(qs.get(dim), (int, float)):
                qs[dim] = max(1, qs[dim] - 1)
        adjustments.append("filler_penalty: hook/conclusion_strength -1")

    vals = [v for v in qs.values() if isinstance(v, (int, float)) and v > 0]
    overall = round(sum(vals) / len(vals), 1) if vals else 0

    return {
        "quality_scores": qs,
        "quality_overall": overall,
        "score_adjustments": adjustments,
    }


def compute_weighted_scores(
    quality_scores: dict,
    content_genre: str = "",
    vertical_category: str = "",
    video_resolution: dict | None = None,
    water_video_flags: dict | None = None,
    filler_flags: dict | None = None,
) -> dict:
    """
    计算三轴加权分数，并应用 A/B/C guardrails。
    """
    # 先跑 guardrails
    guard = apply_score_guardrails(
        quality_scores,
        video_resolution=video_resolution,
        water_video_flags=water_video_flags,
        filler_flags=filler_flags,
    )
    qs = guard["quality_scores"]

    # 找垂直类权重
    vertical = vertical_category or GENRE_TO_VERTICAL.get(content_genre, "default")
    weights = VERTICAL_WEIGHTS.get(vertical, VERTICAL_WEIGHTS.get("default", {}))

    tech_weights = weights.get("tech", {})
    mkt_weights  = weights.get("mkt", {})

    # 技术分
    tech_total = sum(tech_weights.values()) or 1
    tech_score = sum(
        qs.get(dim, 0) * w / tech_total
        for dim, w in tech_weights.items()
        if qs.get(dim, 0) > 0
    )

    # 营销分
    mkt_total = sum(mkt_weights.values()) or 1
    mkt_score = sum(
        qs.get(dim, 0) * w / mkt_total
        for dim, w in mkt_weights.items()
        if qs.get(dim, 0) > 0
    )

    tech_status = compute_tech_status(qs)

    # ── 三轴评分: brand_exposure / storytelling / tech_floor ──
    brand_dims_vals = [qs.get(d, 0) for d in BRAND_EXPOSURE_DIMS if qs.get(d, 0) > 0]
    story_dims_vals = [qs.get(d, 0) for d in STORYTELLING_DIMS if qs.get(d, 0) > 0]
    brand_exposure_score = round(sum(brand_dims_vals) / len(brand_dims_vals), 1) if brand_dims_vals else 0
    storytelling_score   = round(sum(story_dims_vals) / len(story_dims_vals), 1) if story_dims_vals else 0

    return {
        "tech_score":       round(tech_score, 1),
        "marketing_score":  round(mkt_score, 1),
        "tech_status":      tech_status["status"],
        "tech_avg":         tech_status["avg"],
        "weak_dims":        tech_status["weak_dims"],
        "quality_scores":   qs,
        "quality_overall":  guard["quality_overall"],
        "score_adjustments": guard["score_adjustments"],
        "vertical":         vertical,
        "brand_exposure_score": brand_exposure_score,
        "storytelling_score":   storytelling_score,
        "tech_floor": {
            "status":    tech_status["status"],
            "avg":       tech_status["avg"],
            "weak_dims": tech_status["weak_dims"],
        },
    }

def get_vertical(content_genre: str) -> str:
    return GENRE_TO_VERTICAL.get(content_genre, "default")
