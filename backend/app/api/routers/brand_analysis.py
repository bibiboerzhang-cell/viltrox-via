"""
brand_analysis.py — Viltrox 战术指挥分析 (操盘手版 v3)
=====================================================
前端打包真实扫描数据 → AI 只解释事实 + 出行动指令
禁止废话，每个结论必须引用具体帖文标题和数字
"""
from __future__ import annotations
import json, time
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from app.api.dependencies.admin import get_admin
from app.core.logging import get_logger
from app.services.ai.retry import call_ai_with_retry
from app.services.security.rate_limiter import rate_limit

try:
    from app.services.ai.clients.claude_client import get_claude_client
    _claude_ok = True
except Exception:
    _claude_ok = False
    def get_claude_client(): return None

router = APIRouter(prefix="/api/admin/intel", tags=["brand-analysis"])
logger = get_logger(__name__)

PROMPT = """你是 Viltrox（唯卓仕）全球社交媒体首席增长官。你手握市场生杀大权，向 CEO 直接汇报。

以下是从 Viltrox 官方矩阵账号真实抓取的全量数据。你的任务是产出【能直接转发给团队执行的战术指令】。

══════ 绝对禁止 ══════
- 禁止"数据显示增长良好"、"建议加强互动"等宏观废话
- 禁止总结已知的数据总和（前端已经显示了）
- 禁止说"不及预期"却不说具体哪条内容、为什么
- 每个结论必须引用【具体帖文标题】+【具体数字】+【具体账号名】
- 如果证据不足，必须写"证据不足"，不要猜

══════ 真实数据 ══════

【全局】
总帖文: {total_posts} | 总播放: {total_views} | 总赞: {total_likes} | 总评论: {total_comments}
每条均播: {avg_views} | 全局互动率: {engagement_rate}

【本周动态】
{week_summary}

【各账号明细】
{per_account_text}

【全平台 TOP 10 爆款】
{top10_text}

【全平台 WORST 10 低效内容】
{worst10_text}

══════ 输出格式（严格 JSON）══════

{{
  "overall_health": "good"/"warning"/"critical",
  "one_liner": "一句话战况（必须提到具体账号+具体数字，例如：IG Flash 的 Z2 系列3条Reel周均5万播放正在形成爆款矩阵，但TK gear 互动率0.3%已进入死区需立即调整）",
  "sections": [
    {{
      "title": "红色警报",
      "icon": "🔴",
      "severity": "critical",
      "bullets": [
        "指出正在发生的具体风险（精确到帖文标题、账号名、数据异常点）"
      ]
    }},
    {{
      "title": "黄金机会",
      "icon": "💰",
      "severity": "ok",
      "bullets": [
        "发现能立刻放大的爆款趋势（引用具体帖文）"
      ]
    }},
    {{
      "title": "战术指令（48小时必须执行）",
      "icon": "⚔️",
      "actions": [
        {{"priority": "urgent", "text": "今天必须做的事（精确到哪个账号、发什么内容）"}},
        {{"priority": "this_week", "text": "本周必须完成"}},
        {{"priority": "ongoing", "text": "持续执行的策略调整"}}
      ]
    }},
    {{
      "title": "各账号战力评级",
      "icon": "📱",
      "platforms": {{
        "账号名": "S/A/B/C/D 评级 + 一句话诊断"
      }}
    }},
    {{
      "title": "内容方向决策",
      "icon": "📊",
      "bullets": [
        "基于 TOP 10 爆款的共同特征，指出应该多做什么",
        "基于 WORST 10 的共同特征，指出应该停止做什么",
        "跨平台内容复用建议"
      ]
    }}
  ]
}}

只输出 JSON。你是操盘手不是秘书，给我能执行的指令不是能听的废话。"""


@router.post("/analyze-brand")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def analyze_brand(request: Request, body: dict):
    get_admin(request)
    if not _claude_ok:
        raise HTTPException(500, "Claude not available")
    client = get_claude_client()
    if not client:
        raise HTTPException(500, "Claude client not initialized")

    if body.get("mode") == "viltrox_scan":
        sd = body.get("scan_data", {})
        g = sd.get("global", {})

        pa_lines = []
        for a in sd.get("per_account", []):
            pa_lines.append(f"[{a.get('platform','?').upper()}] {a.get('name','?')}:")
            pa_lines.append(f"  帖文:{a.get('posts',0)} 播放:{a.get('views',0)} 赞:{a.get('likes',0)} 均播:{a.get('avg_views',0)} 互动率:{a.get('engagement_rate','?')}")
            for t in (a.get('top3') or [])[:3]:
                pa_lines.append(f"  爆款: {t}")
            for t in (a.get('worst3') or [])[:2]:
                pa_lines.append(f"  低效: {t}")
            for t in (a.get('recent5') or [])[:3]:
                pa_lines.append(f"  最新: {t}")

        prompt = PROMPT.format(
            total_posts=g.get("total_posts", 0),
            total_views=g.get("total_views", 0),
            total_likes=g.get("total_likes", 0),
            total_comments=g.get("total_comments", 0),
            avg_views=g.get("avg_views_per_post", 0),
            engagement_rate=g.get("global_engagement_rate", "?"),
            week_summary=sd.get("week_summary", "暂无"),
            per_account_text="\n".join(pa_lines) or "暂无",
            top10_text="\n".join(sd.get("top10_all_time", [])[:10]) or "暂无",
            worst10_text="\n".join(sd.get("worst10_all_time", [])[:10]) or "暂无",
        )
    else:
        s = body.get("summary", {})
        prompt = f"快速分析：{s.get('total',0)}条投稿，{s.get('total_views',0)}播放。返回JSON: overall_health, one_liner, sections。"

    # 清洗孤立代理对（前端 .slice 切坏的 emoji/花体字母）
    prompt = prompt.encode("utf-8", "replace").decode("utf-8")

    try:
        logger.info("brand_analysis.claude_started", extra={"prompt_chars": len(prompt)})
        t0 = time.time()
        resp = call_ai_with_retry(
            "brand_analysis.claude",
            lambda: client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        elapsed = time.time() - t0
        text = (resp.content[0].text if resp.content else "").strip()
        logger.info("brand_analysis.claude_complete", extra={"elapsed_sec": round(elapsed, 1), "char_count": len(text)})

        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        result = json.loads(text)
        result["generated_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        result["response_time_sec"] = round(elapsed, 1)
        return result
    except json.JSONDecodeError as e:
        logger.warning("brand_analysis.json_parse_failed", extra={"error": str(e)})
        return {
            "overall_health": "unknown",
            "one_liner": "AI 返回格式异常",
            "error": "Analysis response could not be parsed",
            "raw_text": text[:2000],
            "sections": [],
        }
    except Exception:
        logger.exception("brand_analysis.failed")
        raise HTTPException(500, "Analysis failed")
