"""KOL Operations admin API."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File

from app.api.dependencies.perms import require_tab
from app.api.routers.kol_ops_helpers import (
    _clean_creator_name,
    _content_rollup_sql,
    _persist_search_candidates,
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
from app.services.kol.content_analyzer import analyze_kol_content_url, analyze_kol_url_standalone
from app.services.kol.content_scorer import score_kol_content
from app.services.kol.metrics import cpv, engagement_rate, roi

from app.api.routers.kol_ops_schema import ensure_kol_schema


router = APIRouter(prefix="/api/admin/kol", tags=["kol-ops"], dependencies=[Depends(ensure_kol_schema)])


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


@router.post("/search/platform")
async def search_kol_platform(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    query = str(body.get("query") or "").strip()
    platform = str(body.get("platform") or "youtube").strip().lower()
    market = str(body.get("market") or "").strip().upper()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    result = await search_platform_content(
        platform,
        query,
        market=market,
        max_results=_int(body.get("max_results"), 25),
    )
    candidate_ids = await db_write(lambda: _persist_search_candidates(result.get("items", []), body, platform, market))
    await db_write(
        lambda: _log_activity_commit(
            staff,
            "platform_search",
            query=query,
            platform=platform,
            market=market,
            api_provider=str(result.get("provider") or result.get("source") or platform),
            api_calls=1,
            result_count=len(result.get("items", [])),
            metadata={"saved_candidates": len(candidate_ids), "niche": body.get("niche", "")},
        )
    )
    return {
        **result,
        "candidate_ids": candidate_ids,
        "saved_candidates": len(candidate_ids),
    }


@router.post("/tools/analyze-url")
async def analyze_kol_url_tool(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    url = str(body.get("url") or "").strip()
    platform = str(body.get("platform") or "").strip().lower()
    creator_handle = str(body.get("creator_handle") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    result = await analyze_kol_url_standalone(url, platform_hint=platform, creator_handle=creator_handle)
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
async def scan_kol_account_endpoint(kol_id: int, body: dict = Body(default={}), staff=Depends(require_tab("kol_ops", "write"))):
    result = await scan_kol_account(int(kol_id), max_posts=_int(body.get("max_posts"), 50))
    await db_write(
        lambda: _log_kol_system_action(
            staff,
            "account_scan",
            int(kol_id),
            query=str(result.get("handle") or ""),
            platform=str(result.get("platform") or ""),
            api_provider="apify",
            api_calls=1,
            result_count=_int(result.get("content_count")),
            metadata={"snapshot_id": result.get("snapshot_id"), "status": result.get("status")},
        )
    )
    return result


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
    return list_kol_posts(int(kol_id), limit=limit, offset=offset)


@router.get("/kols/{kol_id}/comments")
def fetch_kol_comments(kol_id: int, limit: int = 25, offset: int = 0, staff=Depends(require_tab("kol_ops", "read"))):
    return list_kol_comments(int(kol_id), limit=limit, offset=offset)


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
    conn = get_conn()
    count = 0
    campaigns = 0
    content_count = 0
    skipped = 0
    for row in rows:
        name = _clean_creator_name(row.get("channel_name") or row.get("media_name") or row.get("project_name") or "", row.get("owner_name", ""))
        platform = _normalize_platform(row.get("platform") or "")
        if not name or not platform:
            skipped += 1
            continue
        country_code = _normalize_country_code(row.get("country", ""))
        contact_status = _row_contact_status(row)
        follower_count = _parse_count(row.get("follower_count"))
        views = _parse_count(row.get("views") or row.get("avg_views"))
        likes = _parse_count(row.get("likes"))
        comments = _parse_count(row.get("comments"))
        shares = _parse_count(row.get("shares"))
        budget_spend_cents = _parse_cents(row.get("budget_spend_cents") or row.get("budget_quote_cents"))
        direct_conversion_cents = _parse_cents(row.get("direct_conversion_cents"))
        assigned_staff_id = _staff_id_by_owner_name(conn, row.get("owner_name", "")) or staff.get("id")
        channel_url = str(row.get("channel_url") or "").strip()
        existing = conn.execute(
            """
            SELECT id FROM kols
            WHERE lower(channel_name)=lower(?) AND lower(platform)=lower(?)
              AND COALESCE(channel_url, '') = COALESCE(?, '')
            LIMIT 1
            """,
            (name, platform, channel_url),
        ).fetchone()
        if existing:
            kol_id = int(existing["id"])
            conn.execute(
                """
                UPDATE kols
                   SET country=?, niche=?,
                       follower_count=CASE WHEN COALESCE(follower_count, 0) > ? THEN follower_count ELSE ? END,
                       avg_views=CASE WHEN COALESCE(avg_views, 0) > ? THEN avg_views ELSE ? END,
                       contact_email=COALESCE(NULLIF(?, ''), contact_email),
                       contact_status=COALESCE(NULLIF(?, ''), contact_status),
                       project_name=?, owner_name=?, media_name=?, duplicate_flag=?,
                       scale_tier=?, content_type=?, approval_note=?, channel_tags=?,
                       affiliate_id=?, affiliate_link=?, discount_code=?, amazon_link=?,
                       short_link=?, primary_category=?, promoted_product=?, updated_at=?
                 WHERE id=?
                """,
                (
                    country_code,
                    row.get("channel_tags") or row.get("content_type") or row.get("niche", ""),
                    follower_count,
                    follower_count,
                    views,
                    views,
                    row.get("contact_email", ""),
                    contact_status,
                    row.get("project_name", ""),
                    row.get("owner_name", ""),
                    row.get("media_name", ""),
                    row.get("duplicate_flag", ""),
                    row.get("scale_tier", ""),
                    row.get("content_type", ""),
                    row.get("approval_note", ""),
                    row.get("channel_tags", ""),
                    row.get("affiliate_id", ""),
                    row.get("affiliate_link", ""),
                    row.get("discount_code", ""),
                    row.get("amazon_link", ""),
                    row.get("short_link", ""),
                    row.get("primary_category", ""),
                    row.get("promoted_product", ""),
                    _now(),
                    kol_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO kols
                    (channel_name, channel_url, platform, country, niche, follower_count, avg_views,
                     contact_email, contact_status, assigned_staff_id, created_by_staff_id,
                     project_name, owner_name, media_name, duplicate_flag, scale_tier, content_type,
                     approval_note, channel_tags, affiliate_id, affiliate_link, discount_code,
                     amazon_link, short_link, primary_category, promoted_product, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    name,
                    channel_url,
                    platform,
                    country_code,
                    row.get("channel_tags") or row.get("content_type") or row.get("niche", ""),
                    follower_count,
                    views,
                    row.get("contact_email", ""),
                    contact_status,
                    assigned_staff_id,
                    staff.get("id"),
                    row.get("project_name", ""),
                    row.get("owner_name", ""),
                    row.get("media_name", ""),
                    row.get("duplicate_flag", ""),
                    row.get("scale_tier", ""),
                    row.get("content_type", ""),
                    row.get("approval_note", ""),
                    row.get("channel_tags", ""),
                    row.get("affiliate_id", ""),
                    row.get("affiliate_link", ""),
                    row.get("discount_code", ""),
                    row.get("amazon_link", ""),
                    row.get("short_link", ""),
                    row.get("primary_category", ""),
                    row.get("promoted_product", ""),
                    _now(),
                    _now(),
                ),
            )
            kol_id = _insert_id(conn, cur, "kols")
            count += 1
        campaign_id = None
        product = str(row.get("promoted_product") or "").strip()
        if product or budget_spend_cents or contact_status:
            note_parts = [
                str(row.get("collaboration_detail") or "").strip(),
                str(row.get("budget_request") or "").strip(),
                str(row.get("approval_note") or "").strip(),
            ]
            cur = conn.execute(
                """
                INSERT INTO kol_campaigns
                    (kol_id, product_sku, staff_id, started_at, ended_at, cost_cents, status, notes, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    kol_id,
                    product,
                    int(assigned_staff_id or staff.get("id") or 0),
                    None,
                    None,
                    budget_spend_cents,
                    contact_status or "planning",
                    " | ".join(part for part in note_parts if part),
                    _now(),
                ),
            )
            campaign_id = _insert_id(conn, cur, "kol_campaigns")
            campaigns += 1
        content_url = str(row.get("content_url") or "").strip()
        if content_url and campaign_id:
            cur = conn.execute(
                """
                INSERT INTO kol_content
                    (campaign_id, content_url, platform, posted_at, views, likes, comments, shares,
                     engagement_rate, last_metric_refresh, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    campaign_id,
                    content_url,
                    platform,
                    None,
                    views,
                    likes,
                    comments,
                    shares,
                    engagement_rate(likes, comments, shares, views),
                    _now(),
                    _now(),
                ),
            )
            content_id = _insert_id(conn, cur, "kol_content")
            content_count += 1
            if direct_conversion_cents:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kol_attribution
                        (content_id, shopify_order_id, attributed_revenue_cents, attributed_at)
                    VALUES (?,?,?,?)
                    """,
                    (content_id, f"import:{file.filename}:{content_count}", direct_conversion_cents, _now()),
                )
    _log_activity(
        conn,
        staff,
        "import_sheet",
        target_type="file",
        query=file.filename or "",
        result_count=count,
        metadata={"rows": len(rows), "campaigns": campaigns, "content": content_count, "skipped": skipped},
    )
    conn.commit()
    return {"imported": count, "rows": len(rows), "campaigns": campaigns, "content": content_count, "skipped": skipped}


