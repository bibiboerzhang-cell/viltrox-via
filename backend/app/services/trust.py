"""
services/trust.py — creator trust score + dynamic limits
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request

from app.core.config import (
    TRUST_LIMIT_MAX_DAILY_POINTS,
    TRUST_LIMIT_MAX_HOURLY,
    TRUST_LIMIT_MIN_DAILY_POINTS,
    TRUST_LIMIT_MIN_HOURLY,
    TRUST_SCORE_DEFAULT,
    TRUST_SCORE_STALE_SEC,
)
from app.core.logging import get_logger
from app.core.security import invalidate_user_cache
from app.db.connection import db_connection_sync_scope, get_conn
from app.services.security.rate_limiter import check_rate_limit

logger = get_logger(__name__)


TRUST_POSITIVE_FEEDBACK_EVENTS = {
    "product_correction",
    "manual_approve",
    "admin_confirmed",
    "review_positive",
}

def _count_paid_shopify_orders(conn, user_id: int, creator_code: str) -> int:
    """
    Count paid Shopify orders attributed to this user.

    Primary path: query orders table by attribution_user_id (O(log N) with
    idx_orders_attr_user_status index). This replaces a previous full-table
    scan of platform_ingest_events that ran on every video submission.

    Fallback path: scan platform_ingest_events by creator_handle (now indexed
    via idx_ingest_shopify_by_handle) for deployments that have not yet
    migrated legacy Shopify events into the orders table.
    """
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM orders
             WHERE attribution_user_id = ?
               AND status = 'paid'
            """,
            (int(user_id),),
        ).fetchone()
        if row is not None:
            return int(row[0] or 0)
    except Exception:
        logger.debug(
            "trust.paid_order_count_lookup_failed",
            extra={"user_id": user_id},
            exc_info=True,
        )

    if not creator_code:
        return 0
    try:
        rows = conn.execute(
            """
            SELECT payload_json FROM platform_ingest_events
             WHERE source_platform = 'shopify'
               AND entity_type = 'order'
               AND ingest_status = 'done'
               AND creator_handle = ?
            """,
            (creator_code,),
        ).fetchall()
    except Exception:
        return 0

    paid_count = 0
    for r in rows:
        compact = str(r["payload_json"] or "").replace(" ", "").lower()
        if ('"financial_status":"paid"' in compact
                or '"displayfinancialstatus":"paid"' in compact):
            paid_count += 1
    return paid_count





def _utcnow() -> datetime:
    return datetime.utcnow()


def _utcnow_iso() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass
class TrustSnapshot:
    score: float
    label: str
    band_key: str
    limits: dict[str, Any]
    metrics: dict[str, Any]
    stale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "label": self.label,
            "band_key": self.band_key,
            "limits": self.limits,
            "metrics": self.metrics,
            "stale": self.stale,
        }


