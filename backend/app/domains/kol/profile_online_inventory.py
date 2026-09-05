"""Inventory identity and safe materialization for online KOLs."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.domains.kol import profile_online_identity, profile_recall_qualification
from app.domains.kol.search_sessions_serde import (
    project_public_asset_url,
    project_public_profile_text,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _persisted_native_ids(raw_data: dict[str, Any]) -> dict[str, Any]:
    online = raw_data.get("online_identity_v1")
    online = online if isinstance(online, dict) else {}
    return {
        field: online.get(field) or raw_data.get(field)
        for field in ("channel_id", "account_id", "platform_user_id", "native_id")
        if online.get(field) or raw_data.get(field)
    }


def _row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "kol_pool_id": row.get("id") or row.get("kol_pool_id"),
        "platform": row.get("platform"),
        "handle": row.get("handle"),
        "profile_url": row.get("profile_url"),
        "raw_platform_data": row.get("raw_platform_data"),
        **_persisted_native_ids(_json_object(row.get("raw_platform_data"))),
    }


def local_identity_snapshot_for_session(session_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    if conn is None:
        from app.db.connection import get_conn
        conn = get_conn()
    session = conn.execute(
        "SELECT id, created_by FROM vkpi_kol_search_sessions WHERE id=?",
        (int(session_id),),
    ).fetchone()
    if not session:
        raise LookupError(f"search session not found: {session_id}")
    rows = conn.execute(
        """
        SELECT i.kol_pool_id, i.payload_json,
               p.platform, p.handle, p.profile_url, p.raw_platform_data
        FROM vkpi_kol_search_session_items i
        LEFT JOIN vkpi_kol_pool p ON p.id=i.kol_pool_id
        WHERE i.session_id=? AND i.item_type='recall_candidate'
        """,
        (int(session_id),),
    ).fetchall()
    aliases: set[str] = set()
    primary: set[str] = set()
    for raw_row in rows:
        row = dict(raw_row)
        payload = _json_object(row.get("payload_json"))
        candidate = {
            **_row_identity(row),
            "channel_id": payload.get("channel_id") or _row_identity(row).get("channel_id"),
            "account_id": payload.get("account_id") or _row_identity(row).get("account_id"),
            "platform_user_id": payload.get("platform_user_id") or _row_identity(row).get("platform_user_id"),
            "native_id": payload.get("native_id") or _row_identity(row).get("native_id"),
        }
        aliases.update(profile_recall_qualification.canonical_creator_aliases(candidate))
        key = profile_recall_qualification.canonical_creator_key(candidate)
        if key and key != "pool:0":
            primary.add(key)
    return {"aliases": {alias for alias in aliases if alias != "pool:0"}, "unique_count": len(primary), "db_reads": 2}


def local_canonical_keys_for_session(session_id: int, *, conn: Any | None = None) -> set[str]:
    return set(local_identity_snapshot_for_session(session_id, conn=conn)["aliases"])


def inventory_alias_snapshot(*, conn: Any | None = None) -> dict[str, Any]:
    """Build one pool+alias snapshot; no per-candidate inventory reads here."""
    if conn is None:
        from app.db.connection import get_conn
        conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, platform, handle, profile_url, raw_platform_data
        FROM vkpi_kol_pool
        WHERE duplicate_of_id IS NULL
        """
    ).fetchall()
    aliases: set[str] = set()
    for raw_row in rows:
        aliases.update(profile_recall_qualification.canonical_creator_aliases(_row_identity(dict(raw_row))))
    alias_rows: list[Any] = []
    try:
        alias_rows = conn.execute(
            "SELECT kol_pool_id, platform, handle, profile_url, metadata_json FROM vkpi_kol_pool_aliases"
        ).fetchall()
    except Exception:
        alias_rows = []
    for raw_row in alias_rows:
        row = dict(raw_row)
        aliases.update(profile_recall_qualification.canonical_creator_aliases({
            "kol_pool_id": row.get("kol_pool_id"),
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "profile_url": row.get("profile_url"),
            **_persisted_native_ids(_json_object(row.get("metadata_json"))),
        }))
    return {
        "aliases": aliases,
        "row_count": len(rows) + len(alias_rows),
        "db_reads": 2,
    }


