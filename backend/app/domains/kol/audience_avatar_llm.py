"""
domains/kol/audience_avatar_llm.py — 受众头像判龄/性别的 Gemini 直连调用(从 audience_stats 抽出)
=========================================================================================
E 路(头像视觉)只在这里碰 SDK:模型解析、下载缩略图、组 contents、generate_content。
audience_stats._age_avatar_batch 只负责挑人/解析结果/聚合,不再直接持有 SDK 细节。

模型口径:默认 gemini-3.6-flash(3.x 家族必须 thinking_level="minimal",thinking_budget=0 会 400;
temperature 已弃用一律不传);AUDIENCE_AVATAR_MODEL env 可钉回旧模型(经 model_registry
TASK_MODEL_ENV_KEYS["audience_avatar"] 同步进任务绑定,否则严格边界判 task_binding_model_mismatch)。

2026-08-23 C3 收口:generate_content 走 llm_production.generate_google_content(任务绑定
audience_avatar;就绪门 + 预算预留/台账/结算);本模块不再直接碰 SDK 调用。
"""
from __future__ import annotations

import os
import urllib.request
from typing import Any

from app.platform import llm_production

AUDIENCE_AVATAR_DEFAULT_MODEL = "gemini-3.6-flash"
AUDIENCE_AVATAR_MAX_OUTPUT_TOKENS = 4000
AUDIENCE_AVATAR_THINKING_LEVEL = "minimal"
AUDIENCE_AVATAR_TASK_BINDING = "audience_avatar"
# 预留估算:头像缩略图按 Gemini 图片瓦片上限保守估(1 张 ≈ 1600 token),文本按 4 字符/token。
AUDIENCE_AVATAR_IMAGE_TOKENS = 1600

AVATAR_BATCH_PROMPT = (
    "Task: AGGREGATE audience statistics for a marketing dashboard. The images above are public "
    "profile avatars of anonymous commenters; results are used ONLY as aggregate percentages "
    "(age buckets, gender split), never attributed to any individual.\n"
    "For EACH numbered image output one object: "
    '{"i": image number, "age": "0-18"|"19-29"|"30-39"|"40+" or "" , '
    '"gender": "male"|"female" or "", "conf": 0.0-1.0}.\n'
    "If the avatar is not a human face (logo, pet, cartoon, landscape, default silhouette) use empty "
    "strings. Estimate from apparent age of the person; be reasonable, not paranoid. "
    "Output STRICTLY one JSON array, no prose, no markdown fences; reply must start with ["
)


def avatar_model() -> str:
    """Exact model id for the avatar pass; ``AUDIENCE_AVATAR_MODEL`` env overrides the default."""
    return str(os.environ.get("AUDIENCE_AVATAR_MODEL") or "").strip() or AUDIENCE_AVATAR_DEFAULT_MODEL


def load_avatar_gemini() -> tuple[Any, Any, str]:
    """Return ``(client, genai_types, status)``; ``status`` is empty when the client is usable."""
    try:
        from app.services.ai.clients.gemini_client import GEMINI_AVAILABLE, gemini_client, genai_types
    except Exception as exc:  # pragma: no cover - import environment specific
        return None, None, f"client_unavailable: {exc}"[:120]
    if not GEMINI_AVAILABLE or gemini_client is None:
        return None, None, "gemini_unavailable"
    return gemini_client, genai_types, ""


def avatar_generate_config(genai_types: Any, *, max_output_tokens: int = AUDIENCE_AVATAR_MAX_OUTPUT_TOKENS) -> Any:
    """GenerateContentConfig for the avatar pass: bounded output + minimal thinking, no sampling params."""
    return genai_types.GenerateContentConfig(
        max_output_tokens=max_output_tokens,
        thinking_config=genai_types.ThinkingConfig(thinking_level=AUDIENCE_AVATAR_THINKING_LEVEL),
    )


def download_avatar(url: str) -> tuple[bytes, str]:
    """Fetch one avatar thumbnail; returns ``(b"", mime)`` on failure.

    CDN 偶发抖动占下载失败大头:失败重试一次,第二次超时收紧到 4s。
    """
    data = b""
    mime = "image/jpeg"
    for timeout_s in (6, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # nosec B310 - 平台 CDN 头像缩略图
                data = resp.read(300_000)
                mime = str(resp.headers.get("Content-Type") or "image/jpeg").split(";")[0]
        except Exception:
            continue
        if data:
            break
    return data, (mime if mime.startswith("image/") else "image/jpeg")


def classify_avatar_batch(
    images: list[tuple[bytes, str]],
    *,
    client: Any,
    genai_types: Any,
    model: str | None = None,
) -> str:
    """Run one multi-image generate_content call and return the raw response text.

    ``images`` is an ordered list of ``(bytes, mime)``; image N in the prompt is ``images[N-1]``.
    Exceptions propagate so the caller can count/skip the batch.
    """
    contents: list[Any] = []
    for idx, (data, mime) in enumerate(images, start=1):
        contents.append(f"Image {idx}:")
        contents.append(genai_types.Part.from_bytes(data=data, mime_type=mime))
    contents.append(AVATAR_BATCH_PROMPT)
    exact_model = model or avatar_model()
    resp = llm_production.generate_google_content(
        client=client,
        contents=contents,
        config=avatar_generate_config(genai_types),
        model=exact_model,
        purpose="audience_avatar",
        max_output_tokens=AUDIENCE_AVATAR_MAX_OUTPUT_TOKENS,
        estimated_input_tokens=avatar_input_token_estimate(len(images)),
        metadata={
            "task_binding": AUDIENCE_AVATAR_TASK_BINDING,
            "surface": "kol_audience_stats",
            "phase": "audience_avatar",
            "subphase": "avatar_batch",
            "batch_size": len(images),
            "target_label": f"avatar batch x{len(images)}",
        },
    )
    return str(getattr(resp, "text", "") or "")


def avatar_input_token_estimate(image_count: int) -> int:
    """Conservative reservation: every image at the tile cap plus the prompt text."""
    text_tokens = max(1, len(AVATAR_BATCH_PROMPT) // 4) + 16 * max(0, int(image_count))
    return max(1, text_tokens + AUDIENCE_AVATAR_IMAGE_TOKENS * max(0, int(image_count)))
