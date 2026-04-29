"""
db/repositories/submissions.py — 投稿记录 CRUD
新增: create_submission_stub / mark_processing / finalize / mark_failed
保留: save_submission (旧同步模式兼容)
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 真分流: stub → processing → done/failed
# ──────────────────────────────────────────────

def create_submission_stub(
    user_id: Optional[int],
    url: str,
    title: str,
    caption: str,
    raw_text: str,
    platform: str,
    extracted_handle: str = "",
    uploaded_video_path: str = "",
    uploaded_video_filename: str = "",
) -> int:
    """
    创建一条 queued 状态的 submission 记录，秒回 submission_id。
    后续由 worker 填充完整分析结果。
    """
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    params = (
        now, user_id, platform, url, extracted_handle,
        (title or "")[:300], (caption or "")[:2000], (raw_text or "")[:5000],
        uploaded_video_path, "queued", "queued",
        f"Queued at {now} | file={uploaded_video_filename or ''}",
        0, 0, 0, 0,
        0, 0, 0, 0, 0,
        0,
    )
    sql = """
        INSERT INTO submissions (
            created_at, user_id, platform, url, extracted_handle,
            title, caption, raw_text,
            video_path, detection_status, job_status,
            memo, final_score, creator_score, overall_score, risk_score,
            views, likes, comments, shares, favorites,
            scraped_ok
        ) VALUES (?,?,?,?,?, ?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        submission_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        submission_id = int(cur.lastrowid)
    conn.commit()
    return submission_id


def mark_submission_running(submission_id: int):
    """worker 取到任务后标记 processing (legacy alias: running)."""
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE submissions SET job_status=?, started_at=?, error_message='' WHERE id=?",
        ("processing", now, submission_id),
    )
    conn.commit()


def finalize_submission(submission_id: int, data: Dict[str, Any]):
    """
    worker 分析完成后，将完整结果写回 stub 记录。
    data 的 key 和 save_submission 的 result dict 保持一致。
    """
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    m = data.get("metrics", {})
    s = data.get("scores", {})
    pm = data.get("product_match", {})
    va = data.get("video_analysis")
    va_json = json.dumps(va, ensure_ascii=False) if va else None

    vpath = ""
    if va and isinstance(va, dict):
        vpath = va.get("path", "") or ""

    genre = ""
    if va and isinstance(va, dict):
        genre = va.get("content_genre", "") or ""
    if not genre:
        genre = data.get("content_genre", "") or ""

    conn.execute(
        """
        UPDATE submissions SET
            job_status          = 'done',
            finished_at         = ?,
            error_message       = '',
            platform            = COALESCE(?, platform),
            extracted_handle    = COALESCE(?, extracted_handle),
            title               = COALESCE(?, title),
            detection_status    = ?,
            product_series      = ?,
            product_label       = ?,
            content_types       = ?,
            final_score         = ?,
            creator_score       = ?,
            overall_score       = ?,
            risk_score          = ?,
            views               = ?,
            likes               = ?,
            comments            = ?,
            shares              = ?,
            favorites           = ?,
            recommendation      = ?,
            memo                = ?,
            evidence            = ?,
            scraped_ok          = ?,
            video_analysis      = ?,
            video_path          = COALESCE(?, video_path),
            tech_score          = ?,
            marketing_score     = ?,
            content_genre       = ?,
            percentile_tech     = ?,
            percentile_mkt      = ?,
            vertical_category   = ?,
            vertical_tech_score = ?,
            vertical_mkt_score  = ?,
            community_value     = ?,
            product_showcase_score = ?,
            brand_exposure_score   = ?,
            storytelling_score  = ?,
            tech_status         = ?,
            logo_detected       = ?,
            product_closeup_count = ?
        WHERE id = ?
        """,
        (
            now,
            data.get("platform", ""),
            data.get("extracted_handle", ""),
            (data.get("title") or "")[:300],
            data.get("detection_status", "not_detected"),
            pm.get("series", ""),
            pm.get("label", ""),
            json.dumps(data.get("content_types", [])),
            s.get("final_score", 0),
            s.get("creator_score", 0),
            s.get("overall_score", 0),
            s.get("risk_score", 0),
            m.get("views", 0),
            m.get("likes", 0),
            m.get("comments", 0),
            m.get("shares", 0),
            m.get("favorites", 0),
            data.get("recommendation", ""),
            (data.get("memo") or "")[:1000],
            json.dumps(data.get("evidence", [])),
            int(data.get("scraped_ok", False)),
            va_json,
            vpath,
            data.get("tech_score", 0),
            data.get("marketing_score", 0),
            genre,
            data.get("percentile_tech", 0),
            data.get("percentile_mkt", 0),
            data.get("vertical_category", ""),
            data.get("vertical_tech_score", 0),
            data.get("vertical_mkt_score", 0),
            data.get("community_value", 0),
            data.get("product_showcase_score", 0),
            data.get("brand_exposure_score", 0),
            data.get("storytelling_score", 0),
            data.get("tech_status", ""),
            data.get("logo_detected", 0),
            data.get("product_closeup_count", 0),
            submission_id,
        ),
    )
    conn.commit()