def collect_trust_metrics(user_id: int) -> dict[str, Any]:
    conn = get_conn()
    user = conn.execute(
        """
        SELECT id, created_at, email_verified, social_verified,
               trust_score, trust_updated_at, role, creator_code
        FROM users
        WHERE id=?
        """,
        (int(user_id),),
    ).fetchone()
    if not user:
        return {
            "user_id": int(user_id),
            "created_at": "",
            "account_age_days": 0,
            "account_age_hours": 0,
            "email_verified": False,
            "social_verified_count": 0,
            "submission_total": 0,
            "confirmed_videos": 0,
            "approval_rate": 0.0,
            "avg_final_score": 0.0,
            "shopify_paid_orders": 0,
            "positive_feedback_count": 0,
            "stored_trust_score": TRUST_SCORE_DEFAULT,
            "trust_updated_at": "",
        }

    created_at = _parse_dt(user["created_at"])
    now = _utcnow()
    age = now - created_at if created_at else timedelta(0)

    social_verified_count = conn.execute(
        "SELECT COUNT(*) FROM user_social_accounts WHERE user_id=? AND verified=1",
        (int(user_id),),
    ).fetchone()[0] or 0

    totals = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
               ROUND(AVG(CASE WHEN detection_status='confirmed' THEN final_score END), 1) AS avg_score
        FROM submissions
        WHERE user_id=?
        """,
        (int(user_id),),
    ).fetchone()
    totals_dict = dict(totals) if totals else {}
    total_submissions = int(totals_dict.get("total", 0) or 0)
    confirmed_videos = int(totals_dict.get("confirmed", 0) or 0)
    avg_final_score = float(totals_dict.get("avg_score", 0) or 0.0)
    approval_rate = (confirmed_videos / total_submissions) if total_submissions else 0.0

    creator_code = str(user["creator_code"] or "").strip()
    # PATCH 2026-04-20: replaced O(N full-table scan + Python string match) with
    # an O(log N) indexed lookup on the orders table (v5 schema). The old path
    # scanned every Shopify webhook event and parsed JSON in Python, which got
    # linearly slower as order volume grew and ran on every video submission.
    paid_orders = _count_paid_shopify_orders(conn, int(user_id), creator_code)

    positive_feedback_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM feedback_events
        WHERE user_id=?
          AND event_type IN ({})
        """.format(",".join("?" for _ in TRUST_POSITIVE_FEEDBACK_EVENTS)),
        (int(user_id), *sorted(TRUST_POSITIVE_FEEDBACK_EVENTS)),
    ).fetchone()[0] or 0

    return {
        "user_id": int(user_id),
        "created_at": user["created_at"] or "",
        "account_age_days": max(0, int(age.total_seconds() // 86400)),
        "account_age_hours": max(0, int(age.total_seconds() // 3600)),
        "email_verified": bool(user["email_verified"]),
        "social_verified_flag": bool(social_verified_count),
        "social_verified_count": int(social_verified_count),
        "submission_total": total_submissions,
        "confirmed_videos": confirmed_videos,
        "approval_rate": round(approval_rate, 4),
        "avg_final_score": round(avg_final_score, 1),
        "shopify_paid_orders": int(paid_orders),
        "positive_feedback_count": int(positive_feedback_count),
        "stored_trust_score": float(user["trust_score"] or TRUST_SCORE_DEFAULT),
        "trust_updated_at": user["trust_updated_at"] or "",
        "role": str(user["role"] or "creator"),
    }


def compute_trust_score(metrics: dict[str, Any]) -> float:
    score = 12.0
    score += min(10.0, (float(metrics.get("account_age_days", 0) or 0) / 30.0) * 10.0)
    if metrics.get("email_verified"):
        score += 5.0
    score += min(15.0, float(metrics.get("social_verified_count", 0) or 0) * 5.0)
    score += 25.0 * min(1.0, float(metrics.get("approval_rate", 0.0) or 0.0) / 0.8)
    score += 15.0 * min(1.0, float(metrics.get("avg_final_score", 0.0) or 0.0) / 250.0)
    score += min(10.0, float(metrics.get("shopify_paid_orders", 0) or 0) * 5.0)
    score += min(20.0, float(metrics.get("positive_feedback_count", 0) or 0) * 5.0)
    return round(_clamp(score, 0.0, 100.0), 1)


def compute_dynamic_limits(metrics: dict[str, Any], trust_score: float) -> dict[str, Any]:
    age_hours = int(metrics.get("account_age_hours", 0) or 0)
    confirmed = int(metrics.get("confirmed_videos", 0) or 0)
    if age_hours < 48 and confirmed == 0:
        band_key = "starter"
        label = "Starter guard"
        hourly_limit = max(TRUST_LIMIT_MIN_HOURLY, 2)
        daily_points_cap = max(TRUST_LIMIT_MIN_DAILY_POINTS, 100)
        cooldown_hours = 48
    elif trust_score >= 80:
        band_key = "elite"
        label = "Elite"
        hourly_limit = min(TRUST_LIMIT_MAX_HOURLY, 15)
        daily_points_cap = min(TRUST_LIMIT_MAX_DAILY_POINTS, 1000)
        cooldown_hours = 0
    elif trust_score >= 50:
        band_key = "trusted"
        label = "Trusted"
        hourly_limit = min(TRUST_LIMIT_MAX_HOURLY, 8)
        daily_points_cap = min(TRUST_LIMIT_MAX_DAILY_POINTS, 500)
        cooldown_hours = 0
    elif trust_score >= 20:
        band_key = "normal"
        label = "Normal"
        hourly_limit = max(TRUST_LIMIT_MIN_HOURLY, min(TRUST_LIMIT_MAX_HOURLY, 5))
        daily_points_cap = max(TRUST_LIMIT_MIN_DAILY_POINTS, min(TRUST_LIMIT_MAX_DAILY_POINTS, 250))
        cooldown_hours = 6
    else:
        band_key = "watch"
        label = "Watch"
        hourly_limit = max(TRUST_LIMIT_MIN_HOURLY, 2)
        daily_points_cap = max(TRUST_LIMIT_MIN_DAILY_POINTS, 100)
        cooldown_hours = 24
    return {
        "band_key": band_key,
        "label": label,
        "hourly_limit": hourly_limit,
        "daily_points_cap": daily_points_cap,
        "cooldown_hours": cooldown_hours,
    }


def persist_trust_score(
    user_id: int,
    *,
    reason: str = "recalculated",
    context: dict[str, Any] | None = None,
) -> TrustSnapshot:
    metrics = collect_trust_metrics(int(user_id))
    trust_score = compute_trust_score(metrics)
    limits = compute_dynamic_limits(metrics, trust_score)
    previous_score = float(metrics.get("stored_trust_score", TRUST_SCORE_DEFAULT) or TRUST_SCORE_DEFAULT)
    now = _utcnow_iso()
    conn = get_conn()
    conn.execute(
        "UPDATE users SET trust_score=?, trust_updated_at=? WHERE id=?",
        (trust_score, now, int(user_id)),
    )
    conn.execute(
        """
        INSERT INTO trust_events
        (user_id, event_type, score_delta, new_total, context_json, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (
            int(user_id),
            str(reason or "recalculated"),
            round(trust_score - previous_score, 2),
            trust_score,
            json.dumps(context or {}, ensure_ascii=False),
            now,
        ),
    )
    conn.commit()
    invalidate_user_cache(int(user_id))
    return TrustSnapshot(
        score=trust_score,
        label=limits["label"],
        band_key=limits["band_key"],
        limits=limits,
        metrics=metrics,
        stale=False,
    )


def get_trust_snapshot(
    user_id: int,
    *,
    persist_if_stale: bool = True,
    reason: str = "read",
    context: dict[str, Any] | None = None,
) -> TrustSnapshot:
    metrics = collect_trust_metrics(int(user_id))
    updated_at = _parse_dt(metrics.get("trust_updated_at"))
    is_stale = (updated_at is None) or ((_utcnow() - updated_at).total_seconds() >= TRUST_SCORE_STALE_SEC)
    if persist_if_stale and is_stale:
        return persist_trust_score(int(user_id), reason=reason, context=context)
    trust_score = float(metrics.get("stored_trust_score", TRUST_SCORE_DEFAULT) or TRUST_SCORE_DEFAULT)
    limits = compute_dynamic_limits(metrics, trust_score)
    return TrustSnapshot(
        score=trust_score,
        label=limits["label"],
        band_key=limits["band_key"],
        limits=limits,
        metrics=metrics,
        stale=is_stale,
    )


def get_remaining_daily_points_cap(user_id: int, daily_points_cap: int) -> dict[str, int]:
    conn = get_conn()
    today_start = _utcnow().strftime("%Y-%m-%dT00:00:00Z")
    earned_today = conn.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0)
        FROM points_log
        WHERE user_id=? AND created_at >= ?
        """,
        (int(user_id), today_start),
    ).fetchone()[0] or 0
    remaining = max(0, int(daily_points_cap) - int(earned_today))
    return {
        "earned_today": int(earned_today),
        "remaining": remaining,
        "daily_points_cap": int(daily_points_cap),
    }


def enforce_dynamic_submission_guard(request: Request, user: dict[str, Any] | None, action: str) -> TrustSnapshot | None:
    if not user or not user.get("id"):
        return None
    role = str(user.get("role") or "").strip().lower()
    tier_status = str(user.get("tier_status") or "").strip().lower()
    if role in {"admin", "founder", "internal"} or tier_status in {"platinum", "vip", "founder", "internal"}:
        return None
    with db_connection_sync_scope():
        snapshot = get_trust_snapshot(
            int(user["id"]),
            persist_if_stale=True,
            reason=f"{action}_guard",
            context={"action": action},
        )
        limits = snapshot.limits
        allowed, _remaining = check_rate_limit(
            f"{action}_dynamic",
            f"user:{int(user['id'])}",
            max_requests=int(limits["hourly_limit"]),
            window_sec=3600,
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"{limits['label']} guard active. Submission frequency is capped at "
                    f"{limits['hourly_limit']} per hour right now."
                ),
            )

        cooldown_hours = int(limits.get("cooldown_hours", 0) or 0)
        total_submissions = int(snapshot.metrics.get("submission_total", 0) or 0)
        if cooldown_hours > 0 and total_submissions > 0:
            conn = get_conn()
            latest = conn.execute(
                "SELECT created_at FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (int(user["id"]),),
            ).fetchone()
            latest_dt = _parse_dt((dict(latest) if latest else {}).get("created_at"))
            if latest_dt and (_utcnow() - latest_dt) < timedelta(hours=cooldown_hours):
                wait_hours = max(1, int((timedelta(hours=cooldown_hours) - (_utcnow() - latest_dt)).total_seconds() // 3600) + 1)
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"{limits['label']} guard active. Please wait about {wait_hours} more hour(s) "
                        "before the next submission."
                    ),
                )
        return snapshot
