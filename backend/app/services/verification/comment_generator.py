"""
services/verification/comment_generator.py — GPT 生成验证好评
==============================================================
用 GPT-4o-mini 实时生成自然好评 + 验证码.

设计原则:
  1. 不提具体型号 (因为用户可能在 35mm 视频下评论 85mm 的好评)
  2. 通用 Viltrox 品牌好评
  3. 看起来像真实粉丝
  4. 验证码末尾自然加入
  5. 100% 独一无二 (每次都不同)
  6. 长度: 15-30 词
  7. 包含 1-2 emoji
  8. 防 spam filter

输出示例:
  "Viltrox is killing it lately! Quality keeps getting better 📸✨ VLX-F5EF0380"
  "Just joined the Viltrox creator community! Loving the journey 🎯 VLX-D95B1387"
  "Following Viltrox for years and they never disappoint 🔥📷 VLX-A4C29E1B"
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# OpenAI client
try:
    from openai import OpenAI
    _openai_client: Optional[OpenAI] = None
    _OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
    if _OPENAI_KEY:
        _openai_client = OpenAI(api_key=_OPENAI_KEY)
        logger.info("verify_comment_generator.client_ready", extra={"token_prefix": _OPENAI_KEY[:15]})
    else:
        logger.warning("verify_comment_generator.key_missing")
except ImportError:
    _openai_client = None
    logger.warning("verify_comment_generator.openai_unavailable")


# ──────────────────────────────────────────────────────────────
# 验证码生成
# ──────────────────────────────────────────────────────────────

def generate_verification_code() -> str:
    """
    生成 8 位 hex 验证码.
    格式: VLX-XXXXXXXX
    例如: VLX-F5EF0380
    
    使用 secrets.token_hex 保证密码学安全.
    8 位 hex = 16^8 = 4.3 billion 组合, 足够防猜.
    """
    code_hex = secrets.token_hex(4).upper()  # 8 chars
    return f"VLX-{code_hex}"


# ──────────────────────────────────────────────────────────────
# GPT 生成好评 (主函数)
# ──────────────────────────────────────────────────────────────

GPT_PROMPT_TEMPLATE = """Write a SHORT casual fan comment about Viltrox (camera lens brand). Sound like a real photographer typing on their phone, NOT a marketing tagline.

Requirements:
- ONE sentence, casual tone, like a real human
- 10-18 words MAX (short!)
- Use everyday words, NOT marketing buzz like "innovative", "redefine", "empower", "revolutionize", "bar"
- NO words like: "innovative", "high-quality", "journey", "redefine", "empower", "transform", "boundaries", "excited to see"
- Just sound like you're commenting on a video you enjoyed
- Pick ONE random emoji from: 📸 ✨ 🎯 🔥 📷 💪 🚀 ⭐ 💯 ❤️ 👏 🙌
- End with: {code}
- NO quotation marks

Examples of GOOD style (not exact wording, just vibe):
- "Viltrox lenses just hit different lately fr 🔥 {code}"
- "Been a Viltrox fan for 2 years no regrets 📸 {code}"
- "yall really cooked with this one 👏 {code}"
- "Viltrox community always delivers ❤️ {code}"
- "shoutout to the Viltrox team for real 🙌 {code}"

