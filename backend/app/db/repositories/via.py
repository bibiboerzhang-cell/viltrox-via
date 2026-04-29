"""
db/repositories/via.py — Via persona/session/memory persistence
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any

from app.core.config import VIA_BASE_MODEL
from app.db.connection import get_conn, is_postgres_runtime


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any, default: Any) -> str:
    data = default if value is None else value
    return json.dumps(data, ensure_ascii=False)


def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _persona_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"] or 0),
        "persona_key": row["persona_key"],
        "display_name": row["display_name"] or "Via",
        "archetype": row["archetype"] or "brand_avatar",
        "temperament": row["temperament"] or "balanced",
        "talk_style": row["talk_style"] or "warm",
        "talkativeness": float(row["talkativeness"] or 0.55),
        "curiosity": float(row["curiosity"] or 0.7),
        "outfit_code": row["outfit_code"] or "viltrox_core_black",
        "accessory_code": row["accessory_code"] or "",
        "profile": _load_json(row["profile_json"], {}),
        "memory_policy": _load_json(row["memory_policy_json"], {}),
        "affinity_points": int(row["affinity_points"] or 0),
        "wardrobe_points": int(row["wardrobe_points"] or 0),
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def _session_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "session_key": row["session_key"],
        "user_id": int(row["user_id"] or 0),
        "persona_id": int(row["persona_id"] or 0),
        "signed_device_id": row["signed_device_id"] or "",
        "client_fingerprint": row["client_fingerprint"] or "",
        "ip_hash": row["ip_hash"] or "",
        "current_surface": row["current_surface"] or "upload",
        "base_model": row["base_model"] or VIA_BASE_MODEL,
        "state": _load_json(row["session_state_json"], {}),
        "last_event_id": row["last_event_id"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "ended_at": row["ended_at"] or "",
    }


def _memory_ref_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "session_id": int(row["session_id"] or 0),
        "memory_kind": row["memory_kind"] or "",
        "source_ref": row["source_ref"] or "",
        "memory_key": row["memory_key"] or "",
        "weight": float(row["weight"] or 0.5),
        "payload": _load_json(row["payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def get_or_create_via_persona(
    *,
    user_id: int = 0,
    persona_key: str = "",
    display_name: str = "Via",
    archetype: str = "brand_avatar",
    temperament: str = "balanced",
    talk_style: str = "warm",
    talkativeness: float = 0.55,
    curiosity: float = 0.7,
    outfit_code: str = "viltrox_core_black",
    accessory_code: str = "",
    profile: Any = None,
    memory_policy: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    persona_key = str(persona_key or (f"user:{int(user_id)}:default" if int(user_id or 0) else "anon:default")).strip()
    conn.execute(
        """
        INSERT INTO via_personas (
            user_id, persona_key, display_name, archetype, temperament, talk_style,
            talkativeness, curiosity, outfit_code, accessory_code, profile_json,
            memory_policy_json, affinity_points, wardrobe_points, created_at, updated_at
        ) VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?)
        ON CONFLICT(persona_key) DO UPDATE SET
            user_id=excluded.user_id,
            display_name=excluded.display_name,
            archetype=excluded.archetype,
            temperament=excluded.temperament,
            talk_style=excluded.talk_style,
            talkativeness=excluded.talkativeness,
            curiosity=excluded.curiosity,
            outfit_code=excluded.outfit_code,
            accessory_code=excluded.accessory_code,
            profile_json=excluded.profile_json,
            memory_policy_json=excluded.memory_policy_json,
            updated_at=excluded.updated_at
        """,
        (
            int(user_id or 0),
            persona_key,
            display_name,
            archetype,
            temperament,
            talk_style,
            float(talkativeness or 0.55),
            float(curiosity or 0.7),
            outfit_code,
            accessory_code,
            _json(profile, {}),
            _json(memory_policy, {}),
            0,
            0,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM via_personas WHERE persona_key=?", (persona_key,)).fetchone()
    conn.commit()
    return _persona_from_row(row)


def update_via_persona(persona_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    if not patch:
        row = get_conn().execute("SELECT * FROM via_personas WHERE id=?", (int(persona_id),)).fetchone()
        return _persona_from_row(row)

    conn = get_conn()
    allowed = {
        "display_name",
        "archetype",
        "temperament",
        "talk_style",
        "talkativeness",
        "curiosity",
        "outfit_code",
        "accessory_code",
        "profile_json",
        "memory_policy_json",
        "affinity_points",
        "wardrobe_points",
    }
    values: list[Any] = []
    sets: list[str] = []
    for key, value in patch.items():
        if key not in allowed:
            continue
        if key.endswith("_json"):
            value = _json(value, {})
        sets.append(f"{key}=?")
        values.append(value)
    sets.append("updated_at=?")
    values.append(_utcnow())
    values.append(int(persona_id))
    conn.execute(f"UPDATE via_personas SET {', '.join(sets)} WHERE id=?", tuple(values))
    row = conn.execute("SELECT * FROM via_personas WHERE id=?", (int(persona_id),)).fetchone()
    conn.commit()
    return _persona_from_row(row)


def create_via_session(
    *,
    user_id: int = 0,
    persona_id: int = 0,
    session_key: str = "",
    signed_device_id: str = "",
    client_fingerprint: str = "",
    ip_hash: str = "",
    current_surface: str = "upload",
    base_model: str = VIA_BASE_MODEL,
    session_state: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    session_key = str(session_key or secrets.token_urlsafe(18)).strip()
    ended_at = None if is_postgres_runtime() else ""
    params = (
        session_key,
        int(user_id or 0),
        int(persona_id or 0),
        signed_device_id,
        client_fingerprint,
        ip_hash,
        current_surface,
        base_model or VIA_BASE_MODEL,
        _json(session_state, {}),
        "",
        now,
        now,
        ended_at,
    )
    sql = """
        INSERT INTO via_sessions (
            session_key, user_id, persona_id, signed_device_id, client_fingerprint,
            ip_hash, current_surface, base_model, session_state_json, last_event_id,
            created_at, updated_at, ended_at
        ) VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        session_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        session_id = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM via_sessions WHERE id=?", (session_id,)).fetchone()
    conn.commit()
    return _session_from_row(row)


