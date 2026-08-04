"""Database identity and admin lookup contracts for release-only JWTs."""

from __future__ import annotations

import ipaddress
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse


PG_READ_ONLY_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c search_path=pg_catalog,public"
)
SAFE_DATABASE_QUERY_PARAMETERS = {
    "application_name",
    "channel_binding",
    "connect_timeout",
    "fallback_application_name",
    "gssencmode",
    "keepalives",
    "keepalives_count",
    "keepalives_idle",
    "keepalives_interval",
    "ssl_min_protocol_version",
    "ssl_max_protocol_version",
    "sslcrl",
    "sslcrldir",
    "sslmode",
    "sslrootcert",
    "sslsni",
    "tcp_user_timeout",
}

PG_ACCEPTANCE_ADMIN_QUERY = """
 SELECT u.id, COALESCE(s.role,u.role,'admin') AS effective_role
 FROM public.users AS u
 LEFT JOIN public.staff AS s ON s.user_id=u.id
 WHERE COALESCE(u.status,'')='approved' AND
 ((COALESCE(s.active,0)=1 AND COALESCE(s.role,'')='admin')
  OR (s.id IS NULL AND COALESCE(u.role,'')='admin'))
"""
SQLITE_ACCEPTANCE_ADMIN_QUERY = """
 SELECT u.id, COALESCE(s.role,u.role,'admin') AS effective_role
 FROM users u LEFT JOIN staff s ON s.user_id=u.id
 WHERE COALESCE(u.status,'')='approved' AND
 ((COALESCE(s.active,0)=1 AND COALESCE(s.role,'')='admin')
  OR (s.id IS NULL AND COALESCE(u.role,'')='admin'))
"""


def _is_loopback(host: str | None) -> bool:
    clean = str(host or "").strip().lower()
    if clean == "localhost":
        return True
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def validated_postgres_database_name(
    database_url: str,
    *,
    require_loopback: bool,
) -> str:
    try:
        parsed = urlparse(str(database_url or "").strip())
        query = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
        _port = parsed.port
    except ValueError as exc:
        raise RuntimeError("configured PostgreSQL URL is invalid") from exc
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or parsed.fragment
        or parsed.path.count("/") != 1
        or not database
        or "/" in database
        or "\x00" in database
        or (require_loopback and not _is_loopback(parsed.hostname))
    ):
        raise RuntimeError("configured PostgreSQL identity is not release-safe")
    if any(
        key.lower() not in SAFE_DATABASE_QUERY_PARAMETERS
        for key, _value in query
    ):
        raise RuntimeError("configured PostgreSQL query may override connection identity")
    return database


def validated_local_database(
    database_url: str,
    db_path: Path,
    root: Path,
) -> str | None:
    if str(database_url or "").strip().lower().startswith(("postgres:", "postgresql:")):
        return validated_postgres_database_name(
            database_url,
            require_loopback=True,
        )
    try:
        db_path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError("configured SQLite database is outside the repository") from exc
    return None


def verify_pg_readonly_identity(cursor: Any, *, expected_database: str) -> None:
    cursor.execute("SHOW transaction_read_only")
    readonly = cursor.fetchone()
    if not readonly or str(readonly[0]).lower() not in {"on", "true", "1"}:
        raise RuntimeError("admin lookup did not enter a read-only transaction")
    cursor.execute("SHOW search_path")
    search_path = cursor.fetchone()
    if not search_path or str(search_path[0]).replace(" ", "") != "pg_catalog,public":
        raise RuntimeError("admin lookup search_path is not release-safe")
    cursor.execute("SELECT pg_catalog.current_database()")
    database = cursor.fetchone()
    if not database or str(database[0]) != str(expected_database):
        raise RuntimeError("admin lookup database identity mismatch")


def select_acceptance_pg_admin(
    connection: Any,
    *,
    expected_database: str,
) -> tuple[int, str]:
    with connection.cursor() as cursor:
        verify_pg_readonly_identity(cursor, expected_database=expected_database)
        cursor.execute(
            "SELECT pg_catalog.to_regclass('public.vkpi_kol_search_sessions')"
        )
        history = bool(cursor.fetchone()[0])
        order = (
            "(SELECT COUNT(*) FROM public.vkpi_kol_search_sessions AS ks "
            "WHERE ks.created_by=u.id) DESC,"
            if history
            else ""
        )
        cursor.execute(
            PG_ACCEPTANCE_ADMIN_QUERY
            + f" ORDER BY {order} COALESCE(s.is_owner,0) DESC,u.id LIMIT 1"
        )
        row = cursor.fetchone()
    if not row:
        raise RuntimeError("no approved local admin principal found")
    return int(row[0]), str(row[1] or "admin")


def select_acceptance_sqlite_admin(
    connection: sqlite3.Connection,
) -> tuple[int, str]:
    row = connection.execute(
        SQLITE_ACCEPTANCE_ADMIN_QUERY
        + " ORDER BY COALESCE(s.is_owner,0) DESC,u.id LIMIT 1"
    ).fetchone()
    if not row:
        raise RuntimeError("no approved local admin principal found")
    return int(row[0]), str(row[1] or "admin")
