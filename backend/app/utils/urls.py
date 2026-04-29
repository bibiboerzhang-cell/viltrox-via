"""
utils/urls.py — URL 解析、平台检测
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.constants import PLATFORM_MAP
from app.core.logging import get_logger

logger = get_logger(__name__)


def valid_url(url: str) -> bool:
    try:
        r = urlparse(url)
        return all([r.scheme in ("http", "https"), r.netloc])
    except Exception:
        return False


def detect_platform(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host.startswith("m."):
            host = host[2:]
        for domain, name in PLATFORM_MAP.items():
            if domain in host:
                return name
    except Exception:
        logger.warning("utils.detect_platform_failed", extra={"url": url}, exc_info=True)
    return "Unknown"
