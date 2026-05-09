"""Employee platform bindings and sync state for Viltrox Marketing."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import date, datetime
from typing import Any

from cryptography.fernet import Fernet

from app.db.connection import get_conn
from app.services.vkpi import scope
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id

SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok", "xhs", "xiaohongshu", "bilibili", "facebook", "reddit", "x", "threads", "twitch", "pinterest", "vimeo", "website", "other"}


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _actor(staff: dict[str, Any] | None) -> int:
    return resolve_staff_id(staff) or 0


def _fernet() -> Fernet:
    key = os.environ.get("VKPI_CHANNELS_ENCRYPTION_KEY") or os.environ.get("JWT_SECRET") or os.environ.get("APP_SECRET") or "vkpi-local-dev-key"
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return _fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"{text[:4]}...{text[-4:]}" if len(text) > 8 else "****"


def _platform(value: Any) -> str:
    raw = str(value or "other").strip().lower()
    if raw == "twitter":
        raw = "x"
    if raw == "red":
        raw = "reddit"
    if raw in {"xiaohongshu", "小红书"}:
        raw = "xhs"
    return raw if raw in SUPPORTED_PLATFORMS else "other"


def bind_channel(body: dict[str, Any], *, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    actor = _actor(staff)
    requested_staff_id = view_as_staff_id or body.get("staff_id")
    target_staff_id = int(scope.effective_staff_id(staff, requested_staff_id) or actor or 0)
    if not target_staff_id:
        raise ValueError("staff_id required")
    platform = _platform(body.get("platform"))
    handle = str(body.get("account_handle") or body.get("handle") or "").strip().lstrip("@")
    if not handle:
        raise ValueError("account_handle required")
    api_key = str(body.get("api_key") or "")
    api_secret = str(body.get("api_secret") or "")
    uid = f"chan-{secrets.token_hex(8)}"
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_employee_channels
            (channel_uid, staff_id, platform, account_handle, account_display_name, account_url, avatar_url,
             api_key_encrypted, api_secret_encrypted, refresh_token_encrypted, access_token_encrypted,
             auth_method, self_reported_followers, self_reported_posts, status, last_sync_status,
             metadata_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(staff_id, platform, account_handle) DO UPDATE SET
            account_display_name=excluded.account_display_name,
            account_url=excluded.account_url,
            avatar_url=excluded.avatar_url,
            api_key_encrypted=CASE WHEN excluded.api_key_encrypted!='' THEN excluded.api_key_encrypted ELSE vkpi_employee_channels.api_key_encrypted END,
            api_secret_encrypted=CASE WHEN excluded.api_secret_encrypted!='' THEN excluded.api_secret_encrypted ELSE vkpi_employee_channels.api_secret_encrypted END,
            self_reported_followers=excluded.self_reported_followers,
            self_reported_posts=excluded.self_reported_posts,
            status='active',
            updated_at=excluded.updated_at
        """,
        (
            uid,
            target_staff_id,
            platform,
            handle,
            str(body.get("account_display_name") or body.get("display_name") or handle),
            str(body.get("account_url") or body.get("url") or ""),
            str(body.get("avatar_url") or ""),
            _encrypt(api_key),
            _encrypt(api_secret),
            _encrypt(str(body.get("refresh_token") or "")),
            _encrypt(str(body.get("access_token") or "")),
            str(body.get("auth_method") or "manual_api_key"),
            _int(body.get("self_reported_followers")),
            _int(body.get("self_reported_posts")),
            "active",
            "not_configured" if not api_key else "configured_pending_provider",
            _json({"api_key_mask": _mask(api_key), "api_secret_mask": _mask(api_secret)}),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM vkpi_employee_channels WHERE staff_id=? AND platform=? AND account_handle=?", (target_staff_id, platform, handle)).fetchone()
    channel_id = int(row["id"]) if row else 0
    conn.execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (channel_id or None, actor or target_staff_id, "bind", f"{platform}:{handle}", now))
    conn.commit()
    return {"channel": get_channel(channel_id, staff=staff).get("channel", {})}


def _visible_staff_id(staff: dict[str, Any] | None, view_as_staff_id: int | None = None) -> int | None:
    actor = _actor(staff)
    return scope.effective_staff_id(staff, view_as_staff_id) or actor or None


