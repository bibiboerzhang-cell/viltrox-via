#!/usr/bin/env python3
"""Provision real staff accounts for the P4 observation window.

Default mode is dry-run. Use --apply only after reviewing the planned actions.

This script intentionally does not invent staff emails. Operators must pass a
CSV or explicit --staff rows from a real team roster.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.permissions import default_permissions_for_role
from app.core.security import hash_password, invalidate_user_cache
from app.db.connection import get_conn, is_postgres_runtime


ALLOWED_ROLES = {"employee", "manager", "admin"}
DEFAULT_PASSWORD_ENV = "VKPI_OBSERVATION_DEFAULT_PASSWORD"


@dataclass
class StaffRecord:
    email: str
    name: str
    role: str = "employee"
    initial_password: str = ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _bool(value: bool) -> int | bool:
    # Current users/staff runtime schema stores bool-like values as integer.
    return 1 if value else 0


def _normalize_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError(f"Invalid email: {value!r}")
    return email


def _normalize_role(value: str) -> str:
    role = str(value or "employee").strip().lower()
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Invalid role {value!r}; expected one of {sorted(ALLOWED_ROLES)}")
    return role


def _safe_name(email: str, name: str) -> str:
    candidate = str(name or "").strip()
    if candidate:
        return candidate
    return email.split("@", 1)[0]


def _generated_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _creator_code(email: str) -> str:
    digest = hashlib.sha1(email.encode("utf-8")).hexdigest()[:10]
    return f"staff_{digest}"


def _fetch_user(conn, email: str):
    return conn.execute("SELECT * FROM users WHERE lower(email)=?", (email.lower(),)).fetchone()


def _fetch_staff(conn, user_id: int):
    return conn.execute("SELECT * FROM staff WHERE user_id=? ORDER BY active DESC, id DESC LIMIT 1", (int(user_id),)).fetchone()


def _insert_user(conn, record: StaffRecord, password_hash: str) -> int:
    now = _now()
    if is_postgres_runtime():
        row = conn.execute(
            """
            INSERT INTO users (
                created_at, email, password_hash, name, creator_code, status, role,
                points_balance, points_pending, points_total, email_verified,
                social_verified, avatar_url, bio, signature, tier_status, trust_score,
                trust_updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            RETURNING id
            """,
            (
                now,
                record.email,
                password_hash,
                record.name,
                _creator_code(record.email),
                "approved",
                record.role,
                0,
                0,
                0,
                _bool(True),
                _bool(False),
                "",
                "",
                "",
                "approved",
                80,
                now,
            ),
        ).fetchone()
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO users (
            created_at, email, password_hash, name, creator_code, status, role,
            points_balance, points_pending, points_total, email_verified,
            social_verified, avatar_url, bio, signature, tier_status, trust_score,
            trust_updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            now,
            record.email,
            password_hash,
            record.name,
            _creator_code(record.email),
            "approved",
            record.role,
            0,
            0,
            0,
            _bool(True),
            _bool(False),
            "",
            "",
            "",
            "approved",
            80,
            now,
        ),
    )
    return int(cur.lastrowid)


def _update_user(conn, user_id: int, record: StaffRecord, password_hash: str | None, *, reset_password: bool) -> None:
    params: list[Any] = [
        record.name,
        "approved",
        record.role,
        _bool(True),
        int(user_id),
    ]
    conn.execute(
        "UPDATE users SET name=?, status=?, role=?, email_verified=? WHERE id=?",
        params,
    )
    if reset_password or password_hash:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, int(user_id)))


def _insert_staff(conn, user_id: int, role: str, permissions: dict[str, str]) -> int:
    now = _now()
    conn.execute(
        """
        INSERT INTO staff (
            user_id, role, permissions_json, mfa_enabled, active, invited_at,
            accepted_at, is_owner, email_domain_verified
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(user_id),
            role,
            _json(permissions),
            _bool(False),
            _bool(True),
            now,
            now,
            _bool(False),
            _bool(True),
        ),
    )
    row = conn.execute("SELECT id FROM staff WHERE user_id=? ORDER BY id DESC LIMIT 1", (int(user_id),)).fetchone()
    if not row:
        raise RuntimeError(f"staff insert failed for user_id={user_id}")
    return int(row["id"])


def _update_staff(conn, staff_id: int, role: str, permissions: dict[str, str]) -> None:
    now = _now()
    conn.execute(
        """
        UPDATE staff
        SET role=?, permissions_json=?, active=?, email_domain_verified=?, accepted_at=COALESCE(accepted_at, ?)
        WHERE id=?
        """,
        (role, _json(permissions), _bool(True), _bool(True), now, int(staff_id)),
    )


def _password_for_record(
    record: StaffRecord,
    *,
    apply: bool,
    default_password: str,
    generate_passwords: bool,
    existing_user: bool,
    reset_password: bool,
) -> tuple[str, str]:
    if not apply:
        return "", "not_required_dry_run"
    if existing_user and not reset_password:
        return "", "unchanged"
    if record.initial_password:
        return record.initial_password, "record"
    if default_password:
        return default_password, "env"
    if generate_passwords:
        return _generated_password(), "generated"
    raise ValueError(
        f"Password required for {record.email}. Set {DEFAULT_PASSWORD_ENV}, pass CSV initial_password, "
        "or use --generate-passwords."
    )


