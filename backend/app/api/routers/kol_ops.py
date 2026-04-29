"""KOL Operations admin API."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from app.api.dependencies.perms import require_tab
from app.core.security import verify_password
from app.db.connection import get_conn
from app.services.kol.content_scorer import score_kol_content
from app.services.kol.metrics import cpv, engagement_rate, roi

router = APIRouter(prefix="/api/admin/kol", tags=["kol-ops"])


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _dict(row) -> dict:
    return dict(row) if row else {}


def _items(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _verify_confirm_password(staff: dict, confirm_password: str | None) -> None:
    if not confirm_password:
        raise HTTPException(status_code=400, detail="confirm_password required")
    row = get_conn().execute("SELECT password_hash FROM users WHERE id = ?", (int(staff.get("user_id") or staff.get("id") or 0),)).fetchone()
    if not row or not verify_password(confirm_password, row["password_hash"]):
        raise HTTPException(status_code=403, detail="Invalid confirmation password")


def _content_rollup_sql(where_sql: str = "", params: list | None = None) -> dict:
    params = params or []
    row = get_conn().execute(
        f"""
        SELECT
            COUNT(DISTINCT k.id) AS kol_count,
            COUNT(DISTINCT ca.id) AS campaign_count,
            COUNT(DISTINCT co.id) AS content_count,
            COALESCE(SUM(ca.cost_cents), 0) AS total_cost_cents,
            COALESCE(SUM(co.views), 0) AS total_views,
            COALESCE(SUM(co.likes), 0) AS total_likes,
            COALESCE(SUM(co.comments), 0) AS total_comments,
            COALESCE(SUM(co.shares), 0) AS total_shares,
            COALESCE(SUM(at.attributed_revenue_cents), 0) AS total_revenue_cents,
            COALESCE(AVG(co.ai_quality_score), 0) AS avg_quality_score
        FROM kols k
        LEFT JOIN kol_campaigns ca ON ca.kol_id = k.id
        LEFT JOIN kol_content co ON co.campaign_id = ca.id
        LEFT JOIN kol_attribution at ON at.content_id = co.id
        {where_sql}
        """,
        params,
    ).fetchone()
    data = _dict(row)
    data["avg_engagement_rate"] = engagement_rate(
        data.get("total_likes", 0),
        data.get("total_comments", 0),
        data.get("total_shares", 0),
        data.get("total_views", 0),
    )
    data["cpv"] = cpv(data.get("total_cost_cents", 0), data.get("total_views", 0))
    data["roi"] = roi(data.get("total_cost_cents", 0), data.get("total_revenue_cents", 0))
    return data


@router.get("/kols")
def list_kols(
    staff_id: int | None = None,
    country: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    limit: int = 200,
    staff=Depends(require_tab("kol_ops", "read")),
):
    conn = get_conn()
    where, params = [], []
    if staff_id:
        where.append("k.assigned_staff_id = ?"); params.append(staff_id)
    if country:
        where.append("k.country = ?"); params.append(country)
    if platform:
        where.append("k.platform = ?"); params.append(platform)
    if status:
        where.append("k.contact_status = ?"); params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
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
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 200), 500))],
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["engagement_rate"] = engagement_rate(item.get("likes", 0), item.get("comments", 0), item.get("shares", 0), item.get("views", 0))
        item["cpv"] = cpv(item.get("cost_cents", 0), item.get("views", 0))
        item["roi"] = roi(item.get("cost_cents", 0), item.get("revenue_cents", 0))
        items.append(item)
    return {"items": items, "summary": _content_rollup_sql()}


@router.get("/kols/{kol_id}")
def get_kol(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="KOL not found")
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
    }


@router.post("/kols")
def create_kol(body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    if not body.get("channel_name") or not body.get("platform"):
        raise HTTPException(status_code=400, detail="channel_name and platform required")
    conn = get_conn()
    cur = conn.execute(
        """
        INSERT INTO kols
            (channel_name, channel_url, platform, country, niche, follower_count, avg_views,
             contact_email, contact_phone, contact_status, notes, assigned_staff_id,
             created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            body.get("channel_name"),
            body.get("channel_url", ""),
            body.get("platform"),
            body.get("country", ""),
            body.get("niche", ""),
            int(body.get("follower_count") or 0),
            int(body.get("avg_views") or 0),
            body.get("contact_email", ""),
            body.get("contact_phone", ""),
            body.get("contact_status", "cold"),
            body.get("notes", ""),
            body.get("assigned_staff_id"),
            staff.get("id"),
            _now(),
            _now(),
        ),
    )
    conn.commit()
    return {"id": cur.lastrowid}


