"""Prompt context helpers for Claude vision analyzers."""
from __future__ import annotations

from app.services.scoring.creator import get_creator_profile


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
