"""KOL Operations admin API."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.api.routers.kol_ops_helpers import (
    _clean_creator_name,
    _content_rollup_sql,
    _persist_platform_search_result,
    _log_kol_system_action,
    _country_filter_variants,
    _dict,
    _dossier_rollup,
    _insert_id,
    _int,
    _items,
    _like,
    _limit_offset,
    _log_activity,
    _log_activity_commit,
    _normalize_country_code,
    _normalize_platform,
    _now,
    _parse_cents,
    _parse_count,
    _row_contact_status,
    _scalar,
    _staff_id_by_owner_name,
    _uploaded_rows,
    _verify_confirm_password,
)
from app.db.connection import db_write, get_conn
from app.services.intelligence.account_scan_service import search_platform_content
from app.services.kol.account_dossier import (
    analyze_kol_account,
    get_kol_dossier,
    list_kol_comments,
    list_kol_posts,
    scan_kol_account,
)
from app.services.kol.content_analyzer import analyze_kol_url_standalone
from app.services.kol.metrics import cpv, engagement_rate, roi
from app.services.kol.platform_search_workflow import (
    execute_platform_search as _execute_platform_search_workflow,
)
from app.domains.access import scope
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import profile_discovery as _kol_discovery  # P0-6 地区排除判据复用
from app.domains.kol.claim_access import assert_kol_access

from app.api.routers.kol_ops_schema import ensure_kol_schema
from app.api.routers.kol_ops_dashboard import router as dashboard_router
from app.api.routers.kol_ops_content import router as content_router
from app.api.routers.kol_ops_import import import_kols_csv_rows


router = APIRouter(prefix="/api/admin/kol", tags=["kol-ops"], dependencies=[Depends(ensure_kol_schema)])
router.include_router(dashboard_router)
router.include_router(content_router)
logger = get_logger(__name__)


def _provider_unavailable(reason: str, operation: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "status": "unavailable",
            "reason": reason,
            "operation": operation,
            "retryable": True,
        },
    )


@router.get("/kols")
def list_kols(
    staff_id: int | None = None,
    country: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    product: str | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    staff=Depends(require_tab("kol_ops", "read")),
):
    conn = get_conn()
    where, params = [], []
    if staff_id:
        where.append("k.assigned_staff_id = ?"); params.append(staff_id)
    if country:
        variants = _country_filter_variants(country)
        where.append("(" + " OR ".join(["LOWER(COALESCE(k.country, '')) = LOWER(?)"] * len(variants)) + ")")
        params.extend(variants)
    if platform:
        where.append("k.platform = ?"); params.append(platform)
    if status:
        where.append("k.contact_status = ?"); params.append(status)
    if product:
        where.append("LOWER(COALESCE(k.promoted_product, '')) LIKE ?"); params.append(_like(product))
    if q:
        where.append(
            "(LOWER(k.channel_name) LIKE ? OR LOWER(k.channel_url) LIKE ? OR LOWER(k.niche) LIKE ? "
            "OR LOWER(k.contact_email) LIKE ? OR LOWER(COALESCE(k.promoted_product, '')) LIKE ? "
            "OR LOWER(COALESCE(k.media_name, '')) LIKE ? OR LOWER(COALESCE(k.owner_name, '')) LIKE ? "
            "OR LOWER(COALESCE(k.channel_tags, '')) LIKE ?)"
        )
        params.extend([_like(q)] * 8)
    if date_from:
        where.append("k.updated_at >= ?"); params.append(date_from)
    if date_to:
        where.append("k.updated_at <= ?"); params.append(date_to)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    safe_limit, safe_offset = _limit_offset(limit, offset)
    total = _scalar(f"SELECT COUNT(*) FROM kols k {where_sql}", params)
    rows = conn.execute(
        f"""
        SELECT
            k.*,
            u.name AS assigned_staff_name,
            COALESCE(SUM(c.cost_cents), 0) AS cost_cents,
            COALESCE(SUM(co.views), 0) AS views,
            COALESCE(SUM(co.likes), 0) AS likes,
            COALESCE(SUM(co.comments), 0) AS comments,
            COALESCE(SUM(co.shares), 0) AS shares,
            COALESCE(SUM(at.attributed_revenue_cents), 0) AS revenue_cents,
            COALESCE(AVG(co.ai_quality_score), 0) AS avg_ai_quality_score
        FROM kols k
        LEFT JOIN staff s ON s.id = k.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kol_campaigns c ON c.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = c.id
        LEFT JOIN kol_attribution at ON at.content_id = co.id
        {where_sql}
        GROUP BY k.id, u.name
        ORDER BY k.updated_at DESC
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, safe_offset],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["creator_name"] = _clean_creator_name(item.get("media_name") or item.get("channel_name") or item.get("project_name") or "", item.get("owner_name", ""))
        item["country_code"] = _normalize_country_code(item.get("country", ""))
        item["engagement_rate"] = engagement_rate(item.get("likes", 0), item.get("comments", 0), item.get("shares", 0), item.get("views", 0))
        item["cpv"] = cpv(item.get("cost_cents", 0), item.get("views", 0))
        item["roi"] = roi(item.get("cost_cents", 0), item.get("revenue_cents", 0))
        item.update(_dossier_rollup(conn, int(item["id"])))
        items.append(item)
    return {
        "items": items,
        "summary": _content_rollup_sql(where_sql, params),
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "total": total,
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < total else None,
            "prev_offset": max(0, safe_offset - safe_limit) if safe_offset > 0 else None,
        },
    }