@router.patch("/kols/{kol_id}")
def update_kol(kol_id: int, body: dict, staff=Depends(require_tab("kol_ops", "write"))):
    allowed = ["channel_name", "channel_url", "platform", "country", "niche", "follower_count", "avg_views", "contact_email", "contact_phone", "contact_status", "notes", "assigned_staff_id"]
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
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    conn = get_conn()
    count = 0
    for row in reader:
        name = (row.get("channel_name") or "").strip()
        platform = (row.get("platform") or "").strip().lower()
        if not name or not platform:
            continue
        conn.execute(
            """
            INSERT INTO kols
                (channel_name, channel_url, platform, country, niche, follower_count, avg_views,
                 contact_email, contact_status, assigned_staff_id, created_by_staff_id, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                name,
                row.get("channel_url", ""),
                platform,
                row.get("country", ""),
                row.get("niche", ""),
                int(row.get("follower_count") or 0),
                int(row.get("avg_views") or 0),
                row.get("contact_email", ""),
                row.get("contact_status", "cold") or "cold",
                row.get("assigned_staff_id") or None,
                staff.get("id"),
                _now(),
                _now(),
            ),
        )
        count += 1
    conn.commit()
    return {"imported": count}


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
    conn.commit()
    return {"id": cur.lastrowid}


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
    conn.commit()
    return {"id": cur.lastrowid}


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
    if not conn.execute("SELECT id FROM kol_campaigns WHERE id = ?", (campaign_id,)).fetchone():
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
    conn.commit()
    return {"id": cur.lastrowid}


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
    conn.commit()
    return {"ok": True}


@router.post("/content/{content_id}/score")
async def score_content(content_id: int, staff=Depends(require_tab("kol_ops", "write"))):
    try:
        return await score_kol_content(content_id)
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
    conn.commit()
    return {"id": cur.lastrowid}


@router.get("/dashboard/staff-performance")
def staff_performance(staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute(
        """
        SELECT
            s.id AS staff_id,
            u.name AS staff_name,
            COUNT(DISTINCT k.id) AS kol_count,
            COUNT(DISTINCT c.id) AS campaign_count,
            COALESCE(SUM(c.cost_cents), 0) AS total_cost_cents,
            COALESCE(SUM(co.views), 0) AS total_views,
            COALESCE(SUM(co.likes), 0) AS total_likes,
            COALESCE(SUM(co.comments), 0) AS total_comments,
            COALESCE(SUM(co.shares), 0) AS total_shares,
            COALESCE(SUM(at.attributed_revenue_cents), 0) AS total_revenue_cents,
            COALESCE(AVG(co.ai_quality_score), 0) AS avg_quality_score
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kols k ON k.assigned_staff_id = s.id
        LEFT JOIN kol_campaigns c ON c.staff_id = s.id
        LEFT JOIN kol_content co ON co.campaign_id = c.id
        LEFT JOIN kol_attribution at ON at.content_id = co.id
        GROUP BY s.id, u.name
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
    return {
        "kol_id": int(kol_id),
        "suggestions": suggestions,
        "similar_kols": _items(similar),
        "mode": "phase_a_llm_pseudo_recommendation",
    }
