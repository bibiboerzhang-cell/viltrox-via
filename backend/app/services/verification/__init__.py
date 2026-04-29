"""
services/verification — 创作者社交账号验证系统
==================================================
通过让用户在 Viltrox 官方账号下评论自然好评 + 验证码
来证明对社交账号的控制权。

核心流程:
  1. 用户输入主页 URL
  2. 系统抓取主页基线 (username, followers, avatar)
  3. GPT 生成自然好评 + 验证码
  4. 用户复制粘贴到 Viltrox 官号任意视频/帖子下
  5. 后台扫描 Viltrox 官号评论
  6. 找到验证码 → 评分制匹配 → 决定状态

状态机:
  pending        → 用户已生成验证码, 还没评论
  awaiting_scan  → 用户点 "我已评论", 等扫描
  verified       → 评分 >= 80, 自动通过
  needs_review   → 评分 60-79, admin 人工审核
  failed         → 评分 < 60 或 24 小时后还没找到
"""

from app.services.verification.comment_generator import generate_praise_comment
from app.services.verification.scoring import score_verification_match, ScoringResult
from app.services.verification.scanner import scan_pending_verifications, scan_single_verification
from app.services.verification.viltrox_official import (
    VILTROX_OFFICIAL_ACCOUNTS,
    get_viltrox_account_url,
    SUPPORTED_PLATFORMS,
)

__all__ = [
    "generate_praise_comment",
    "score_verification_match",
    "ScoringResult",
    "scan_pending_verifications",
    "scan_single_verification",
    "VILTROX_OFFICIAL_ACCOUNTS",
    "get_viltrox_account_url",
    "SUPPORTED_PLATFORMS",
]
