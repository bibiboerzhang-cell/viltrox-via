"""Compatibility helpers for P1 package adoption.

The P1 packages were authored against a slightly different router and schema
shape. This module centralizes the compatibility contract so each P1 round does
not rediscover the same prefix, permission, and field-name issues.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn


ADMIN_VKPI_PREFIX = "/api/admin/vkpi"


def admin_router_prefix(feature: str) -> str:
    """Return the canonical admin V-KPI API prefix for a P1 feature."""
    key = str(feature or "").strip().strip("/")
    return f"{ADMIN_VKPI_PREFIX}/{key}" if key else ADMIN_VKPI_PREFIX


def first_present(row: dict[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    """Return the first non-empty value from a dict-like row."""
    for key in keys:
        try:
            value = row.get(key) if hasattr(row, "get") else row[key]
        except Exception:
            value = None
        if value is not None and value != "":
            return value
    return default


def json_loads(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return {} if default is None else default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return {} if default is None else default


def normalize_industry_post(row: dict[str, Any] | Any) -> dict[str, Any]:
    """Normalize current ``vkpi_industry_posts`` fields for P1 comments code."""
    return {
        "id": first_present(row, "id"),
        "account_id": first_present(row, "account_id"),
        "platform": first_present(row, "platform", default=""),
        "external_post_id": first_present(row, "external_post_id", "platform_post_id", default=""),
        "raw_data_json": first_present(row, "raw_data_json", "raw_platform_data", default="{}"),
        "raw_data": json_loads(first_present(row, "raw_data_json", "raw_platform_data", default="{}"), {}),
    }


def industry_post_projection_sql() -> str:
    """Projection that aliases current fields to the names expected by P1.3."""
    return """
        SELECT
            id,
            account_id,
            platform,
            platform_post_id AS external_post_id,
            raw_platform_data AS raw_data_json
        FROM vkpi_industry_posts
        WHERE id = ?
    """


def resolve_post_for_comments(post_id: int, post_table: str = "industry_posts") -> dict[str, Any] | None:
    """Resolve a post row into the normalized P1 comments shape.

    Only ``industry_posts`` is stable in the current repo. ``kol_posts`` is kept
    as a guarded future hook and returns ``None`` when the table is absent.
    """
    conn = get_conn()
    table_key = str(post_table or "industry_posts").strip().lower()
    if table_key in {"industry_posts", "vkpi_industry_posts"}:
        row = conn.execute(industry_post_projection_sql(), (int(post_id),)).fetchone()
        return normalize_industry_post(dict(row)) if row else None

    if table_key in {"vkpi_kol_video_evidence", "kol_video_evidence"}:
        # KOL Pool evidence 钩(2026-06-12 裁令"评论的展示也要有"):
        # external_post_id = YT 取视频 id(Data API 要求),IG/TT 取帖子 URL(Apify actor 吃 URL)。
        row = conn.execute(
            """
            SELECT id, NULL AS account_id, LOWER(COALESCE(platform,'')) AS platform,
                   COALESCE(content_url,'') AS content_url
            FROM vkpi_kol_video_evidence
            WHERE id = ?
            """,
            (int(post_id),),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        external_post_id = str(data.get("content_url") or "")
        if data.get("platform") == "youtube" and external_post_id:
            from app.domains.kol.pool import _youtube_video_id

            external_post_id = _youtube_video_id(external_post_id) or external_post_id
        return {
            "id": data.get("id"),
            "account_id": None,
            "platform": data.get("platform") or "",
            "external_post_id": external_post_id,
            "raw_data_json": "{}",
            "raw_data": {},
        }

    if table_key in {"kol_posts", "vkpi_kol_posts"}:
        try:
            row = conn.execute(
                """
                SELECT
                    id,
                    kol_id AS account_id,
                    platform,
                    COALESCE(external_post_id, platform_post_id, '') AS external_post_id,
                    COALESCE(raw_data_json, raw_platform_data, '{}') AS raw_data_json
                FROM vkpi_kol_posts
                WHERE id = ?
                """,
                (int(post_id),),
            ).fetchone()
        except Exception:
            return None
        return normalize_industry_post(dict(row)) if row else None

    return None
