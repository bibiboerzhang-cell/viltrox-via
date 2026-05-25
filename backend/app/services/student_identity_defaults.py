"""School and registry defaults for student identity."""
from __future__ import annotations

import json
from typing import Any

from app.core.config import IS_PRODUCTION
from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.student_identity import create_or_update_school, get_school, list_schools
from app.services.student_identity_common import (
    _PILOT_SCHOOL_THEMES,
    _STUDENT_ID_REGISTRY_SEEDS,
    _normalize_public_vid,
    _utcnow,
)

def ensure_student_school_defaults() -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    for school_id, payload in _PILOT_SCHOOL_THEMES.items():
        created.append(
            create_or_update_school(
                school_id=school_id,
                school_code=str(payload.get("school_code") or ""),
                school_name=str(payload.get("school_name") or ""),
                school_name_native=str(payload.get("school_name_native") or ""),
                country=str(payload.get("country") or ""),
                region=str(payload.get("region") or ""),
                school_type="film",
                tier=str(payload.get("tier") or "standard"),
                partnership_status=str(payload.get("partnership_status") or "pilot"),
                visual_theme=payload.get("visual_theme") or {},
                metadata={},
            )
        )
    return created

def _ensure_student_identity_registry_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_identity_registry (
                id BIGSERIAL PRIMARY KEY,
                student_id_code TEXT NOT NULL UNIQUE,
                school_id TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                major TEXT DEFAULT '',
                year_label TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                source TEXT DEFAULT 'seed',
                bound_user_id BIGINT DEFAULT 0,
                claimed_at TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_identity_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id_code TEXT NOT NULL UNIQUE,
                school_id TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                major TEXT DEFAULT '',
                year_label TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                source TEXT DEFAULT 'seed',
                bound_user_id INTEGER DEFAULT 0,
                claimed_at TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    conn.commit()

def ensure_student_identity_registry_defaults() -> list[dict[str, Any]]:
    ensure_student_school_defaults()
    _ensure_student_identity_registry_schema()
    conn = get_conn()
    now = _utcnow()
    for item in _STUDENT_ID_REGISTRY_SEEDS:
        conn.execute(
            """
            INSERT OR IGNORE INTO student_identity_registry (
                student_id_code, school_id, full_name, major, year_label, status,
                source, bound_user_id, claimed_at, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(item.get("student_id_code") or "").strip().upper(),
                str(item.get("school_id") or "").strip(),
                str(item.get("full_name") or "").strip(),
                str(item.get("major") or "").strip(),
                str(item.get("year_label") or "").strip(),
                "active",
                "seed",
                0,
                "",
                json.dumps({"seeded": True}, ensure_ascii=False),
                now,
                now,
            ),
        )
    conn.commit()
    rows = conn.execute(
        """
        SELECT * FROM student_identity_registry
        ORDER BY school_id ASC, student_id_code ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]

def _school_from_student_id_code(student_id_code: str) -> dict[str, Any]:
    code = str(student_id_code or "").strip().upper()
    if not code:
        return {}
    school_code = code.split("-", 1)[0].strip()
    if not school_code:
        return {}
    for school in list_schools(limit=240):
        if str(school.get("school_code") or "").strip().upper() == school_code:
            return school
    return {}
