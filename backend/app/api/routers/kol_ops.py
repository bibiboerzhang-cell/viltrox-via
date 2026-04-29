"""KOL Operations admin API."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from app.api.dependencies.perms import require_tab
from app.db.connection import get_conn
from app.services.kol.content_scorer import score_kol_content

router = APIRouter(prefix="/api/admin/kol", tags=["kol-ops"])


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


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
        where.append("assigned_staff_id = ?"); params.append(staff_id)
    if country:
        where.append("country = ?"); params.append(country)
    if platform:
        where.append("platform = ?"); params.append(platform)
    if status:
        where.append("contact_status = ?"); params.append(status)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT * FROM kols {where_sql} ORDER BY updated_at DESC LIMIT ?",
        [*params, max(1, min(int(limit or 200), 500))],
    ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.get("/kols/{kol_id}")
def get_kol(kol_id: int, staff=Depends(require_tab("kol_ops", "read"))):
    conn = get_conn()
    row = conn.execute("SELECT * FROM kols WHERE id = ?", (int(kol_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="KOL not found")
    outreach = conn.execute("SELECT * FROM kol_outreach WHERE kol_id = ? ORDER BY action_at DESC", (int(kol_id),)).fetchall()
    campaigns = conn.execute("SELECT * FROM kol_campaigns WHERE kol_id = ? ORDER BY created_at DESC", (int(kol_id),)).fetchall()
    return {"kol": dict(row), "outreach": [dict(r) for r in outreach], "campaigns": [dict(r) for r in campaigns]}


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
    get_conn().execute(f"UPDATE kols SET {', '.join(fields)} WHERE id = ?", params)
    get_conn().commit()
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


@router.post("/content/{content_id}/score")
async def score_content(content_id: int, staff=Depends(require_tab("kol_ops", "write"))):
    return await score_kol_content(content_id)


@router.get("/dashboard/staff-performance")
def staff_performance(staff=Depends(require_tab("kol_ops", "read"))):
    rows = get_conn().execute(
        """
        SELECT
            s.id AS staff_id,
            u.name AS staff_name,
            COUNT(DISTINCT k.id) AS kol_count,
            COUNT(DISTINCT c.id) AS campaign_count,
            COALESCE(SUM(c.cost_cents), 0) AS total_cost_cents
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN kols k ON k.assigned_staff_id = s.id
        LEFT JOIN kol_campaigns c ON c.staff_id = s.id
        GROUP BY s.id, u.name
        ORDER BY kol_count DESC
        """
    ).fetchall()
    return {"items": [dict(row) for row in rows]}
