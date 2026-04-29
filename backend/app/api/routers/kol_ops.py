"""KOL Operations admin API."""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File

from app.api.dependencies.perms import require_tab
from app.core.security import verify_password
from app.db.connection import db_write, get_conn, is_postgres_runtime
from app.services.intelligence.account_scan_service import search_platform_content
from app.services.kol.content_scorer import score_kol_content
from app.services.kol.metrics import cpv, engagement_rate, roi

_SCHEMA_READY = False


def ensure_kol_schema() -> None:
    """Create local SQLite KOL tables for dev/demo runs.

    Production Postgres uses migrations/019_kol_operations.sql; this local guard
    prevents admin demo screens from failing when SQLite migrations have not yet
    been applied.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kols (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_name TEXT NOT NULL,
            channel_url TEXT,
            platform TEXT NOT NULL,
            country TEXT,
            niche TEXT,
            project_name TEXT,
            owner_name TEXT,
            media_name TEXT,
            duplicate_flag TEXT,
            scale_tier TEXT,
            content_type TEXT,
            approval_note TEXT,
            channel_tags TEXT,
            affiliate_id TEXT,
            affiliate_link TEXT,
            discount_code TEXT,
            amazon_link TEXT,
            short_link TEXT,
            primary_category TEXT,
            promoted_product TEXT,
            follower_count INTEGER DEFAULT 0,
            avg_views INTEGER DEFAULT 0,
            contact_email TEXT,
            contact_phone TEXT,
            contact_status TEXT DEFAULT 'cold',
            notes TEXT,
            assigned_staff_id INTEGER,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kols_assigned ON kols(assigned_staff_id);
        CREATE INDEX IF NOT EXISTS idx_kols_platform_country ON kols(platform, country);
        CREATE INDEX IF NOT EXISTS idx_kols_status ON kols(contact_status);

        CREATE TABLE IF NOT EXISTS kol_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            action_at TEXT NOT NULL,
            notes TEXT,
            next_action_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_outreach_kol ON kol_outreach(kol_id);

        CREATE TABLE IF NOT EXISTS kol_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_id INTEGER NOT NULL,
            product_sku TEXT,
            staff_id INTEGER NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            cost_cents INTEGER DEFAULT 0,
            status TEXT DEFAULT 'planning',
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_campaigns_kol ON kol_campaigns(kol_id);
        CREATE INDEX IF NOT EXISTS idx_campaigns_staff ON kol_campaigns(staff_id);

        CREATE TABLE IF NOT EXISTS kol_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            content_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            posted_at TEXT,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            ai_quality_score INTEGER,
            ai_summary TEXT,
            ai_topics_json TEXT,
            last_metric_refresh TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_content_campaign ON kol_content(campaign_id);

        CREATE TABLE IF NOT EXISTS kol_attribution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            shopify_order_id TEXT,
            attributed_revenue_cents INTEGER DEFAULT 0,
            attributed_at TEXT NOT NULL,
            UNIQUE(content_id, shopify_order_id)
        );

        CREATE TABLE IF NOT EXISTS kol_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            channel_url TEXT,
            handle TEXT,
            country TEXT,
            niche TEXT,
            source_url TEXT,
            sample_title TEXT,
            follower_count INTEGER DEFAULT 0,
            avg_views INTEGER DEFAULT 0,
            contact_email TEXT,
            status TEXT DEFAULT 'new',
            search_query TEXT,
            market TEXT,
            reviewed_by_staff_id INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_status ON kol_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_platform_market ON kol_candidates(platform, market);
        CREATE INDEX IF NOT EXISTS idx_kol_candidates_query ON kol_candidates(search_query);

        CREATE TABLE IF NOT EXISTS kol_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER,
            user_id INTEGER,
            staff_name TEXT,
            action_type TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            query TEXT,
            platform TEXT,
            market TEXT,
            api_provider TEXT,
            api_calls INTEGER DEFAULT 0,
            result_count INTEGER DEFAULT 0,
            metadata_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kol_activity_created ON kol_activity_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_kol_activity_staff ON kol_activity_log(staff_id, created_at DESC);
        """
    )
    _ensure_sqlite_columns(
        conn,
        "kols",
        {
            "project_name": "TEXT",
            "owner_name": "TEXT",
            "media_name": "TEXT",
            "duplicate_flag": "TEXT",
            "scale_tier": "TEXT",
            "content_type": "TEXT",
            "approval_note": "TEXT",
            "channel_tags": "TEXT",
            "affiliate_id": "TEXT",
            "affiliate_link": "TEXT",
            "discount_code": "TEXT",
            "amazon_link": "TEXT",
            "short_link": "TEXT",
            "primary_category": "TEXT",
            "promoted_product": "TEXT",
        },
    )
    conn.commit()
    _SCHEMA_READY = True