def mark_submission_failed(submission_id: int, error_message: str):
    """worker 分析失败时写回"""
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        UPDATE submissions
        SET job_status=?, finished_at=?, error_message=?, detection_status=?
        WHERE id=?
        """,
        ("failed", now, (error_message or "")[:1000], "failed", submission_id),
    )
    conn.commit()


def get_submission_status(submission_id: int) -> Optional[Dict[str, Any]]:
    """前端轮询用"""
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, job_status, detection_status, error_message,
               tech_score, marketing_score, creator_score, final_score, overall_score,
               started_at, finished_at
        FROM submissions WHERE id=?
        """,
        (submission_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


# ──────────────────────────────────────────────
# 旧模式兼容: 同步保存完整 submission
# ──────────────────────────────────────────────

def save_submission(data: Dict[str, Any], user_id: int = None):
    try:
        conn = get_conn()
        c = conn.cursor()
        m = data.get("metrics", {})
        s = data.get("scores", {})
        pm = data.get("product_match", {})
        va = data.get("video_analysis")
        va_json = json.dumps(va, ensure_ascii=False) if va else None
        vpath = ""
        if data.get("video_analysis") and isinstance(data["video_analysis"], dict):
            vpath = data["video_analysis"].get("path", "") or ""
        genre = ""
        if data.get("video_analysis") and isinstance(data["video_analysis"], dict):
            genre = data["video_analysis"].get("content_genre", "") or ""
        if not genre:
            genre = data.get("content_genre", "") or ""
        c.execute("""
            INSERT INTO submissions
            (created_at, platform, url, extracted_handle, title, detection_status,
             product_series, product_label, content_types,
             final_score, creator_score, overall_score, risk_score,
             views, likes, comments, shares, favorites,
             recommendation, memo, evidence, scraped_ok, video_analysis, video_path,
             tech_score, marketing_score, content_genre,
             percentile_tech, percentile_mkt,
             vertical_category, vertical_tech_score, vertical_mkt_score,
             community_value, product_showcase_score,
             brand_exposure_score, storytelling_score, tech_status,
             logo_detected, product_closeup_count, user_id,
             job_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            data.get("platform", ""),
            data.get("url", ""),
            data.get("extracted_handle", ""),
            (data.get("title") or "")[:300],
            data.get("detection_status", "not_detected"),
            pm.get("series", ""),
            pm.get("label", ""),
            json.dumps(data.get("content_types", [])),
            s.get("final_score", 0),
            s.get("creator_score", 0),
            s.get("overall_score", 0),
            s.get("risk_score", 0),
            m.get("views", 0),
            m.get("likes", 0),
            m.get("comments", 0),
            m.get("shares", 0),
            m.get("favorites", 0),
            data.get("recommendation", ""),
            (data.get("memo") or "")[:1000],
            json.dumps(data.get("evidence", [])),
            int(data.get("scraped_ok", False)),
            va_json,
            vpath,
            data.get("tech_score", 0),
            data.get("marketing_score", 0),
            genre,
            data.get("percentile_tech", 0),
            data.get("percentile_mkt", 0),
            data.get("vertical_category", ""),
            data.get("vertical_tech_score", 0),
            data.get("vertical_mkt_score", 0),
            data.get("community_value", 0),
            data.get("product_showcase_score", 0),
            data.get("brand_exposure_score", 0),
            data.get("storytelling_score", 0),
            data.get("tech_status", ""),
            data.get("logo_detected", 0),
            data.get("product_closeup_count", 0),
            user_id,
            "legacy",
        ))
        conn.commit()
    except Exception as e:
        logger.exception("db.save_submission_failed", extra={"user_id": user_id})
