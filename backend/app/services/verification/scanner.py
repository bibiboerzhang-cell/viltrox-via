"""
services/verification/scanner.py — 验证扫描器
==============================================
扫描 Viltrox 官方账号的评论, 找含验证码的, 匹配后更新 verifications 表.

触发方式:
  1. Admin 手动触发 (admin dashboard 按钮)
  2. Cron 自动触发 (pending >= 100 或 > 24h)
  3. 用户主动触发 (点 "I've Posted, Verify Me")

扫描流程:
  1. 拿所有 awaiting_scan 的 verifications (按平台分组)
  2. 对每个平台:
     a. 调 fetch_viltrox_comments(platform) 拿最新评论
     b. 提取所有验证码
     c. 与 pending 验证一一匹配
     d. 评分 → 更新 status
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from functools import partial
from typing import Optional, List, Dict, Any

from app.core.logging import get_logger
from app.db.connection import db_read, db_write, get_conn
from app.services.verification.comment_generator import (
    extract_code_from_text,
)
from app.services.verification.scoring import (
    score_verification_match,
    ScoringResult,
)
from app.services.verification.viltrox_official import SUPPORTED_PLATFORMS
from app.db.repositories.users import mark_social_account_verified

logger = get_logger(__name__)


def _load_pending_verifications(
    platform: Optional[str] = None,
    only_oldest_n: Optional[int] = None,
) -> List[Dict[str, Any]]:
    conn = get_conn()
    sql = "SELECT * FROM verifications WHERE status = 'awaiting_scan'"
    params: list[Any] = []
    if platform:
        sql += " AND platform = ?"
        params.append(platform.lower())
    sql += " ORDER BY created_at ASC"
    if only_oldest_n:
        sql += " LIMIT ?"
        params.append(int(only_oldest_n))
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _apply_platform_scan_batch(
    verifications: List[Dict[str, Any]],
    code_to_comment: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    conn = get_conn()
    stats = {
        "scanned": 0,
        "verified": 0,
        "needs_review": 0,
        "still_pending": 0,
        "failed": 0,
        "expired": 0,
    }
    for verification in verifications:
        if _is_verification_expired(verification):
            _expire_verification(
                conn,
                int(verification["id"]),
                _build_expiry_message(verification.get("expires_at")),
            )
            stats["expired"] += 1
            continue
        stats["scanned"] += 1
        outcome = _apply_scan_for_verification(conn, verification, code_to_comment)
        status = str(outcome.get("status") or "")
        if status in stats:
            stats[status] += 1
        else:
            stats["failed"] += 1
    conn.commit()
    return stats


def _load_verification(verification_id: int) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM verifications WHERE id = ?",
        (verification_id,),
    ).fetchone()
    return dict(row) if row else None


def _expire_single_verification(verification: Dict[str, Any]) -> None:
    conn = get_conn()
    _expire_verification(conn, int(verification["id"]), _build_expiry_message(verification.get("expires_at")))
    conn.commit()


def _apply_single_scan(
    verification: Dict[str, Any],
    code_to_comment: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    conn = get_conn()
    outcome = _apply_scan_for_verification(conn, verification, code_to_comment)
    conn.commit()
    return outcome


def _load_scan_trigger_state() -> Dict[str, Any]:
    conn = get_conn()
    pending_count = conn.execute(
        "SELECT COUNT(*) FROM verifications WHERE status = 'awaiting_scan'"
    ).fetchone()[0]
    oldest_created_at = None
    if pending_count > 0:
        oldest = conn.execute(
            """
            SELECT created_at FROM verifications
            WHERE status = 'awaiting_scan'
            ORDER BY created_at ASC LIMIT 1
            """
        ).fetchone()
        oldest_created_at = oldest[0] if oldest else None
    return {
        "pending_count": int(pending_count or 0),
        "oldest_created_at": oldest_created_at,
    }


# ──────────────────────────────────────────────────────────────
# 扫描入口 (cron / admin / user 都用这个)
# ──────────────────────────────────────────────────────────────

async def scan_pending_verifications(
    platform: Optional[str] = None,
    only_oldest_n: Optional[int] = None,
) -> Dict[str, Any]:
    """
    扫描所有 awaiting_scan 的 verifications.
    
    Args:
        platform: 只扫某个平台 (None = 全部)
        only_oldest_n: 只扫最早的 N 条 (None = 全部)
    
    Returns:
        {
          "scanned": int,           # 扫了多少条 verification
          "verified": int,          # 自动通过
          "needs_review": int,      # 进入审核队列
          "still_pending": int,     # 还没找到评论 (下次再扫)
          "failed": int,            # 直接失败
          "errors": list,
          "duration_sec": float,
        }
    """
    from app.services.scraping.apify import fetch_viltrox_comments, normalize_comment
    
    t0 = asyncio.get_running_loop().time()
    pending_verifications = await db_read(partial(_load_pending_verifications, platform, only_oldest_n))
    logger.info(
        "verification.scanner.pending_loaded",
        extra={"count": len(pending_verifications), "platform": platform or "all"},
    )
    
    if not pending_verifications:
        return {
            "scanned": 0,
            "verified": 0,
            "needs_review": 0,
            "still_pending": 0,
            "failed": 0,
            "errors": [],
            "duration_sec": 0,
        }
    
    # Step 2: 按平台分组
    by_platform: Dict[str, List[Dict]] = {}
    for v in pending_verifications:
        p = (v.get("platform") or "").lower()
        if p not in SUPPORTED_PLATFORMS:
            continue
        by_platform.setdefault(p, []).append(v)
    
    # Step 3: 对每个平台拉评论 + 匹配
    stats = {
        "scanned": 0,
        "verified": 0,
        "needs_review": 0,
        "still_pending": 0,
        "failed": 0,
        "expired": 0,
        "errors": [],
    }
    
    for plat, verifs in by_platform.items():
        logger.info("verification.scanner.platform_start", extra={"platform": plat, "count": len(verifs)})
        
        # 拉这个平台的 Viltrox 评论
        try:
            raw_comments = await fetch_viltrox_comments(plat)
        except Exception as exc:
            logger.exception("verification.scanner.fetch_failed", extra={"platform": plat})
            stats["errors"].append(f"{plat}: {exc}")
            continue
        
        if not raw_comments:
            logger.info("verification.scanner.no_comments", extra={"platform": plat})
            stats["still_pending"] += len(verifs)
            continue
        
        # 标准化评论
        comments = [normalize_comment(c, plat) for c in raw_comments]
        
        # 提取所有评论里的验证码 (建索引)
        code_to_comment: Dict[str, Dict] = {}
        for c in comments:
            code = extract_code_from_text(c["text"])
            if code:
                code_to_comment[code] = c
        
        logger.info(
            "verification.scanner.comments_indexed",
            extra={"platform": plat, "comments": len(comments), "codes": len(code_to_comment)},
        )
        batch_stats = await db_write(partial(_apply_platform_scan_batch, verifs, code_to_comment))
        for key in ("scanned", "verified", "needs_review", "still_pending", "failed", "expired"):
            stats[key] += int(batch_stats.get(key, 0))
    
    duration = asyncio.get_running_loop().time() - t0
    stats["duration_sec"] = round(duration, 2)
    
    logger.info("verification.scanner.complete", extra={"duration_sec": round(duration, 2), **stats})
    return stats


# ──────────────────────────────────────────────────────────────
# 单条扫描 (用户主动触发, 立刻验证某一条)
# ──────────────────────────────────────────────────────────────

async def scan_single_verification(verification_id: int) -> Dict[str, Any]:
    """
    立刻扫描单条 verification.
    用于:
      - 用户点 "I've Posted, Verify Me"
      - Admin 在后台点 "重新扫描"
    
    Returns:
        {
          "status": "verified" / "needs_review" / "failed" / "still_pending",
          "score": float,
          "message": str,
        }
    """
    from app.services.scraping.apify import fetch_viltrox_comments, normalize_comment
    
    verification = await db_read(partial(_load_verification, verification_id))
    if not verification:
        return {"status": "error", "message": "Verification not found"}
    plat = (verification.get("platform") or "").lower()
    
    if plat not in SUPPORTED_PLATFORMS:
        return {"status": "error", "message": f"Platform {plat} not supported"}

    if _is_verification_expired(verification):
        await db_write(partial(_expire_single_verification, verification))
        return {"status": "expired", "score": 0.0, "message": _build_expiry_message(verification.get("expires_at"))}
    
    try:
        raw_comments = await fetch_viltrox_comments(plat)
    except Exception as exc:
        logger.exception("verification.scanner.single_fetch_failed", extra={"verification_id": verification_id, "platform": plat})
        return {"status": "error", "message": "Failed to fetch verification comments"}

    comments = [normalize_comment(c, plat) for c in (raw_comments or [])]
    code_to_comment: Dict[str, Dict[str, Any]] = {}
    for comment in comments:
        code = extract_code_from_text(comment["text"])
        if code:
            code_to_comment[code] = comment

    return await db_write(partial(_apply_single_scan, verification, code_to_comment))


def _apply_scan_for_verification(
    conn,
    verification: Dict[str, Any],
    code_to_comment: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    verification_id = int(verification["id"])
    expected_code = str(verification.get("code") or "").strip().upper()
    if not expected_code:
        _update_verification_status(conn, verification_id, "failed", reason="No code in DB")
        return {"status": "failed", "score": 0.0, "message": "No verification code stored"}

    matched_comment = code_to_comment.get(expected_code)
    if not matched_comment:
        _bump_scan_count(conn, verification_id)
        return {"status": "still_pending", "score": 0.0, "message": "No matching comment found yet"}

    scoring = score_verification_match(
        claimed_handle=verification.get("baseline_username") or verification.get("handle", ""),
        claimed_followers=verification.get("baseline_followers"),
        claimed_avatar_url=verification.get("baseline_avatar_url"),
        claimed_bio=verification.get("baseline_bio"),
        comment_author=matched_comment["author"],
        comment_has_code=True,
        comment_published_text=matched_comment["published_text"],
        actual_profile_accessible=False,
    )
    _save_scan_result(
        conn,
        verification_id=verification_id,
        verification=verification,
        scoring=scoring,
        matched_comment=matched_comment,
    )
    return {
        "status": scoring.status,
        "score": scoring.score,
        "message": f"Scan complete. Status: {scoring.status}",
        "verification_id": verification_id,
    }


# ──────────────────────────────────────────────────────────────
# 数据库辅助函数
# ──────────────────────────────────────────────────────────────

def _update_verification_status(conn, verification_id: int, status: str, reason: str = ""):
    """更新 verification 状态"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE verifications 
        SET status = ?, last_scanned_at = ?, note = ?
        WHERE id = ?
        """,
        (status, now, reason, verification_id),
    )


def _parse_utc_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _build_expiry_message(expires_at: Optional[str]) -> str:
    if expires_at:
        return f"Verification code expired at {expires_at} before a matching comment was found"
    return "Verification code expired before a matching comment was found"


def _is_verification_expired(verification: Dict[str, Any]) -> bool:
    expires_at = _parse_utc_timestamp(verification.get("expires_at"))
    if not expires_at:
        return False
    return datetime.utcnow() >= expires_at


def _expire_verification(conn, verification_id: int, reason: str):
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE verifications
        SET status = 'expired',
            last_scanned_at = ?,
            note = ?
        WHERE id = ? AND status IN ('pending', 'awaiting_scan')
        """,
        (now, reason, verification_id),
    )