router = APIRouter(prefix="/api/admin/kol", tags=["kol-ops"], dependencies=[Depends(ensure_kol_schema)])


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


def _limit_offset(limit: int = 50, offset: int = 0) -> tuple[int, int]:
    return max(1, min(int(limit or 50), 200)), max(0, int(offset or 0))


def _like(value: str) -> str:
    return f"%{value.strip().lower()}%"


def _scalar(query: str, params: list | tuple | None = None) -> int:
    row = get_conn().execute(query, params or []).fetchone()
    if not row:
        return 0
    return int(row[0] or 0)


def _ensure_sqlite_columns(conn, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def _staff_identity(staff: dict) -> dict[str, object]:
    return {
        "staff_id": int(staff.get("id") or 0),
        "user_id": int(staff.get("user_id") or 0),
        "staff_name": str(staff.get("name") or staff.get("email") or f"staff#{staff.get('id') or staff.get('user_id') or 0}"),
    }


def _log_activity(
    conn,
    staff: dict,
    action_type: str,
    *,
    target_type: str = "",
    target_id: int | None = None,
    query: str = "",
    platform: str = "",
    market: str = "",
    api_provider: str = "",
    api_calls: int = 0,
    result_count: int = 0,
    metadata: dict | None = None,
) -> None:
    ident = _staff_identity(staff)
    conn.execute(
        """
        INSERT INTO kol_activity_log
            (staff_id, user_id, staff_name, action_type, target_type, target_id, query,
             platform, market, api_provider, api_calls, result_count, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ident["staff_id"],
            ident["user_id"],
            ident["staff_name"],
            action_type,
            target_type,
            target_id,
            query,
            platform,
            market,
            api_provider,
            int(api_calls or 0),
            int(result_count or 0),
            json.dumps(metadata or {}, ensure_ascii=False),
            _now(),
        ),
    )


HEADER_ALIASES = {
    "项目-红人": "project_name",
    "项目红人": "project_name",
    "创建日期": "source_created_at",
    "一级类目": "primary_category",
    "登记/对接人": "owner_name",
    "登记对接人": "owner_name",
    "推广产品": "promoted_product",
    "红人/媒体": "media_name",
    "红人媒体": "media_name",
    "重复": "duplicate_flag",
    "红人视频链接": "channel_url",
    "国家": "country",
    "平台": "platform",
    "量级": "scale_tier",
    "粉丝数/访客数": "follower_count",
    "粉丝数访客数": "follower_count",
    "内容类型": "content_type",
    "合作进度": "contact_status",
    "预算报价": "budget_quote_cents",
    "审批意见": "approval_note",
    "频道内容标签": "channel_tags",
    "合作内容详情": "collaboration_detail",
    "预算申请": "budget_request",
    "Affiliate ID": "affiliate_id",
    "affiliate id": "affiliate_id",
    "独立站Affiliate Link": "affiliate_link",
    "独立站affiliate link": "affiliate_link",
    "折扣码": "discount_code",
    "亚马逊链": "amazon_link",
    "短链": "short_link",
    "回片链接": "content_url",
    "观看量": "views",
    "点赞": "likes",
    "评论": "comments",
    "转发": "shares",
    "互动率": "engagement_rate",
    "产品成本": "product_cost_cents",
    "预算支出$": "budget_spend_cents",
    "预算支出": "budget_spend_cents",
    "直接转化$(独立站)": "direct_conversion_cents",
    "直接转化独立站": "direct_conversion_cents",
    "CPV": "cpv",
    "ROAS": "roas",
    "channel_name": "channel_name",
    "channel_url": "channel_url",
    "platform": "platform",
    "country": "country",
    "niche": "niche",
    "follower_count": "follower_count",
    "avg_views": "avg_views",
    "contact_email": "contact_email",
    "contact_status": "contact_status",
}


def _norm_header(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\ufeff", "")).strip()
    compact = re.sub(r"\s+", "", text)
    return HEADER_ALIASES.get(text) or HEADER_ALIASES.get(compact) or text.lower().replace(" ", "_")


def _parse_count(value) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    multiplier = 1
    if text.endswith(("W", "w", "万")):
        multiplier = 10000
        text = text[:-1]
    elif text.endswith(("K", "k")):
        multiplier = 1000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        digits = re.sub(r"[^0-9.]", "", text)
        return int(float(digits) * multiplier) if digits else 0


def _parse_cents(value) -> int:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return 0
    text = re.sub(r"[$￥¥]", "", text)
    try:
        return int(round(float(text) * 100))
    except ValueError:
        digits = re.sub(r"[^0-9.]", "", text)
        return int(round(float(digits) * 100)) if digits else 0


def _normalize_platform(value: str) -> str:
    text = str(value or "").strip().lower()
    if "youtube" in text or text == "yt":
        return "youtube"
    if "tiktok" in text or "抖音" in text or text == "tk":
        return "tiktok"
    if "instagram" in text or text == "ig":
        return "instagram"
    if "twitter" in text or text == "x":
        return "twitter"
    if "reddit" in text:
        return "reddit"
    return text or "unknown"


def _xlsx_rows(raw: bytes) -> list[dict[str, str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in item.findall(".//a:t", ns)))
        sheet_candidates = sorted(name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        if not sheet_candidates:
            return []
        sheet_name = sheet_candidates[0]
        root = ET.fromstring(zf.read(sheet_name))
        matrix: list[list[str]] = []
        for row in root.findall(".//a:sheetData/a:row", ns):
            values: dict[int, str] = {}
            for cell in row.findall("a:c", ns):
                ref = str(cell.attrib.get("r") or "")
                letters = re.sub(r"[^A-Z]", "", ref.upper())
                col = 0
                for ch in letters:
                    col = col * 26 + (ord(ch) - 64)
                col = max(0, col - 1)
                ctype = cell.attrib.get("t")
                value = ""
                if ctype == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//a:t", ns))
                else:
                    node = cell.find("a:v", ns)
                    raw_value = node.text if node is not None else ""
                    if ctype == "s" and raw_value != "":
                        idx = int(raw_value)
                        value = shared[idx] if 0 <= idx < len(shared) else ""
                    else:
                        value = raw_value or ""
                values[col] = str(value).strip()
            if values:
                width = max(values) + 1
                matrix.append([values.get(i, "") for i in range(width)])
        if not matrix:
            return []
        headers = [_norm_header(cell) for cell in matrix[0]]
        rows: list[dict[str, str]] = []
        for raw_row in matrix[1:]:
            row = {headers[i]: (raw_row[i] if i < len(raw_row) else "") for i in range(len(headers)) if headers[i]}
            if any(str(v).strip() for v in row.values()):
                rows.append(row)
        return rows


def _uploaded_rows(filename: str, raw: bytes) -> list[dict[str, str]]:
    lower = filename.lower()
    if lower.endswith(".xlsx"):
        return _xlsx_rows(raw)
    text = raw.decode("utf-8-sig")
    return [{_norm_header(k): v for k, v in row.items()} for row in csv.DictReader(io.StringIO(text))]


def _staff_id_by_owner_name(conn, owner_name: str) -> int | None:
    owner = str(owner_name or "").strip()
    if not owner:
        return None
    row = conn.execute(
        """
        SELECT s.id
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE lower(COALESCE(u.name, u.email, '')) = lower(?)
           OR lower(COALESCE(u.email, '')) LIKE lower(?)
        ORDER BY s.active DESC, s.id DESC
        LIMIT 1
        """,
        (owner, f"{owner}%"),
    ).fetchone()
    return int(row["id"]) if row else None


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
        where.append("k.country = ?"); params.append(country)
    if platform:
        where.append("k.platform = ?"); params.append(platform)
    if status:
        where.append("k.contact_status = ?"); params.append(status)
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
        item["engagement_rate"] = engagement_rate(item.get("likes", 0), item.get("comments", 0), item.get("shares", 0), item.get("views", 0))
        item["cpv"] = cpv(item.get("cost_cents", 0), item.get("views", 0))
        item["roi"] = roi(item.get("cost_cents", 0), item.get("revenue_cents", 0))
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


def _candidate_payload_from_item(item: dict, body: dict) -> dict:
    return {
        "platform": item.get("platform") or body.get("platform") or "",
        "channel_name": item.get("channel_name") or "Unknown creator",
        "channel_url": item.get("channel_url") or "",
        "handle": item.get("handle") or "",
        "country": body.get("market") or item.get("market") or "",
        "niche": body.get("niche") or "",
        "source_url": item.get("source_url") or "",
        "sample_title": item.get("sample_title") or "",
        "follower_count": _int(item.get("follower_count")),
        "avg_views": _int(item.get("avg_views") or item.get("views")),
        "contact_email": "",
        "status": "new",
        "search_query": body.get("query") or item.get("search_query") or "",
        "market": body.get("market") or item.get("market") or "",
        "notes": "",
    }


def _upsert_candidate(conn, payload: dict) -> int:
    source_url = str(payload.get("source_url") or "").strip()
    if source_url:
        existing = conn.execute("SELECT id FROM kol_candidates WHERE source_url = ?", (source_url,)).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE kol_candidates
                SET avg_views = ?, sample_title = ?, updated_at = ?
                WHERE id = ?
                """,
                (_int(payload.get("avg_views")), payload.get("sample_title", ""), _now(), int(existing["id"])),
            )
            return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO kol_candidates
            (platform, channel_name, channel_url, handle, country, niche, source_url, sample_title,
             follower_count, avg_views, contact_email, status, search_query, market, notes, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("platform", ""),
            payload.get("channel_name", "Unknown creator"),
            payload.get("channel_url", ""),
            payload.get("handle", ""),
            payload.get("country", ""),
            payload.get("niche", ""),
            payload.get("source_url", ""),
            payload.get("sample_title", ""),
            _int(payload.get("follower_count")),
            _int(payload.get("avg_views")),
            payload.get("contact_email", ""),
            payload.get("status", "new"),
            payload.get("search_query", ""),
            payload.get("market", ""),
            payload.get("notes", ""),
            _now(),
            _now(),
        ),
    )
    return int(cur.lastrowid)