@router.post("/kols/{kol_id}/outreach")
def create_outreach(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("action_type"):
        raise HTTPException(status_code=400, detail="action_type required")
    conn = get_conn()
    if not conn.execute("SELECT id FROM kols WHERE id = ?", (int(kol_id),)).fetchone():
        raise HTTPException(status_code=404, detail="KOL not found")
    cur = conn.execute(
        """
        INSERT INTO kol_outreach (kol_id, staff_id, action_type, action_at, notes, next_action_at)
        VALUES (?,?,?,?,?,?)
        """,
        (int(kol_id), int(staff.get("id") or 0), body.get("action_type"), body.get("action_at") or _now(), body.get("notes", ""), body.get("next_action_at")),
    )
    conn.execute("UPDATE kols SET updated_at = ? WHERE id = ?", (_now(), int(kol_id)))
    _log_activity(conn, staff, "outreach_add", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"action_type": body.get("action_type")})
    conn.commit()
    return {"id": _insert_id(conn, cur, "kol_outreach")}


@router.get("/kols/{kol_id}/outreach")
def list_outreach(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_outreach WHERE kol_id = ? ORDER BY action_at DESC", (int(kol_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/kols/{kol_id}/campaigns")
def create_campaign(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    conn = get_conn()
    if not conn.execute("SELECT id FROM kols WHERE id = ?", (int(kol_id),)).fetchone():
        raise HTTPException(status_code=404, detail="KOL not found")
    cur = conn.execute(
        """
        INSERT INTO kol_campaigns
            (kol_id, product_sku, staff_id, started_at, ended_at, cost_cents, status, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(kol_id),
            body.get("product_sku", ""),
            int(body.get("staff_id") or staff.get("id") or 0),
            body.get("started_at"),
            body.get("ended_at"),
            _int(body.get("cost_cents")),
            body.get("status", "planning"),
            body.get("notes", ""),
            _now(),
        ),
    )
    conn.execute("UPDATE kols SET updated_at = ? WHERE id = ?", (_now(), int(kol_id)))
    campaign_id = _insert_id(conn, cur, "kol_campaigns")
    _log_activity(conn, staff, "campaign_create", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"campaign_id": campaign_id, "product_sku": body.get("product_sku", "")})
    conn.commit()
    return {"id": campaign_id}


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["product_sku", "staff_id", "started_at", "ended_at", "cost_cents", "status", "notes"]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?")
            params.append(body[key])
    if not fields:
        return {"ok": True}
    params.append(int(campaign_id))
    conn = get_conn()
    conn.execute(f"UPDATE kol_campaigns SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(conn, staff, "campaign_update", target_type="campaign", target_id=int(campaign_id), result_count=1, metadata={"fields": list(body.keys())})
    conn.commit()
    return {"ok": True}


@router.get("/campaigns/{campaign_id}/content")
def list_campaign_content(campaign_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_content WHERE campaign_id = ? ORDER BY created_at DESC", (int(campaign_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/content")
def create_content(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("campaign_id") or not body.get("content_url") or not body.get("platform"):
        raise HTTPException(status_code=400, detail="campaign_id, content_url and platform required")
    conn = get_conn()
    campaign_id = int(body.get("campaign_id"))
    campaign = conn.execute("SELECT id, kol_id FROM kol_campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    views = _int(body.get("views"))
    likes = _int(body.get("likes"))
    comments = _int(body.get("comments"))
    shares = _int(body.get("shares"))
    cur = conn.execute(
        """
        INSERT INTO kol_content
            (campaign_id, content_url, platform, posted_at, views, likes, comments, shares,
             engagement_rate, ai_quality_score, ai_summary, ai_topics_json, last_metric_refresh, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            campaign_id,
            body.get("content_url"),
            body.get("platform"),
            body.get("posted_at"),
            views,
            likes,
            comments,
            shares,
            engagement_rate(likes, comments, shares, views),
            body.get("ai_quality_score"),
            body.get("ai_summary", ""),
            json.dumps(body.get("ai_topics", []), ensure_ascii=False),
            _now(),
            _now(),
        ),
    )
    conn.execute("UPDATE kol_campaigns SET status = ? WHERE id = ?", ("已回片", campaign_id))
    conn.execute("UPDATE kols SET contact_status = ?, updated_at = ? WHERE id = ?", ("已回片", _now(), int(campaign["kol_id"])))
    content_id = _insert_id(conn, cur, "kol_content")
    _log_activity(conn, staff, "content_create", target_type="content", target_id=content_id, result_count=1, metadata={"campaign_id": campaign_id, "views": views})
    conn.commit()
    return {"id": content_id}


@router.patch("/content/{content_id}")
def update_content(content_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["content_url", "platform", "posted_at", "views", "likes", "comments", "shares", "ai_quality_score", "ai_summary", "last_metric_refresh"]
    fields, params = [], []
    for key in allowed:
        if key in body:
            fields.append(f"{key} = ?")
            params.append(body[key])
    metric_keys = {"views", "likes", "comments", "shares"}
    if metric_keys.intersection(body.keys()):
        current = get_conn().execute("SELECT views, likes, comments, shares FROM kol_content WHERE id = ?", (int(content_id),)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Content not found")
        views = _int(body.get("views", current["views"]))
        likes = _int(body.get("likes", current["likes"]))
        comments = _int(body.get("comments", current["comments"]))
        shares = _int(body.get("shares", current["shares"]))
        fields.append("engagement_rate = ?")
        params.append(engagement_rate(likes, comments, shares, views))
        fields.append("last_metric_refresh = ?")
        params.append(_now())
    if "ai_topics" in body:
        fields.append("ai_topics_json = ?")
        params.append(json.dumps(body.get("ai_topics") or [], ensure_ascii=False))
    if not fields:
        return {"ok": True}
    params.append(int(content_id))
    conn = get_conn()
    conn.execute(f"UPDATE kol_content SET {', '.join(fields)} WHERE id = ?", params)
    _log_activity(conn, staff, "data_update", target_type="content", target_id=int(content_id), result_count=1, metadata={"fields": list(body.keys())})
    conn.commit()
    return {"ok": True}


@router.post("/content/{content_id}/score")
async def score_content(
    content_id: int,
    request: Request,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("kol_ops", "write")),
):
    if bool(body.get("async")):
        queue = getattr(request.app.state, "job_queue", None)
        if queue is None:
            raise HTTPException(status_code=503, detail="job queue unavailable")
        task_id = await queue.enqueue("score_kol_content", {"content_id": int(content_id)})
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "ai_score_queue",
                target_type="content",
                target_id=int(content_id),
                api_provider="claude",
                api_calls=1,
                result_count=1,
                metadata={"job_id": task_id},
            )
        )
        return {"status": "queued", "job_id": task_id, "content_id": int(content_id)}
    try:
        result = await score_kol_content(content_id)
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "ai_score",
                target_type="content",
                target_id=int(content_id),
                api_provider="claude",
                api_calls=1,
                result_count=1,
                metadata={"score": result.get("quality_score")},
            )
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/content/{content_id}/analyze-url")
async def analyze_content_url(
    content_id: int,
    staff=Depends(require_tab("kol_ops", "write")),
):
    try:
        result = await analyze_kol_content_url(content_id)
        providers = []
        for layer in result.get("layers_used") or []:
            text = str(layer).lower()
            if "gpt" in text and "openai" not in providers:
                providers.append("openai")
            if "gemini" in text and "gemini" not in providers:
                providers.append("gemini")
            if "claude" in text or "text_" in text:
                if "claude" not in providers:
                    providers.append("claude")
        if not providers and result.get("status") == "failed":
            providers.append("scrape")
        await db_write(
            lambda: _log_activity_commit(
                staff,
                "content_analyze_url",
                target_type="content",
                target_id=int(content_id),
                api_provider="+".join(providers) if providers else str(result.get("method") or "analysis"),
                api_calls=max(1, len(providers)),
                result_count=1 if result.get("status") != "failed" else 0,
                metadata={
                    "status": result.get("status"),
                    "method": result.get("method"),
                    "quality_score": result.get("quality_score"),
                    "error": result.get("error"),
                },
            )
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/content/{content_id}/attribution")
def list_content_attribution(content_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute("SELECT * FROM kol_attribution WHERE content_id = ? ORDER BY attributed_at DESC", (int(content_id),)).fetchall()
    return {"items": _items(rows)}


@router.post("/content/{content_id}/attribute")
def attribute_content(content_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("shopify_order_id"):
        raise HTTPException(status_code=400, detail="shopify_order_id required")
    conn = get_conn()
    if not conn.execute("SELECT id FROM kol_content WHERE id = ?", (int(content_id),)).fetchone():
        raise HTTPException(status_code=404, detail="Content not found")
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO kol_attribution
            (content_id, shopify_order_id, attributed_revenue_cents, attributed_at)
        VALUES (?,?,?,?)
        """,
        (int(content_id), body.get("shopify_order_id"), _int(body.get("attributed_revenue_cents")), _now()),
    )
    attribution = conn.execute(
        """
        SELECT id FROM kol_attribution
        WHERE content_id = ? AND shopify_order_id = ?
        LIMIT 1
        """,
        (int(content_id), body.get("shopify_order_id")),
    ).fetchone()
    _log_activity(conn, staff, "attribution_add", target_type="content", target_id=int(content_id), result_count=1, metadata={"shopify_order_id": body.get("shopify_order_id", "")})
    conn.commit()
    return {"id": int(attribution["id"]) if attribution else None}


@router.get("/dashboard/staff-performance")
def staff_performance(staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute(
        """
        SELECT
            COALESCE(k.assigned_staff_id, 0) AS staff_id,
            COALESCE(NULLIF(u.name, ''), NULLIF(k.owner_name, ''), 'Unassigned') AS staff_name,
            COUNT(DISTINCT k.id) AS kol_count,
            COUNT(DISTINCT c.id) AS campaign_count,
            COALESCE(SUM(c.cost_cents), 0) AS total_cost_cents,
            COALESCE(SUM(co.views), 0) AS total_views,
            COALESCE(SUM(co.likes), 0) AS total_likes,
            COALESCE(SUM(co.comments), 0) AS total_comments,
            COALESCE(SUM(co.shares), 0) AS total_shares,
            COALESCE(SUM(at.attributed_revenue_cents), 0) AS total_revenue_cents,
            COALESCE(AVG(co.ai_quality_score), 0) AS avg_quality_score
        FROM kols k
        LEFT JOIN staff s ON s.id = k.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kol_campaigns c ON c.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = c.id
        LEFT JOIN kol_attribution at ON at.content_id = co.id
        GROUP BY COALESCE(k.assigned_staff_id, 0), COALESCE(NULLIF(u.name, ''), NULLIF(k.owner_name, ''), 'Unassigned')
        ORDER BY kol_count DESC
        """
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["avg_engagement_rate"] = engagement_rate(item.get("total_likes", 0), item.get("total_comments", 0), item.get("total_shares", 0), item.get("total_views", 0))
        item["roi"] = roi(item.get("total_cost_cents", 0), item.get("total_revenue_cents", 0))
        items.append(item)
    return {"items": items}


@router.get("/dashboard/staff-activity")
def staff_activity(
    date_from: str | None = None,
    date_to: str | None = None,
    staff=Depends(require_tab("kol_ops", "read")),
):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    start = f"{date_from or today}T00:00:00Z"
    end = f"{date_to or today}T23:59:59Z"
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(staff_name, ''), 'Unknown') AS staff_name,
            COALESCE(staff_id, 0) AS staff_id,
            COALESCE(user_id, 0) AS user_id,
            COUNT(*) AS actions,
            COALESCE(SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END), 0) AS searches,
            COALESCE(SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END), 0) AS kol_views,
            COALESCE(SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END), 0) AS reviews,
            COALESCE(SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END), 0) AS imports,
            COALESCE(SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END), 0) AS data_updates,
            COALESCE(SUM(CASE WHEN SUBSTR(action_type, 1, 3) = 'ai_' THEN 1 ELSE 0 END), 0) AS ai_actions,
            COALESCE(SUM(api_calls), 0) AS api_calls,
            COALESCE(SUM(result_count), 0) AS result_count,
            MAX(created_at) AS last_action_at
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        GROUP BY staff_name, staff_id, user_id
        ORDER BY actions DESC, last_action_at DESC
        """,
        (start, end),
    ).fetchall()
    recent = conn.execute(
        """
        SELECT *
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        ORDER BY created_at DESC
        LIMIT 80
        """,
        (start, end),
    ).fetchall()
    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS actions,
            COALESCE(SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END), 0) AS searches,
            COALESCE(SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END), 0) AS kol_views,
            COALESCE(SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END), 0) AS reviews,
            COALESCE(SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END), 0) AS imports,
            COALESCE(SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END), 0) AS data_updates,
            COALESCE(SUM(CASE WHEN SUBSTR(action_type, 1, 3) = 'ai_' THEN 1 ELSE 0 END), 0) AS ai_actions,
            COALESCE(SUM(api_calls), 0) AS api_calls,
            COALESCE(SUM(result_count), 0) AS result_count
        FROM kol_activity_log
        WHERE created_at >= ? AND created_at <= ?
        """,
        (start, end),
    ).fetchone()
    return {
        "window": {"date_from": date_from or today, "date_to": date_to or today},
        "totals": _dict(totals),
        "items": _items(rows),
        "recent": _items(recent),
    }


@router.get("/dashboard/cross-filter")
def cross_filter(
    staff_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    country: str | None = None,
    platform: str | None = None,
    product_sku: str | None = None,
    staff=Depends(require_tab("kol_ops", "read")),
):
    where, params = [], []
    if staff_id:
        where.append("k.assigned_staff_id = ?"); params.append(staff_id)
    if country:
        where.append("k.country = ?"); params.append(country)
    if platform:
        where.append("k.platform = ?"); params.append(platform)
    if product_sku:
        where.append("ca.product_sku = ?"); params.append(product_sku)
    if date_from:
        where.append("COALESCE(co.posted_at, ca.started_at, ca.created_at, k.created_at) >= ?"); params.append(date_from)
    if date_to:
        where.append("COALESCE(co.posted_at, ca.started_at, ca.created_at, k.created_at) <= ?"); params.append(date_to)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    conn = get_conn()
    kols = conn.execute(
        f"""
        SELECT DISTINCT k.*, u.name AS assigned_staff_name
        FROM kols k
        LEFT JOIN staff s ON s.id = k.assigned_staff_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kol_campaigns ca ON ca.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = ca.id
        {where_sql}
        ORDER BY k.updated_at DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    content = conn.execute(
        f"""
        SELECT co.*, k.channel_name, ca.product_sku
        FROM kols k
        JOIN kol_campaigns ca ON ca.kol_id = k.id
        JOIN kol_content co ON co.campaign_id = ca.id
        {where_sql}
        ORDER BY COALESCE(co.posted_at, co.created_at) DESC
        LIMIT 200
        """,
        params,
    ).fetchall()
    return {
        "kpis": _content_rollup_sql(where_sql, params),
        "kols": _items(kols),
        "content": _items(content),
        "performance_breakdown": {
            "country": country or "all",
            "platform": platform or "all",
            "product_sku": product_sku or "all",
        },
    }


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
