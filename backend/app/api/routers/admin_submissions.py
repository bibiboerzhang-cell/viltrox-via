"""Admin submission, creator, verification, and review routes."""
from __future__ import annotations

from app.api.routers.admin_common import *

router = APIRouter(tags=["admin"])

@router.get("/api/admin/submissions")
def admin_submissions(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, le=200),
    platform: str = Query(default=""),
    status: str = Query(default=""),
    series: str = Query(default=""),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()

        conditions = []
        params: List[Any] = []
        if platform:
            conditions.append("s.platform = ?")
            params.append(platform)
        if status:
            conditions.append("s.detection_status = ?")
            params.append(status)
        if series:
            conditions.append("s.product_series = ?")
            params.append(series)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (page - 1) * limit
        total = c.execute(f"SELECT COUNT(*) FROM submissions s {where}", params).fetchone()[0]
        rows = c.execute(
            f"""SELECT s.*,
                       u.name        AS user_name,
                       u.email       AS user_email,
                       u.creator_code AS user_creator_code
                FROM submissions s
                LEFT JOIN users u ON s.user_id = u.id
                {where}
                ORDER BY s.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        items = []
        for r in rows:
            d = dict(r)
            d["display_name"] = d.get("user_name") or d.get("extracted_handle") or "—"
            items.append(d)
        return {"total": total, "page": page, "limit": limit, "items": items}

    return _admin_cache_get_or_build(
        "submissions",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        page=page,
        limit=limit,
        platform=platform or "all",
        status=status or "all",
        series=series or "all",
    )


@router.get("/api/admin/creator/{handle}")
def get_creator_profile_api(handle: str, request: Request):
    require_admin(request)
    from app.services.scoring.creator import get_creator_profile
    profile = get_creator_profile(handle)
    if not profile:
        return {"found": False, "handle": handle}
    return {"found": True, **profile}

@router.get("/api/admin/creators")
def list_creators(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        profiles: list[dict[str, Any]] = []
        user_rows = conn.execute(
            """
            SELECT
                u.id,
                u.created_at,
                u.email,
                u.name,
                u.creator_code,
                u.status,
                u.role,
                u.points_balance,
                u.points_total,
                u.tier_status,
                u.trust_score,
                (SELECT COUNT(*)
                   FROM submissions s
                  WHERE s.user_id=u.id
                    AND LOWER(COALESCE(s.detection_status, '')) NOT IN ('rejected', 'failed', 'prefilter_rejected', 'error')) AS submissions,
                (SELECT ROUND(AVG(COALESCE(s.overall_score, s.final_score, 0)), 1)
                   FROM submissions s
                  WHERE s.user_id=u.id) AS avg_score,
                (SELECT MAX(s.created_at)
                   FROM submissions s
                  WHERE s.user_id=u.id) AS last_seen,
                (SELECT usa.platform
                   FROM user_social_accounts usa
                  WHERE usa.user_id=u.id
                  ORDER BY usa.verified DESC, usa.id DESC
                  LIMIT 1) AS primary_platform,
                (SELECT usa.handle
                   FROM user_social_accounts usa
                  WHERE usa.user_id=u.id
                  ORDER BY usa.verified DESC, usa.id DESC
                  LIMIT 1) AS primary_handle
            FROM users u
            WHERE COALESCE(u.creator_code, '') <> ''
               OR COALESCE(u.role, '') IN ('creator', 'admin')
            ORDER BY submissions DESC, u.id DESC
            LIMIT 500
            """
        ).fetchall()
        for row in user_rows:
            profiles.append(
                {
                    "id": int(row["id"] or 0),
                    "user_id": int(row["id"] or 0),
                    "email": row["email"] or "",
                    "display_name": row["name"] or row["email"] or "",
                    "handle": row["primary_handle"] or row["creator_code"] or "",
                    "primary_handle": row["primary_handle"] or "",
                    "creator_code": row["creator_code"] or "",
                    "status": row["status"] or "",
                    "role": row["role"] or "",
                    "tier_status": row["tier_status"] or "",
                    "points_balance": int(row["points_balance"] or 0),
                    "points_total": int(row["points_total"] or 0),
                    "trust_score": float(row["trust_score"] or 0),
                    "submissions": int(row["submissions"] or 0),
                    "submission_count": int(row["submissions"] or 0),
                    "valid_videos": int(row["submissions"] or 0),
                    "avg_score": float(row["avg_score"] or 0),
                    "score": float(row["avg_score"] or 0),
                    "platform": row["primary_platform"] or "—",
                    "primary_platform": row["primary_platform"] or "—",
                    "last_seen": row["last_seen"] or row["created_at"] or "",
                }
            )
        for p in CREATOR_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                if any(str(item.get("handle") or "").lower() == str(data.get("handle") or p.stem).lower() for item in profiles):
                    continue
                profiles.append({
                    "handle": data.get("handle", p.stem),
                    "platform": data.get("platform", ""),
                    "submission_count": data.get("submission_count", 0),
                    "cameras": data.get("cameras", []),
                    "viltrox_lenses": data.get("viltrox_lenses", []),
                    "last_seen": data.get("last_seen", ""),
                    "competitor_brands_seen": data.get("competitor_brands_seen", []),
                })
            except Exception:
                logger.warning("admin.creator_profile_read_failed", extra={"path": str(p)}, exc_info=True)
        profiles.sort(key=lambda x: x["submission_count"], reverse=True)
        return {"total": len(profiles), "creators": profiles}

    return _admin_cache_get_or_build(
        "creators",
        _build,
        ttl=max(10, ADMIN_READ_CACHE_TTL_SEC),
    )


@router.get("/api/admin/creator-public/shop-heroes")
def admin_list_creator_shop_heroes(request: Request, user_id: int = Query(0)):
    require_admin(request)
    try:
        return {"status": "success", "shopHeroes": list_creator_shop_heroes(int(user_id), include_inactive=True)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/admin/creator-public/shop-heroes")
async def admin_upsert_creator_shop_hero(request: Request):
    require_admin(request)
    try:
        payload = await request.json()
        hero = upsert_creator_shop_hero(payload if isinstance(payload, dict) else {})
        _invalidate_admin_cache()
        return {"status": "success", "shopHero": hero}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/admin/creator-public/shop-heroes/{hero_id}")
def admin_delete_creator_shop_hero(hero_id: str, request: Request):
    require_admin(request)
    try:
        result = delete_creator_shop_hero(hero_id)
        _invalidate_admin_cache()
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/admin/stats")
def admin_stats(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        total     = c.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        confirmed = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='confirmed'").fetchone()[0]
        suspected = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='suspected'").fetchone()[0]
        not_detected = c.execute("SELECT COUNT(*) FROM submissions WHERE detection_status='not_detected'").fetchone()[0]
        avg_final   = c.execute("SELECT ROUND(AVG(final_score),1) FROM submissions WHERE detection_status='confirmed'").fetchone()[0] or 0
        avg_creator = c.execute("SELECT ROUND(AVG(creator_score),1) FROM submissions").fetchone()[0] or 0

        agg = c.execute(
            "SELECT COALESCE(SUM(views),0), COALESCE(SUM(likes),0), "
            "COALESCE(SUM(comments),0), COALESCE(SUM(shares),0), COALESCE(SUM(favorites),0) "
            "FROM submissions"
        ).fetchone()
        total_views, total_likes, total_comments, total_shares, total_favorites = agg

        handles = c.execute("SELECT CASE WHEN extracted_handle!='' THEN extracted_handle ELSE url END FROM submissions").fetchall()
        unique_creators = len(set(h[0] for h in handles if h[0]))

        by_date = c.execute("SELECT substr(created_at,1,10) as day, COUNT(*) as n FROM submissions WHERE created_at >= date('now','-90 days') GROUP BY day ORDER BY day ASC").fetchall()
        by_platform = c.execute("SELECT platform, COUNT(*) as n FROM submissions GROUP BY platform ORDER BY n DESC").fetchall()
        by_series = c.execute("SELECT product_series, COUNT(*) as n FROM submissions WHERE product_series!='' GROUP BY product_series ORDER BY n DESC").fetchall()
        by_status = c.execute("SELECT detection_status, COUNT(*) as n FROM submissions GROUP BY detection_status").fetchall()
        top_scores = c.execute("SELECT id, created_at, platform, title, overall_score, final_score, creator_score, recommendation FROM submissions ORDER BY overall_score DESC LIMIT 10").fetchall()

        try:
            pending_ver = conn.execute("SELECT COUNT(*) FROM verifications WHERE status='pending'").fetchone()[0] if table_exists("verifications") else 0
        except Exception:
            logger.warning("admin.pending_verification_count_failed", exc_info=True)
            pending_ver = 0

        return {
            "total":             total,
            "confirmed":         confirmed,
            "suspected":         suspected,
            "not_detected":      not_detected,
            "avg_final_score":   round(avg_final or 0, 1),
            "avg_creator_score": round(avg_creator or 0, 1),
            "total_views":       total_views,
            "total_likes":       total_likes,
            "total_comments":    total_comments,
            "total_shares":      total_shares,
            "total_favorites":   total_favorites,
            "unique_creators":   unique_creators,
            "by_date":           [{"date": r[0], "count": r[1]} for r in by_date],
            "by_platform":       [{"platform": r[0], "count": r[1]} for r in by_platform],
            "by_series":         [{"series": r[0], "count": r[1]} for r in by_series],
            "by_status":         [{"status": r[0], "count": r[1]} for r in by_status],
            "top_scores":        [dict(zip(["id","created_at","platform","title","overall_score","final_score","creator_score","recommendation"], r)) for r in top_scores],
            "pending_verifications": pending_ver,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    return _admin_cache_get_or_build(
        "stats",
        _build,
        ttl=ADMIN_STATS_CACHE_TTL_SEC,
    )


@router.delete("/api/admin/submissions/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def delete_submission(submission_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    reversed_points: dict[str, Any] | None = None
    try:
        row = conn.execute("SELECT id FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Submission not found")
        reversed_points = reverse_submission_points(
            submission_id,
            reason=f"Submission #{submission_id} deleted by admin",
            conn=conn,
            commit=False,
        )
        cur = conn.execute("DELETE FROM submissions WHERE id=?", (submission_id,))
        if cur.rowcount != 1:
            raise HTTPException(status_code=404, detail="Submission not found")
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("admin.delete_submission_failed", extra={"submission_id": submission_id})
        raise HTTPException(status_code=500, detail="Could not delete submission")
    _refresh_user_points_state((reversed_points or {}).get("user_id"), reason="submission_delete")
    _invalidate_admin_cache()
    return {"status": "deleted", "id": submission_id}


# ── Approve / Reject ──
@router.post("/api/admin/submissions/manual")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_add_submission(req: ManualSubmissionRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    c = conn.cursor()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = (
        now, req.platform, req.url, req.extracted_handle, req.title,
        req.detection_status, req.product_series, req.product_label,
        req.final_score, req.creator_score, req.overall_score,
        req.views, req.likes, req.comments, req.shares,
        req.recommendation, req.memo or f"Manually added at {now}",
    )
    sql = """INSERT INTO submissions
        (created_at, platform, url, extracted_handle, title,
         detection_status, product_series, product_label,
         final_score, creator_score, overall_score, risk_score,
         views, likes, comments, shares, favorites,
         recommendation, memo, scraped_ok)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,0,?,?,0)"""
    if is_postgres_runtime():
        c.execute(sql + " RETURNING id", params)
        inserted = c.fetchone()
        new_id = inserted["id"] if inserted else 0
    else:
        c.execute(sql, params)
        new_id = c.lastrowid
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "created", "id": new_id}


# ── Account Verification Endpoints ──
@router.post("/api/verify/register")
@rate_limit("verify_binding", max_requests=10, window_sec=300)
def register_verification(req: VerifyRegisterRequest, request: Request):
    require_admin(request)
    _register_verification_request(
        req.platform.lower(),
        req.handle.lstrip("@"),
        req.code,
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _invalidate_admin_cache()
    return {"status": "registered", "code": req.code}

@router.get("/api/admin/verifications")
def list_verifications(request: Request, status: str = ""):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        if status:
            rows = c.execute("SELECT * FROM verifications WHERE status=? ORDER BY created_at DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM verifications ORDER BY created_at DESC").fetchall()
        cols = [d[0] for d in c.description]
        return {"items": [dict(zip(cols, r)) for r in rows]}

    return _admin_cache_get_or_build(
        "verifications",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/verifications/{ver_id}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def approve_verification(ver_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    row = _approve_verification_override(
        ver_id,
        payload.get("note", ""),
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Verification not found")
    _invalidate_admin_cache()
    return {"status": "approved_override", "platform": row["platform"], "handle": row["handle"]}

@router.post("/api/admin/verifications/{ver_id}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def reject_verification(ver_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    updated = _reject_verification_record(ver_id, payload.get("note", ""))
    if not updated:
        raise HTTPException(status_code=404, detail="Verification not found")
    _invalidate_admin_cache()
    return {"status": "rejected"}

@router.delete("/api/admin/verifications/{ver_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def delete_verification(ver_id: int, request: Request):
    require_admin(request)
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM verifications WHERE id=?", (ver_id,))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "deleted"}


@router.post("/api/admin/reanalyze/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def reanalyze_submission(submission_id: int, request: Request, req: ReanalyzeRequest = None):
    await require_admin_async(request)
    from app.services.ai.orchestrator import VideoJobInput

    if req is None: req = ReanalyzeRequest()
    row = await db_read(partial(_load_submission_row, submission_id))
    if not row: return {"status": "error", "message": "Submission not found"}
    r_dict = dict(row)

    # Build a VideoJobInput from existing submission
    submission_url = req.url or r_dict["url"] or ""
    platform = r_dict["platform"] or ""
    handle = r_dict["extracted_handle"] or ""
    title = r_dict["title"] or ""

    # For uploaded videos, pass the file path
    uploaded_video = None
    if platform == "Uploaded Video" or not submission_url:
        video_path = r_dict.get("video_path") or ""
        if not video_path:
            try:
                va_ex = json.loads(r_dict.get("video_analysis") or "{}")
                video_path = va_ex.get("path", "") or ""
            except Exception:
                logger.warning(
                    "admin.reanalyze_video_analysis_parse_failed",
                    extra={"submission_id": submission_id},
                    exc_info=True,
                )
        if video_path and os.path.exists(video_path):
            uploaded_video = {
                "path": video_path,
                "filename": os.path.basename(video_path),
            }
        else:
            return {"status": "error", "message": "Video file not found. Re-upload to analyze."}

    queued_metrics = {
        "views": r_dict.get("views", 0) or 0,
        "likes": r_dict.get("likes", 0) or 0,
        "comments": r_dict.get("comments", 0) or 0,
        "shares": r_dict.get("shares", 0) or 0,
        "favorites": r_dict.get("favorites", 0) or 0,
    }

    job = VideoJobInput(
        submission_id=submission_id,
        url=submission_url,
        title=title,
        handle=handle,
        platform=platform,
        caption=r_dict.get("caption", "") or "",
        scraped_text=r_dict.get("raw_text", "") or "",
        gpt_already_done=False,
        uploaded_video=uploaded_video,
        metrics=queued_metrics,
        hints={},
    )

    try:
        await db_write(partial(_mark_submission_reanalyze, submission_id))
        queue = getattr(request.app.state, "job_queue", None)
        if queue is None:
            raise RuntimeError("job queue not available")
        task_id = await queue.enqueue(
            "audit_submission",
            job,
            submission_id=submission_id,
        )
        return {
            "status": "queued",
            "submission_id": submission_id,
            "job_id": task_id,
            "message": "Reanalysis queued",
        }
    except Exception as e:
        logger.exception("admin.reanalyze_failed", extra={"submission_id": submission_id})
        return {"status": "error", "message": "Could not queue reanalysis"}
    finally:
        _invalidate_admin_cache()

@router.get("/api/videos/{submission_id}")
def serve_video(submission_id: int):
    row = _load_submission_row(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return resolve_video_response(row)


@router.get("/api/admin/best_frame/{submission_id}")
def serve_best_frame(submission_id: int):
    row = _load_submission_row(submission_id)
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")
    return resolve_poster_response(row)


def _manual_approval_values(row: dict[str, Any], req: ManualApproveRequest) -> dict[str, Any]:
    hint_bonus = sum(
        (
            15 if req.hints and req.hints.get("logo") else 0,
            12 if req.hints and req.hints.get("product") else 0,
            10 if req.hints and req.hints.get("voice") else 0,
            10 if req.hints and req.hints.get("review") else 0,
        )
    )
    campaign = min(
        400,
        req.campaign_score
        if req.campaign_score is not None
        else (row.get("final_score") or 0) + hint_bonus,
    )
    creator = req.creator_score if req.creator_score is not None else row.get("creator_score") or 0
    overall = req.overall_score if req.overall_score is not None else round(campaign * 0.7 + creator * 0.3)
    series = req.product_series if req.product_series else row.get("product_series") or "VILTROX"
    label = req.product_label if req.product_label else row.get("product_label")
    memo = row.get("memo") or ""
    if hint_bonus > 0:
        memo += f" [Manual hint bonus applied: +{hint_bonus}]"
    if req.memo_append:
        memo += f" [Admin note: {req.memo_append}]"
    return {
        "campaign": campaign,
        "creator": creator,
        "overall": overall,
        "series": series,
        "label": label,
        "memo": memo,
    }


def _sync_manual_approval_scores(conn: Any, cursor: Any, submission_id: int) -> None:
    try:
        va_row = conn.execute(
            "SELECT video_analysis, tech_score FROM submissions WHERE id=?",
            (submission_id,),
        ).fetchone()
        if not va_row or va_row["tech_score"]:
            return
        va_data = json.loads(va_row["video_analysis"] or "{}") if va_row["video_analysis"] else {}
        tech_score = va_data.get("tech_score", 0)
        marketing_score = va_data.get("marketing_score", 0)
        genre = va_data.get("content_genre", "")
        if tech_score <= 0:
            return
        percentiles = update_genre_benchmark(genre, tech_score, marketing_score) if genre else {}
        cursor.execute(
            "UPDATE submissions SET tech_score=?, marketing_score=?, content_genre=?, percentile_tech=?, percentile_mkt=? WHERE id=?",
            (
                tech_score,
                marketing_score,
                genre,
                percentiles.get("percentile_tech", 0),
                percentiles.get("percentile_mkt", 0),
                submission_id,
            ),
        )
    except Exception:
        logger.exception(
            "admin.approve_score_sync_failed",
            extra={"submission_id": submission_id},
        )


def _persist_manual_approval(
    conn: Any,
    cursor: Any,
    submission_id: int,
    row: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    try:
        cursor.execute(
            """UPDATE submissions
               SET detection_status=?, recommendation=?, final_score=?, creator_score=?,
                   overall_score=?, product_series=?, product_label=?, memo=?
               WHERE id=?""",
            (
                "confirmed",
                "Approved by admin review",
                values["campaign"],
                values["creator"],
                values["overall"],
                values["series"],
                values["label"],
                values["memo"],
                submission_id,
            ),
        )
        _sync_manual_approval_scores(conn, cursor, submission_id)
        points = auto_award_points(
            submission_id,
            row.get("extracted_handle", ""),
            values["campaign"],
            conn=conn,
            commit=False,
        )
        conn.commit()
        return points
    except Exception:
        conn.rollback()
        logger.exception(
            "admin.manual_approve_failed",
            extra={"submission_id": submission_id},
        )
        raise HTTPException(status_code=500, detail="Could not approve submission")


def _record_manual_approval_learning(
    submission_id: int,
    row: dict[str, Any],
    req: ManualApproveRequest,
    values: dict[str, Any],
) -> None:
    try:
        old_series = (row.get("product_series") or "").strip()
        old_label = (row.get("product_label") or "").strip()
        new_series = values["series"]
        new_label = values["label"]
        if not (
            (new_series or new_label)
            and (new_series != old_series or new_label != old_label)
        ):
            return
        learned_text = " ".join(
            filter(
                None,
                [
                    row.get("title") or "",
                    row.get("memo") or "",
                    (
                        json.loads(row.get("video_analysis") or "{}").get("notes", "")
                        if row.get("video_analysis")
                        else ""
                    ),
                ],
            )
        )
        from app.services.audit.learning import record_correction

        record_correction(
            submission_id=submission_id,
            url=row.get("url") or "",
            correct_series=new_series or old_series,
            correct_label=new_label or old_label,
            learned_text=learned_text,
            note=req.memo_append or "",
        )
    except Exception:
        logger.exception(
            "admin.approve_learning_sync_failed",
            extra={"submission_id": submission_id},
        )

@router.post("/api/admin/approve/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_approve(submission_id: int, request: Request, req: ManualApproveRequest = None):
    require_admin(request)
    if req is None:
        req = ManualApproveRequest()
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")

    r = dict(row)
    values = _manual_approval_values(r, req)
    pts_result = _persist_manual_approval(conn, c, submission_id, r, values)

    _refresh_user_points_state(pts_result.get("user_id"), reason="points_award")
    _record_manual_approval_learning(submission_id, r, req, values)
    _invalidate_admin_cache()
    return {
        "status": "approved",
        "id": submission_id,
        "campaign_score": values["campaign"],
        "points_awarded": pts_result.get("points", 0),
    }

@router.post("/api/admin/reject/{submission_id}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def manual_reject(submission_id: int, request: Request, body: dict | None = None):
    require_admin(request)
    payload = body or {}
    conn = get_conn()
    c = conn.cursor()
    cur = c.execute(
        "UPDATE submissions SET detection_status=?, recommendation=?, final_score=?, creator_score=?, overall_score=? WHERE id=?",
        ("not_detected", f"Rejected: {payload.get('note', 'Rejected by admin')}", 0, 0, 0, submission_id),
    )
    conn.commit()
    if cur.rowcount != 1:
        raise HTTPException(status_code=404, detail="Submission not found")
    _invalidate_admin_cache()
    return {"status": "rejected", "id": submission_id}