def provision_records(
    records: list[StaffRecord],
    *,
    apply: bool = False,
    allow_external: bool = False,
    default_password: str = "",
    generate_passwords: bool = False,
    reset_password: bool = False,
    include_passwords: bool = False,
) -> dict[str, Any]:
    conn = get_conn()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        email = _normalize_email(raw.email)
        if email in seen:
            raise ValueError(f"Duplicate email in input: {email}")
        seen.add(email)
        if not allow_external and not email.endswith("@viltrox.com"):
            raise ValueError(f"Refusing non-viltrox staff email without --allow-external: {email}")
        record = StaffRecord(
            email=email,
            name=_safe_name(email, raw.name),
            role=_normalize_role(raw.role),
            initial_password=str(raw.initial_password or ""),
        )
        user = _fetch_user(conn, record.email)
        existing_user = bool(user)
        password, password_source = _password_for_record(
            record,
            apply=apply,
            default_password=default_password,
            generate_passwords=generate_passwords,
            existing_user=existing_user,
            reset_password=reset_password,
        )
        password_hash = hash_password(password) if password else None
        action = "update_existing_user" if existing_user else "create_user"
        user_id = int(user["id"]) if user else 0
        staff_id = 0
        staff_action = "dry_run"
        if apply:
            if existing_user:
                _update_user(conn, user_id, record, password_hash, reset_password=reset_password)
            else:
                assert password_hash
                user_id = _insert_user(conn, record, password_hash)
            permissions = default_permissions_for_role(record.role, owner=False)
            staff = _fetch_staff(conn, user_id)
            if staff:
                staff_id = int(staff["id"])
                _update_staff(conn, staff_id, record.role, permissions)
                staff_action = "update_staff"
            else:
                staff_id = _insert_staff(conn, user_id, record.role, permissions)
                staff_action = "create_staff"
            conn.commit()
            invalidate_user_cache(user_id)
        results.append(
            {
                "email": record.email,
                "name": record.name,
                "role": record.role,
                "action": action,
                "staff_action": staff_action,
                "user_id": user_id if apply else None,
                "staff_id": staff_id if apply else None,
                "password_source": password_source,
                **({"initial_password": password} if include_passwords and password else {}),
            }
        )
    return {
        "mode": "apply" if apply else "dry_run",
        "count": len(results),
        "records": results,
        "passwords_included": bool(include_passwords),
    }


def load_csv(path: Path) -> list[StaffRecord]:
    rows: list[StaffRecord] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=2):
            email = str(row.get("email") or "").strip()
            if not email:
                raise ValueError(f"CSV row {idx} missing email")
            rows.append(
                StaffRecord(
                    email=email,
                    name=str(row.get("name") or "").strip(),
                    role=str(row.get("role") or "employee").strip() or "employee",
                    initial_password=str(row.get("initial_password") or "").strip(),
                )
            )
    return rows


def parse_staff_arg(value: str) -> StaffRecord:
    parts = [part.strip() for part in str(value or "").split(",")]
    if len(parts) == 1:
        return StaffRecord(email=parts[0], name="", role="employee")
    if len(parts) == 2:
        return StaffRecord(email=parts[0], name=parts[1], role="employee")
    return StaffRecord(email=parts[0], name=parts[1], role=parts[2] or "employee")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provision V-KPI observation staff accounts.")
    parser.add_argument("--csv", type=Path, help="CSV with columns: email,name,role[,initial_password].")
    parser.add_argument(
        "--staff",
        action="append",
        default=[],
        help="Inline staff row: email,name,role. Can be repeated.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually create/update users and staff.")
    parser.add_argument("--allow-external", action="store_true", help="Allow non-@viltrox.com emails.")
    parser.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV, help="Environment variable holding default password.")
    parser.add_argument("--generate-passwords", action="store_true", help="Generate passwords for new or reset accounts.")
    parser.add_argument("--reset-password", action="store_true", help="Reset existing users' passwords.")
    parser.add_argument("--show-passwords", action="store_true", help="Print generated/provided passwords in output.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        records: list[StaffRecord] = []
        if args.csv:
            records.extend(load_csv(args.csv))
        records.extend(parse_staff_arg(row) for row in args.staff)
        if not records:
            parser.error("Pass --csv or at least one --staff row.")
        default_password = os.environ.get(str(args.password_env or DEFAULT_PASSWORD_ENV), "")
        result = provision_records(
            records,
            apply=bool(args.apply),
            allow_external=bool(args.allow_external),
            default_password=default_password,
            generate_passwords=bool(args.generate_passwords),
            reset_password=bool(args.reset_password),
            include_passwords=bool(args.show_passwords),
        )
    except Exception as exc:
        error = {"status": "error", "message": str(exc), "mode": "apply" if args.apply else "dry_run"}
        print(json.dumps(error, ensure_ascii=False, sort_keys=True) if args.json else f"VKPI_OBSERVATION_STAFF_PROVISIONING_ERROR {json.dumps(error, ensure_ascii=False, sort_keys=True)}")
        return 2
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=None if args.json else 2)
    if args.json:
        print(text)
    else:
        print("VKPI_OBSERVATION_STAFF_PROVISIONING", text)
        if not args.apply:
            print("Dry-run only. Review output, then rerun with --apply.")
        if args.apply and args.generate_passwords and not args.show_passwords:
            print("Generated passwords were hidden. Use --show-passwords only in a private terminal if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