def _persist_search_candidates(items: list[dict], body: dict, platform: str, market: str) -> list[int]:
    conn = get_conn()
    candidate_ids = []
    for item in items:
        candidate_ids.append(_upsert_candidate(conn, _candidate_payload_from_item(item, {**body, "platform": platform, "market": market})))
    conn.commit()
    return candidate_ids


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
    conn = get_conn()
    _log_activity(
        conn,
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
    conn.commit()
    return {
        **result,
        "candidate_ids": candidate_ids,
        "saved_candidates": len(candidate_ids),
    }


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
        metadata={"kol_id": int(cur.lastrowid)},
    )
    conn.commit()
    return {"id": cur.lastrowid, "candidate_id": int(candidate_id)}


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
             project_name, owner_name, media_name, duplicate_flag, scale_tier, content_type,
             approval_note, channel_tags, affiliate_id, affiliate_link, discount_code,
             amazon_link, short_link, primary_category, promoted_product,
             created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    _log_activity(conn, staff, "create_kol", target_type="kol", target_id=int(cur.lastrowid), result_count=1)
    conn.commit()
    return {"id": cur.lastrowid}


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
        name = (row.get("channel_name") or row.get("media_name") or row.get("project_name") or "").strip()
        platform = _normalize_platform(row.get("platform") or "")
        if not name or not platform:
            skipped += 1
            continue
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
                    row.get("country", ""),
                    row.get("channel_tags") or row.get("content_type") or row.get("niche", ""),
                    follower_count,
                    follower_count,
                    views,
                    views,
                    row.get("contact_email", ""),
                    row.get("contact_status") or "cold",
                    row.get("project_name", ""),
                    row.get("owner_name", ""),
                    row.get("media_name", name),
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
                    row.get("country", ""),
                    row.get("channel_tags") or row.get("content_type") or row.get("niche", ""),
                    follower_count,
                    views,
                    row.get("contact_email", ""),
                    row.get("contact_status") or "cold",
                    assigned_staff_id,
                    staff.get("id"),
                    row.get("project_name", ""),
                    row.get("owner_name", ""),
                    row.get("media_name", name),
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
            kol_id = int(cur.lastrowid)
            count += 1
        campaign_id = None
        product = str(row.get("promoted_product") or "").strip()
        if product or budget_spend_cents or row.get("contact_status"):
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
                    row.get("contact_status") or "planning",
                    " | ".join(part for part in note_parts if part),
                    _now(),
                ),
            )
            campaign_id = int(cur.lastrowid)
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
            content_count += 1
            if direct_conversion_cents:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO kol_attribution
                        (content_id, shopify_order_id, attributed_revenue_cents, attributed_at)
                    VALUES (?,?,?,?)
                    """,
                    (int(cur.lastrowid), f"import:{file.filename}:{content_count}", direct_conversion_cents, _now()),
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
    _log_activity(conn, staff, "campaign_create", target_type="kol", target_id=int(kol_id), result_count=1, metadata={"campaign_id": int(cur.lastrowid), "product_sku": body.get("product_sku", "")})
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
    _log_activity(conn, staff, "content_create", target_type="content", target_id=int(cur.lastrowid), result_count=1, metadata={"campaign_id": campaign_id, "views": views})
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
        conn = get_conn()
        _log_activity(conn, staff, "ai_score_queue", target_type="content", target_id=int(content_id), api_provider="claude", api_calls=1, result_count=1, metadata={"job_id": task_id})
        conn.commit()
        return {"status": "queued", "job_id": task_id, "content_id": int(content_id)}
    try:
        result = await score_kol_content(content_id)
        conn = get_conn()
        _log_activity(conn, staff, "ai_score", target_type="content", target_id=int(content_id), api_provider="claude", api_calls=1, result_count=1, metadata={"score": result.get("quality_score")})
        conn.commit()
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
    _log_activity(conn, staff, "attribution_add", target_type="content", target_id=int(content_id), result_count=1, metadata={"shopify_order_id": body.get("shopify_order_id", "")})
    conn.commit()
    return {"id": cur.lastrowid}


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
            SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END) AS searches,
            SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END) AS kol_views,
            SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END) AS reviews,
            SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END) AS imports,
            SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END) AS data_updates,
            SUM(CASE WHEN action_type LIKE 'ai_%' THEN 1 ELSE 0 END) AS ai_actions,
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
            SUM(CASE WHEN action_type='platform_search' THEN 1 ELSE 0 END) AS searches,
            SUM(CASE WHEN action_type='view_kol' THEN 1 ELSE 0 END) AS kol_views,
            SUM(CASE WHEN action_type IN ('candidate_review','candidate_promote') THEN 1 ELSE 0 END) AS reviews,
            SUM(CASE WHEN action_type='import_sheet' THEN 1 ELSE 0 END) AS imports,
            SUM(CASE WHEN action_type IN ('data_update','content_create') THEN 1 ELSE 0 END) AS data_updates,
            SUM(CASE WHEN action_type LIKE 'ai_%' THEN 1 ELSE 0 END) AS ai_actions,
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
