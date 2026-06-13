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
        # api.openai.com 不可直连(SSL 握手超时);走 YTDLP_PROXY/OPENAI_PROXY 残留代理(实测可达)。
        _oai_proxy = (os.environ.get("OPENAI_PROXY") or os.environ.get("YTDLP_PROXY") or "").strip()
        if _oai_proxy:
            try:
                import httpx as _httpx

                openai_client = OpenAI(api_key=_openai_key, http_client=_httpx.Client(proxy=_oai_proxy, timeout=60.0))
            except Exception:
                openai_client = OpenAI(api_key=_openai_key)
        else:
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
