"""
utils/handles.py — 社交账号 handle 提取 & 规范化
"""
from __future__ import annotations
import re

from app.core.logging import get_logger

__all__ = [
    "normalize_platform",
    "normalize_handle",
    "extract_handle_from_url",
]

logger = get_logger(__name__)

_PLATFORM_NORMALIZE = {
    "tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube",
    "facebook": "Facebook", "reddit": "Reddit", "bilibili": "Bilibili",
    "xiaohongshu": "Xiaohongshu",
}

def normalize_platform(p: str) -> str:
    """防空值处理，规范化平台名称"""
    p = (p or "").strip()
    if not p:
        return ""
    return _PLATFORM_NORMALIZE.get(p.lower(), p)

def normalize_handle(handle: str) -> str:
    """规范化 handle 前缀"""
    h = handle.strip()
    if not h:
        return ""
    
    # Bilibili 风格兼容
    if h.startswith("uid:"):
        return h
        
    # Reddit 风格兼容
    if re.match(r"^u/", h, re.I):
        return "u/" + h[2:].lstrip("/")
        
    # 普通账号加 @
    return "@" + h.lstrip("@")

def extract_handle_from_url(url: str) -> str:
    """
    Extract creator handle / username / ID from platform profile or video URLs.
    Returns normalized handle string, or '' if not extractable.
    """
    if not url: return ""
    try:
        # TikTok: /@username (暂不处理 vm/vt 短链，交由后续 scraper)
        m = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", url)
        if m: return "@" + m.group(1)
        
        # Instagram profile: /username/ (not /p/, /reel/, /tv/, etc.)
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)/?(?:\?|$)", url)
        if m and m.group(1) not in {"p", "reel", "tv", "explore", "stories", "accounts", "direct", "about", "tagged"}:
            return "@" + m.group(1)
            
        # YouTube: /@channel
        m = re.search(r"youtube\.com/@([A-Za-z0-9._-]+)", url)
        if m: return "@" + m.group(1)
        
        # YouTube: /c/channel or /user/channel (修复括号截断问题)
        m = re.search(r"youtube\.com/(?:c|user|channel)/([A-Za-z0-9._-]+)", url)
        if m: return "@" + m.group(1)
        
        # Reddit: /u/username or /user/username
        m = re.search(r"reddit\.com/(?:u|user)/([A-Za-z0-9_-]+)", url)
        if m: return "u/" + m.group(1)
        
        # Bilibili: space.bilibili.com/uid
        m = re.search(r"space\.bilibili\.com/(\d+)", url)
        if m: return "uid:" + m.group(1)
        
        # Facebook: /pagename (skip generic paths)
        m = re.search(r"facebook\.com/([A-Za-z0-9.]+)/?(?:\?|$|/)", url)
        if m and m.group(1) not in {"watch", "video", "groups", "events", "pages", "profile", "story", "share"}:
            # 采用方案 B：统一加 @ 前缀
            return "@" + m.group(1)
            
    except Exception:
        logger.warning(
            "utils.extract_handle_from_url_failed",
            extra={"url": url},
            exc_info=True,
        )
        
    return ""