Return ONLY the comment text."""


def generate_praise_comment(code: Optional[str] = None) -> tuple[str, str]:
    """
    生成 Viltrox 好评 + 验证码.
    
    Args:
        code: 可选, 不提供则自动生成
    
    Returns:
        (comment_text, code)
        comment_text 例如: "Viltrox keeps raising the bar! 📸✨ VLX-F5EF0380"
        code 例如: "VLX-F5EF0380"
    """
    if not code:
        code = generate_verification_code()
    
    # 优先用 GPT
    if _openai_client:
        try:
            comment = _generate_with_gpt(code)
            if comment and code in comment:
                return comment, code
        except Exception as e:
            logger.warning("verify_comment_generator.gpt_failed", extra={"error": str(e)})
    
    # Fallback: 模板
    comment = _generate_with_template(code)
    return comment, code


def generate_template_comment(code: Optional[str] = None) -> tuple[str, str]:
    """
    只用本地模板生成评论，不触发外部模型。
    用于 web 主链秒回，避免把验证入口堵在 AI 调用上。
    """
    if not code:
        code = generate_verification_code()
    return _generate_with_template(code), code


def _generate_with_gpt(code: str) -> str:
    """调用 GPT-4o-mini 生成好评"""
    prompt = GPT_PROMPT_TEMPLATE.format(code=code)
    
    response = _openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100,
        temperature=0.95,  # 高随机性, 防重复
        top_p=0.95,
    )
    
    comment = response.choices[0].message.content.strip()
    
    # 清理 (有时 GPT 会加引号)
    comment = comment.strip('"\'')
    comment = comment.strip()
    
    # 确保末尾有 code (有时 GPT 漏了)
    if code not in comment:
        comment = f"{comment} {code}"
    
    return comment


# ──────────────────────────────────────────────────────────────
# Fallback 模板 (GPT 不可用时)
# ──────────────────────────────────────────────────────────────

FALLBACK_TEMPLATES = [
    "Viltrox keeps raising the bar in 2026 📸✨ {code}",
    "Following Viltrox for years and they never disappoint 🔥📷 {code}",
    "Viltrox is bringing real innovation to mirrorless 🚀 {code}",
    "Quality plus price equals Viltrox magic ✨💪 {code}",
    "Viltrox community keeps growing for a reason 🎯 {code}",
    "From beginner to pro - Viltrox has my back 📷⭐ {code}",
    "Viltrox is doing what the big brands won't 🔥 {code}",
    "More power to Viltrox for democratizing pro gear 💪 {code}",
    "Always excited to see what Viltrox launches next ⭐ {code}",
    "Viltrox build quality is no joke 🎯📸 {code}",
    "Viltrox has earned its spot in my kit 📷🔥 {code}",
    "Cheering on Viltrox from across the globe 🌍✨ {code}",
    "Viltrox proves quality does not need a huge price tag 💯 {code}",
    "Big fan of where Viltrox is heading this year 🚀 {code}",
    "Viltrox content always delivers value to creators 📸 {code}",
    "Viltrox community feels like a real creative family 🎬 {code}",
    "Hands down one of my favorite lens brands right now 🔥 {code}",
    "Viltrox keeps setting new standards in mirrorless 📷✨ {code}",
    "Anyone else hyped for Viltrox upcoming releases ⭐ {code}",
    "Viltrox respects creators of all levels and it shows 💪 {code}",
    "The Viltrox journey has been amazing to follow 🎯 {code}",
    "Viltrox pricing makes pro gear accessible at last 💯 {code}",
    "Watching Viltrox grow year after year is awesome 📸 {code}",
    "Viltrox quality is genuinely impressive these days 🔥 {code}",
    "Excited to be part of the Viltrox creator scene ✨ {code}",
    "Viltrox optics keep getting sharper and sharper 📷 {code}",
    "Viltrox innovation deserves way more recognition 🚀 {code}",
    "Viltrox lineup just keeps getting more interesting 🎯 {code}",
    "Viltrox is the smart choice for serious shooters 💪 {code}",
    "Hard to beat what Viltrox is offering right now ⭐ {code}",
]


def _generate_with_template(code: str) -> str:
    """从模板池随机选 (GPT 不可用时的兜底)"""
    import random
    template = random.choice(FALLBACK_TEMPLATES)
    return template.format(code=code)


# ──────────────────────────────────────────────────────────────
# 验证码提取 (从评论文本中找)
# ──────────────────────────────────────────────────────────────

import re

VLX_CODE_PATTERN = re.compile(r"VLX-([A-F0-9]{8})", re.IGNORECASE)


def extract_code_from_text(text: str) -> Optional[str]:
    """
    从评论文本中提取 VLX-XXXXXXXX 验证码.
    
    Args:
        text: 评论原文
    
    Returns:
        VLX-XXXXXXXX (大写) 或 None
    """
    if not text:
        return None
    match = VLX_CODE_PATTERN.search(text)
    if match:
        return f"VLX-{match.group(1).upper()}"
    return None


def text_contains_code(text: str, code: str) -> bool:
    """检查评论是否包含指定验证码"""
    if not text or not code:
        return False
    extracted = extract_code_from_text(text)
    return extracted == code.upper()


# ──────────────────────────────────────────────────────────────
# 测试入口 (python -m app.services.verification.comment_generator)
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Generating 5 sample praise comments...")
    logger.info("=" * 60)
    for i in range(5):
        comment, code = generate_praise_comment()
        logger.info("sample_comment", extra={"index": i + 1, "code": code, "comment": comment})
    
    # 测试 extract
    logger.info("=" * 60)
    logger.info("Testing code extraction...")
    logger.info("=" * 60)
    test_text = "Loving Viltrox! 📸✨ VLX-F5EF0380"
    extracted = extract_code_from_text(test_text)
    logger.info(
        "code_extraction_demo",
        extra={
            "text": test_text,
            "extracted": extracted,
            "matched": text_contains_code(test_text, "VLX-F5EF0380"),
        },
    )