def _assert_channel_access(channel_id: int, staff: dict[str, Any] | None) -> None:
    if scope.can_view_all(staff):
        return
    actor = _actor(staff)
    if not actor:
        raise scope.ScopeDenied("channel scope denied")
    row = get_conn().execute(
        "SELECT staff_id FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL",
        (int(channel_id),),
    ).fetchone()
    if not row:
        return
    if int(dict(row).get("staff_id") or 0) != actor:
        raise scope.ScopeDenied("channel scope denied")


def _channel_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key in ["api_key_encrypted", "api_secret_encrypted", "refresh_token_encrypted", "access_token_encrypted"]:
        data.pop(key, None)
    meta = {}
    try:
        meta = json.loads(data.get("metadata_json") or "{}")
    except Exception:
        meta = {}
    data["api_key_mask"] = meta.get("api_key_mask", "")
    data["sync_status"] = data.get("last_sync_status") or "待同步"
    return data


def get_channel(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    row = get_conn().execute("SELECT * FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL", (int(channel_id),)).fetchone()
    if not row:
        raise LookupError("channel not found")
    return {"channel": _channel_row_to_dict(row)}


def list_channels(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    target = _visible_staff_id(staff, view_as_staff_id)
    where = "WHERE deleted_at IS NULL"
    params: list[Any] = []
    if target:
        where += " AND staff_id=?"
        params.append(target)
    rows = get_conn().execute(
        f"""
        SELECT c.*,
               (SELECT followers FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_followers,
               (SELECT posts_count FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_posts,
               (SELECT total_views FROM vkpi_channel_metrics m WHERE m.channel_id=c.id ORDER BY snapshot_date DESC LIMIT 1) AS latest_views
        FROM vkpi_employee_channels c
        {where}
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        """,
        (*params, max(1, min(300, int(limit or 100)))),
    ).fetchall()
    return {"channels": [_channel_row_to_dict(row) for row in rows]}


def unbind_channel(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    actor = _actor(staff)
    now = _utcnow()
    get_conn().execute("UPDATE vkpi_employee_channels SET status='revoked', deleted_at=?, updated_at=? WHERE id=?", (now, now, int(channel_id)))
    get_conn().execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (int(channel_id), actor or None, "unbind", "manual unbind", now))
    get_conn().commit()
    return {"status": "revoked", "channel_id": int(channel_id)}


def sync_now(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Do not return zero metrics when official API adapters are not configured."""
    ensure_vkpi_channels_schema()
    channel = get_channel(channel_id, staff=staff)["channel"]
    status = "not_configured"
    message = "平台官方 API / OAuth 尚未配置，未同步真实粉丝或播放数据。"
    now = _utcnow()
    get_conn().execute(
        "UPDATE vkpi_employee_channels SET last_sync_at=?, last_sync_status=?, last_sync_error=?, sync_failure_count=sync_failure_count+1, updated_at=? WHERE id=?",
        (now, status, message, now, int(channel_id)),
    )
    get_conn().execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (int(channel_id), _actor(staff) or channel.get("staff_id"), "sync_skipped", message, now))
    get_conn().commit()
    return {"channel_id": int(channel_id), "sync_status": status, "message": message, "metrics": None}


def metrics(channel_id: int, limit: int = 30, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    rows = get_conn().execute("SELECT * FROM vkpi_channel_metrics WHERE channel_id=? ORDER BY snapshot_date DESC LIMIT ?", (int(channel_id), max(1, min(365, int(limit or 30))))).fetchall()
    return {"metrics": [dict(row) for row in rows], "sync_status": "待同步" if not rows else "synced"}


def team_overview() -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    try:
        rows = get_conn().execute("SELECT * FROM vkpi_team_channels_overview LIMIT 200").fetchall()
        return {"rows": [dict(row) for row in rows]}
    except Exception:
        rows = get_conn().execute(
            """
            SELECT staff_id, COUNT(*) AS active_channels, 0 AS total_followers, 0 AS total_views, MAX(last_sync_at) AS most_recent_sync_at
            FROM vkpi_employee_channels
            WHERE deleted_at IS NULL AND status='active'
            GROUP BY staff_id
            ORDER BY active_channels DESC
            """
        ).fetchall()
        return {"rows": [dict(row) for row in rows]}


def team_detail(staff_id: int) -> dict[str, Any]:
    return list_channels(staff={"id": int(staff_id)}, limit=200)
