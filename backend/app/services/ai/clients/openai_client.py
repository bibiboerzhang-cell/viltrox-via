"""
services/ai/clients/openai_client.py — OpenAI SDK 初始化
"""
from __future__ import annotations

import os

from app.core.logging import get_logger


logger = get_logger(__name__)

try:
    from openai import OpenAI
    _openai_key = os.environ.get("OPENAI_API_KEY", "")
    if _openai_key:
        openai_client = OpenAI(api_key=_openai_key)
        OPENAI_AVAILABLE = True
        logger.info("ai.openai.ready")
    else:
        openai_client = None
        OPENAI_AVAILABLE = False
        logger.warning("ai.openai.disabled_missing_key")
except ImportError:
    openai_client = None
    OPENAI_AVAILABLE = False
    logger.warning("ai.openai.sdk_missing")
