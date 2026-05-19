"""
db/connection.py — pooled compat DB runtime for Viltrox 2.0

2.0 keeps the old sqlite-style repository SQL surface (`?` placeholders,
sqlite-like row access, `lastrowid`, `PRAGMA table_info`) but runs production
traffic through a pooled Postgres runtime.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets as secrets_mod
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.logging import get_logger
from app.core.config import (
    DB_RUNTIME_URL,
    DB_BUSY_TIMEOUT_MS,
    DB_CACHE_SIZE_KB,
    DB_MMAP_SIZE_MB,
    DB_PATH,
    DB_RUNTIME_BACKEND,
    DB_USE_PGBOUNCER,
    DB_WAL_AUTOCHECKPOINT,
    IS_PRODUCTION,
    POSTGRES_POOL_MAX_SIZE,
    POSTGRES_POOL_MIN_SIZE,
    POSTGRES_POOL_TIMEOUT_SEC,
)


logger = get_logger(__name__)

_db_local = threading.local()
_scoped_conn: ContextVar[Any | None] = ContextVar("viltrox_db_scoped_conn", default=None)
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _PROJECT_ROOT / "migrations"
_ENV_PATH = _PROJECT_ROOT / ".env"
_POSTGRES_MIGRATION_SEQUENCE = (
    "003_postgres_baseline.sql",
    "005_via_control_stack.sql",
    "006_policy_governance_student_identity.sql",
    "007_via_reward_trace.sql",
    "008_p1_observability.sql",
    "009_job_runtime_stack.sql",
    "010_party_layer.sql",
    "011_security_lockout.sql",
    "012_admin_system_ops.sql",
    "013_creator_public.sql",
    "014_pending_asset_cleanup.sql",
    "015_v5_commerce_admin_schema.sql",
    "016_ai_usage_log.sql",
    "018_staff_permissions.sql",
    "019_kol_operations.sql",
    "020_kol_candidates.sql",
    "021_activities.sql",
    "022_kol_account_dossier.sql",
    "023_vkpi_core.sql",
    "024_vkpi_metric_lineage.sql",
    "025_vkpi_product_cost_catalog.sql",
    "026_vkpi_reports.sql",
    "027_vkpi_reconciliation.sql",
    "028_vkpi_audit_logs.sql",
    "029_vkpi_analytics.sql",
    "030_vkpi_employee_channels.sql",
    "031_vkpi_p5_selected.sql",
    "032_kol_contact_profile.sql",
    "033_vkpi_daily_outreach_digest.sql",
    "034_vkpi_shopify_order_snapshots.sql",
    "035_vkpi_project_evidence_assets.sql",
    "036_vkpi_cost_lifecycle_audit.sql",
    "037_vkpi_product_launches.sql",
    "038_vkpi_market_scan.sql",
    "039_vkpi_kol_pool.sql",
    "040_vkpi_kol_recommendations.sql",
    "041_vkpi_industry_projects.sql",
    "042_vkpi_industry_accounts.sql",
    "043_vkpi_industry_snapshots.sql",
    "044_vkpi_industry_posts.sql",
    "045_vkpi_automation_outcomes.sql",
    "046_vkpi_settings.sql",
    "047_vkpi_user_preferences.sql",
    "048_vkpi_notification_settings.sql",
    "055_vkpi_industry_post_media.sql",
    "056_vkpi_job_runtime_extensions.sql",
    "057_vkpi_ai_cost_budget.sql",
    "058_vkpi_legacy_import.sql",
    "058a_vkpi_legacy_import_launch_plan.sql",
    "058b_vkpi_legacy_import_dedupe.sql",
    "058c_vkpi_legacy_import_batch_column_compat.sql",
    "058d_vkpi_legacy_official_materials.sql",
    "058e_vkpi_legacy_entity_resolution.sql",
    "058f_vkpi_legacy_kol_entities_decisions.sql",
    "059_vkpi_memory_tables.sql",
)

try:
    import psycopg
except Exception:
    psycopg = None

try:
    from psycopg_pool import ConnectionPool
except Exception:
    ConnectionPool = None

_PG_POOL: Any | None = None
_POOL_LOCK = threading.Lock()


class _ScopedConnectionHandle:
    """Mutable request-scope holder that survives sync endpoint thread hops."""

    __slots__ = ("conn",)

    def __init__(self) -> None:
        self.conn: Any | None = None

    def get(self) -> Any:
        if self.conn is None:
            self.conn = _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()
        return self.conn

    def close(self) -> None:
        if self.conn is None:
            return
        try:
            self.conn.close()
        finally:
            self.conn = None


class CompatRow:
    """A sqlite3.Row-like object that works for psycopg result rows."""

    __slots__ = ("_columns", "_values", "_mapping")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(str(col) for col in columns)
        self._values = tuple(values)
        self._mapping = {self._columns[idx]: self._values[idx] for idx in range(len(self._columns))}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def items(self) -> list[tuple[str, Any]]:
        return list(self._mapping.items())

    def values(self) -> list[Any]:
        return list(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def __repr__(self) -> str:
        return f"CompatRow({self._mapping!r})"


def _column_name(col: Any) -> str:
    return str(getattr(col, "name", col[0] if isinstance(col, (tuple, list)) and col else col))


def _normalize_pg_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _translate_sql_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not in_double:
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            out.append("%s")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _translate_insert_or_ignore(sql: str) -> str:
    if not re.match(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", sql, flags=re.IGNORECASE):
        return sql
    translated = re.sub(
        r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+",
        "INSERT INTO ",
        sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if re.search(r"\bON\s+CONFLICT\b", translated, flags=re.IGNORECASE):
        return translated
    if re.search(r"\bRETURNING\b", translated, flags=re.IGNORECASE):
        parts = re.split(r"\bRETURNING\b", translated, maxsplit=1, flags=re.IGNORECASE)
        return f"{parts[0].rstrip()} ON CONFLICT DO NOTHING RETURNING {parts[1].lstrip()}"
    return translated.rstrip() + " ON CONFLICT DO NOTHING"


def _translate_pragma_table_info(sql: str) -> tuple[str, bool]:
    match = re.match(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", sql, flags=re.IGNORECASE)
    if not match:
        return sql, False
    table_name = match.group(1).strip().strip("'").strip('"')
    translated = """
        SELECT
            (cols.ordinal_position - 1) AS cid,
            cols.column_name AS name,
            COALESCE(cols.data_type, '') AS type,
            CASE WHEN cols.is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
            cols.column_default AS dflt_value,
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                     AND tc.table_schema = current_schema()
                     AND tc.table_name = %s
                     AND kcu.column_name = cols.column_name
                ) THEN 1 ELSE 0
            END AS pk
        FROM information_schema.columns cols
        WHERE cols.table_schema = current_schema()
          AND cols.table_name = %s
        ORDER BY cols.ordinal_position
    """
    return translated, True


def _translate_sqlite_master(sql: str) -> tuple[str, bool]:
    normalized = " ".join(sql.strip().split())
    if "FROM sqlite_master" not in normalized:
        return sql, False
    translated = """
        SELECT table_name AS name
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_type = 'BASE TABLE'
          AND table_name = %s
    """
    return translated, True


def _translate_sql_dialect(sql: str) -> str:
    translated = _translate_sql_placeholders(_translate_insert_or_ignore(sql))
    translated = re.sub(
        r"\s+COLLATE\s+NOCASE\b",
        "",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"strftime\('%Y-W%W',\s*([^)]+?)\s*\)",
        r"""TO_CHAR(\1, 'IYYY-"W"IW')""",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"strftime\('%H',\s*([^)]+?)\s*\)",
        r"TO_CHAR(CAST(\1 AS TIMESTAMP), 'HH24')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"substr\(([^,]+),\s*(\d+),\s*(\d+)\)",
        r"SUBSTRING(CAST(\1 AS TEXT) FROM \2 FOR \3)",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now',\s*'start of month'\)",
        r"(DATE_TRUNC('month', NOW()))::date",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now',\s*'start of year'\)",
        r"(DATE_TRUNC('year', NOW()))::date",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now',\s*'start of month'\)",
        r"DATE_TRUNC('month', NOW())",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now',\s*'start of year'\)",
        r"DATE_TRUNC('year', NOW())",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now',\s*%s\s*\|\|\s*' days'\)",
        r"(NOW() + ((%s || ' days'))::interval)",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now',\s*%s\s*\|\|\s*' days'\)",
        r"DATE(NOW() + ((%s || ' days'))::interval)",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now',\s*%s\)",
        r"(NOW() + (%s)::interval)",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now',\s*'([^']+)'\)",
        lambda match: f"(NOW() + INTERVAL '{match.group(1)}')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now',\s*'([^']+)'\)",
        lambda match: f"DATE(NOW() + INTERVAL '{match.group(1)}')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"datetime\('now'\)",
        "NOW()",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"date\('now'\)",
        "DATE(NOW())",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"GROUP_CONCAT\s*\(\s*DISTINCT\s+([^)]+?)\s*\)",
        r"STRING_AGG(DISTINCT (\1)::text, ',' ORDER BY (\1)::text)",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"GROUP_CONCAT\s*\(\s*([^)]+?)\s*\)",
        r"STRING_AGG((\1)::text, ',')",
        translated,
        flags=re.IGNORECASE,
    )
    translated = re.sub(
        r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*_json)\s+(NOT\s+)?LIKE\b",
        lambda match: f"CAST({match.group(1)} AS TEXT) {match.group(2) or ''}LIKE",
        translated,
        flags=re.IGNORECASE,
    )
    return translated


def is_postgres_runtime() -> bool:
    return DB_RUNTIME_BACKEND == "postgres" and bool(DB_RUNTIME_URL) and psycopg is not None and ConnectionPool is not None


def _get_pg_pool():
    global _PG_POOL
    if not is_postgres_runtime():
        return None
    if _PG_POOL is None:
        with _POOL_LOCK:
            if _PG_POOL is None:
                _PG_POOL = ConnectionPool(
                    conninfo=DB_RUNTIME_URL,
                    min_size=POSTGRES_POOL_MIN_SIZE,
                    max_size=POSTGRES_POOL_MAX_SIZE,
                    timeout=float(POSTGRES_POOL_TIMEOUT_SEC),
                    open=False,
                    kwargs={
                        "connect_timeout": 5,
                        # PgBouncer transaction pooling is incompatible with server-side prepared statements.
                        "prepare_threshold": None if DB_USE_PGBOUNCER else 5,
                    },
                    name="viltrox-2.0",
                )
                _PG_POOL.open(wait=True, timeout=float(POSTGRES_POOL_TIMEOUT_SEC))
    return _PG_POOL


class PostgresCompatCursor:
    def __init__(self, raw_cursor: Any) -> None:
        self._cursor = raw_cursor
        self._prefetched_rows: list[Sequence[Any]] = []
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        translated_sql, translated_params = _translate_special_sql(sql, params or ())
        try:
            self._cursor.execute(_translate_sql_dialect(translated_sql), list(translated_params or ()))
            if self._cursor.description and re.search(r"\bRETURNING\b", translated_sql, flags=re.IGNORECASE):
                row = self._cursor.fetchone()
                if row is not None:
                    self._prefetched_rows.append(row)
                    try:
                        self.lastrowid = int(row[0])
                    except Exception:
                        self.lastrowid = None
        except Exception:
            try:
                self._cursor.connection.rollback()
            except Exception:
                pass
            raise
        return self

    @property
    def description(self):
        desc = self._cursor.description or []
        return [(_column_name(col), None, None, None, None, None, None) for col in desc]

    @property
    def rowcount(self) -> int:
        return int(getattr(self._cursor, "rowcount", 0) or 0)

    def _columns(self) -> list[str]:
        return [_column_name(col) for col in (self._cursor.description or [])]

    def fetchone(self):
        if self._prefetched_rows:
            row = self._prefetched_rows.pop(0)
        else:
            row = self._cursor.fetchone()
        if row is None:
            return None
        return CompatRow(self._columns(), [_normalize_pg_value(value) for value in row])

    def fetchall(self):
        rows = list(self._prefetched_rows)
        self._prefetched_rows.clear()
        fetched = self._cursor.fetchall()
        if fetched:
            rows.extend(fetched)
        if not rows:
            return []
        return [CompatRow(self._columns(), [_normalize_pg_value(value) for value in row]) for row in rows]

    def close(self) -> None:
        self._cursor.close()


def _translate_special_sql(sql: str, params: Sequence[Any]) -> tuple[str, Sequence[Any]]:
    translated, matched = _translate_pragma_table_info(sql)
    if matched:
        table_name = re.match(r"^\s*PRAGMA\s+table_info\(([^)]+)\)\s*$", sql, flags=re.IGNORECASE).group(1).strip().strip("'").strip('"')
        return translated, (table_name, table_name)
    translated, matched = _translate_sqlite_master(sql)
    if matched:
        name = params[0] if params else ""
        return translated, (name,)
    return sql, params


class PostgresCompatConnection:
    """A minimal sqlite-compatible wrapper over psycopg connections."""

    def __init__(self, raw_conn: Any, pool: Any | None = None) -> None:
        self._raw = raw_conn
        self._pool = pool
        self._closed = False
        self.row_factory = sqlite3.Row

    def cursor(self) -> PostgresCompatCursor:
        return PostgresCompatCursor(self._raw.cursor())

    def execute(self, sql: str, params: Sequence[Any] | None = None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.rollback()
        except Exception:
            pass
        if self._pool is not None:
            self._pool.putconn(self._raw)
        else:
            self._raw.close()


def _build_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH),
        check_same_thread=False,
        timeout=max(5, DB_BUSY_TIMEOUT_MS / 1000),
        cached_statements=512,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute(f"PRAGMA cache_size={-abs(DB_CACHE_SIZE_KB)}")
    conn.execute(f"PRAGMA mmap_size={max(0, DB_MMAP_SIZE_MB) * 1024 * 1024}")
    conn.execute(f"PRAGMA wal_autocheckpoint={max(100, DB_WAL_AUTOCHECKPOINT)}")
    return conn


def _build_postgres_conn() -> PostgresCompatConnection:
    pool = _get_pg_pool()
    if pool is None:
        raise RuntimeError("Postgres runtime requested but psycopg/psycopg_pool is unavailable")
    return PostgresCompatConnection(pool.getconn(), pool=pool)


def open_standalone_conn() -> Any:
    """Return a dedicated connection for background work that must manage its own lifecycle."""
    return _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()


def close_standalone_conn(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle) and scoped.conn is conn:
        scoped.conn = None
    elif scoped is conn:
        _scoped_conn.set(None)
    if getattr(_db_local, "conn", None) is conn:
        _db_local.conn = None


def _bootstrap_sqlite_runtime() -> None:
    from app.db import migrations as sqlite_migrations

    sqlite_migrations.init_db()


def _run_runtime_seeders() -> None:
    from app.db.repositories.users import backfill_user_social_verified_flags
    from app.services.runtime_seed import ensure_runtime_seed_data

    try:
        ensure_runtime_seed_data()
        backfill_user_social_verified_flags()
    finally:
        local_conn = getattr(_db_local, "conn", None)
        if local_conn is not None:
            try:
                local_conn.close()
            except Exception:
                pass
            _db_local.conn = None


async def init_db_runtime() -> None:
    if not is_postgres_runtime():
        await asyncio.to_thread(_bootstrap_sqlite_runtime)
        await asyncio.to_thread(_run_runtime_seeders)
        return
    _get_pg_pool()
    await asyncio.to_thread(_run_postgres_migrations)
    await asyncio.to_thread(_bootstrap_default_admin)
    await asyncio.to_thread(_run_runtime_seeders)


async def close_db_runtime() -> None:
    global _PG_POOL
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle):
        try:
            scoped.close()
        except Exception:
            pass
        _scoped_conn.set(None)
    elif scoped is not None:
        try:
            scoped.close()
        except Exception:
            pass
        _scoped_conn.set(None)
    local_conn = getattr(_db_local, "conn", None)
    if local_conn is not None:
        try:
            local_conn.close()
        except Exception:
            pass
        _db_local.conn = None
    if _PG_POOL is not None:
        _PG_POOL.close()
        _PG_POOL = None


def _run_postgres_migrations() -> None:
    pool = _get_pg_pool()
    if pool is None:
        return
    with pool.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))")
            cur.execute("SELECT version_key FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
            for migration_name in _POSTGRES_MIGRATION_SEQUENCE:
                migration = _MIGRATIONS_DIR / migration_name
                if not migration.exists():
                    raise RuntimeError(f"Required Postgres migration is missing: {migration_name}")
                if migration.name in applied:
                    continue
                cur.execute(migration.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations(version_key) VALUES (%s) ON CONFLICT DO NOTHING",
                    (migration.name,),
                )
        conn.commit()


def _read_env_override(key: str) -> str:
    if not _ENV_PATH.exists():
        return ""
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except Exception:
        return ""
    return ""


def _bootstrap_default_admin() -> None:
    from app.core.security import hash_password

    pool = _get_pg_pool()
    if pool is None:
        return
    with pool.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE email = %s LIMIT 1", ("admin@viltrox.com",))
            admin_exists = cur.fetchone() is not None
            admin_pw_plain = os.environ.get("ADMIN_PASSWORD", "").strip() or _read_env_override("ADMIN_PASSWORD")
            if not admin_pw_plain and not admin_exists:
                if IS_PRODUCTION:
                    raise RuntimeError("2.0 production bootstrap requires ADMIN_PASSWORD when admin@viltrox.com does not exist")
                admin_pw_plain = secrets_mod.token_urlsafe(16)
                logger.warning(
                    "Generated ephemeral ADMIN_PASSWORD for local Postgres bootstrap only; persist manually if needed: %s",
                    admin_pw_plain,
                )
            if not admin_pw_plain:
                cur.execute("SELECT id FROM users WHERE email = %s LIMIT 1", ("admin@viltrox.com",))
            else:
                admin_pw = hash_password(admin_pw_plain)
                cur.execute(
                    """
                    INSERT INTO users (created_at, email, password_hash, name, status, role)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO UPDATE SET
                        role = EXCLUDED.role,
                        status = EXCLUDED.status
                    RETURNING id
                    """,
                    (
                        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "admin@viltrox.com",
                        admin_pw,
                        "Admin",
                        "approved",
                        "admin",
                    ),
                )
            admin_row = cur.fetchone()
            if admin_row:
                admin_user_id = int(admin_row[0])
                cur.execute("SELECT id FROM staff WHERE user_id = %s LIMIT 1", (admin_user_id,))
                if cur.fetchone() is None:
                    cur.execute(
                        """
                        INSERT INTO staff (
                            user_id, role, permissions_json, mfa_enabled, active,
                            invited_by, invited_at, accepted_at, is_owner, email_domain_verified
                        ) VALUES (%s, 'admin', %s, 0, 1, %s, now(), now(), 1, 1)
                        """,
                        (admin_user_id, json.dumps({"vkpi": "write"}), admin_user_id),
                    )
        conn.commit()


@asynccontextmanager
async def db_connection_scope() -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle()
        token = _scoped_conn.set(handle)
        try:
            yield None
        finally:
            try:
                handle.close()
            finally:
                _scoped_conn.reset(token)
        return
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_sqlite_conn()
        _db_local.conn = conn
    yield conn


@contextmanager
def db_connection_sync_scope() -> Iterator[Any]:
    if is_postgres_runtime():
        handle = _ScopedConnectionHandle()
        token = _scoped_conn.set(handle)
        try:
            yield None
        finally:
            try:
                handle.close()
            finally:
                _scoped_conn.reset(token)
        return
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_sqlite_conn()
        _db_local.conn = conn
    yield conn


def get_conn() -> Any:
    """Return a scoped DB connection, preferring a request/task-scoped Postgres handle."""
    scoped = _scoped_conn.get()
    if isinstance(scoped, _ScopedConnectionHandle):
        return scoped.get()
    if scoped is not None:
        return scoped
    conn = getattr(_db_local, "conn", None)
    if conn is None:
        conn = _build_postgres_conn() if is_postgres_runtime() else _build_sqlite_conn()
        _db_local.conn = conn
    return conn


def table_exists(table_name: str) -> bool:
    conn = get_conn()
    if is_postgres_runtime():
        row = conn.execute(
            "SELECT to_regclass(current_schema() || '.' || ?) AS regclass",
            (table_name,),
        ).fetchone()
        return bool(row and row["regclass"])
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


async def start_db_actor() -> None:
    if is_postgres_runtime():
        await init_db_runtime()
        logger.info("Postgres pooled runtime initialized")
        return
    backend = "sqlite"
    logger.info("Single-writer DB actor started (%s)", backend)


async def stop_db_actor() -> None:
    if is_postgres_runtime():
        await close_db_runtime()
        logger.info("Postgres pooled runtime closed")
        return
    conn = getattr(_db_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _db_local.conn = None
    logger.info("DB actor stopped")


async def db_read(fn: Callable[[], Any]) -> Any:
    async with db_connection_scope():
        return await asyncio.to_thread(fn)


async def db_write(fn: Callable[[], Any]) -> Any:
    async with db_connection_scope():
        return await asyncio.to_thread(fn)


def get_db_actor_stats() -> dict[str, Any]:
    if is_postgres_runtime():
        pool = _PG_POOL
        stats = pool.get_stats() if pool is not None else {}
        return {
            "mode": "postgres_pool",
            "running": pool is not None,
            "runtime_backend": "postgres",
            "pool": stats,
        }
    return {
        "mode": "sqlite_local",
        "running": True,
        "runtime_backend": "sqlite",
    }


def probe_postgres_connectivity() -> dict[str, Any]:
    result = {
        "configured": bool(DB_RUNTIME_URL),
        "driver_available": psycopg is not None and ConnectionPool is not None,
        "reachable": False,
        "runtime_selected": DB_RUNTIME_BACKEND == "postgres",
        "pool_open": _PG_POOL is not None,
        "pooler_enabled": bool(DB_USE_PGBOUNCER),
        "using_pool_url": bool(DB_USE_PGBOUNCER and DB_RUNTIME_URL),
    }
    if not DB_RUNTIME_URL or psycopg is None:
        return result
    try:
        if _PG_POOL is not None:
            with _PG_POOL.connection(timeout=float(POSTGRES_POOL_TIMEOUT_SEC)) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            result["reachable"] = True
            result["ok"] = True
            result["pool_stats"] = _PG_POOL.get_stats()
            return result
        with psycopg.connect(DB_RUNTIME_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        result["reachable"] = True
        result["ok"] = True
        return result
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:240]
        return result


__all__ = [
    "CompatRow",
    "db_connection_scope",
    "db_connection_sync_scope",
    "db_read",
    "db_write",
    "get_conn",
    "get_db_actor_stats",
    "init_db_runtime",
    "close_db_runtime",
    "is_postgres_runtime",
    "probe_postgres_connectivity",
    "start_db_actor",
    "stop_db_actor",
    "table_exists",
]
