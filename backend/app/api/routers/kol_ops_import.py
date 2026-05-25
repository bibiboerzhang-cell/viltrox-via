"""CSV import helpers for KOL Operations routes."""
from __future__ import annotations

from typing import Any

from app.api.routers.kol_ops_helpers import (
    _clean_creator_name,
    _insert_id,
    _log_activity,
    _normalize_country_code,
    _normalize_platform,
    _now,
    _parse_cents,
    _parse_count,
    _row_contact_status,
    _staff_id_by_owner_name,
)
from app.db.connection import get_conn
from app.services.kol.metrics import engagement_rate


def import_kols_csv_rows(filename: str, rows: list[dict[str, Any]], staff: dict[str, Any]) -> dict[str, int]:
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
        campaign_id = _import_campaign(conn, row, kol_id, assigned_staff_id, staff, budget_spend_cents, contact_status)
        if campaign_id:
            campaigns += 1
        content_count += _import_content(conn, row, filename, content_count, campaign_id, platform, views, likes, comments, shares, direct_conversion_cents)

    _log_activity(
        conn,
        staff,
        "import_sheet",
        target_type="file",
        query=filename,
        result_count=count,
        metadata={"rows": len(rows), "campaigns": campaigns, "content": content_count, "skipped": skipped},
    )
    conn.commit()
    return {"imported": count, "rows": len(rows), "campaigns": campaigns, "content": content_count, "skipped": skipped}


def _import_campaign(conn, row: dict[str, Any], kol_id: int, assigned_staff_id: int | None, staff: dict[str, Any], budget_spend_cents: int, contact_status: str) -> int | None:
    product = str(row.get("promoted_product") or "").strip()
    if not (product or budget_spend_cents or contact_status):
        return None
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
    return _insert_id(conn, cur, "kol_campaigns")


def _import_content(
    conn,
    row: dict[str, Any],
    filename: str,
    existing_content_count: int,
    campaign_id: int | None,
    platform: str,
    views: int,
    likes: int,
    comments: int,
    shares: int,
    direct_conversion_cents: int,
) -> int:
    content_url = str(row.get("content_url") or "").strip()
    if not content_url or not campaign_id:
        return 0
    cur = conn.execute(
        """
        INSERT INTO kol_content
            (campaign_id, content_url, platform, posted_at, views, likes, comments, shares,
             engagement_rate, last_metric_refresh, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (campaign_id, content_url, platform, None, views, likes, comments, shares, engagement_rate(likes, comments, shares, views), _now(), _now()),
    )
    content_id = _insert_id(conn, cur, "kol_content")
    if direct_conversion_cents:
        conn.execute(
            """
            INSERT OR IGNORE INTO kol_attribution
                (content_id, shopify_order_id, attributed_revenue_cents, attributed_at)
            VALUES (?,?,?,?)
            """,
            (content_id, f"import:{filename}:{existing_content_count + 1}", direct_conversion_cents, _now()),
        )
    return 1