def touch_via_session(
    session_key: str,
    *,
    current_surface: str = "",
    last_event_id: str = "",
    session_state: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    updates = ["updated_at=?"]
    values: list[Any] = [_utcnow()]
    if current_surface:
        updates.append("current_surface=?")
        values.append(current_surface)
    if last_event_id:
        updates.append("last_event_id=?")
        values.append(last_event_id)
    if session_state is not None:
        updates.append("session_state_json=?")
        values.append(_json(session_state, {}))
    values.append(session_key)
    conn.execute(f"UPDATE via_sessions SET {', '.join(updates)} WHERE session_key=?", tuple(values))
    row = conn.execute("SELECT * FROM via_sessions WHERE session_key=?", (session_key,)).fetchone()
    conn.commit()
    return _session_from_row(row)


def add_via_memory_ref(
    *,
    session_id: int,
    memory_kind: str,
    source_ref: str,
    memory_key: str = "",
    weight: float = 0.5,
    payload: Any = None,
) -> int:
    conn = get_conn()
    params = (
        int(session_id),
        memory_kind,
        source_ref,
        memory_key,
        float(weight or 0.5),
        _json(payload, {}),
        _utcnow(),
    )
    sql = """
        INSERT INTO via_memory_refs (
            session_id, memory_kind, source_ref, memory_key, weight, payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        ref_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        ref_id = int(cur.lastrowid)
    conn.commit()
    return ref_id


def list_via_memory_refs(session_id: int, limit: int = 40) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_memory_refs
        WHERE session_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(session_id), int(limit)),
    ).fetchall()
    return [_memory_ref_from_row(row) for row in rows]


def get_via_session_bundle(session_key: str, limit_memory: int = 40) -> dict[str, Any]:
    conn = get_conn()
    session_row = conn.execute("SELECT * FROM via_sessions WHERE session_key=?", (session_key,)).fetchone()
    if not session_row:
        return {}
    session = _session_from_row(session_row)
    persona_row = conn.execute("SELECT * FROM via_personas WHERE id=?", (session["persona_id"],)).fetchone()
    return {
        "session": session,
        "persona": _persona_from_row(persona_row),
        "memory_refs": list_via_memory_refs(session["id"], limit=limit_memory),
    }


def find_via_session(session_key: str) -> dict[str, Any]:
    row = get_conn().execute("SELECT * FROM via_sessions WHERE session_key=?", (session_key,)).fetchone()
    return _session_from_row(row)
