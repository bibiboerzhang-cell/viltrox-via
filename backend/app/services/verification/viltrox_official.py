"""
services/verification/viltrox_official.py — Viltrox 官方账号配置
================================================================
所有平台的 Viltrox 官方账号 URL.
这些是用户去评论验证码的目标账号.

数据来源: 通过 Apify YouTube channel scraper 从 @viltroxofficial 频道
descriptionLinks 字段抓取得到 (2026-04-09 验证).
"""
from __future__ import annotations

from typing import Optional

from app.shared import social_identity as _social_identity


# ──────────────────────────────────────────────────────────────
# Viltrox 官方账号清单 (5 平台 + Twitter)
# ──────────────────────────────────────────────────────────────

VILTROX_OFFICIAL_ACCOUNTS = {
    "youtube":   "https://www.youtube.com/@viltroxofficial",
    "instagram": "https://www.instagram.com/viltrox_global/",
    "facebook":  "https://www.facebook.com/viltrox.official/",
    "tiktok":    "https://www.tiktok.com/@viltrox.global",
    "reddit":    "https://www.reddit.com/r/VILTROX_GLOBAL/",
    "twitter":   "https://twitter.com/ViltroxOfficial",
}

# 用户友好的展示名 (前端用)
VILTROX_DISPLAY_NAMES = {
    "youtube":   "Viltrox Official (YouTube)",
    "instagram": "@viltrox_global (Instagram)",
    "facebook":  "Viltrox Official (Facebook)",
    "tiktok":    "@viltrox.global (TikTok)",
    "reddit":    "r/VILTROX_GLOBAL (Reddit)",
    "twitter":   "@ViltroxOfficial (Twitter)",
}

# 验证系统支持的平台 (Twitter 暂不在内, 加入需另写 actor)
SUPPORTED_PLATFORMS = ["youtube", "instagram", "facebook", "tiktok", "reddit"]


def get_viltrox_account_url(platform: str) -> Optional[str]:
    """获取 Viltrox 在指定平台的官号 URL"""
    return VILTROX_OFFICIAL_ACCOUNTS.get(platform.lower())


def get_viltrox_display_name(platform: str) -> str:
    """前端展示用的友好名称"""
    return VILTROX_DISPLAY_NAMES.get(platform.lower(), platform.title())


def normalize_claimed_handle(handle: str, platform: str) -> str:
    return _social_identity.normalize_claimed_handle(handle, platform)


def build_profile_url(platform: str, handle: str) -> str:
    plat = (platform or "").lower().strip()
    clean = (handle or "").strip()
    if not plat or not clean:
        return ""
    if plat == "instagram":
        return f"https://www.instagram.com/{clean.lstrip('@')}/"
    if plat == "tiktok":
        return f"https://www.tiktok.com/@{clean.lstrip('@')}"
    if plat == "youtube":
        return f"https://www.youtube.com/@{clean.lstrip('@')}"
    if plat == "facebook":
        return f"https://www.facebook.com/{clean.lstrip('@')}/"
    if plat == "reddit":
        if clean.lower().startswith("u/"):
            return f"https://www.reddit.com/{clean}/"
        return f"https://www.reddit.com/u/{clean.lstrip('@/')}/"
    if plat == "twitter":
        return f"https://twitter.com/{clean.lstrip('@')}"
    return ""


# ──────────────────────────────────────────────────────────────
# 主页 URL 解析 — 从用户输入的 profile URL 提取真实 handle
# ──────────────────────────────────────────────────────────────

def detect_platform_from_profile_url(url: str) -> Optional[str]:
    """从用户输入的主页 URL 自动判断平台"""
    return _social_identity.detect_platform_from_profile_url(url)


def extract_handle_from_profile_url(url: str, platform: Optional[str] = None) -> str:
    """
    从主页 URL 提取真实 handle (username).
    
    Examples:
      https://www.instagram.com/petapixel/         → petapixel
      https://www.instagram.com/petapixel/?hl=en   → petapixel
      https://www.tiktok.com/@viltrox.usa          → viltrox.usa
      https://www.tiktok.com/@viltrox.usa/         → viltrox.usa
      https://www.youtube.com/@PetaPixel           → PetaPixel
      https://www.youtube.com/c/PetaPixel          → PetaPixel
      https://www.youtube.com/channel/UCxxxxx      → UCxxxxx
      https://www.youtube.com/user/PetaPixel       → PetaPixel
      https://www.reddit.com/user/PetaPixel        → PetaPixel
      https://www.reddit.com/u/PetaPixel           → PetaPixel
      https://www.facebook.com/PetaPixel           → PetaPixel
      https://www.facebook.com/pages/Name/12345    → 12345
    
    Returns:
      handle (lowercase 不强制 - 平台区分大小写)
    """
    if not url:
        return ""
    resolved_platform = platform or detect_platform_from_profile_url(url)
    if not resolved_platform:
        return ""
    return _social_identity.extract_handle_from_profile_url(url, resolved_platform)


# ──────────────────────────────────────────────────────────────
# 黑名单 — 受保护的大 V 账号 (绑定时直接进入人工审核)
# ──────────────────────────────────────────────────────────────

PROTECTED_HANDLES = {
    "youtube": [
        "petapixel", "dpreview", "fstoppers", "maxyuryev",
        "tonynorthrup", "thomasheaton", "peterlindgren",
        "viltroxofficial", "viltrox", "viltroxglobal",
    ],
    "instagram": [
        "petapixel", "dpreview", "fstoppers",
        "viltrox_global", "viltrox.flash", "viltrox.usa",
        "viltrox", "viltroxofficial",
    ],
    "tiktok": [
        "petapixel", "viltrox.global", "viltrox.usa",
        "viltrox", "viltroxofficial",
    ],
    "reddit": [
        "viltrox", "viltroxofficial",
    ],
    "facebook": [
        "viltrox.official", "viltrox", "petapixel",
    ],
}


def is_protected_handle(handle: str, platform: str) -> bool:
    """检查 handle 是否在受保护名单 (大 V 或 Viltrox 自己)"""
    if not handle:
        return False
    handle_lower = handle.lower().lstrip("@")
    protected = PROTECTED_HANDLES.get(platform.lower(), [])
    return handle_lower in [p.lower() for p in protected]
