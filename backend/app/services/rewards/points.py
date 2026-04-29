"""
services/rewards/points.py — 积分管理（原子事务版）
所有积分操作使用 WHERE points_balance >= ? 防止超扣
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import close_standalone_conn, db_write, get_conn, is_postgres_runtime, open_standalone_conn
from app.core.config import CAMPAIGN_START
from app.core.security import invalidate_user_cache
from app.services.cache import cache_clear
from app.services.creator_program import sync_creator_program_state
from app.services.trust import get_remaining_daily_points_cap, get_trust_snapshot

logger = get_logger(__name__)


def calculate_submission_points(final_score: int) -> int:
    score = int(final_score or 0)
    if score <= 0:
        return 0
    return max(10, score)


def _invalidate_creator_runtime_cache(user_id: int | None) -> None:
    if user_id:
        cache_clear(prefix=f"creator:{int(user_id)}:")


def _sync_creator_program_after_points_change(user_id: int | None, reason: str) -> None:
    if not user_id:
        return
    invalidate_user_cache(int(user_id))
    _invalidate_creator_runtime_cache(int(user_id))
    try:
        sync_creator_program_state(int(user_id), reason=reason)
    except Exception as exc:
        logger.warning(
            "creator program sync after points mutation failed | user_id=%s | reason=%s | error=%s",
            user_id,
            reason,
            exc,
        )


def _resolve_points_user_id(conn: Any, submission_id: int, user_id: int | None, extracted_handle: str) -> int | None:
    resolved_user_id = int(user_id or 0)
    if resolved_user_id > 0:
        return resolved_user_id
    if not extracted_handle:
        return None
    norm = extracted_handle.lstrip("@").lower()
    social = conn.execute(
        """SELECT user_id FROM user_social_accounts
           WHERE LOWER(REPLACE(handle,'@',''))=? AND verified=1
           LIMIT 1""",
        (norm,),
    ).fetchone()
    if social:
        return int(social["user_id"] or 0) or None
    logger.info("points unmatched | submission_id=%s | handle=%s", submission_id, extracted_handle)
    return None


def _award_points_on_conn(conn: Any, submission_id: int, extracted_handle: str, final_score: int) -> dict:
    if final_score <= 0:
        return {"points": 0, "user_id": None}

    if datetime.utcnow() < CAMPAIGN_START:
        conn.execute(
            "UPDATE submissions SET points_status='test', points_awarded=0 WHERE id=?",
            (submission_id,),
        )
        logger.info("points skipped for test period | submission_id=%s", submission_id)
        return {"points": 0, "user_id": None, "test_period": True}

    sub = conn.execute(
        "SELECT points_status, user_id FROM submissions WHERE id=?",
        (submission_id,),
    ).fetchone()
    if not sub:
        return {"points": 0, "user_id": None}
    if sub["points_status"] in {"partial", "awarded"}:
        return {"points": 0, "user_id": int(sub["user_id"] or 0) or None}

    user_id = _resolve_points_user_id(conn, submission_id, sub["user_id"], extracted_handle)
    if not user_id:
        conn.execute(
            "UPDATE submissions SET points_status='unmatched' WHERE id=?",
            (submission_id,),
        )
        return {"points": 0, "user_id": None}

    user = conn.execute(
        "SELECT id, status, points_balance FROM users WHERE id=? AND status='approved'",
        (user_id,),
    ).fetchone()
    if not user:
        return {"points": 0, "user_id": None}

    trust = get_trust_snapshot(
        int(user_id),
        persist_if_stale=True,
        reason="points_award",
        context={"submission_id": int(submission_id)},
    ).as_dict()

    total_points = calculate_submission_points(final_score)
    cap_status = get_remaining_daily_points_cap(
        int(user_id),
        int((trust.get("limits") or {}).get("daily_points_cap", total_points)),
    )
    total_points = min(total_points, int(cap_status["remaining"]))
    if total_points <= 0:
        conn.execute(
            """
            UPDATE submissions
            SET points_status='cap_blocked', points_awarded=0, points_pending=0, user_id=?
            WHERE id=?
            """,
            (user_id, submission_id),
        )
        return {
            "points": 0,
            "user_id": user_id,
            "capped": True,
            "daily_points_cap": cap_status["daily_points_cap"],
            "earned_today": cap_status["earned_today"],
        }

    partial_points = max(1, int(total_points * 0.4))
    pending_points = total_points - partial_points
    now_dt = datetime.utcnow()
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    confirm_at = (now_dt + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn.execute(
        "UPDATE users SET points_balance=points_balance+?, points_total=points_total+? WHERE id=?",
        (partial_points, partial_points, user_id),
    )
    conn.execute(
        """INSERT INTO points_log
           (created_at, user_id, submission_id, delta, reason, balance_after)
           VALUES (?,?,?,?,?,
             (SELECT points_balance FROM users WHERE id=?))""",
        (
            now,
            user_id,
            submission_id,
            partial_points,
            f"投稿初审通过 #{submission_id} 立即发放 40% (活动分={final_score}, 总分={total_points})",
            user_id,
        ),
    )
    conn.execute(
        """UPDATE submissions
           SET points_status='partial', points_awarded=?, points_pending=?,
               confirm_at=?, user_id=?
           WHERE id=?""",
        (partial_points, pending_points, confirm_at, user_id, submission_id),
    )
    logger.info(
        "points awarded | user_id=%s | submission_id=%s | points=%s",
        user_id,
        submission_id,
        partial_points,
    )
    return {"points": partial_points, "user_id": user_id}


def _reverse_submission_points_on_conn(conn: Any, submission_id: int, reason: str = "") -> dict:
    row = conn.execute(
        "SELECT id, user_id, points_awarded, points_pending, points_status FROM submissions WHERE id=?",
        (submission_id,),
    ).fetchone()
    if not row:
        return {"status": "missing", "points": 0, "user_id": None}

    user_id = int(row["user_id"] or 0)
    awarded = int(row["points_awarded"] or 0)
    pending = int(row["points_pending"] or 0)
    if awarded <= 0:
        conn.execute(
            "UPDATE submissions SET points_awarded=0, points_pending=0, points_status='revoked' WHERE id=?",
            (submission_id,),
        )
        return {"status": "ok", "points": 0, "pending_cleared": pending, "user_id": user_id or None}

    if user_id <= 0:
        raise RuntimeError(f"Submission #{submission_id} has awarded points but no linked user")

    user = conn.execute(
        "SELECT points_balance, points_total FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not user:
        raise RuntimeError(f"User #{user_id} not found while reversing submission #{submission_id}")

    current_balance = int(user["points_balance"] or 0)
    current_total = int(user["points_total"] or 0)
    if current_balance < awarded:
        raise ValueError("User has already spent awarded points; adjust balance before deleting submission")

    new_balance = current_balance - awarded
    new_total = max(0, current_total - awarded)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    reason_text = reason.strip() or f"Submission #{submission_id} points reversed"
    conn.execute(
        "UPDATE users SET points_balance=?, points_total=? WHERE id=?",
        (new_balance, new_total, user_id),
    )
    conn.execute(
        """INSERT INTO points_log
           (created_at, user_id, submission_id, delta, reason, balance_after)
           VALUES (?,?,?,?,?,?)""",
        (now, user_id, submission_id, -awarded, reason_text, new_balance),
    )
    conn.execute(
        "UPDATE submissions SET points_awarded=0, points_pending=0, points_status='revoked' WHERE id=?",
        (submission_id,),
    )
    logger.info(
        "points reversed | user_id=%s | submission_id=%s | points=%s",
        user_id,
        submission_id,
        awarded,
    )
    return {"status": "ok", "points": awarded, "pending_cleared": pending, "user_id": user_id}


def _redeem_points_atomic_sync(user_id: int, points_cost: int, body: dict) -> int:
    conn = get_conn()

    addr_snapshot = "{}"
    if body.get("address_id"):
        addr = conn.execute(
            "SELECT * FROM user_addresses WHERE id=? AND user_id=?",
            (body["address_id"], user_id)
        ).fetchone()
        if addr:
            import json
            addr_snapshot = json.dumps(dict(addr), ensure_ascii=False)

    cur = conn.execute(
        """UPDATE users
           SET points_balance = points_balance - ?
           WHERE id = ? AND points_balance >= ?""",
        (points_cost, user_id, points_cost)
    )
    if cur.rowcount != 1:
        raise ValueError("Insufficient points")

    now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    red_params = (
        now,
        user_id,
        body.get("reward_id"),
        body.get("item_name", ""),
        body.get("item_category", ""),
        points_cost,
        body.get("address_id"),
        addr_snapshot,
        "pending",
    )
    red_sql = """INSERT INTO redemptions
           (created_at, user_id, reward_id, item_name, item_category, points_cost,
            address_id, address_snapshot, status)
           VALUES (?,?,?,?,?,?,?,?,?)"""
    if is_postgres_runtime():
        red_cur = conn.execute(red_sql + " RETURNING id", red_params)
        inserted = red_cur.fetchone()
        redemption_id = int(inserted["id"]) if inserted else 0
    else:
        red_cur = conn.execute(red_sql, red_params)
        redemption_id = int(red_cur.lastrowid)

    bal = conn.execute(
        "SELECT points_balance FROM users WHERE id=?", (user_id,)
    ).fetchone()["points_balance"]
    conn.execute(
        """INSERT INTO points_log (created_at, user_id, delta, reason, balance_after)
           VALUES (?,?,?,?,?)""",
        (now, user_id, -points_cost, f"Redeem: {body.get('item_name','')}", bal)
    )
    conn.commit()
    return redemption_id


def auto_award_points(
    submission_id: int,
    extracted_handle: str,
    final_score: int,
    *,
    conn: Any | None = None,
    commit: bool = True,
) -> dict:
    """
    投稿审批通过后自动发放积分。
    4月18日前的投稿标记为 test，不发放真实积分。
    """
    owns_conn = conn is None
    active_conn = conn or open_standalone_conn()
    try:
        result = _award_points_on_conn(active_conn, submission_id, extracted_handle, final_score)
        if commit:
            active_conn.commit()
        if commit:
            _sync_creator_program_after_points_change(result.get("user_id"), "points_award")
        return result
    except Exception:
        if commit:
            try:
                active_conn.rollback()
            except Exception:
                logger.warning(
                    "points.award_rollback_failed",
                    extra={"submission_id": int(submission_id)},
                    exc_info=True,
                )
        raise
    finally:
        if owns_conn:
            close_standalone_conn(active_conn)


async def redeem_points_atomic(user_id: int, points_cost: int, body: dict) -> dict:
    """
    积分兑换原子事务：WHERE points_balance >= points_cost 防止并发超扣。
    """
    if points_cost <= 0:
        return {"status": "error", "message": "Invalid points cost"}

    try:
        from app.db.connection import db_write as _db_write_fn
        rid = await _db_write_fn(lambda: _redeem_points_atomic_sync(user_id, points_cost, body))
        invalidate_user_cache(user_id)
        _invalidate_creator_runtime_cache(user_id)
        return {"status": "success", "redemption_id": rid}
    except ValueError:
        return {"status": "error", "message": "Insufficient points"}
    except Exception as e:
        logger.exception("points.redeem_atomic_failed", extra={"user_id": int(user_id), "points_cost": int(points_cost)})
        return {"status": "error", "message": "Could not complete redemption"}


def credit_points_to_user(user_id_or_handle, points: int, submission_id: int):
    """
    直接发放积分（同步版，用于非 async 上下文）。
    """
    if not user_id_or_handle or points <= 0:
        return
    try:
        conn = get_conn()
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        uid = None
        if isinstance(user_id_or_handle, int):
            uid = user_id_or_handle
        else:
            sub = conn.execute(
                "SELECT user_id FROM submissions WHERE id=?", (submission_id,)
            ).fetchone()
            if sub and sub["user_id"]:
                uid = sub["user_id"]
            else:
                handle_clean = str(user_id_or_handle).strip()
                row = conn.execute(
                    "SELECT user_id FROM user_social_accounts WHERE handle=? AND verified=1 LIMIT 1",
                    (handle_clean,)
                ).fetchone()
                if row:
                    uid = row["user_id"]

        if not uid:
            logger.info("credit points unmatched | submission_id=%s", submission_id)
            return

        user = conn.execute(
            "SELECT status, points_balance FROM users WHERE id=?", (uid,)
        ).fetchone()
        if not user or user["status"] != "approved":
            return

        new_bal = (user["points_balance"] or 0) + points
        conn.execute(
            "UPDATE users SET points_balance=points_balance+?, points_total=points_total+? WHERE id=?",
            (points, points, uid)
        )
        conn.execute(
            "UPDATE submissions SET points_awarded=?, points_status='awarded' WHERE id=?",
            (points, submission_id)
        )
        conn.execute(
            """INSERT INTO points_log (created_at, user_id, submission_id, delta, reason, balance_after)
               VALUES (?,?,?,?,?,?)""",
            (now, uid, submission_id, points,
             f"Credit #{submission_id}", new_bal)
        )
        conn.commit()
        invalidate_user_cache(uid)
        _invalidate_creator_runtime_cache(uid)
        logger.info("points credited | user_id=%s | submission_id=%s | points=%s", uid, submission_id, points)
    except Exception as e:
        logger.exception("credit_points_to_user error | error=%s", e)


def reverse_submission_points(
    submission_id: int,
    *,
    reason: str = "",
    conn: Any | None = None,
    commit: bool = True,
) -> dict:
    owns_conn = conn is None
    active_conn = conn or open_standalone_conn()
    try:
        result = _reverse_submission_points_on_conn(active_conn, submission_id, reason=reason)
        if commit:
            active_conn.commit()
        if commit:
            _sync_creator_program_after_points_change(result.get("user_id"), "submission_delete")
        return result
    except Exception:
        if commit:
            try:
                active_conn.rollback()
            except Exception:
                logger.warning(
                    "points.reverse_rollback_failed",
                    extra={"submission_id": int(submission_id)},
                    exc_info=True,
                )
        raise
    finally:
        if owns_conn:
            close_standalone_conn(active_conn)

# 向后兼容别名
_auto_award_points = auto_award_points



def confirm_partial_awards() -> dict:
    """
    定时任务: 把所有 confirm_at <= now 的 partial 投稿补发剩余 60%
    返回: {confirmed: N, total_pts: M}
    """
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    confirmed = 0
    total_pts = 0
    conn = open_standalone_conn()
    try:
        rows = conn.execute(
            """SELECT id, user_id, points_pending, final_score
               FROM submissions
               WHERE points_status='partial' AND confirm_at IS NOT NULL AND confirm_at <= ?""",
            (now,)
        ).fetchall()

        for row in rows:
            sid = row["id"]
            uid = row["user_id"]
            pending = int(row["points_pending"] or 0)
            if pending <= 0 or not uid:
                conn.execute(
                    "UPDATE submissions SET points_status='awarded', points_pending=0 WHERE id=?",
                    (sid,)
                )
                conn.commit()
                continue
            try:
                if not is_postgres_runtime():
                    conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE users SET points_balance=points_balance+?, points_total=points_total+? WHERE id=?",
                    (pending, pending, uid)
                )
                conn.execute(
                    """INSERT INTO points_log
                       (created_at, user_id, submission_id, delta, reason, balance_after)
                       VALUES (?,?,?,?,?,
                         (SELECT points_balance FROM users WHERE id=?))""",
                    (now, uid, sid, pending,
                     f"投稿确认期通过 #{sid} 补发剩余 60%", uid)
                )
                conn.execute(
                    "UPDATE submissions SET points_status='awarded', points_pending=0 WHERE id=?",
                    (sid,)
                )
                conn.commit()
                confirmed += 1
                total_pts += pending
                invalidate_user_cache(uid)
                _invalidate_creator_runtime_cache(uid)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    logger.warning(
                        "points.confirm_pending_rollback_failed",
                        extra={"submission_id": sid},
                        exc_info=True,
                    )
                logger.warning("confirm pending points error | submission_id=%s | error=%s", sid, e)
    finally:
        close_standalone_conn(conn)

    if confirmed:
        logger.info("confirmed partial awards | count=%s | total_points=%s", confirmed, total_pts)
    return {"confirmed": confirmed, "total_pts": total_pts}