def _matching_pool_ids(
    conn: Any,
    probe: dict[str, Any],
    *,
    fail_closed: bool = False,
) -> set[int]:
    platform = unicodedata.normalize("NFKC", _text(probe.get("platform"))).casefold()
    raw_handles = (probe.get("handle"), probe.get("_provider_handle"))
    handles: set[str] = set()
    for value in raw_handles:
        raw_handle = _text(value).casefold().lstrip("@")
        if raw_handle:
            handles.update({raw_handle, unicodedata.normalize("NFKC", raw_handle)})
    handles = sorted(handles)
    postgres_runtime = conn.__class__.__name__ == "PostgresCompatConnection"
    handle_sql = "replace(lower(COALESCE(handle, '')), '@', '')"
    if postgres_runtime:
        # PostgreSQL's normalize closes the compatibility-character gap while
        # the advisory lock serializes every canonical alias in this txn.
        handle_sql = "lower(normalize(replace(COALESCE(handle, ''), '@', ''), NFKC))"
        handles = sorted({unicodedata.normalize("NFKC", value) for value in handles})
    profile_url = _text(probe.get("profile_url"))
    native_ids = profile_online_identity.safe_native_identity(probe, platform=platform)
    if platform == "youtube":
        uc_handle = next(
            (
                value
                for value in handles
                if re.fullmatch(r"UC[0-9A-Za-z_-]{10,}", value, re.IGNORECASE)
            ),
            "",
        )
        url_match = re.search(r"/channel/(UC[0-9A-Za-z_-]{10,})", profile_url, re.IGNORECASE)
        channel_id = uc_handle or (url_match.group(1) if url_match else "")
        if channel_id and not native_ids.get("channel_id"):
            native_ids["channel_id"] = channel_id
    conditions: list[str] = []
    params: list[Any] = [platform]
    if handles:
        placeholders = ",".join("?" for _ in handles)
        conditions.append(f"{handle_sql} IN ({placeholders})")
        params.extend(handles)
    if profile_url:
        conditions.append("lower(COALESCE(profile_url, ''))=lower(?)")
        params.append(profile_url)
    for value in native_ids.values():
        conditions.append("lower(COALESCE(raw_platform_data, '')) LIKE ?")
        params.append(f"%{str(value).casefold()}%")
    if not conditions:
        return set()
    if postgres_runtime:
        pool_sql = f"""
            SELECT id, platform, handle, profile_url, raw_platform_data
            FROM vkpi_kol_pool
            WHERE duplicate_of_id IS NULL AND lower(platform)=?
              AND ({' OR '.join(conditions)})
        """
        pool_params = tuple(params)
    else:
        # SQLite lower() is ASCII-only and cannot express NFKC. Local/dev must
        # read the platform slice and perform the same canonical alias
        # intersection in Python, otherwise two compatibility forms can race
        # into distinct pool rows. Production PostgreSQL stays index-bounded.
        pool_sql = """
            SELECT id, platform, handle, profile_url, raw_platform_data
            FROM vkpi_kol_pool
            WHERE duplicate_of_id IS NULL AND lower(platform)=?
        """
        pool_params = (platform,)
    pool_rows = conn.execute(pool_sql, pool_params).fetchall()
    alias_conditions: list[str] = []
    alias_params: list[Any] = [platform]
    if handles:
        placeholders = ",".join("?" for _ in handles)
        alias_conditions.append(f"{handle_sql} IN ({placeholders})")
        alias_params.extend(handles)
    if profile_url:
        alias_conditions.append("lower(COALESCE(profile_url, ''))=lower(?)")
        alias_params.append(profile_url)
    for value in native_ids.values():
        alias_conditions.append("lower(COALESCE(metadata_json, '')) LIKE ?")
        alias_params.append(f"%{str(value).casefold()}%")
    try:
        if postgres_runtime:
            alias_sql = f"""
                SELECT kol_pool_id, platform, handle, profile_url, metadata_json
                FROM vkpi_kol_pool_aliases
                WHERE lower(platform)=? AND ({' OR '.join(alias_conditions)})
            """
            bounded_alias_params = tuple(alias_params)
        else:
            alias_sql = """
                SELECT kol_pool_id, platform, handle, profile_url, metadata_json
                FROM vkpi_kol_pool_aliases
                WHERE lower(platform)=?
            """
            bounded_alias_params = (platform,)
        alias_rows = conn.execute(alias_sql, bounded_alias_params).fetchall()
    except Exception:
        if fail_closed:
            raise
        alias_rows = []
    target_aliases = profile_recall_qualification.canonical_creator_aliases(probe)
    matched = {
        int(dict(row)["id"])
        for row in pool_rows
        if target_aliases.intersection(
            profile_recall_qualification.canonical_creator_aliases(_row_identity(dict(row)))
        )
    }
    for raw_row in alias_rows:
        row = dict(raw_row)
        alias_identity = {
            "kol_pool_id": row.get("kol_pool_id"),
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "profile_url": row.get("profile_url"),
            **_persisted_native_ids(_json_object(row.get("metadata_json"))),
        }
        if target_aliases.intersection(profile_recall_qualification.canonical_creator_aliases(alias_identity)):
            matched.add(int(row["kol_pool_id"]))
    return matched


