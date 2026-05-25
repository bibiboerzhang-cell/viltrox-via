"""Admin VIOS, reward, point, leaderboard, insight, and redemption routes."""
from __future__ import annotations

from app.api.routers.admin_common import *

router = APIRouter(tags=["admin"])

@router.get("/api/vios/dashboard")
def vios_dashboard(request: Request):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        total, confirmed, suspected, not_detected = c.execute("""SELECT COUNT(*), SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END), SUM(CASE WHEN detection_status='suspected' THEN 1 ELSE 0 END), SUM(CASE WHEN detection_status='not_detected' THEN 1 ELSE 0 END) FROM submissions""").fetchone()
        views, likes, comments, shares = c.execute("SELECT COALESCE(SUM(views),0), COALESCE(SUM(likes),0), COALESCE(SUM(comments),0), COALESCE(SUM(shares),0) FROM submissions").fetchone()
        avg_campaign, avg_creator = c.execute("SELECT ROUND(AVG(final_score),1), ROUND(AVG(creator_score),1) FROM submissions WHERE detection_status='confirmed'").fetchone()
        products = c.execute("SELECT product_series, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes, ROUND(AVG(final_score),0) as avg_score FROM submissions WHERE product_series IS NOT NULL AND product_series != '' GROUP BY product_series ORDER BY cnt DESC LIMIT 15").fetchall()
        platforms = c.execute("SELECT platform, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes, ROUND(AVG(creator_score),1) as avg_creator FROM submissions GROUP BY platform ORDER BY cnt DESC").fetchall()
        top_creators = c.execute("SELECT extracted_handle, platform, COUNT(*) as submissions, ROUND(AVG(creator_score),0) as avg_creator, COALESCE(SUM(views),0) as total_views, COALESCE(SUM(likes),0) as total_likes, MAX(final_score) as best_score, MAX(detection_status) as status FROM submissions WHERE extracted_handle IS NOT NULL AND extracted_handle != '' GROUP BY extracted_handle, platform ORDER BY avg_creator DESC, total_views DESC LIMIT 20").fetchall()
        trend = c.execute("SELECT DATE(created_at) as day, COUNT(*) as cnt, COALESCE(SUM(views),0) as views, COALESCE(SUM(likes),0) as likes FROM submissions WHERE created_at >= DATE('now', '-30 days') GROUP BY day ORDER BY day").fetchall()
        score_dist = c.execute("SELECT SUM(CASE WHEN final_score >= 300 THEN 1 ELSE 0 END) as elite, SUM(CASE WHEN final_score >= 200 AND final_score < 300 THEN 1 ELSE 0 END) as high, SUM(CASE WHEN final_score >= 100 AND final_score < 200 THEN 1 ELSE 0 END) as mid, SUM(CASE WHEN final_score > 0 AND final_score < 100 THEN 1 ELSE 0 END) as low, SUM(CASE WHEN final_score = 0 THEN 1 ELSE 0 END) as zero FROM submissions").fetchone()
        pending_ver = c.execute("SELECT COUNT(*) FROM verifications WHERE status='pending'").fetchone()[0] if table_exists("verifications") else 0
        recent = c.execute("SELECT id, created_at, platform, extracted_handle, title, detection_status, product_series, final_score, creator_score, views, likes, recommendation FROM submissions ORDER BY created_at DESC LIMIT 20").fetchall()
        return {
            "summary": {"total": total or 0, "confirmed": confirmed or 0, "suspected": suspected or 0, "not_detected": not_detected or 0, "avg_campaign": avg_campaign or 0, "avg_creator": avg_creator or 0, "total_views": views or 0, "total_likes": likes or 0, "total_comments": comments or 0, "total_shares": shares or 0, "pending_verifications": pending_ver, "score_dist": {"elite": score_dist[0] or 0, "high":  score_dist[1] or 0, "mid":   score_dist[2] or 0, "low":   score_dist[3] or 0, "zero":  score_dist[4] or 0}},
            "products": [{"series": r[0], "count": r[1], "views": r[2], "likes": r[3], "avg_score": r[4]} for r in products],
            "platforms": [{"platform": r[0], "count": r[1], "views": r[2], "likes": r[3], "avg_creator": r[4]} for r in platforms],
            "creators": [{"handle": r[0], "platform": r[1], "submissions": r[2], "avg_creator": r[3], "total_views": r[4], "total_likes": r[5], "best_score": r[6], "status": r[7]} for r in top_creators],
            "trend": [{"date": r[0], "count": r[1], "views": r[2], "likes": r[3]} for r in trend],
            "recent": [dict(zip(["id","created_at","platform","handle","title","status","product","campaign","creator","views","likes","rec"], r)) for r in recent],
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    return _admin_cache_get_or_build(
        "vios_dashboard",
        _build,
        ttl=ADMIN_STATS_CACHE_TTL_SEC,
    )

@router.get("/api/vios/share-token")
def generate_share_token(request: Request):
    require_admin(request)
    token = _secrets.token_urlsafe(16)
    now = datetime.utcnow()
    expires = now + timedelta(days=7)
    _persist_share_token(
        token,
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return {"token": token, "expires_in": "7 days"}

@router.get("/api/vios/verify-token/{token}")
def verify_share_token(token: str):
    meta = _load_share_token_meta(token)
    if meta:
        return {"valid": True, "meta": meta}
    return {"valid": False}

@router.get("/api/admin/rewards")
def admin_list_rewards(request: Request, status: str = Query(default="")):
    require_admin(request)

    def _build():
        conn = get_conn()
        if status: rows = conn.execute("SELECT * FROM reward_catalog WHERE status=? ORDER BY sort_order ASC, id DESC", (status,)).fetchall()
        else: rows = conn.execute("SELECT * FROM reward_catalog ORDER BY sort_order ASC, id DESC").fetchall()
        return {"rewards": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "rewards",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        status=status or "all",
    )

@router.post("/api/admin/rewards")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_create_reward(req: RewardItemRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = (
        now,
        now,
        req.title.strip(),
        req.description.strip(),
        req.category.strip(),
        int(req.points_cost),
        req.meta_label.strip(),
        req.image_url.strip(),
        int(req.stock),
        int(req.sort_order),
        req.status.strip() or "draft",
    )
    sql = "INSERT INTO reward_catalog (created_at, updated_at, title, description, category, points_cost, meta_label, image_url, stock, sort_order, status) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        reward_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        reward_id = int(cur.lastrowid)
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "success", "id": reward_id}

@router.patch("/api/admin/rewards/{rid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_update_reward(rid: int, req: RewardItemRequest, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET updated_at=?, title=?, description=?, category=?, points_cost=?, meta_label=?, image_url=?, stock=?, sort_order=?, status=? WHERE id=?",
        (now, req.title.strip(), req.description.strip(), req.category.strip(), int(req.points_cost), req.meta_label.strip(), req.image_url.strip(), int(req.stock), int(req.sort_order), req.status.strip() or "draft", rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "updated"}

@router.post("/api/admin/rewards/{rid}/publish")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_publish_reward(rid: int, request: Request):
    admin = require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET status='published', updated_at=?, published_at=?, published_by=? WHERE id=?", (now, now, admin["id"], rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "published"}

@router.post("/api/admin/rewards/{rid}/archive")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_archive_reward(rid: int, request: Request):
    require_admin(request)
    conn = get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE reward_catalog SET status='archived', updated_at=? WHERE id=?", (now, rid))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "archived"}

@router.delete("/api/admin/rewards/{rid}")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
def admin_delete_reward(rid: int, request: Request):
    """永久删除奖品（不是下架）。如果有 redemption 关联会保留但显示为 deleted。"""
    require_admin(request)
    conn = get_conn()
    in_use = conn.execute(
        "SELECT COUNT(*) FROM redemptions WHERE reward_id=?", (rid,)
    ).fetchone()[0]
    if in_use > 0:
        # Soft delete: mark as archived instead
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "UPDATE reward_catalog SET status='archived', updated_at=? WHERE id=?",
            (now, rid)
        )
        conn.commit()
        _invalidate_admin_cache()
        return {"status": "archived", "message": f"已存在 {in_use} 条兑换记录，已改为下架而非删除"}
    conn.execute("DELETE FROM reward_catalog WHERE id=?", (rid,))
    conn.commit()
    _invalidate_admin_cache()
    return {"status": "deleted"}


# ── Points log API ──
@router.get("/api/admin/points-log")
def admin_points_log(
    request: Request,
    uid: int = 0,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=20, le=500),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        params: list[Any] = []
        where = ""
        if uid:
            where = "WHERE pl.user_id=?"
            params.append(uid)
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM points_log pl LEFT JOIN users u ON pl.user_id=u.id {where}",
                params,
            ).fetchone()[0]
            or 0
        )
        offset = (page - 1) * limit
        rows = conn.execute(
            f"""SELECT pl.*,u.email
                FROM points_log pl
                LEFT JOIN users u ON pl.user_id=u.id
                {where}
                ORDER BY pl.id DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "page": page, "limit": limit, "log": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "points_log",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        uid=uid or "all",
        page=page,
        limit=limit,
    )


@router.post("/api/admin/users/batch-grant-points")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def admin_batch_grant_points(request: Request):
    admin = await require_admin_async(request)
    body = await request.json()
    user_ids = [int(x) for x in (body.get("user_ids") or []) if str(x).strip().isdigit()]
    points = int(body.get("points") or 0)
    reason = str(body.get("reason") or "Admin batch grant").strip()
    if not user_ids:
        raise HTTPException(status_code=400, detail="user_ids required")
    if points <= 0:
        raise HTTPException(status_code=400, detail="points must be greater than 0")
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    results = []
    for uid in sorted(set(user_ids)):
        new_balance = await db_write(partial(_grant_points_to_user, uid, points, reason, now))
        if new_balance is not None:
            _refresh_user_points_state(uid, reason="admin_batch_grant_points")
            results.append({"user_id": uid, "new_balance": new_balance})
    record_admin_action(
        actor=admin,
        action="batch_grant_points",
        target_type="users",
        target_id=",".join(str(r["user_id"]) for r in results[:20]),
        detail={"points": points, "reason": reason, "count": len(results)},
        request=request,
    )
    _invalidate_admin_cache()
    return {"status": "success", "count": len(results), "results": results}


@router.post("/api/admin/users/grant-points-by-rule")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def admin_grant_points_by_rule(request: Request):
    admin = await require_admin_async(request)
    body = await request.json()
    points = int(body.get("points") or 0)
    reason = str(body.get("reason") or "Admin rule grant").strip()
    role = str(body.get("role") or "").strip().lower()
    status = str(body.get("status") or "").strip().lower()
    limit = min(max(int(body.get("limit") or 100), 1), 1000)
    if points <= 0:
        raise HTTPException(status_code=400, detail="points must be greater than 0")
    where, params = [], []
    if role:
        where.append("LOWER(COALESCE(role, '')) = ?")
        params.append(role)
    if status:
        where.append("LOWER(COALESCE(status, '')) = ?")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    user_ids = await db_read(partial(_select_user_ids_for_points_rule, where_sql, params, limit))
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    granted = []
    for uid in user_ids:
        new_balance = await db_write(partial(_grant_points_to_user, uid, points, reason, now))
        if new_balance is not None:
            _refresh_user_points_state(uid, reason="admin_rule_grant_points")
            granted.append({"user_id": uid, "new_balance": new_balance})
    record_admin_action(
        actor=admin,
        action="grant_points_by_rule",
        target_type="users",
        target_id=f"rule:{role or '*'}:{status or '*'}",
        detail={"points": points, "reason": reason, "role": role, "status": status, "count": len(granted)},
        request=request,
    )
    _invalidate_admin_cache()
    return {"status": "success", "count": len(granted), "results": granted}


@router.put("/api/admin/users/{uid}/creator-code")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def admin_update_creator_code(uid: int, request: Request):
    admin = await require_admin_async(request)
    body = await request.json()
    new_code = str(body.get("creator_code") or "").strip().upper()
    if not re.match(r"^[A-Z0-9_-]{3,40}$", new_code):
        raise HTTPException(status_code=400, detail="creator_code must be 3-40 chars: A-Z, 0-9, _, -")
    try:
        updated = await db_write(partial(_update_creator_code_sync, int(uid), new_code))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    old_code = updated["old_creator_code"]
    invalidate_user_cache(int(uid))
    record_admin_action(
        actor=admin,
        action="update_creator_code",
        target_type="user",
        target_id=str(uid),
        detail={"old_creator_code": old_code, "new_creator_code": new_code},
        request=request,
    )
    _invalidate_admin_cache()
    return {"status": "success", "user_id": uid, "old_creator_code": old_code, "creator_code": new_code}


@router.post("/api/admin/users/{uid}/adjust_points")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_adjust_points(uid: int, request: Request):
    await require_admin_async(request)
    body   = await request.json()
    delta  = int(body.get("delta", 0))
    reason = body.get("reason", "Admin adjustment")
    if delta == 0: return {"status":"error","message":"delta cannot be 0"}
    now  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_bal = await db_write(partial(_adjust_user_points, uid, delta, reason, now))
    if new_bal is None:
        return {"status":"error","message":"User not found"}
    _refresh_user_points_state(uid, reason="admin_adjust_points")
    _invalidate_admin_cache()
    return {"status":"success","new_balance":new_bal,"delta":delta}

# ── Leaderboard ──
@router.get("/api/admin/leaderboard")
def get_leaderboard(request: Request, period: str = Query(default="month")):
    require_admin(request)

    def _build():
        conn = get_conn()
        c = conn.cursor()
        if period == "month": date_filter = "AND s.created_at >= date('now', '-30 days')"
        elif period == "year": date_filter = "AND s.created_at >= date('now', '-365 days')"
        else: date_filter = ""

        rows = c.execute(f"""
            SELECT s.extracted_handle,
                   COALESCE(MAX(u.name), '') AS user_name,
                   COALESCE(MAX(u.creator_code), '') AS creator_code,
                   COALESCE(MAX(s.platform), '') AS platform,
                   COUNT(*) as submissions,
                   COALESCE(SUM(s.views), 0) as total_views,
                   COALESCE(SUM(s.likes), 0) as total_likes,
                   COALESCE(SUM(s.comments), 0) as total_comments,
                   COALESCE(SUM(s.shares), 0) as total_shares,
                   COALESCE(SUM(s.favorites), 0) as total_favorites,
                   ROUND(AVG(s.overall_score), 0) as avg_score,
                   MAX(s.overall_score) as best_score,
                   COALESCE(SUM(s.final_score), 0) as total_campaign_score,
                   COALESCE(SUM(COALESCE(s.points_awarded, 0) + COALESCE(s.points_pending, 0)), 0) as total_points,
                   GROUP_CONCAT(DISTINCT s.platform) as platforms
            FROM submissions s
            LEFT JOIN users u ON s.user_id = u.id
            WHERE s.extracted_handle IS NOT NULL AND s.extracted_handle != ''
                  AND s.detection_status = 'confirmed' {date_filter}
            GROUP BY s.extracted_handle
            ORDER BY total_views DESC, avg_score DESC LIMIT 50
        """).fetchall()
        cols = ["handle","user_name","creator_code","platform","submissions","total_views","total_likes","total_comments","total_shares","total_favorites","avg_score","best_score","total_campaign_score","total_points","platforms"]

        items = []
        for i, r in enumerate(rows):
            d = dict(zip(cols, r))
            d["rank"] = i + 1
            d["estimated_points"] = int(d.get("total_points") or 0)
            d["display_name"] = d.get("user_name") or d.get("handle") or "—"
            items.append(d)
        return {"period": period, "total": len(items), "items": items}

    return _admin_cache_get_or_build(
        "leaderboard",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        period=period,
    )

# ── Insights ──
@router.get("/api/admin/insights")
async def get_insights(request: Request, days: int = Query(default=90), refresh: bool = Query(default=False)):
    await require_admin_async(request)
    from app.db.repositories.insights import compute_market_insights
    if not refresh:
        try:
            row = await db_read(_load_cached_insights_row)
            if row:
                cache_dt = datetime.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
                if (datetime.utcnow() - cache_dt).total_seconds() / 3600 < 24:
                    return json.loads(row["value"])
        except Exception:
            logger.warning("admin.insights_cache_read_failed", exc_info=True)
    return await asyncio.to_thread(compute_market_insights, days)

@router.get("/api/admin/benchmarks")
def get_benchmarks(request: Request):
    require_admin(request)
    return get_all_benchmarks()

@router.get("/api/admin/creator/{handle}/growth")
def get_creator_growth(handle: str, request: Request):
    require_admin(request)
    from app.services.scoring.creator import get_creator_profile, compute_creator_trend

    def _build():
        clean_handle = handle.lstrip("@")
        profile = (
            get_creator_profile(clean_handle)
            or get_creator_profile(handle)
            or get_creator_profile("@" + clean_handle)
            or {}
        )

        try:
            conn = get_conn()
            handle_variants = [clean_handle, "@" + clean_handle, handle]
            rows = conn.execute(
                "SELECT id, created_at, content_genre, tech_score, marketing_score, "
                "overall_score, final_score, percentile_tech, percentile_mkt, "
                "platform, title, video_analysis FROM submissions "
                "WHERE extracted_handle IN (?,?,?) ORDER BY created_at ASC",
                handle_variants
            ).fetchall()
            submissions_timeline = []
            agg_cameras = set(profile.get("cameras", []))
            agg_lenses = set(profile.get("viltrox_lenses", []))
            agg_competitors = set(profile.get("competitor_brands_seen", []))
            scores_for_avg = []

            for r in rows:
                row = dict(r)
                va_str = row.pop("video_analysis", None)
                if va_str:
                    try:
                        va = json.loads(va_str) if isinstance(va_str, str) else va_str
                        if va.get("camera_body"):
                            agg_cameras.add(va["camera_body"])
                        if va.get("viltrox_lens"):
                            agg_lenses.add(va["viltrox_lens"])
                        for vp in (va.get("viltrox_products_all") or []):
                            agg_lenses.add(vp)
                        for cb in (va.get("competitor_brands") or []):
                            agg_competitors.add(cb)
                    except Exception:
                        logger.warning(
                            "admin.creator_growth_video_analysis_parse_failed",
                            extra={"handle": clean_handle},
                            exc_info=True,
                        )
                if row.get("tech_score") and row.get("marketing_score"):
                    scores_for_avg.append({
                        "tech": row["tech_score"],
                        "mkt": row["marketing_score"],
                        "overall": row.get("overall_score", 0),
                    })
                submissions_timeline.append(row)
        except Exception as e:
            logger.exception("admin.creator_growth_query_failed", extra={"handle": clean_handle})
            submissions_timeline = []
            agg_cameras = set(profile.get("cameras", []))
            agg_lenses = set(profile.get("viltrox_lenses", []))
            agg_competitors = set(profile.get("competitor_brands_seen", []))
            scores_for_avg = []

        if not profile and not submissions_timeline:
            return {"error": "Creator not found"}

        normalized_handle = clean_handle
        submission_count = profile.get("submission_count") or len(submissions_timeline)
        last_seen = profile.get("last_seen") or (
            submissions_timeline[-1].get("created_at", "") if submissions_timeline else ""
        )
        avg_scores = profile.get("avg_scores") or (
            {
                "tech": round(sum(s["tech"] for s in scores_for_avg) / len(scores_for_avg), 1),
                "mkt":  round(sum(s["mkt"]  for s in scores_for_avg) / len(scores_for_avg), 1),
                "overall": round(sum(s["overall"] for s in scores_for_avg) / len(scores_for_avg), 1),
            } if scores_for_avg else {}
        )

        trend = profile.get("trend") or (
            compute_creator_trend(profile.get("score_history", []))
            if len(profile.get("score_history", [])) >= 2
            else {"direction": "new"}
        )

        benchmarks = get_all_benchmarks()
        genre = profile.get("genre") or (submissions_timeline[-1]["content_genre"] if submissions_timeline else "")
        bench = benchmarks.get(genre, {})

        return {
            "handle": normalized_handle,
            "submission_count": submission_count,
            "last_seen": last_seen,
            "cameras": sorted(agg_cameras),
            "viltrox_lenses": sorted(agg_lenses),
            "competitor_brands_seen": sorted(agg_competitors),
            "avg_scores": avg_scores,
            "weak_areas": profile.get("weak_areas", []),
            "trend": trend,
            "score_history": profile.get("score_history", []),
            "submissions_timeline": submissions_timeline,
            "genre_benchmark": bench,
        }

    return _admin_cache_get_or_build(
        "creator_growth",
        _build,
        ttl=max(10, ADMIN_READ_CACHE_TTL_SEC),
        handle=handle,
    )

@router.get("/api/admin/redemptions")
def admin_get_redemptions(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=20, le=500),
):
    require_admin(request)

    def _build():
        conn = get_conn()
        total = int(conn.execute("SELECT COUNT(*) FROM redemptions").fetchone()[0] or 0)
        offset = (page - 1) * limit
        rows = conn.execute("""
            SELECT r.*, u.email, u.name as user_name
            FROM redemptions r
            LEFT JOIN users u ON r.user_id = u.id
            ORDER BY r.id DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        return {"total": total, "page": page, "limit": limit, "items": [dict(r) for r in rows]}

    return _admin_cache_get_or_build(
        "redemptions",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        page=page,
        limit=limit,
    )


@router.get("/api/admin/affiliate")
def admin_get_affiliate_ops(request: Request, limit: int = Query(default=200, ge=20, le=600)):
    require_admin(request)

    def _build():
        return build_affiliate_ops_snapshot(limit=limit)

    return _admin_cache_get_or_build(
        "affiliate_ops",
        _build,
        ttl=ADMIN_READ_CACHE_TTL_SEC,
        limit=limit,
    )


@router.post("/api/admin/redemptions/{rid}/update")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_update_redemption(rid: int, request: Request):
    await require_admin_async(request)
    body = await request.json()
    updated = await db_write(
        partial(
            _update_redemption_record,
            rid,
            body.get("status", "pending"),
            body.get("tracking_number", ""),
            body.get("admin_note", body.get("note", "")),
        )
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Redemption not found")
    _invalidate_admin_cache()
    return {"status": "updated"}


@router.get("/api/admin/redemptions/{rid}")
def admin_get_redemption_detail(rid: int, request: Request):
    require_admin(request)
    conn = get_conn()
    _ensure_redemption_ops_schema(conn)
    row = conn.execute(
        """
        SELECT r.*, u.email, u.name AS user_name, u.creator_code
        FROM redemptions r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE r.id = ?
        """,
        (int(rid),),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Redemption not found")
    return {"item": dict(row)}


async def _admin_redemption_transition(rid: int, request: Request, status: str):
    admin = await require_admin_async(request)
    body = await request.json() if request.method.upper() == "POST" else {}
    try:
        item = await db_write(
            partial(
                _transition_redemption_record,
                rid,
                status,
                admin_id=int(admin["id"]),
                note=str(body.get("note") or body.get("reason") or ""),
                tracking_number=str(body.get("tracking_number") or ""),
                shipping_carrier=str(body.get("shipping_carrier") or body.get("carrier") or ""),
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Redemption not found")
    record_admin_action(
        actor=admin,
        action=f"redemption_{status}",
        target_type="redemption",
        target_id=str(rid),
        detail={"status": status},
        request=request,
    )
    _refresh_user_points_state(int(item.get("user_id") or 0), reason=f"redemption_{status}")
    _invalidate_admin_cache()
    return {"status": "success", "item": item}


@router.post("/api/admin/redemptions/{rid}/approve")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_approve_redemption(rid: int, request: Request):
    return await _admin_redemption_transition(rid, request, "approved")


@router.post("/api/admin/redemptions/{rid}/pack")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_pack_redemption(rid: int, request: Request):
    return await _admin_redemption_transition(rid, request, "packed")


@router.post("/api/admin/redemptions/{rid}/ship")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_ship_redemption(rid: int, request: Request):
    return await _admin_redemption_transition(rid, request, "shipped")


@router.post("/api/admin/redemptions/{rid}/deliver")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_deliver_redemption(rid: int, request: Request):
    return await _admin_redemption_transition(rid, request, "delivered")


@router.post("/api/admin/redemptions/{rid}/reject")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_reject_redemption(rid: int, request: Request):
    return await _admin_redemption_transition(rid, request, "rejected")


@router.post("/api/admin/users/{uid}/grant_points")
@rate_limit("admin_mutation", max_requests=120, window_sec=60)
async def admin_grant_points(uid: int, request: Request):
    await require_admin_async(request)
    body = await request.json()
    points = int(body.get("points", 0))
    reason = body.get("reason", "Admin grant")
    if points <= 0:
        return {"status": "error", "message": "Points must be greater than 0"}
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    new_bal = await db_write(partial(_grant_points_to_user, uid, points, reason, now))
    if new_bal is None:
        return {"status": "error", "message": "User not found"}
    _refresh_user_points_state(uid, reason="admin_grant_points")
    _invalidate_admin_cache()
    return {"status": "success", "new_balance": new_bal}
