from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.services.ai.clients.claude_client import get_claude_client
from app.services.ai.clients.openai_client import OPENAI_AVAILABLE, openai_client
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
from app.services.ai.retry import call_ai_with_retry

logger = get_logger("viltrox.deepsight.triad")

# 三模型并发是平台最贵路径;过去只记 ai_usage_log,不进 budget_guard 月度台账 → 预算对它失明。
# 把成本记进台账(可观测/可告警);单价为粗估(美元/百万 token),非精确账单。
_TRIAD_SCOPE = "cron:deepsight_triad"
_TRIAD_PRICE = {
    "claude": (3.0, 15.0),
    "gpt": (0.15, 0.60),
    "gemini": (0.075, 0.30),
}


def _usage_tokens(resp: Any) -> tuple[int, int]:
    """从 SDK 响应defensive 抽取 (input, output) token —— 兼容 anthropic/openai/gemini 三家字段名。"""
    u = getattr(resp, "usage", None) or getattr(resp, "usage_metadata", None)
    if u is None:
        return 0, 0
    ti = getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", None) or getattr(u, "prompt_token_count", None) or 0
    to = getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", None) or getattr(u, "candidates_token_count", None) or 0
    try:
        return int(ti or 0), int(to or 0)
    except (TypeError, ValueError):
        return 0, 0


def _record_triad_cost(provider: str, resp: Any) -> None:
    """成功调用后把成本记进 budget_guard 月度台账(护栏① 不再对 triad 失明)。绝不抛。"""
    try:
        from app.domains.costs import budget_guard

        ti, to = _usage_tokens(resp)
        pin, pout = _TRIAD_PRICE.get(provider, (0.0, 0.0))
        usd = ti / 1_000_000.0 * pin + to / 1_000_000.0 * pout
        if usd > 0:
            budget_guard.record_cost(_TRIAD_SCOPE, round(usd, 6))
    except Exception:
        logger.debug("triad.record_cost_failed", exc_info=True)


ROLE_PROMPTS = {
    "claude": "你是 Claude，角色是冷静结构分析师。只看结构、风险、断点和错配。输出 JSON。",
    "gpt": "你是 GPT，角色是市场共情分析师。只看用户情绪、受众匹配和品牌感受。输出 JSON。",
    "gemini": "你是 Gemini，角色是增长机会猎手。只看流量、传播、商业机会和动作。输出 JSON。",
}


def _clean_json_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"```$", "", text)
    return text.strip()


def _fallback_structural(pack: dict) -> dict:
    risks = pack.get("risk_flags", [])[:4]
    plats = pack.get("platform_breakdown", [])[:3]
    return {
        "role": "claude",
        "summary": "基于规则层的结构诊断",
        "risks": risks,
        "platform_notes": [f"{p['platform']} -> {p['diagnostic_flag']}" for p in plats],
    }


def _fallback_empathy(pack: dict) -> dict:
    c = pack.get("comment_analysis", {})
    return {
        "role": "gpt",
        "summary": "基于评论层的情绪诊断",
        "brand_mood": "mixed" if c.get("negative_ratio", 0) >= 0.2 else "stable",
        "positive_keywords": c.get("positive_keywords", [])[:6],
        "negative_keywords": c.get("negative_keywords", [])[:6],
        "purchase_keywords": c.get("purchase_keywords", [])[:6],
    }


def _fallback_growth(pack: dict) -> dict:
    opportunities = pack.get("opportunities", [])[:5]
    return {
        "role": "gemini",
        "summary": "基于机会层的增长诊断",
        "opportunities": opportunities,
    }


async def _ask_claude(pack: dict) -> dict:
    client = get_claude_client()
    if not client:
        return _fallback_structural(pack)
    prompt = ROLE_PROMPTS["claude"] + "\n只允许输出 JSON，包含 summary, risks, platform_notes。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:35000]
    try:
        def _call():
            return call_ai_with_retry(
                "deepsight.triad.claude",
                lambda: client.messages.create(model=CLAUDE_MODEL, max_tokens=1600, messages=[{"role": "user", "content": prompt}]),
            )
        resp = await asyncio.to_thread(_call)
        _record_triad_cost("claude", resp)
        text = _clean_json_text(resp.content[0].text if resp.content else "")
        return json.loads(text)
    except Exception:
        return _fallback_structural(pack)


async def _ask_gpt(pack: dict) -> dict:
    if not OPENAI_AVAILABLE or not openai_client:
        return _fallback_empathy(pack)
    prompt = ROLE_PROMPTS["gpt"] + "\n只允许输出 JSON，包含 summary, brand_mood, positive_keywords, negative_keywords, purchase_keywords。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:35000]
    try:
        def _call():
            return openai_client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                max_tokens=1200,
                messages=[
                    {"role": "system", "content": ROLE_PROMPTS["gpt"]},
                    {"role": "user", "content": prompt},
                ],
            )
        resp = await asyncio.to_thread(_call)
        _record_triad_cost("gpt", resp)
        text = _clean_json_text(resp.choices[0].message.content or "")
        return json.loads(text)
    except Exception:
        return _fallback_empathy(pack)


async def _ask_gemini(pack: dict) -> dict:
    if not GEMINI_AVAILABLE or not gemini_client:
        return _fallback_growth(pack)
    prompt = ROLE_PROMPTS["gemini"] + "\n只允许输出 JSON，包含 summary, opportunities。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:28000]
    try:
        def _call():
            return gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        resp = await asyncio.to_thread(_call)
        _record_triad_cost("gemini", resp)
        text = _clean_json_text(getattr(resp, "text", "") or "")
        return json.loads(text)
    except Exception:
        return _fallback_growth(pack)


async def run_triad(pack: dict) -> dict:
    claude, gpt, gemini = await asyncio.gather(_ask_claude(pack), _ask_gpt(pack), _ask_gemini(pack))
    split_vote = False
    if pack.get("evidence_confidence", {}).get("confidence_score", 0) < 0.45:
        split_vote = True
    return {
        "claude": claude,
        "gpt": gpt,
        "gemini": gemini,
        "split_vote": split_vote,
    }
