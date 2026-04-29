"""
api/routers/leaderboard.py — 公开排行榜（前台用）+ admin 排行榜
"""
from __future__ import annotations
import json
from fastapi import APIRouter
from app.db.connection import get_conn
from app.core.security import require_admin
from app.services.cache import cached
from app.services.media.storage import resolve_local_media_path

router = APIRouter(tags=["leaderboard"])


def _is_remote_media_url(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith("http://") or text.startswith("https://")


def _public_profile_url(platform: str, handle: str) -> str:
    safe_platform = str(platform or "").strip().lower()
    safe_handle = str(handle or "").strip().lstrip("@")
    if not safe_platform or not safe_handle:
        return ""
    mapping = {
        "tiktok": f"https://www.tiktok.com/@{safe_handle}",
        "instagram": f"https://www.instagram.com/{safe_handle}",
        "facebook": f"https://www.facebook.com/{safe_handle}",
        "youtube": f"https://www.youtube.com/@{safe_handle}",
        "reddit": f"https://www.reddit.com/user/{safe_handle}",
    }
    return mapping.get(safe_platform, "")


def _build_public_leaderboard_payload(period: str) -> dict:
    conn = get_conn()
    if period == "month":
        date_filter = "AND created_at >= date('now','start of month')"
        detail_date_filter = "AND s.created_at >= date('now','start of month')"
    elif period == "year":
        date_filter = "AND created_at >= date('now','start of year')"
        detail_date_filter = "AND s.created_at >= date('now','start of year')"
    else:
        date_filter = ""
        detail_date_filter = ""

    rows = conn.execute(f"""
        SELECT
            LOWER(extracted_handle) as handle,
            COUNT(*) as submissions,
            SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END) as confirmed,
            MAX(final_score) as top_score,
            SUM(final_score) as total_campaign_score,
            SUM(COALESCE(points_awarded,0)) as total_points_earned,
            platform
        FROM submissions
        WHERE detection_status='confirmed'
        AND extracted_handle IS NOT NULL
        AND extracted_handle != ''
        {date_filter}
        GROUP BY LOWER(extracted_handle), platform
        ORDER BY total_campaign_score DESC
        LIMIT 50
    """).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        handle = str(item.get("handle") or "").strip().lower()
        platform = str(item.get("platform") or "").strip()
        best_submission = conn.execute(
            f"""
            SELECT
                s.id,
                s.user_id,
                s.title,
                s.video_path,
                s.video_analysis,
                s.product_label,
                s.final_score,
                s.points_awarded,
                COALESCE(u.creator_code, '') AS creator_code,
                COALESCE(u.name, '') AS display_name
            FROM submissions s
            LEFT JOIN users u ON u.id=s.user_id
            WHERE s.detection_status='confirmed'
              AND LOWER(s.extracted_handle)=?
              AND s.platform=?
              {detail_date_filter}
            ORDER BY s.final_score DESC, s.id DESC
            LIMIT 1
            """,
            (handle, platform),
        ).fetchone()
        video_analysis = {}
        if best_submission:
            try:
                video_analysis = json.loads(best_submission["video_analysis"] or "{}")
            except Exception:
                video_analysis = {}
            raw_video_path = str(best_submission["video_path"] or "").strip()
            analysis_path = str(video_analysis.get("path") or "").strip() if isinstance(video_analysis, dict) else ""
            direct_video_url = raw_video_path if _is_remote_media_url(raw_video_path) else ""
            if not direct_video_url and _is_remote_media_url(analysis_path):
                direct_video_url = analysis_path
            local_video_path = resolve_local_media_path(raw_video_path)
            local_analysis_path = resolve_local_media_path(analysis_path)
            has_video = bool(
                direct_video_url
                or local_video_path
                or local_analysis_path
                or video_analysis.get("r2_key")
            )
            item["user_id"] = best_submission["user_id"]
            item["creator_code"] = best_submission["creator_code"] or ""
            item["display_name"] = best_submission["display_name"] or handle or "Creator"
            item["uploaded_video_url"] = direct_video_url or (f"/api/submissions/{best_submission['id']}/video" if has_video else "")
            item["poster_url"] = f"/api/submissions/{best_submission['id']}/poster" if has_video else ""
            item["gear_tag"] = (
                video_analysis.get("gear_combo")
                or video_analysis.get("viltrox_lens")
                or best_submission["product_label"]
                or ""
            )
        else:
            item["user_id"] = None
            item["creator_code"] = ""
            item["display_name"] = handle or "Creator"
            item["uploaded_video_url"] = ""
            item["poster_url"] = ""
            item["gear_tag"] = ""
        item["platform"] = platform.title() if platform else ""
        profile_url = _public_profile_url(platform, handle)
        item["external_links"] = [{"label": platform.title(), "url": profile_url}] if profile_url else []
        items.append(item)
    return {"items": items, "period": period}


@cached(ttl=60, key="leaderboard_public:month")
def _build_public_leaderboard_month() -> dict:
    return _build_public_leaderboard_payload("month")


@cached(ttl=300, key="leaderboard_public:year")
def _build_public_leaderboard_year() -> dict:
    return _build_public_leaderboard_payload("year")


@router.get("/api/leaderboard")
def public_leaderboard(period: str = "month"):
    """公开排行榜，前台直接调用，无需 token"""
    if period == "month":
        return _build_public_leaderboard_month()
    if period == "year":
        return _build_public_leaderboard_year()
    return _build_public_leaderboard_payload(period)


@cached(ttl=120, key="public_rewards")
def _build_public_rewards() -> dict:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, title, description, category, points_cost, meta_label, image_url, stock, sort_order
        FROM reward_catalog
        WHERE status='published'
        ORDER BY sort_order ASC, id DESC
        """
    ).fetchall()
    return {"rewards": [dict(r) for r in rows]}


@router.get("/api/rewards")
def public_rewards():
    return _build_public_rewards()
