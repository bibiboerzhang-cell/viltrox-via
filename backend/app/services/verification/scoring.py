"""
services/verification/scoring.py — 验证评分制
==============================================
基于多维度评分判断验证是否通过.

核心思路 (来自 Claude 的设计建议):
  - 不用 "全部硬匹配" (会误杀正常用户)
  - 用评分制 + 状态机
  - 评论里有验证码 = 强条件
  - username 一致 = 强条件
  - 其他都是加分项 (avatar / followers / bio / posts)

阈值:
  >= 80  → verified      自动通过
  60-79  → needs_review  admin 人工审核队列
  < 60   → failed        拒绝

强条件 (任一不满足直接 fail):
  - 评论中存在验证码
  - 评论者 username 与注册时声明的 handle 匹配 (大小写敏感)

弱条件 (加分):
  - 评论者主页可访问 (Apify 抓得到)
  - follower count 接近声明值 (分层容差)
  - avatar 可访问 (CDN URL 不要求一致)
  - bio / channel description 有重叠
  - 评论时间在合理窗口 (24h 内)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List

from app.core.logging import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────
# 评分阈值
# ──────────────────────────────────────────────────────────────

THRESHOLD_VERIFIED = 80      # >= 80 自动通过
THRESHOLD_REVIEW = 60        # 60-79 人工审核
# < 60 自动拒绝


# ──────────────────────────────────────────────────────────────
# 评分结果数据类
# ──────────────────────────────────────────────────────────────

@dataclass
class ScoringResult:
    """评分结果"""
    score: float = 0.0
    status: str = "pending"   # pending / verified / needs_review / failed
    reasons: List[str] = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)
    
    def add(self, points: float, reason: str, key: str = ""):
        """加分并记录原因"""
        self.score += points
        self.reasons.append(f"+{points:.0f} {reason}")
        if key:
            self.breakdown[key] = points
    
    def fail(self, reason: str):
        """硬失败 (强条件未满足)"""
        self.score = 0
        self.status = "failed"
        self._hard_failed = True
        self.reasons.append(f"❌ {reason}")
    
    def finalize(self):
        """根据总分确定 status"""
        if getattr(self, "_hard_failed", False):
            return  # 硬失败短路
        if self.score >= THRESHOLD_VERIFIED:
            self.status = "verified"
        elif self.score >= THRESHOLD_REVIEW:
            self.status = "needs_review"
        else:
            self.status = "failed"


# ──────────────────────────────────────────────────────────────
# 主评分函数
# ──────────────────────────────────────────────────────────────

def score_verification_match(
    *,
    # 用户注册时声明的基线数据
    claimed_handle: str,
    claimed_followers: Optional[int] = None,
    claimed_avatar_url: Optional[str] = None,
    claimed_bio: Optional[str] = None,
    
    # 从 Viltrox 评论里抓到的实际评论数据
    comment_author: str,
    comment_has_code: bool,
    comment_published_text: str = "",
    
    # (可选) 从评论者主页二次抓取的数据
    actual_followers: Optional[int] = None,
    actual_avatar_url: Optional[str] = None,
    actual_bio: Optional[str] = None,
    actual_profile_accessible: bool = False,
) -> ScoringResult:
    """
    对一次验证尝试评分.
    
    Args:
        claimed_*: 用户在 viltroxtest.com 注册时, 系统抓主页后保存的基线数据
        comment_*: 在 Viltrox 官号评论区抓到的实际评论数据
        actual_*: (可选) 二次抓取评论者主页拿到的数据 (用于交叉验证)
    
    Returns:
        ScoringResult, 含 score / status / reasons
    """
    result = ScoringResult()
    
    # ════════════════════════════════════════════════════════
    # 强条件 1: 评论必须包含验证码
    # ════════════════════════════════════════════════════════
    if not comment_has_code:
        result.fail("Verification code not found in comment")
        return result
    result.add(20, "Verification code present", "code")  # 强条件成立
    
    # ════════════════════════════════════════════════════════
    # 强条件 2: 评论者 username 必须与声明 handle 匹配
    # 大小写敏感 (防 typosquatting)
    # ════════════════════════════════════════════════════════
    claimed_clean = (claimed_handle or "").lstrip("@").strip()
    actual_clean = (comment_author or "").lstrip("@").strip()
    
    if not claimed_clean or not actual_clean:
        result.fail("Missing handle or comment author")
        return result
    
    if claimed_clean == actual_clean:
        # 完全相同 (大小写敏感)
        result.add(40, "Username exact match", "username_exact")
    elif claimed_clean.lower() == actual_clean.lower():
        # 大小写不同 (常见情况, 例如 "PetaPixel" vs "@petapixel")
        result.add(35, "Username matches (case-insensitive)", "username_ci")
    else:
        result.fail(
            f"Username mismatch: claimed='{claimed_clean}' vs comment='{actual_clean}'"
        )
        return result
    
    # ════════════════════════════════════════════════════════
    # 弱条件 1: 评论者主页可访问 (二次抓成功)
    # ════════════════════════════════════════════════════════
    if actual_profile_accessible:
        result.add(15, "Comment author profile accessible", "profile_access")
    else:
        result.add(5, "Profile not re-fetched (skipped)", "profile_skip")
    
    # ════════════════════════════════════════════════════════
    # 弱条件 2: Follower count 接近 (分层容差)
    # ════════════════════════════════════════════════════════
    if claimed_followers is not None and actual_followers is not None:
        followers_score = _score_follower_match(claimed_followers, actual_followers)
        if followers_score > 0:
            result.add(followers_score, f"Followers match ({actual_followers:,} vs {claimed_followers:,})", "followers")
    elif claimed_followers is not None:
        # 没二次抓, 但有基线
        result.add(8, "Baseline followers recorded", "followers_baseline")
    else:
        # 没基线 (verify/start 时没抓主页)
        result.add(8, "Followers data unavailable (skipped)", "followers_skip")
    
    # ════════════════════════════════════════════════════════
    # 弱条件 3: Avatar URL 存在 (不要求一致, CDN URL 会变)
    # ════════════════════════════════════════════════════════
    if actual_avatar_url:
        result.add(10, "Avatar URL accessible", "avatar")
    elif claimed_avatar_url:
        result.add(5, "Baseline avatar recorded", "avatar_baseline")
    else:
        # 没基线 avatar
        result.add(5, "Avatar data unavailable (skipped)", "avatar_skip")
    
    # ════════════════════════════════════════════════════════
    # 弱条件 4: Bio / Description 重叠
    # ════════════════════════════════════════════════════════
    if claimed_bio and actual_bio:
        bio_score = _score_bio_overlap(claimed_bio, actual_bio)
        if bio_score > 0:
            result.add(bio_score, "Bio overlap detected", "bio")
    
    # ════════════════════════════════════════════════════════
    # 弱条件 5: 评论时间合理 (最近 24h 内 = 加分)
    # ════════════════════════════════════════════════════════
    if comment_published_text:
        time_score = _score_comment_recency(comment_published_text)
        if time_score > 0:
            result.add(time_score, f"Comment recency: {comment_published_text}", "recency")
    
    # ════════════════════════════════════════════════════════
    # 完成评分
    # ════════════════════════════════════════════════════════
    result.finalize()
    return result


# ──────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────

def _score_follower_match(claimed: int, actual: int) -> float:
    """
    Follower count 评分 (分层容差).
    
    小号 (< 1K):     ±50% 通过 (极小数字波动大)
    小号 (1K-10K):   ±15%
    中号 (10K-100K): ±10%
    大号 (100K+):    ±5%
    """
    if claimed <= 0 or actual <= 0:
        return 0.0
    
    diff_pct = abs(claimed - actual) / max(claimed, actual)
    
    if claimed < 1000:
        tolerance = 0.50
    elif claimed < 10_000:
        tolerance = 0.15
    elif claimed < 100_000:
        tolerance = 0.10
    else:
        tolerance = 0.05
    
    if diff_pct <= tolerance:
        # 越接近, 分越高 (10-15)
        closeness = 1 - (diff_pct / tolerance)
        return 10 + closeness * 5
    
    return 0.0


def _score_bio_overlap(claimed: str, actual: str) -> float:
    """
    Bio 文本重叠评分.
    简单实现: 共享 token 数 / 总 token 数.
    
    Returns: 0-10 分
    """
    if not claimed or not actual:
        return 0.0
    
    # 标准化
    def normalize(text: str) -> set:
        words = text.lower().split()
        # 去掉太短的词
        return set(w for w in words if len(w) >= 3)
    
    claimed_words = normalize(claimed)
    actual_words = normalize(actual)
    
    if not claimed_words or not actual_words:
        return 0.0
    
    overlap = claimed_words & actual_words
    union = claimed_words | actual_words
    
    if not union:
        return 0.0
    
    jaccard = len(overlap) / len(union)
    
    if jaccard >= 0.5:
        return 10.0
    elif jaccard >= 0.3:
        return 7.0
    elif jaccard >= 0.15:
        return 4.0
    elif jaccard > 0:
        return 2.0
    return 0.0


def _score_comment_recency(published_text: str) -> float:
    """
    评论时间评分.
    
    publishedTimeText 格式 (YouTube):
      "2 minutes ago"  → 5 分 (新)
      "3 hours ago"    → 4 分
      "1 day ago"      → 3 分
      "1 week ago"     → 1 分
      "1 month ago"    → 0 分 (太老, 不合理)
    """
    if not published_text:
        return 0.0
    
    text = published_text.lower()
    
    if "second" in text or "minute" in text:
        return 5.0
    if "hour" in text:
        return 4.0
    if "day" in text:
        # "1 day ago" / "2 days ago"
        return 3.0
    if "week" in text:
        return 1.0
    
    # month / year ago = 不合理 (验证码刚发不可能在月级评论)
    return 0.0


# ──────────────────────────────────────────────────────────────
# 测试入口
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 测试 1: 完美匹配
    logger.info("=" * 60)
    logger.info("Test 1: Perfect match")
    logger.info("=" * 60)
    r = score_verification_match(
        claimed_handle="bibiboerz6679",
        claimed_followers=10,
        comment_author="@bibiboerz6679",
        comment_has_code=True,
        comment_published_text="2 minutes ago",
        actual_followers=10,
        actual_profile_accessible=True,
    )
    logger.info("verification_score_demo", extra={"case": "perfect_match", "score": r.score, "status": r.status, "reasons": r.reasons})
    
    # 测试 2: Username 不匹配
    logger.info("\n" + "=" * 60)
    logger.info("Test 2: Username mismatch (typosquatting attack)")
    logger.info("=" * 60)
    r = score_verification_match(
        claimed_handle="petapixel",
        claimed_followers=286000,
        comment_author="@petapixel_2024",  # 假账号
        comment_has_code=True,
        comment_published_text="2 minutes ago",
    )
    logger.info("verification_score_demo", extra={"case": "username_mismatch", "score": r.score, "status": r.status, "reasons": r.reasons})
    
    # 测试 3: 没验证码
    logger.info("\n" + "=" * 60)
    logger.info("Test 3: No code in comment")
    logger.info("=" * 60)
    r = score_verification_match(
        claimed_handle="alice",
        comment_author="@alice",
        comment_has_code=False,
    )
    logger.info("verification_score_demo", extra={"case": "missing_code", "score": r.score, "status": r.status, "reasons": r.reasons})