def _bump_scan_count(conn, verification_id: int):
    """没找到时, 增加扫描次数"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE verifications
        SET scan_count = COALESCE(scan_count, 0) + 1,
            last_scanned_at = ?
        WHERE id = ?
        """,
        (now, verification_id),
    )


def _save_scan_result(
    conn,
    verification_id: int,
    verification: Dict[str, Any],
    scoring: ScoringResult,
    matched_comment: Dict[str, Any],
):
    """保存扫描结果到 verifications 表"""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    conn.execute(
        """
        UPDATE verifications
        SET status              = ?,
            match_score         = ?,
            comment_id          = ?,
            comment_username    = ?,
            comment_text        = ?,
            comment_video_url   = ?,
            scan_count          = COALESCE(scan_count, 0) + 1,
            last_scanned_at     = ?,
            note                = ?,
            approved_at         = CASE WHEN ? = 'verified' THEN ? ELSE approved_at END
        WHERE id = ?
        """,
        (
            scoring.status,
            scoring.score,
            matched_comment.get("comment_id", ""),
            matched_comment.get("author", ""),
            matched_comment.get("text", "")[:500],
            matched_comment.get("video_url", ""),
            now,
            " | ".join(scoring.reasons[:5]),
            scoring.status,  # for CASE
            now,
            verification_id,
        ),
    )
    if scoring.status == "verified":
        try:
            mark_social_account_verified(
                user_id=int(verification.get("user_id") or 0),
                platform=str(verification.get("platform") or "").lower(),
                handle=str(verification.get("handle") or ""),
            )
        except Exception as exc:
            logger.warning(
                "verification.scanner.social_sync_failed",
                extra={"verification_id": verification_id, "user_id": int(verification.get("user_id") or 0)},
                exc_info=True,
            )