def materialize_online_candidate(raw: dict[str, Any], *, conn: Any | None = None) -> dict[str, Any]:
    """Atomically reject inventory races and persist only bounded public identity."""
    historical = raw.get("historical_match") if isinstance(raw.get("historical_match"), dict) else {}
    existing_id = _positive_int(raw.get("history_kol_pool_id") or raw.get("kol_pool_id") or historical.get("kol_pool_id"))
    if existing_id:
        return {"duplicate_local_inventory": True, "kol_pool_id": existing_id, "operation": "existing", "db_reads": 0}

    identity = profile_online_identity.stable_creator_identity(raw)
    if identity.get("passed") is not True:
        return {"rejected": True, "reason": "unsafe_public_identity", "db_reads": 0}
    native_ids = profile_online_identity.safe_native_identity(raw, platform=identity.get("platform"))
    probe = {**identity, **native_ids, "_provider_handle": raw.get("handle")}
    aliases = profile_recall_qualification.canonical_creator_aliases(probe)
    fingerprint = profile_online_identity.canonical_fingerprint(probe)
    if not aliases or not fingerprint:
        return {"rejected": True, "reason": "unsafe_public_identity", "db_reads": 0}

    if conn is None:
        from app.db.connection import get_conn
        conn = get_conn()
    from app.domains.kol.profile_basics import _lock_creator_aliases

    _lock_creator_aliases(conn, aliases)
    preexisting = _matching_pool_ids(conn, probe)
    if preexisting:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        return {
            "duplicate_local_inventory": True,
            "kol_pool_id": min(preexisting),
            "operation": "existing",
            "db_reads": 2,
        }

    from app.domains.kol.profile_basics import write_kol_profile_basics

    latest = profile_online_identity.latest_video_evidence(raw)
    profile_data = {
        "platform": identity.get("platform"),
        "handle": identity.get("handle"),
        "display_name": project_public_profile_text(
            raw.get("display_name") or raw.get("channel_name") or raw.get("name"), limit=240
        ),
        "profile_url": identity.get("profile_url"),
        "avatar_url": project_public_asset_url(raw.get("avatar_url") or raw.get("avatar")),
        "followers": raw.get("followers") or raw.get("subscriber_count") or raw.get("follower_count"),
        "last_video_at": latest.get("posted_at"),
        "raw_platform_data": json.dumps({
            "online_identity_v1": {
                "version": 1,
                "canonical_fingerprint": fingerprint,
                **native_ids,
            }
        }, ensure_ascii=True, separators=(",", ":")),
    }
    if identity.get("platform") in {"x", "reddit"}:
        from app.domains.kol.profile_online_post_evidence import SCHEMA_KEY, build_post_evidence
        profile_data.pop("last_video_at", None)
        saved = _json_object(profile_data["raw_platform_data"])
        saved[SCHEMA_KEY] = build_post_evidence(raw, raw.get("posts") or [])
        profile_data["raw_platform_data"] = json.dumps(saved, ensure_ascii=True, separators=(",", ":"))
    try:
        result = write_kol_profile_basics(
            None,
            profile_data,
            dry_run=False,
            conn=conn,
            method="online_strict_qualified_materialize_v1",
            commit_write=False,
        )
        target_id = _positive_int(result.get("kol_pool_id")) if isinstance(result, dict) else None
        if not target_id or result.get("matched_existing") is True:
            conn.rollback()
            return {"duplicate_local_inventory": True, "kol_pool_id": target_id, "operation": "existing", "db_reads": 2}
        postexisting = _matching_pool_ids(conn, probe) - {target_id}
        if postexisting:
            conn.rollback()
            return {
                "duplicate_local_inventory": True,
                "kol_pool_id": min(postexisting),
                "operation": "race_existing",
                "db_reads": 4,
            }
        conn.commit()
        return {**result, "matched_existing": False, "db_reads": 4, "canonical_fingerprint": fingerprint}
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        raise
