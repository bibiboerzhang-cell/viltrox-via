"""Gemini/OpenAI/Anthropic 关键帧判定分析(从 gemini_video.py 抽出,行为不变)。

业务函数簇:从 Layer1 文本 + 关键帧 JPG 判定 Layer2/3,或对 final_v1 做关键帧 QA。
依赖 gemini_video_results 的结果整形纯函数 + gemini_video_prompts 的 prompt 构造器,
以及 gemini_client / llm_gateway / 多模态内容构造。被 gemini_video re-export 回灌,调用点不变。
红线:LLM 判定只整形结果,零触 viltrox_fit_score。

2026-08-23 C3 收口:四条直连 SDK 调用全部改走 llm_production 严格边界——
Gemini 裁判/QA → generate_google_content(任务绑定 keyframe_qa),
OpenAI 裁判 → generate_openai_responses(keyframe_openai_judge,client 用 services.ai.clients.openai_client
的代理感知实例,不再 OpenAI(api_key=...) 裸建),
Claude 裁判 → generate_anthropic_messages(keyframe_claude_judge)。
``llm_context``(worker 传入 cost_tag/triggered_by/staff/metadata)用于台账归属;缺省也能跑。
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

from app.platform import llm_gateway, llm_production
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
# 关键帧裁判输出上限与思考口径由本模块显式给出(边界只补缺省、不覆盖显式值)。
# 4096 与 worker 的 APIFY_WORKER_LLM_MAX_OUTPUT_TOKENS 默认一致;QA/判定 JSON 远小于此。
KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS = max(
    256, int(os.environ.get("GEMINI_KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS", "4096"))
)
KEYFRAME_JUDGE_OUTPUT_TOKENS_OPENAI_ANTHROPIC = 4000
# 预留估算:关键帧 JPG 按图片瓦片上限保守估(1 张 ≈ 1600 token),文本按 3 字符/token。
KEYFRAME_IMAGE_RESERVE_TOKENS = 1600
KEYFRAME_TASK_BINDING_GEMINI = "keyframe_qa"
KEYFRAME_TASK_BINDING_OPENAI = "keyframe_openai_judge"
KEYFRAME_TASK_BINDING_ANTHROPIC = "keyframe_claude_judge"


def _keyframe_input_token_estimate(text: str, image_count: int) -> int:
    return max(1, len(str(text or "")) // 3 + 512 + KEYFRAME_IMAGE_RESERVE_TOKENS * max(0, int(image_count)))


def _strict_call_kwargs(
    llm_context: dict[str, Any] | None,
    *,
    task_binding: str,
    subphase: str,
    title: str,
    image_count: int,
) -> dict[str, Any]:
    """cost_tag/triggered_by/staff/metadata for one strict judge call (worker context optional)."""
    context = llm_context if isinstance(llm_context, dict) else {}
    base_metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    return {
        "purpose": str(context.get("purpose") or task_binding),
        "cost_tag": str(context.get("cost_tag") or "") or None,
        "triggered_by": context.get("triggered_by"),
        "staff": context.get("staff") if isinstance(context.get("staff"), dict) else None,
        "metadata": {
            **base_metadata,
            "task_binding": task_binding,
            "phase": str(base_metadata.get("phase") or "video_analysis"),
            "subphase": subphase,
            "keyframe_count": int(image_count),
            "target_label": str(base_metadata.get("target_label") or title or subphase)[:160],
        },
    }


def _gemini_keyframe_contents(text: str, keyframes: list[dict[str, Any]]) -> list[Any]:
    contents: list[Any] = [text]
    for frame in keyframes:
        image_path = Path(str(frame.get("image_path") or ""))
        if not image_path.exists():
            continue
        contents.append(genai_types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"))
    return contents


def _strict_gemini_generate(
    *,
    model_name: str,
    contents: list[Any],
    llm_context: dict[str, Any] | None,
    subphase: str,
    title: str,
) -> Any:
    text = str(contents[0]) if contents and isinstance(contents[0], str) else ""
    return llm_production.generate_google_content(
        client=gemini_client,
        contents=contents,
        config=_keyframe_judge_generate_config(model_name),
        model=model_name,
        max_output_tokens=KEYFRAME_JUDGE_MAX_OUTPUT_TOKENS,
        estimated_input_tokens=_keyframe_input_token_estimate(text, len(contents) - 1),
        **_strict_call_kwargs(
            llm_context,
            task_binding=KEYFRAME_TASK_BINDING_GEMINI,
            subphase=subphase,
            title=title,
            image_count=len(contents) - 1,
        ),
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
    llm_context: dict[str, Any] | None = None,
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
    contents = _gemini_keyframe_contents(f"视频标题: {title}\n\n{prompt}", keyframes)
    try:
        resp = await asyncio.to_thread(
            lambda: _strict_gemini_generate(
                model_name=model_name,
                contents=contents,
                llm_context=llm_context,
                subphase="keyframe_judgment",
                title=title,
            )
        )
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
    llm_context: dict[str, Any] | None = None,
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
    contents = _gemini_keyframe_contents(f"视频标题: {title}\n\n{prompt}", keyframes)
    if len(contents) <= 1:
        result["error"] = "no keyframe images available for QA"
        return result

    try:
        resp = await asyncio.to_thread(
            lambda: _strict_gemini_generate(
                model_name=model_name,
                contents=contents,
                llm_context=llm_context,
                subphase="final_v1_keyframe_qa",
                title=title,
            )
        )
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


def _openai_judge_client() -> Any:
    """Proxy-aware OpenAI client (services.ai.clients.openai_client); None when unavailable."""
    try:
        from app.services.ai.clients import openai_client as _module
    except Exception:  # pragma: no cover - import environment specific
        return None
    if not getattr(_module, "OPENAI_AVAILABLE", False):
        return None
    return getattr(_module, "openai_client", None)


async def analyze_v2_judgment_with_openai_keyframes(
    *,
    layer1_visual_content: dict[str, Any],
    keyframes: list[dict[str, Any]],
    title: str,
    performance_context: dict[str, Any] | None = None,
    model_name: str = "gpt-5.5",
    llm_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge Layer2+3 from Layer1 text plus keyframe JPGs using OpenAI vision."""
    result = {
        "analyzed": False,
        "method": f"openai_keyframe_judgment_{model_name}",
        "model": model_name,
        "usage_metadata": {},
        "error": None,
    }
    # 代理感知的进程级 client(api.openai.com 本网络不可直连;OPENAI_PROXY/YTDLP_PROXY),
    # 取代此前 OpenAI(api_key=...) 裸建(不吃代理,线上必 SSL 超时)。
    client = _openai_judge_client()
    if client is None:
        result["error"] = "OpenAI not available"
        return result

    prompt = _video_v2_judgment_prompt(
        layer1_visual_content=layer1_visual_content,
        keyframes=keyframes,
        performance_context=performance_context,
    )
    content = build_openai_multimodal_content(f"视频标题: {title}\n\n{prompt}", keyframes)
    input_items = [{"role": "user", "content": content}]
    try:
        resp = await asyncio.to_thread(
            lambda: llm_production.generate_openai_responses(
                client=client,
                input_items=input_items,
                model=model_name,
                max_output_tokens=KEYFRAME_JUDGE_OUTPUT_TOKENS_OPENAI_ANTHROPIC,
                **_strict_call_kwargs(
                    llm_context,
                    task_binding=KEYFRAME_TASK_BINDING_OPENAI,
                    subphase="openai_keyframe_judgment",
                    title=title,
                    image_count=max(0, len(content) - 1),
                ),
            )
        )
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
        raw = llm_production.openai_response_text(resp)
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
    llm_context: dict[str, Any] | None = None,
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
    messages = [{"role": "user", "content": content}]
    try:
        # 思考策略由边界按 env 统一(默认 disabled,成本中性);max_tokens 4000 全给正文。无 temperature。
        resp = await asyncio.to_thread(
            lambda: llm_production.generate_anthropic_messages(
                client=client,
                messages=messages,
                model=model_name,
                max_output_tokens=KEYFRAME_JUDGE_OUTPUT_TOKENS_OPENAI_ANTHROPIC,
                **_strict_call_kwargs(
                    llm_context,
                    task_binding=KEYFRAME_TASK_BINDING_ANTHROPIC,
                    subphase="anthropic_keyframe_judgment",
                    title=title,
                    image_count=max(0, len(content) - 1),
                ),
            )
        )
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