async def _execute_platform_search(body: dict, *, staff: dict) -> dict:
    return await _execute_platform_search_workflow(
        body,
        staff=staff,
        search_content=search_platform_content,
        annotate_items=kol_history_match.annotate_platform_items,
        db_write_fn=db_write,
        persist_result=_persist_platform_search_result,
        country_in_excluded_region=_kol_discovery._country_in_excluded_region,
        unavailable_error=_provider_unavailable,
    )


@router.post("/search/platform")
async def search_kol_platform(
    request: Request,
    body: dict,
    staff=Depends(require_tab("kol_ops", "write")),
):
    """Queue paid platform discovery; worker persists normalized candidates."""
    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    # Zero-provider validations remain immediate and deterministic.
    platform = str(body.get("platform") or "youtube").strip().lower()
    market = str(body.get("market") or "").strip().upper()
    if platform == "douyin" or (
        bool(body.get("exclude_chinese", True))
        and _kol_discovery._country_in_excluded_region(market)
    ):
        return await _execute_platform_search(body, staff=staff)
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    try:
        task_id = await queue.enqueue(
            "kol_platform_search",
            {"body": dict(body), "staff": dict(staff or {})},
            lock_key=f"kol_platform_search:{platform}:{market}:{query.casefold()}",
            timeout_seconds=1200,
        )
    except Exception as exc:  # queue faults are retryable but never expose internals
        logger.warning("KOL platform search enqueue failed", exc_info=True)
        raise _provider_unavailable(
            "platform_search_unavailable", "platform_search"
        ) from exc
    return {
        "status": "queued",
        "job_id": task_id,
        "progressive": True,
        "initial_stage": "queued",
        "provider_calls_performed": False,
    }


@router.post("/tools/analyze-url")
async def analyze_kol_url_tool(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    url = str(body.get("url") or "").strip()
    platform = str(body.get("platform") or "").strip().lower()
    creator_handle = str(body.get("creator_handle") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        result = await analyze_kol_url_standalone(url, platform_hint=platform, creator_handle=creator_handle)
    except Exception as exc:  # noqa: BLE001 - provider faults are a retryable route state
        logger.warning("kol URL analysis provider failed", exc_info=True)
        raise _provider_unavailable("url_analysis_unavailable", "url_analysis") from exc
    providers = result.get("providers") or []
    await db_write(
        lambda: _log_activity_commit(
            staff,
            "url_analyze",
            target_type="url",
            query=url,
            platform=str((result.get("scrape") or {}).get("platform") or platform),
            api_provider="+".join(providers) if providers else str(result.get("method") or "scrape"),
            api_calls=max(1, len(providers)),
            result_count=1 if result.get("status") != "failed" else 0,
            metadata={
                "status": result.get("status"),
                "quality_score": result.get("quality_score"),
                "title": (result.get("scrape") or {}).get("title"),
                "error": result.get("error"),
            },
        )
    )
    return result


@router.get("/candidates")
def list_candidates(
    q: str | None = None,
    platform: str | None = None,
    market: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    staff=Depends(require_tab("kol_ops", "read")),
):
    where, params = [], []
    if q:
        where.append("(LOWER(channel_name) LIKE ? OR LOWER(sample_title) LIKE ? OR LOWER(search_query) LIKE ? OR LOWER(source_url) LIKE ?)")
        params.extend([_like(q), _like(q), _like(q), _like(q)])
    if platform:
        where.append("platform = ?"); params.append(platform)
    if market:
        where.append("market = ?"); params.append(market.upper())
    if status:
        where.append("status = ?"); params.append(status)
    if date_from:
        where.append("updated_at >= ?"); params.append(date_from)
    if date_to:
        where.append("updated_at <= ?"); params.append(date_to)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    safe_limit, safe_offset = _limit_offset(limit, offset)
    total = _scalar(f"SELECT COUNT(*) FROM kol_candidates {where_sql}", params)
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM kol_candidates
        {where_sql}
        ORDER BY updated_at DESC
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, safe_offset],
    ).fetchall()
    return {
        "items": _items(rows),
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "total": total,
            "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < total else None,
            "prev_offset": max(0, safe_offset - safe_limit) if safe_offset > 0 else None,
        },
    }


