"""
services/ai/clients/gemini_client.py — Google Gemini SDK 初始化
"""
from __future__ import annotations

import os

from app.core.logging import get_logger


logger = get_logger(__name__)

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    _gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if _gemini_key:
        gemini_client = google_genai.Client(api_key=_gemini_key)
        GEMINI_AVAILABLE = True
        logger.info("ai.gemini.ready")
    else:
        gemini_client = None
        GEMINI_AVAILABLE = False
        logger.warning("ai.gemini.disabled_missing_key")
except ImportError:
    google_genai = None
    gemini_client = None
    GEMINI_AVAILABLE = False
    logger.warning("ai.gemini.sdk_missing")
