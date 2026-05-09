"""Shared helper functions for KOL Operations routers."""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from fastapi import HTTPException

from app.core.security import verify_password
from app.db.connection import get_conn, is_postgres_runtime
from app.services.kol.metrics import cpv, engagement_rate, roi


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _dict(row) -> dict:
    return dict(row) if row else {}


def _items(rows) -> list[dict]:
    return [dict(row) for row in rows]


def _insert_id(conn, cur, table: str) -> int:
    lastrowid = getattr(cur, "lastrowid", None)
    if lastrowid not in (None, 0):
        return int(lastrowid)
    if is_postgres_runtime():
        row = conn.execute(
            "SELECT currval(pg_get_serial_sequence(?, 'id')) AS id",
            (table,),
        ).fetchone()
        if row:
            return int(row["id"])
    raise RuntimeError(f"Could not resolve inserted id for {table}")


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
    if is_postgres_runtime():
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        ).fetchall()
        existing = {str(row["column_name"]) for row in rows}
    else:
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


def _log_activity_commit(
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
    conn = get_conn()
    _log_activity(
        conn,
        staff,
        action_type,
        target_type=target_type,
        target_id=target_id,
        query=query,
        platform=platform,
        market=market,
        api_provider=api_provider,
        api_calls=api_calls,
        result_count=result_count,
        metadata=metadata,
    )
    conn.commit()


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
    if "douyin" in text or "抖音" in text:
        return "douyin"
    if "tiktok" in text or text == "tk":
        return "tiktok"
    if "instagram" in text or text == "ig":
        return "instagram"
    if "twitter" in text or text == "x":
        return "twitter"
    if "reddit" in text:
        return "reddit"
    return text or "unknown"


COUNTRY_CODE_ALIASES = {
    "美国": "US",
    "usa": "US",
    "united states": "US",
    "us": "US",
    "加拿大": "CA",
    "canada": "CA",
    "ca": "CA",
    "澳大利亚": "AU",
    "australia": "AU",
    "au": "AU",
    "英国": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "gb": "GB",
    "德国": "DE",
    "germany": "DE",
    "de": "DE",
    "法国": "FR",
    "france": "FR",
    "fr": "FR",
    "日本": "JP",
    "japan": "JP",
    "jp": "JP",
    "韩国": "KR",
    "south korea": "KR",
    "kr": "KR",
    "俄罗斯": "RU",
    "russia": "RU",
    "ru": "RU",
    "西班牙": "ES",
    "spain": "ES",
    "es": "ES",
    "意大利": "IT",
    "italy": "IT",
    "it": "IT",
    "荷兰": "NL",
    "netherlands": "NL",
    "nl": "NL",
    "巴西": "BR",
    "brazil": "BR",
    "br": "BR",
    "墨西哥": "MX",
    "mexico": "MX",
    "mx": "MX",
    "印度": "IN",
    "india": "IN",
    "in": "IN",
    "新加坡": "SG",
    "singapore": "SG",
    "sg": "SG",
    "马来西亚": "MY",
    "malaysia": "MY",
    "my": "MY",
    "泰国": "TH",
    "thailand": "TH",
    "th": "TH",
    "越南": "VN",
    "vietnam": "VN",
    "vn": "VN",
    "菲律宾": "PH",
    "philippines": "PH",
    "ph": "PH",
    "印尼": "ID",
    "印度尼西亚": "ID",
    "indonesia": "ID",
    "id": "ID",
}


def _normalize_country_code(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return COUNTRY_CODE_ALIASES.get(text.lower()) or COUNTRY_CODE_ALIASES.get(text) or text.upper()


def _country_filter_variants(value: str) -> list[str]:
    code = _normalize_country_code(value)
    variants = {str(value or "").strip(), code}
    for alias, alias_code in COUNTRY_CODE_ALIASES.items():
        if alias_code == code:
            variants.add(alias)
            variants.add(alias.upper())
    return [item for item in variants if item]


def _clean_creator_name(value: str, owner_name: str = "") -> str:
    text = str(value or "").strip()
    owner = str(owner_name or "").strip()
    if owner and text.lower().startswith(owner.lower()):
        text = re.sub(rf"^{re.escape(owner)}\\s*[-_–—]\\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^[\u4e00-\u9fff]{2,8}\s*[-_–—]\s*", "", text).strip()
    text = re.sub(r"\s*[-_–—]?\s*【[^】]*(youtube|tiktok|douyin|抖音|instagram|reddit|twitter|x|yt|tk|ig)[^】]*】\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*[-_–—]?\s*\[[^\]]*(youtube|tiktok|douyin|抖音|instagram|reddit|twitter|x|yt|tk|ig)[^\]]*\]\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" -_–—") or str(value or "").strip()


def _row_contact_status(row: dict[str, str]) -> str:
    status = str(row.get("contact_status") or "").strip()
    if status:
        return status
    if str(row.get("content_url") or "").strip():
        return "已回片"
    return "cold"


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


def _dossier_rollup(conn, kol_id: int) -> dict:
    snapshot = conn.execute(
        """
        SELECT *
        FROM kol_account_snapshots
        WHERE kol_id = ?
        ORDER BY scanned_at DESC
        LIMIT 1
        """,
        (int(kol_id),),
    ).fetchone()
    report = conn.execute(
        """
        SELECT *
        FROM kol_analysis_reports
        WHERE kol_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (int(kol_id),),
    ).fetchone()
    if not snapshot and not report:
        return {
            "latest_scan_status": "not_scanned",
            "account_content_count": 0,
            "account_comment_count": 0,
            "account_total_views": 0,
            "account_avg_views": 0,
            "account_engagement_rate": 0,
            "brand_mentions_count": 0,
            "competitor_mentions_count": 0,
            "latest_account_score": None,
            "latest_analyzed_at": None,
        }
    snap = dict(snapshot) if snapshot else {}
    rep = dict(report) if report else {}
    try:
        brand_mentions = json.loads(snap.get("brand_mentions_json") or "[]")
    except Exception:
        brand_mentions = []
    try:
        competitor_mentions = json.loads(snap.get("competitor_mentions_json") or "[]")
    except Exception:
        competitor_mentions = []
    return {
        "latest_scan_status": snap.get("scan_status") or "not_scanned",
        "latest_scanned_at": snap.get("scanned_at"),
        "account_handle": snap.get("handle") or "",
        "account_url": snap.get("account_url") or "",
        "account_content_count": _int(snap.get("content_count")),
        "account_comment_count": _int(snap.get("comment_count")),
        "account_total_views": _int(snap.get("total_views")),
        "account_avg_views": _int(snap.get("avg_views")),
        "account_engagement_rate": float(snap.get("engagement_rate") or 0),
        "brand_mentions_count": len(brand_mentions) if isinstance(brand_mentions, list) else 0,
        "competitor_mentions_count": len(competitor_mentions) if isinstance(competitor_mentions, list) else 0,
        "latest_account_score": rep.get("account_score") if rep else None,
        "latest_risk_level": rep.get("risk_level") if rep else None,
        "latest_analyzed_at": rep.get("created_at") if rep else None,
    }