@router.patch("/candidates/{candidate_id}")
def update_candidate(candidate_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["status", "notes", "niche", "country", "contact_email"]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?")
            params.append(body[key])
    if "status" in body:
        fields.append("reviewed_by_staff_id = ?")
        params.append(int(staff.get("id") or 0))
    if not fields:
        return {"ok": True}
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(int(candidate_id))
    conn = get_conn()
    conn.execute(f"UPDATE kol_candidates SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(
        conn,
        staff,
        "candidate_review" if "status" in body else "candidate_update",
        target_type="candidate",
        target_id=int(candidate_id),
        result_count=1,
        metadata={"status": body.get("status", ""), "fields": list(body.keys())},
    )
    conn.commit()
    return {"ok": True}


@router.post("/candidates/{candidate_id}/promote")
def promote_candidate(candidate_id: int, body: dict | None = None, staff=Depends(require_tab("kol_ops", "write"))):
    conn = get_conn()
    row = conn.execute("SELECT * FROM kol_candidates WHERE id = ?", (int(candidate_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Candidate not found")
    payload = body or {}
    cur = conn.execute(
        """
        INSERT INTO kols
            (channel_name, channel_url, platform, country, niche, follower_count, avg_views,
             contact_email, contact_status, notes, assigned_staff_id, created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["channel_name"],
            row["channel_url"],
            row["platform"],
            payload.get("country") or row["country"],
            payload.get("niche") or row["niche"],
            _int(row["follower_count"]),
            _int(row["avg_views"]),
            payload.get("contact_email") or row["contact_email"],
            "cold",
            payload.get("notes") or row["notes"] or f"Promoted from candidate #{candidate_id}",
            payload.get("assigned_staff_id") or staff.get("id"),
            staff.get("id"),
            _now(),
            _now(),
        ),
    )
    kol_id = _insert_id(conn, cur, "kols")
    conn.execute(
        """
        UPDATE kol_candidates
        SET status = 'imported', reviewed_by_staff_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (int(staff.get("id") or 0), _now(), int(candidate_id)),
    )
    _log_activity(
        conn,
        staff,
        "candidate_promote",
        target_type="candidate",
        target_id=int(candidate_id),
        result_count=1,
        metadata={"kol_id": kol_id},
    )
    conn.commit()
    return {"id": kol_id, "candidate_id": int(candidate_id)}


@router.get("/kols/{kol_id}")
def get_kol(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="KOL not found")
    _log_activity(
        conn,
        staff,
        "view_kol",
        target_type="kol",
        target_id=int(kol_id),
        query=str(row["channel_name"] or ""),
        platform=str(row["platform"] or ""),
        market=str(row["country"] or ""),
    )
    conn.commit()
    outreach = conn.execute("SELECT * FROM kol_outreach WHERE kol_id = ? ORDER BY action_at DESC", (int(kol_id),)).fetchall()
    campaigns = conn.execute("SELECT * FROM kol_campaigns WHERE kol_id = ? ORDER BY created_at DESC", (int(kol_id),)).fetchall()
    content = conn.execute(
        """
        SELECT co.*, ca.product_sku, ca.status AS campaign_status
        FROM kol_content co
        JOIN kol_campaigns ca ON ca.id = co.campaign_id
        WHERE ca.kol_id = ?
        ORDER BY co.created_at DESC
        """,
        (int(kol_id),),
    ).fetchall()
    attribution = conn.execute(
        """
        SELECT at.*
        FROM kol_attribution at
        JOIN kol_content co ON co.id = at.content_id
        JOIN kol_campaigns ca ON ca.id = co.campaign_id
        WHERE ca.kol_id = ?
        ORDER BY at.attributed_at DESC
        """,
        (int(kol_id),),
    ).fetchall()
    return {
        "kol": dict(row),
        "outreach": _items(outreach),
        "campaigns": _items(campaigns),
        "content": _items(content),
        "attribution": _items(attribution),
        "dossier": get_kol_dossier(int(kol_id)),
    }


@router.post("/kols/{kol_id}/scan-account")
async def scan_kol_account_endpoint(
    request: Request,
    kol_id: int,
    body: dict = Body(default={}),
    staff=Depends(require_tab("kol_ops", "write")),
):
    assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    task_id = await queue.enqueue(
        "kol_dossier_scan",
        {
            "kol_id": int(kol_id),
            "max_posts": max(1, min(_int(body.get("max_posts"), 50), 500)),
            "staff": dict(staff or {}),
            "surface": "kol_ops",
        },
        lock_key=f"kol_dossier_scan:{int(kol_id)}",
        timeout_seconds=1800,
    )
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.post("/kols/{kol_id}/analyze-account")
async def analyze_kol_account_endpoint(kol_id: int, body: dict = Body(default={}), staff=Depends(require_tab("kol_ops", "write"))):
    result = await analyze_kol_account(
        int(kol_id),
        product_sku=str(body.get("product_sku") or ""),
        snapshot_id=_int(body.get("snapshot_id"), 0) or None,
    )
    await db_write(
        lambda: _log_kol_system_action(
            staff,
            "account_analyze",
            int(kol_id),
            query=str(body.get("product_sku") or ""),
            api_provider=str(result.get("method") or "metrics"),
            api_calls=1 if "claude" in str(result.get("method") or "") else 0,
            result_count=1,
            metadata={"report_id": result.get("report_id"), "snapshot_id": result.get("snapshot_id")},
        )
    )
    return result


@router.get("/kols/{kol_id}/dossier")
def fetch_kol_dossier(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    conn = get_conn()
    row = conn.execute("SELECT platform, country, channel_name FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="KOL not found")
    _log_activity(
        conn,
        staff,
        "view_kol_dossier",
        target_type="kol",
        target_id=int(kol_id),
        query=str(row["channel_name"] or ""),
        platform=str(row["platform"] or ""),
        market=str(row["country"] or ""),
    )
    conn.commit()
    return get_kol_dossier(int(kol_id))


@router.get("/kols/{kol_id}/posts")
def fetch_kol_posts(kol_id: int, limit: int = 25, offset: int = 0, staff=Depends(require_tab("kol_ops", "read"))):
    try:
        assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "KOL not found") from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    return list_kol_posts(int(kol_id), limit=limit, offset=offset, prefer_main_id=True)


@router.get("/kols/{kol_id}/comments")
def fetch_kol_comments(
    kol_id: int,
    limit: int = 25,
    offset: int = 0,
    post_url: str = "",
    staff=Depends(require_tab("kol_ops", "read")),
):
    try:
        assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "KOL not found") from exc
    except scope.ScopeDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
    return list_kol_comments(int(kol_id), limit=limit, offset=offset, post_url=post_url, prefer_main_id=True)


@router.post("/kols")
def create_kol(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("channel_name") or not body.get("platform"):
        raise HTTPException(status_code=400, detail="channel_name and platform required")
    platform = _normalize_platform(body.get("platform", ""))
    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO kols
            (channel_name, channel_url, platform, country, niche, follower_count, avg_views,
             contact_email, contact_phone, contact_status, notes, assigned_staff_id,
             project_name, owner_name, media_name, duplicate_flag, scale_tier, content_type,
             approval_note, channel_tags, affiliate_id, affiliate_link, discount_code,
             amazon_link, short_link, primary_category, promoted_product,
             created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _clean_creator_name(body.get("channel_name", ""), body.get("owner_name", "")),
            body.get("channel_url", ""),
            platform,
            _normalize_country_code(body.get("country", "")),
            body.get("niche", ""),
            int(body.get("follower_count") or 0),
            int(body.get("avg_views") or 0),
            body.get("contact_email", ""),
            body.get("contact_phone", ""),
            body.get("contact_status", "cold"),
            body.get("notes", ""),
            body.get("assigned_staff_id"),
            body.get("project_name", ""),
            body.get("owner_name", ""),
            body.get("media_name", ""),
            body.get("duplicate_flag", ""),
            body.get("scale_tier", ""),
            body.get("content_type", ""),
            body.get("approval_note", ""),
            body.get("channel_tags", ""),
            body.get("affiliate_id", ""),
            body.get("affiliate_link", ""),
            body.get("discount_code", ""),
            body.get("amazon_link", ""),
            body.get("short_link", ""),
            body.get("primary_category", ""),
            body.get("promoted_product", ""),
            staff.get("id"),
            _now(),
            _now(),
        ),
    )
    kol_id = _insert_id(conn, cur, "kols")
    _log_activity(conn, staff, "create_kol", target_type="kol", target_id=kol_id, result_count=1)
    conn.commit()
    return {"id": kol_id}


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = [
        "channel_name", "channel_url", "platform", "country", "niche", "follower_count", "avg_views",
        "contact_email", "contact_phone", "contact_status", "notes", "assigned_staff_id",
        "project_name", "owner_name", "media_name", "duplicate_flag", "scale_tier", "content_type",
        "approval_note", "channel_tags", "affiliate_id", "affiliate_link", "discount_code",
        "amazon_link", "short_link", "primary_category", "promoted_product",
    ]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?"); params.append(body[key])
    if not fields:
        return {"ok": True}
    fields.append("updated_at = ?"); params.append(_now())
    params.append(int(kol_id))
    conn = get_conn()
    conn.execute(f"UPDATE kols SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(conn, staff, "update_kol", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"fields": list(body.keys())})
    conn.commit()
    return {"ok": True}


@router.delete("/kols/{kol_id}")
def delete_kol(kol_id: int, body: dict | None = None, staff=Depends(require_tab("kol_ops", "write"))):
    _verify_confirm_password(staff, (body or {}).get("confirm_password"))
    conn = get_conn()
    if not conn.execute("SELECT id FROM kols WHERE id = ?", (int(kol_id),)).fetchone():
        raise HTTPException(status_code=404, detail="KOL not found")
    content_ids = [row["id"] for row in conn.execute(
        """
        SELECT co.id
        FROM kol_content co
        JOIN kol_campaigns ca ON ca.id = co.campaign_id
        WHERE ca.kol_id = ?
        """,
        (int(kol_id),),
    ).fetchall()]
    if content_ids:
        placeholders = ",".join("?" for _ in content_ids)
        conn.execute(f"DELETE FROM kol_attribution WHERE content_id IN ({placeholders})", content_ids)
    campaign_ids = [row["id"] for row in conn.execute("SELECT id FROM kol_campaigns WHERE kol_id = ?", (int(kol_id),)).fetchall()]
    if campaign_ids:
        placeholders = ",".join("?" for _ in campaign_ids)
        conn.execute(f"DELETE FROM kol_content WHERE campaign_id IN ({placeholders})", campaign_ids)
    conn.execute("DELETE FROM kol_campaigns WHERE kol_id = ?", (int(kol_id),))
    conn.execute("DELETE FROM kol_outreach WHERE kol_id = ?", (int(kol_id),))
    conn.execute("DELETE FROM kols WHERE id = ?", (int(kol_id),))
    conn.commit()
    return {"ok": True}


@router.post("/kols/import-csv")
def import_kols_csv(request: Request, file: UploadFile = File(...), staff=Depends(require_tab("kol_ops", "write"))):
    raw = file.file.read()
    rows = _uploaded_rows(file.filename or "", raw)
    return import_kols_csv_rows(file.filename or "", rows, staff)


@router.post("/kols/{kol_id}/ai-suggestions")
def ai_suggestions(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    conn = get_conn()
    kol = conn.execute("SELECT * FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not kol:
        raise HTTPException(status_code=404, detail="KOL not found")
    content = conn.execute(
        """
        SELECT co.*
        FROM kol_content co
        JOIN kol_campaigns ca ON ca.id = co.campaign_id
        WHERE ca.kol_id = ?
        ORDER BY co.created_at DESC
        LIMIT 20
        """,
        (int(kol_id),),
    ).fetchall()
    similar = conn.execute(
        """
        SELECT id, channel_name, platform, country, follower_count, avg_views
        FROM kols
        WHERE id != ? AND (niche = ? OR platform = ?)
        ORDER BY avg_views DESC
        LIMIT 3
        """,
        (int(kol_id), kol["niche"], kol["platform"]),
    ).fetchall()
    avg_score = 0
    if content:
        scores = [int(row["ai_quality_score"] or 0) for row in content if row["ai_quality_score"] is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    suggestions = [
        f"Keep the next brief anchored on {kol['niche'] or 'camera/lens'} use cases and ask for one clear product moment in the first 10 seconds.",
        "Request one pinned comment with affiliate link and one short follow-up clip within 72 hours.",
        f"Current average AI score is {avg_score}; prioritize creators above this benchmark for paid repeats.",
    ]
    _log_activity(conn, staff, "ai_suggestion", target_type="kol", target_id=int(kol_id), api_provider="claude", api_calls=1, result_count=len(suggestions))
    conn.commit()
    return {
        "kol_id": int(kol_id),
        "suggestions": suggestions,
        "similar_kols": _items(similar),
        "mode": "phase_a_llm_pseudo_recommendation",
    }
