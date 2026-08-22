"""Gemini/OpenAI/Anthropic 关键帧判定分析(从 gemini_video.py 抽出,行为不变)。

业务函数簇:从 Layer1 文本 + 关键帧 JPG 判定 Layer2/3,或对 final_v1 做关键帧 QA。
依赖 gemini_video_results 的结果整形纯函数 + gemini_video_prompts 的 prompt 构造器,
以及 gemini_client / llm_gateway / 多模态内容构造。被 gemini_video re-export 回灌,调用点不变。
红线:LLM 判定只整形结果,零触 viltrox_fit_score。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from app.core.gemini_models import DEFAULT_GEMINI_JUDGE_MODEL, is_gemini_3_family
from app.core.logging import get_logger
from app.core.model_registry import CLAUDE_OPUS_EXACT_MODEL, is_selectable_model
from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client
try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

from app.platform import llm_gateway
from app.services.media.video_keyframes import (
    build_anthropic_multimodal_content,
    build_openai_multimodal_content,
)
from app.services.ai.analyzers.gemini_video_prompts import (
    _video_final_v1_keyframe_qa_prompt,
    _video_v2_judgment_prompt,
)
from app.services.ai.analyzers.gemini_video_results import (
    _apply_v2_result,
    _normalise_final_v1_keyframe_qa,
    _parse_json_response_text,
    _response_usage_metadata,
)

logger = get_logger(__name__)
# 关键帧裁判直连 SDK(不走 llm_production 边界):输出上限与思考口径必须自己带上。
# 4096 与 worker 的 APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS 默认一致;QA/判定 JSON 远小于此。
KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS = max(
    256, int(os.environ.get("GEMINI_KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS", "4096"))
)


def _keyframe_judge_generate_config(model_name: str) -> Any:
    """按模型家族给思考口径:3.x 只认 thinking_level='minimal'(thinking_budget=0 会 400);
    2.5 系反之只认 thinking_budget(0 关死,思考 token 不再吃 max_output_tokens)。
    绝不带 temperature/top_p/top_k(3.6-flash 已弃用)。"""
    if is_gemini_3_family(model_name):
        thinking = genai_types.ThinkingConfig(thinking_level="minimal")
    else:
        thinking = genai_types.ThinkingConfig(thinking_budget=0)
    return genai_types.GenerateContentConfig(
        max_output_tokens=KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS,
        thinking_config=thinking,
    )


async def analyze_v2_judgment_with_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_GEMINI_JUDGE_MODEL,
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs."""
    result = {
        "analyzed": False,
        "method": f"gemini_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        result["error"] = "Gemini not available"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    contents: list[Any] = [f"视频标题: {title}\n\n{prompt}"]
    for frame in keyframes:
        image_path = Path(str(frame.get("image_path") or ""))
        if not image_path.exists():
            continue
        contents.append(genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"))
    try:
        def _analyze():
            return gemini_client.models.generate_content(
                model=model_name,
                contents=contents,
                config=_keyframe_judge_generate_config(model_name),
            )

        resp = await asyncio.to_thread(_analyze)
        usage_metadata = _response_usage_metadata(resp)
        raw = resp.text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"gemini_keyframe_judgment_{model_name}",
            model=model_name,
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"Gemini keyframe judgment failed: {str(exc)[:300]}"
        logger.warning("gemini_keyframe_judgment_failed", extra={"error": str(exc)[:120]})
        return result


async def analyze_final_v1_keyframe_qa(
    *,
    final_v1_result: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = DEFAULT_GEMINI_JUDGE_MODEL,
) -> dict[str, Any]:
    """QA final_v1 facts from keyframe JPGs without regenerating the six-layer result."""
    result = {
        "analyzed": False,
        "method": f"gemini_final_v1_keyframe_qa_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    if not GEMINI_AVAILABLE or not gemini_client or not genai_types:
        result["error"] = "Gemini not available"
        return result

    prompt = _video_final_v1_keyframe_qa_prompt(
        final_v1_result=final_v1_result,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    contents: list[Any] = [f"视频标题: {title}\n\n{prompt}"]
    for frame in keyframes:
        image_path = Path(str(frame.get("image_path") or ""))
        if not image_path.exists():
            continue
        contents.append(genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"))
    if len(contents) <= 1:
        result["error"] = "no keyframe images available for QA"
        return result

    try:
        def _analyze():
            return gemini_client.models.generate_content(
                model=model_name,
                contents=contents,
                config=_keyframe_judge_generate_config(model_name),
            )

        resp = await asyncio.to_thread(_analyze)
        usage_metadata = _response_usage_metadata(resp)
        raw = resp.text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        qa_payload = _normalise_final_v1_keyframe_qa(parsed)
        result.update(
            {
                "analyzed": True,
                "method": f"gemini_final_v1_keyframe_qa_{model_name}",
                "model": model_name,
                "usage_metadata": usage_metadata,
                "schema_version": "video_analysis_final_v1_keyframe_qa",
                "final_v1_keyframe_qa": qa_payload,
                "qa_pass": qa_payload.get("qa_pass"),
            }
        )
        return result
    except Exception as exc:
        result["error"] = f"Gemini final_v1 keyframe QA failed: {str(exc)[:500]}"
        logger.warning("gemini_final_v1_keyframe_qa_failed", extra={"error": str(exc)[:160]})
        return result


async def analyze_v2_judgment_with_openai_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "gpt-5.5",
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs using OpenAI vision."""
    result = {
        "analyzed": False,
        "method": f"openai_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    api_key = llm_gateway._get_api_key("openai")
    if not api_key:
        result["error"] = "OpenAI not available"
        return result
    try:
        from openai import OpenAI
    except Exception as exc:
        result["error"] = f"OpenAI SDK unavailable: {exc}"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    content = build_openai_multimodal_content(f"视频标题: {title}\n\n{prompt}", keyframes)
    client = OpenAI(api_key=api_key)
    try:
        def _analyze():
            return client.responses.create(
                model=model_name,
                input=[{"role": "user", "content": content}],
                max_output_tokens=4000,
            )

        resp = await asyncio.to_thread(_analyze)
        usage = getattr(resp, "usage", None)
        usage_metadata: dict[str, Any] = {}
        if usage:
            if hasattr(usage, "model_dump"):
                usage_metadata = usage.model_dump(mode="json", exclude_none=True)
            else:
                usage_metadata = {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                }
        raw = str(getattr(resp, "output_text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"openai_keyframe_judgment_{model_name}",
            model=str(getattr(resp, "model", None) or model_name),
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"OpenAI keyframe judgment failed: {str(exc)[:500]}"
        logger.warning("openai_keyframe_judgment_failed", extra={"error": str(exc)[:160]})
        return result


async def analyze_v2_judgment_with_anthropic_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = CLAUDE_OPUS_EXACT_MODEL,
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs using Claude vision."""
    result = {
        "analyzed": False,
        "method": f"anthropic_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    if not is_selectable_model(f"anthropic/{model_name}"):
        result["error"] = (
            "Anthropic model must be an exact id registered in model_registry: "
            f"{model_name or '<empty>'}"
        )
        return result
    api_key = llm_gateway._get_api_key("anthropic")
    if not api_key:
        result["error"] = "Anthropic not available"
        return result
    try:
        import anthropic
    except Exception as exc:
        result["error"] = f"Anthropic SDK unavailable: {exc}"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    content = build_anthropic_multimodal_content(f"视频标题: {title}\n\n{prompt}", keyframes)
    client = anthropic.Anthropic(api_key=api_key)
    try:
        def _analyze():
            # 思考默认关死(成本中性,判定 JSON 不需要);max_tokens 4000 全给正文。无 temperature。
            return client.messages.create(
                model=model_name,
                max_tokens=4000,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": content}],
            )

        resp = await asyncio.to_thread(_analyze)
        usage = getattr(resp, "usage", None)
        usage_metadata: dict[str, Any] = {}
        if usage:
            if hasattr(usage, "model_dump"):
                usage_metadata = usage.model_dump(mode="json", exclude_none=True)
            else:
                usage_metadata = {
                    "input_tokens": getattr(usage, "input_tokens", None),
                    "output_tokens": getattr(usage, "output_tokens", None),
                }
        raw = "\n".join(str(block.text) for block in getattr(resp, "content", []) if getattr(block, "type", "") == "text").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        parsed = _parse_json_response_text(raw)
        subtitle_used = bool((layer1_visual_content.get("evidence") or {}).get("subtitle_used"))
        merged = {"layer1_visual_content": layer1_visual_content, **parsed}
        _apply_v2_result(
            result,
            merged,
            method=f"anthropic_keyframe_judgment_{model_name}",
            model=str(getattr(resp, "model", None) or model_name),
            usage_metadata=usage_metadata,
            subtitle_used=subtitle_used,
        )
        return result
    except Exception as exc:
        result["error"] = f"Anthropic keyframe judgment failed: {str(exc)[:500]}"
        logger.warning("anthropic_keyframe_judgment_failed", extra={"error": str(exc)[:160]})
        return result
