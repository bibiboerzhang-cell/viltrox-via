"""Channel binding, access, and team overview service functions."""
from __future__ import annotations

from app.services.vkpi.channels_common import *

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
    _clear_channel_read_cache()
    return {"channel": get_channel(channel_id, staff=staff).get("channel", {})}


def _visible_staff_id(staff: dict[str, Any] | None, view_as_staff_id: int | None = None) -> int | None:
    actor = _actor(staff)
    return scope.effective_staff_id(staff, view_as_staff_id) or actor or None


def _assert_channel_access(channel_id: int, staff: dict[str, Any] | None, *, write: bool = False) -> None:
    if scope.can_view_all(staff):
        return
    actor = _actor(staff)
    if not actor:
        raise scope.ScopeDenied("channel scope denied")
    row = get_conn().execute(
        "SELECT staff_id, metadata_json FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL",
        (int(channel_id),),
    ).fetchone()
    if not row:
        return
    item = dict(row)
    if int(item.get("staff_id") or 0) == actor:
        return
    if not write and _is_official_channel_row(item):
        return
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


def get_channel(channel_id: int, *, staff: dict[str, Any] | None = None, write: bool = False) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff, write=write)
    row = get_conn().execute("SELECT * FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL", (int(channel_id),)).fetchone()
    if not row:
        raise LookupError("channel not found")
    return {"channel": _channel_row_to_dict(row)}


def list_channels(*, staff: dict[str, Any] | None = None, view_as_staff_id: int | None = None, limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    safe_limit = max(1, min(300, int(limit or 100)))
    cache_key = _channel_cache_key("list", staff=staff, view_as_staff_id=view_as_staff_id, limit=safe_limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
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
        (*params, safe_limit),
    ).fetchall()
    return _channel_cache_store(cache_key, {"channels": [_channel_row_to_dict(row) for row in rows]})


def unbind_channel(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff, write=True)
    actor = _actor(staff)
    now = _utcnow()
    get_conn().execute("UPDATE vkpi_employee_channels SET status='revoked', deleted_at=?, updated_at=? WHERE id=?", (now, now, int(channel_id)))
    get_conn().execute("INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)", (int(channel_id), actor or None, "unbind", "manual unbind", now))
    get_conn().commit()
    _clear_channel_read_cache()
    return {"status": "revoked", "channel_id": int(channel_id)}


def sync_now(channel_id: int, *, staff: dict[str, Any] | None = None, max_posts: int = 12) -> dict[str, Any]:
    """Sync a bound official channel through configured providers."""
    ensure_vkpi_channels_schema()
    channel = get_channel(channel_id, staff=staff, write=True)["channel"]
    from app.services.vkpi import channel_refill

    result = channel_refill.sync_channel_snapshot(channel, staff=staff, max_posts=max(1, min(1000, int(max_posts or 12))))
    _clear_channel_read_cache()
    return result


def metrics(channel_id: int, limit: int = 30, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    _assert_channel_access(channel_id, staff)
    rows = get_conn().execute("SELECT * FROM vkpi_channel_metrics WHERE channel_id=? ORDER BY snapshot_date DESC LIMIT ?", (int(channel_id), max(1, min(365, int(limit or 30))))).fetchall()
    return {"metrics": [dict(row) for row in rows], "sync_status": "待同步" if not rows else "synced"}


def team_overview() -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    cache_key = _channel_cache_key("team_overview", limit=200)
    cached = cache_get(cache_key)
    if cached is not None:
        return _channel_cache_hit(cached)
    try:
        rows = get_conn().execute("SELECT * FROM vkpi_team_channels_overview LIMIT 200").fetchall()
        return _channel_cache_store(cache_key, {"rows": [dict(row) for row in rows]})
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
        return _channel_cache_store(cache_key, {"rows": [dict(row) for row in rows]})


def team_detail(staff_id: int) -> dict[str, Any]:
    return list_channels(staff={"id": int(staff_id)}, limit=200)


__all__ = [name for name in globals() if not name.startswith("__")]