# ──────────────────────────────────────────────────────────────
# Cron job 入口 (APScheduler 调用)
# ──────────────────────────────────────────────────────────────

# 触发阈值
SCAN_TRIGGER_THRESHOLD = 100   # pending >= 100 → 触发
SCAN_MAX_AGE_HOURS = 24        # 最早一条 > 24h 也触发


async def cron_scan_check():
    """
    Cron 入口 — 每 5 分钟检查是否需要扫描.
    
    触发条件 (任一满足):
      A. pending >= SCAN_TRIGGER_THRESHOLD
      B. 最早 pending 等了 > SCAN_MAX_AGE_HOURS 小时
    """
    state = await db_read(_load_scan_trigger_state)
    pending_count = int(state.get("pending_count") or 0)
    
    if pending_count >= SCAN_TRIGGER_THRESHOLD:
        logger.info("verification.scanner.cron_trigger_size", extra={"pending": pending_count, "threshold": SCAN_TRIGGER_THRESHOLD})
        return await scan_pending_verifications()
    
    # Check 2: 时间
    if pending_count > 0:
        oldest_created_at = state.get("oldest_created_at")
        if oldest_created_at:
            try:
                oldest_dt = datetime.fromisoformat(str(oldest_created_at).replace("Z", "+00:00"))
                age_hours = (datetime.utcnow().replace(tzinfo=oldest_dt.tzinfo) - oldest_dt).total_seconds() / 3600
                
                if age_hours >= SCAN_MAX_AGE_HOURS:
                    logger.info("verification.scanner.cron_trigger_age", extra={"pending": pending_count, "age_hours": round(age_hours, 1)})
                    return await scan_pending_verifications()
            except Exception:
                logger.warning("verification.scanner.cron_parse_failed", extra={"oldest_created_at": oldest_created_at}, exc_info=True)
    
    logger.info("verification.scanner.cron_skip", extra={"pending": pending_count, "threshold": SCAN_TRIGGER_THRESHOLD})
    return {"scanned": 0, "skipped": True, "pending": pending_count}
