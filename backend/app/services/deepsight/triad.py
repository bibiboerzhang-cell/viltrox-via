from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.model_registry import current_task_model_binding, split_binding
from app.platform import llm_production

# Three-model fan-out is a high-cost production path.  Every branch now uses
# the exact task binding and the atomic reservation-backed gateway; the direct
# SDK path could neither prove readiness nor appear as in-flight LLM work.
_TRIAD_SCOPE = "cron:deepsight_triad"
_TRIAD_TASKS = {
    "claude": "deepsight_strategy",
    "gpt": "deepsight_market_empath",
    "gemini": "deepsight_opportunity",
}


def _binding(task_binding: str) -> tuple[str, str]:
    return split_binding(current_task_model_binding().get(task_binding) or "")


async def _strict_json(
    *,
    role: str,
    prompt: str,
    max_output_tokens: int,
    required_keys: tuple[str, ...],
) -> dict[str, Any] | None:
    task_binding = _TRIAD_TASKS[role]
    provider, model = _binding(task_binding)
    if not provider or not model:
        return None
    result = await asyncio.to_thread(
        llm_production.generate_json,
        prompt,
        provider=provider,
        model=model,
        purpose=task_binding,
        max_output_tokens=max_output_tokens,
        cost_tag=_TRIAD_SCOPE,
        required_keys=required_keys,
        metadata={
            "surface": "deepsight_triad",
            "task_binding": task_binding,
            "phase": "structured_generation",
            "subphase": "provider_generation",
            "attempt_index": 1,
            "attempt_total": 1,
            "target_label": f"DeepSight {role}",
        },
    )
    payload = result.get("json") if isinstance(result, dict) else None
    return payload if isinstance(payload, dict) else None


ROLE_PROMPTS = {
    "claude": "你是 Claude，角色是冷静结构分析师。只看结构、风险、断点和错配。输出 JSON。",
    "gpt": "你是 GPT，角色是市场共情分析师。只看用户情绪、受众匹配和品牌感受。输出 JSON。",
    "gemini": "你是 Gemini，角色是增长机会猎手。只看流量、传播、商业机会和动作。输出 JSON。",
}


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
    prompt = ROLE_PROMPTS["claude"] + "\n只允许输出 JSON，包含 summary, risks, platform_notes。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:35000]
    try:
        result = await _strict_json(
            role="claude",
            prompt=prompt,
            max_output_tokens=1600,
            required_keys=("summary", "risks", "platform_notes"),
        )
        return result or _fallback_structural(pack)
    except Exception:
        return _fallback_structural(pack)


async def _ask_gpt(pack: dict) -> dict:
    prompt = ROLE_PROMPTS["gpt"] + "\n只允许输出 JSON，包含 summary, brand_mood, positive_keywords, negative_keywords, purchase_keywords。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:35000]
    try:
        result = await _strict_json(
            role="gpt",
            prompt=prompt,
            max_output_tokens=1200,
            required_keys=(
                "summary",
                "brand_mood",
                "positive_keywords",
                "negative_keywords",
                "purchase_keywords",
            ),
        )
        return result or _fallback_empathy(pack)
    except Exception:
        return _fallback_empathy(pack)


async def _ask_gemini(pack: dict) -> dict:
    prompt = ROLE_PROMPTS["gemini"] + "\n只允许输出 JSON，包含 summary, opportunities。\nEvidence Pack:\n" + json.dumps(pack, ensure_ascii=False)[:28000]
    try:
        result = await _strict_json(
            role="gemini",
            prompt=prompt,
            max_output_tokens=1200,
            required_keys=("summary", "opportunities"),
        )
        return result or _fallback_growth(pack)
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
