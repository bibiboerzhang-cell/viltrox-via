"""
Student identity table setup for app.db.migrations.
"""
from __future__ import annotations

from typing import Any


def create_student_identity_tables(c: Any) -> None:
    # ── schools ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS schools (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            school_id         TEXT NOT NULL UNIQUE,
            school_code       TEXT NOT NULL,
            school_name       TEXT NOT NULL,
            school_name_native TEXT DEFAULT '',
            country           TEXT DEFAULT '',
            region            TEXT DEFAULT '',
            school_type       TEXT DEFAULT 'film',
            tier              TEXT DEFAULT 'standard',
            partnership_status TEXT DEFAULT 'pilot',
            visual_theme_json TEXT DEFAULT '{}',
            metadata_json     TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    # ── student_qr_codes ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_qr_codes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            qr_id             TEXT NOT NULL UNIQUE,
            school_id         TEXT NOT NULL,
            issued_batch      TEXT DEFAULT '',
            display_serial    TEXT DEFAULT '',
            claim_token       TEXT NOT NULL,
            claim_signature   TEXT NOT NULL,
            claim_url         TEXT DEFAULT '',
            qr_code_url       TEXT DEFAULT '',
            card_image_url    TEXT DEFAULT '',
            manifest_url      TEXT DEFAULT '',
            status            TEXT DEFAULT 'issued',
            roster_mode       TEXT DEFAULT 'anonymous',
            bound_user_id     INTEGER DEFAULT 0,
            bound_at          TEXT DEFAULT '',
            issued_at         TEXT NOT NULL,
            expires_at        TEXT DEFAULT '',
            revoked_reason    TEXT DEFAULT '',
            prefilled_json    TEXT DEFAULT '{}',
            metadata_json     TEXT DEFAULT '{}'
        )
    """)

    # ── student_verifications ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_verifications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL,
            school_id         TEXT NOT NULL,
            student_id_code   TEXT NOT NULL,
            verification_method TEXT DEFAULT 'qr_scan',
            verification_proof_json TEXT DEFAULT '{}',
            status            TEXT DEFAULT 'active',
            commission_rate_override REAL DEFAULT 0.10,
            verified_by       TEXT DEFAULT 'system_qr',
            verified_at       TEXT NOT NULL,
            expires_at        TEXT DEFAULT '',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            UNIQUE(user_id, school_id)
        )
    """)

    # ── student_identity_registry ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_identity_registry (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id_code   TEXT NOT NULL UNIQUE,
            school_id         TEXT NOT NULL,
            full_name         TEXT DEFAULT '',
            major             TEXT DEFAULT '',
            year_label        TEXT DEFAULT '',
            status            TEXT DEFAULT 'active',
            source            TEXT DEFAULT 'seed',
            bound_user_id     INTEGER DEFAULT 0,
            claimed_at        TEXT DEFAULT '',
            metadata_json     TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)

    # ── student_scan_events ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_scan_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key         TEXT NOT NULL UNIQUE,
            qr_id             TEXT DEFAULT '',
            user_id           INTEGER DEFAULT 0,
            school_id         TEXT DEFAULT '',
            event_type        TEXT NOT NULL,
            location          TEXT DEFAULT '',
            event_payload_json TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)

    # ── student_identity_audit_log ──
    c.execute("""
        CREATE TABLE IF NOT EXISTS student_identity_audit_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            audit_key         TEXT NOT NULL UNIQUE,
            qr_id             TEXT DEFAULT '',
            user_id           INTEGER DEFAULT 0,
            school_id         TEXT DEFAULT '',
            audit_type        TEXT NOT NULL,
            actor             TEXT DEFAULT '',
            reason            TEXT DEFAULT '',
            payload_json      TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL
        )
    """)
